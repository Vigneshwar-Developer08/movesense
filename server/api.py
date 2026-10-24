"""FastAPI REST API server for BLE device management.

Wraps the existing acquisition.py BLE functions (discover_devices, verify_device)
into HTTP endpoints consumed by the Next.js frontend.

Usage:
    cd server
    uvicorn api:app --host 0.0.0.0 --port 8000 --reload
"""
import asyncio
import logging
import math
import os
import re
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import acquisition
import auth as auth_mod
import config
import local_store
import session as session_mod
import trajectory as trajectory_mod

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


# ---------------------------------------------------------------------------
# In-memory device state (single-user lab workstation model)
# ---------------------------------------------------------------------------
@dataclass
class ConnectedDevice:
    """Tracks a device that has been verified / connected."""
    address: str
    name: str
    serial: str | None = None
    model: str | None = None
    firmware: str | None = None
    manufacturer: str | None = None
    rssi: int | None = None
    connected_at: str = ""
    has_gsp: bool = False
    has_hr: bool = False
    gsp_hello: dict[str, str] | None = None
    status: str = "connected"  # connected | disconnected | error


class DeviceManager:
    """Thread-safe registry of known/connected devices."""

    def __init__(self) -> None:
        self._devices: dict[str, ConnectedDevice] = {}
        self._lock = asyncio.Lock()
        self._scanning = False

    @property
    def scanning(self) -> bool:
        return self._scanning

    async def list_devices(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [asdict(d) for d in self._devices.values()]

    async def get_device(self, address: str) -> ConnectedDevice | None:
        async with self._lock:
            return self._devices.get(address)

    async def upsert(self, device: ConnectedDevice) -> None:
        async with self._lock:
            self._devices[device.address] = device

    async def remove(self, address: str) -> bool:
        async with self._lock:
            return self._devices.pop(address, None) is not None

    async def set_status(self, address: str, status: str) -> bool:
        async with self._lock:
            dev = self._devices.get(address)
            if dev:
                dev.status = status
                return True
            return False

    async def restore(self) -> int:
        """Restore persisted device metadata.

        BLE connectivity cannot be restored across a server restart, so every
        restored device is registered as ``disconnected``.  The user must scan
        and connect again before streaming.
        """
        records = local_store.load_devices()
        fields = ConnectedDevice.__dataclass_fields__
        restored: dict[str, ConnectedDevice] = {}
        for record in records:
            payload = {key: value for key, value in record.items() if key in fields}
            payload["status"] = "disconnected"
            try:
                device = ConnectedDevice(**payload)
            except TypeError as exc:
                log.warning("skipping malformed persisted device: %s", exc)
                continue
            restored[device.address] = device
        async with self._lock:
            self._devices = restored
        return len(restored)


manager = DeviceManager()


async def _persist_devices() -> None:
    """Best-effort local snapshot; a disk problem must not break BLE control."""
    try:
        local_store.save_devices(await manager.list_devices())
    except Exception as exc:  # pragma: no cover - disk failures are environment-specific
        log.warning("could not persist device registry: %s", exc)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Movesense API server starting")
    log.info(
        "Local output: raw=%s processed=%s state=%s",
        config.RAW_DIR,
        config.PROCESSED_DIR,
        local_store.DATA_DIR,
    )
    try:
        restored = await manager.restore()
        if restored:
            log.info("Restored %d device(s) as disconnected", restored)
    except Exception as exc:  # pragma: no cover - environment-specific disk issue
        log.warning("could not restore device registry: %s", exc)
    try:
        interrupted = local_store.mark_interrupted_tracking_sessions()
        if interrupted:
            log.info("Marked %d unfinished tracking session(s) as interrupted", interrupted)
    except Exception as exc:  # pragma: no cover - environment-specific disk issue
        log.warning("could not update tracking-session history: %s", exc)
    yield
    log.info("Movesense API server shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Movesense BLE API",
    description="REST API for BLE device scanning, connection, and management",
    version="0.1.0",
    lifespan=lifespan,
)

_extra_origins = [
    origin.strip()
    for origin in os.environ.get("MOVESENSE_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    # Same-origin deployments (one Vercel project) do not need CORS at all;
    # these entries keep the split localhost workflow working.
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", *_extra_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    timeout_s: float = Field(default=config.SCAN_TIMEOUT_S, gt=0, le=60)
    name_filter: str = Field(default=config.NAME_FILTER, max_length=64)


class ConnectRequest(BaseModel):
    address: str = Field(min_length=1, max_length=64)
    timeout_s: float = Field(default=config.CONNECT_TIMEOUT_S, gt=0, le=60)


class DisconnectRequest(BaseModel):
    address: str = Field(min_length=1, max_length=64)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def _bluetooth_adapter_name() -> str | None:
    """Best-effort adapter name without opening a BLE connection."""
    try:
        adapters = sorted(Path("/sys/class/bluetooth").glob("hci*"))
    except OSError:
        return None
    return adapters[0].name if adapters else None


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bluetooth_adapter": _bluetooth_adapter_name(),
        "bluetooth_available": acquisition.BLUETOOTH_AVAILABLE
        and not config.ON_SERVERLESS,
        "serverless": config.ON_SERVERLESS,
        "writable_data_dir": str(config.DATA_ROOT),
    }


# ---------------------------------------------------------------------------
# Local accounts + cookie session
# ---------------------------------------------------------------------------
class SignUpRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class SignInRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = True


def _is_secure_request(request: Request) -> bool:
    """Whether the browser reached us over HTTPS (Vercel sets X-Forwarded-Proto)."""
    hostname = (request.url.hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        # ``vercel dev`` proxies over plain HTTP but still forwards an HTTPS
        # proto header, so the host check must win for local development.
        return False
    if os.environ.get("VERCEL_ENV") in {"production", "preview"}:
        return True
    forwarded = request.headers.get("x-forwarded-proto", "")
    if forwarded:
        return forwarded.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _set_session_cookie(
    response: Response, request: Request, token: str, remember: bool
) -> None:
    """HttpOnly cookie so the browser reports the signed-in user on every request.

    The frontend and API share one origin when deployed as a single Vercel
    project, so the cookie is marked ``Secure`` there. Local HTTP development
    (including ``vercel dev``) keeps ``secure=False``, otherwise the browser
    would drop the cookie.
    """
    response.set_cookie(
        key=auth_mod.SESSION_COOKIE,
        value=token,
        max_age=auth_mod.REMEMBER_TTL_S if remember else None,
        httponly=True,
        samesite="lax",
        secure=config.SECURE_COOKIES or _is_secure_request(request),
        path="/",
    )


@app.post("/api/auth/signup", status_code=201)
def auth_signup(req: SignUpRequest, request: Request, response: Response):
    try:
        user = auth_mod.create_user(req.full_name, req.email, req.password)
    except auth_mod.AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    token = auth_mod.create_session(str(user["id"]), remember=True)
    _set_session_cookie(response, request, token, remember=True)
    return {"user": auth_mod.public_user(user)}


@app.post("/api/auth/signin")
def auth_signin(req: SignInRequest, request: Request, response: Response):
    user = auth_mod.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = auth_mod.create_session(str(user["id"]), remember=req.remember)
    _set_session_cookie(response, request, token, remember=req.remember)
    return {"user": auth_mod.public_user(user)}


@app.post("/api/auth/signout")
def auth_signout(request: Request, response: Response):
    auth_mod.delete_session(request.cookies.get(auth_mod.SESSION_COOKIE))
    response.delete_cookie(auth_mod.SESSION_COOKIE, path="/")
    return {"signed_out": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    user = auth_mod.user_for_token(request.cookies.get(auth_mod.SESSION_COOKIE))
    if user is None:
        # Drop a stale/expired cookie so the client does not bounce between
        # the signed-in redirect and a 401 forever.
        unauthorized = JSONResponse({"detail": "Not signed in."}, status_code=401)
        unauthorized.delete_cookie(auth_mod.SESSION_COOKIE, path="/")
        return unauthorized
    return {"user": auth_mod.public_user(user)}


# ---------------------------------------------------------------------------
# BLE Scanning
# ---------------------------------------------------------------------------
@app.post("/api/devices/scan")
async def scan_devices(req: ScanRequest | None = None):
    """Discover nearby Movesense devices via BLE scan."""
    if manager.scanning:
        raise HTTPException(status_code=409, detail="A scan is already in progress")

    timeout = req.timeout_s if req else config.SCAN_TIMEOUT_S
    name_filter = req.name_filter if req else config.NAME_FILTER

    manager._scanning = True
    try:
        found = None
        for attempt in range(2):
            try:
                found = await acquisition.discover_devices(timeout_s=timeout, name_filter=name_filter)
                break
            except Exception as e:
                msg, fatal = acquisition.describe_ble_error(e)
                # BlueZ often returns "Operation already in progress" for a
                # second while a previous connect/disconnect is settling.
                if fatal or attempt == 1 or "in progress" not in str(e).lower():
                    raise HTTPException(
                        status_code=503 if fatal else 500,
                        detail={"message": msg, "fatal": fatal},
                    )
                log.info("BLE scan busy, retrying once: %s", msg)
                await asyncio.sleep(1.5)
        assert found is not None
        results = []
        for dev in found:
            # check if already connected
            existing = await manager.get_device(dev.address)
            results.append({
                "name": dev.name,
                "address": dev.address,
                "rssi": dev.rssi,
                "advertises_gsp": dev.advertises_gsp,
                "is_connected": existing is not None and existing.status == "connected",
            })
        # A sensor that is already streaming stops advertising, so a plain BLE
        # scan cannot see it. Include active live-tracking devices explicitly;
        # otherwise Scan looks empty even though the link is up and in use.
        seen = {item["address"] for item in results}
        for sess in tracking_sessions.values():
            if sess.finished or sess.address in seen:
                continue
            existing = await manager.get_device(sess.address)
            results.append({
                "name": sess.device_name or (existing.name if existing else sess.address),
                "address": sess.address,
                "rssi": existing.rssi if existing else None,
                "advertises_gsp": existing.has_gsp if existing else True,
                "is_connected": True,
                "tracking_session_id": sess.session_id,
            })
        try:
            local_store.append_scan(
                {
                    "timeout_s": timeout,
                    "name_filter": name_filter,
                    "count": len(results),
                    "devices": results,
                }
            )
        except Exception as exc:  # pragma: no cover - disk failure is non-fatal
            log.warning("could not persist scan result: %s", exc)
        return {"devices": results, "count": len(results), "scan_duration_s": timeout}
    except HTTPException:
        raise
    except Exception as e:
        msg, fatal = acquisition.describe_ble_error(e)
        raise HTTPException(
            status_code=503 if fatal else 500,
            detail={"message": msg, "fatal": fatal},
        )
    finally:
        manager._scanning = False


# ---------------------------------------------------------------------------
# Connect (verify + register)
# ---------------------------------------------------------------------------
@app.post("/api/devices/connect")
async def connect_device(req: ConnectRequest):
    """Connect to a Movesense sensor, verify its GATT services, and register it."""
    address = req.address.strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    existing = await manager.get_device(address)
    if existing and existing.status == "connected":
        active = _active_tracking_for(address)
        if active is not None:
            # The link is genuinely up because a live stream holds it.
            return {
                "device": asdict(existing),
                "message": "Already connected",
                "tracking_session_id": active.session_id,
            }
        # The registry is only a persisted "verified" flag (verify_device closes
        # its BLE link when it returns). Never let that stale flag block a real
        # reconnection attempt; fall through and verify over BLE now.

    try:
        report = await acquisition.verify_device(address, timeout_s=req.timeout_s)
    except Exception as e:
        # Clear the stale "connected" flag so the UI offers Connect again.
        if existing and existing.status == "connected":
            await manager.set_status(address, "disconnected")
            await _persist_devices()
        msg, fatal = acquisition.describe_ble_error(e, address)
        raise HTTPException(
            status_code=503 if fatal else 500,
            detail={"message": msg, "fatal": fatal},
        )

    if not report.connected:
        raise HTTPException(status_code=502, detail="Could not establish BLE connection")

    device = ConnectedDevice(
        address=address,
        name=report.identity.get("model") or "Movesense",
        serial=report.identity.get("serial"),
        model=report.identity.get("model"),
        firmware=report.identity.get("firmware"),
        manufacturer=report.identity.get("manufacturer"),
        connected_at=datetime.now(timezone.utc).isoformat(),
        has_gsp=report.has_gsp_service,
        has_hr=report.has_heart_rate_service,
        gsp_hello=report.gsp_hello,
        status="connected",
    )
    await manager.upsert(device)
    await _persist_devices()
    return {"device": asdict(device), "message": "Connected successfully"}


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------
@app.post("/api/devices/disconnect")
async def disconnect_device(req: DisconnectRequest):
    """Disconnect a previously connected device (removes from active registry)."""
    address = req.address.strip()
    existing = await manager.get_device(address)
    if not existing:
        raise HTTPException(status_code=404, detail="Device not found in connected registry")
    active = _active_tracking_for(address)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Tracking session {active.session_id} is active; stop it before disconnecting",
        )

    await manager.set_status(address, "disconnected")
    await _persist_devices()
    return {"address": address, "status": "disconnected", "message": "Device disconnected"}


# ---------------------------------------------------------------------------
# List Connected
# ---------------------------------------------------------------------------
@app.get("/api/devices")
async def list_connected_devices():
    """Return all known/connected devices."""
    devices = await manager.list_devices()
    connected = [d for d in devices if d["status"] == "connected"]
    return {
        "devices": devices,
        "connected_count": len(connected),
        "total_count": len(devices),
    }


# ---------------------------------------------------------------------------
# Single device detail
# ---------------------------------------------------------------------------
@app.get("/api/devices/{address}")
async def get_device_detail(address: str):
    """Get detailed info for a specific device."""
    device = await manager.get_device(address)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"device": asdict(device)}


# ---------------------------------------------------------------------------
# Remove device from registry
# ---------------------------------------------------------------------------
@app.delete("/api/devices/{address}")
async def remove_device(address: str):
    """Remove a device from the registry entirely."""
    active = _active_tracking_for(address)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Tracking session {active.session_id} is active; stop it before removing this device",
        )
    removed = await manager.remove(address)
    if not removed:
        raise HTTPException(status_code=404, detail="Device not found")
    await _persist_devices()
    return {"address": address, "message": "Device removed from registry"}


# ---------------------------------------------------------------------------
# Scan status
# ---------------------------------------------------------------------------
@app.get("/api/devices/scan/status")
async def scan_status():
    return {"scanning": manager.scanning}


# ---------------------------------------------------------------------------
# Live tracking sessions (driven by the Next.js frontend)
#
# Flow: Devices page (scan -> connect/verify) registers the sensor, then
# Live Tracking page calls POST /api/tracking/start with that address.
# The desktop matplotlib viewer (app.py) is NOT needed for this flow.
# ---------------------------------------------------------------------------
@dataclass
class LiveTrackingSession:
    session_id: str
    address: str
    device_name: str
    rate: int
    continuous: bool
    controller: Any = field(repr=False)
    stop_event: Any = field(repr=False)
    task: Any = field(default=None, repr=False)
    connected: bool = False
    status: str = "Starting..."
    ping_ms: float | None = None
    ping_at: float = 0.0
    finished: bool = False
    sample_count: int = 0
    last_acc: list[float] | None = None
    last_gyro: list[float] | None = None
    last_ts_ms: float | None = None
    started_at: str = ""
    finished_at: str = ""
    user_id: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    exported_ids: set = field(default_factory=set)
    recent_samples: deque = field(default_factory=lambda: deque(maxlen=300))

    def sink(self, ts: float, acc, gyro) -> None:
        try:
            import numpy as _np

            a = _np.asarray(acc, dtype=float)
            g = _np.asarray(gyro, dtype=float)
            self.controller.feed(float(ts), a, g)
            self.sample_count += 1
            self.last_acc = [float(v) for v in a.tolist()]
            self.last_gyro = [float(v) for v in g.tolist()]
            self.last_ts_ms = float(ts)
            self.recent_samples.append(
                (float(ts), [float(v) for v in a.tolist()], [float(v) for v in g.tolist()])
            )
        except Exception as e:
            log.warning("tracking sink error: %s", e)

    def event(self, kind: str, value) -> None:
        if kind == "status":
            self.status = str(value)
        elif kind == "connected":
            self.connected = bool(value)
        elif kind == "device":
            self.device_name = str(value)
        elif kind == "ping":
            import time as _time

            self.ping_ms, self.ping_at = value, _time.monotonic()


tracking_sessions: dict[str, LiveTrackingSession] = {}
MAX_TRACKING_SESSIONS = 50


def _active_tracking_for(address: str) -> LiveTrackingSession | None:
    for s in tracking_sessions.values():
        if s.address == address and not s.finished:
            return s
    return None


def _new_tracking_id() -> str:
    """Timestamp-based id, suffixed if two devices start within one second."""
    base = f"TRK_{datetime.now():%Y%m%d_%H%M%S}"
    if base not in tracking_sessions:
        return base
    suffix = 1
    while f"{base}_{suffix:02d}" in tracking_sessions:
        suffix += 1
    return f"{base}_{suffix:02d}"


def _prune_tracking_sessions() -> None:
    """Drop oldest finished sessions so the in-memory registry stays bounded."""
    overflow = len(tracking_sessions) - MAX_TRACKING_SESSIONS
    if overflow <= 0:
        return
    for sid in [s for s, sess in tracking_sessions.items() if sess.finished][:overflow]:
        tracking_sessions.pop(sid, None)


def _last_speed_mps(result) -> float | None:
    """Magnitude of the newest velocity sample of a trajectory, in m/s."""
    import numpy as _np

    try:
        velocity = getattr(result, "velocity_mps", None)
        arr = _np.asarray(velocity, dtype=float)
        if arr.ndim == 1:
            if arr.size != 3:
                return None
            latest = arr
        else:
            if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] < 3:
                return None
            latest = arr[-1, :3]
        speed = float(_np.linalg.norm(latest))
        return speed if math.isfinite(speed) else None
    except Exception:
        return None


def _current_path_for(ctrl) -> tuple[Any | None, str, float | None]:
    try:
        st = ctrl.state
        if ctrl.result is not None and (
            st is session_mod.SessionState.LOCKED
            or (st is session_mod.SessionState.ARMED and ctrl.continuous)
        ):
            return (
                ctrl.result.position_cm,
                "final",
                _last_speed_mps(ctrl.result),
            )
        if st is session_mod.SessionState.RECORDING:
            prov = ctrl.provisional_trajectory()
            if prov is not None:
                return prov.position_cm, "provisional", _last_speed_mps(prov)
    except Exception as exc:
        # A bad preview must not take down /status and block the entire poll.
        log.warning("live trajectory preview failed: %s", exc)
        return None, "error", None
    return None, "", None


def _tracking_snapshot(sess: LiveTrackingSession) -> dict[str, Any]:
    import time as _time

    import numpy as _np

    ctrl = sess.controller
    pos, kind, speed_mps = _current_path_for(ctrl)
    distance_cm: float | None = None
    point_b_cm: list[float] | None = None
    path_cm: float | None = None
    if pos is not None and len(pos) > 1:
        try:
            candidate = float(_np.linalg.norm(pos[-1] - pos[0]))
            if math.isfinite(candidate):
                distance_cm = candidate
            candidate_b = [float(v) for v in pos[-1].tolist()]
            if all(math.isfinite(v) for v in candidate_b):
                point_b_cm = candidate_b
            candidate_path = float(
                _np.linalg.norm(_np.diff(pos, axis=0), axis=1).sum()
            )
            if math.isfinite(candidate_path):
                path_cm = candidate_path
        except Exception:
            pass
    ping_age = _time.monotonic() - sess.ping_at if sess.ping_at else 999.0
    ping = sess.ping_ms if (sess.ping_ms is not None and ping_age < 6.0 and sess.connected) else None
    result = ctrl.result if ctrl.state is session_mod.SessionState.LOCKED else None
    flags = list(result.flags) if result is not None else []
    return {
        "session_id": sess.session_id,
        "address": sess.address,
        "device": sess.device_name,
        "rate_hz": sess.rate,
        "state": ctrl.state.value,
        "motion": ctrl.live_motion(),
        "holding_still": ctrl.is_holding_still,
        "connected": sess.connected,
        "status": sess.status,
        "ping_ms": ping,
        "kind": kind,
        "distance_cm": distance_cm,
        "path_length_cm": path_cm,
        "speed_mps": speed_mps,
        "point_b_cm": point_b_cm,
        "n_trajectories": len(ctrl.completed),
        "sample_count": sess.sample_count,
        "finished": sess.finished,
        "last_acc": sess.last_acc,
        "last_gyro": sess.last_gyro,
        "last_ts_ms": sess.last_ts_ms,
        "quality_flags": flags,
        "started_at": sess.started_at,
        "finished_at": sess.finished_at,
        "raw_files": local_store.raw_artifacts(sess.session_id),
        "processed_session_ids": sorted(sess.exported_ids),
    }


async def _run_tracking(sess: LiveTrackingSession, seconds: float) -> None:
    error_message: str | None = None
    processed: list[dict[str, Any]] = []
    try:
        await acquisition.record_session(
            sess.address,
            sess.rate,
            seconds,
            sess.session_id,
            "unspecified",
            "frontend live tracking",
            sample_sink=sess.sink,
            stop_event=sess.stop_event,
            event_sink=sess.event,
            ping_interval_s=2.0,
        )
    except Exception as e:
        error_message = str(e)
        sess.connected = False
        sess.status = f"Error: {e}"
        log.warning("tracking session %s error: %s", sess.session_id, e)
    finally:
        sess.finished = True
        sess.finished_at = datetime.now(timezone.utc).isoformat()
        sess.connected = False
        raw_files = local_store.raw_artifacts(sess.session_id)
        # Export any completed trajectories (same artefacts as app.py NEW/LOCKED flow)
        try:
            for result in sess.controller.completed:
                if result.session_id in sess.exported_ids:
                    continue
                context = {
                    "mode": "live-frontend",
                    "sample_rate_hz": sess.controller.fs,
                    "viewer": "api/tracking",
                    "source_session_id": sess.session_id,
                    "raw_files": raw_files,
                    "events": [{"t_ms": t, "message": m} for t, m in sess.controller.events],
                }
                if sess.user_name:
                    context["analysed_for"] = {
                        "id": sess.user_id,
                        "name": sess.user_name,
                        "email": sess.user_email,
                    }
                summary = trajectory_mod.summary_dict(result, context, None, None)
                paths = trajectory_mod.export_trajectory(
                    result, summary, config.PROCESSED_DIR
                )
                processed.append(
                    {
                        "session_id": result.session_id,
                        **{
                            name: local_store.relative_path(path)
                            for name, path in paths.items()
                        },
                    }
                )
                sess.exported_ids.add(result.session_id)
        except Exception as e:
            log.warning("tracking export failed: %s", e)
            if error_message is None:
                error_message = f"Trajectory export failed: {e}"
        try:
            local_store.finish_tracking_record(
                sess.session_id,
                status=sess.status,
                sample_count=sess.sample_count,
                finished_at=sess.finished_at,
                processed=processed,
                error=error_message,
            )
        except Exception as e:  # pragma: no cover - disk failure is non-fatal
            log.warning("could not persist tracking session %s: %s", sess.session_id, e)


class TrackingStartRequest(BaseModel):
    address: str = Field(min_length=1, max_length=64)
    rate: int = 52
    seconds: float = Field(default=3600.0, gt=0, le=86400)
    continuous: bool = False


@app.post("/api/tracking/start")
async def tracking_start(req: TrackingStartRequest, request: Request):
    """Start live IMU streaming + A->B detection for a device already connected via /api/devices/connect."""
    address = req.address.strip()
    if not address:
        raise HTTPException(status_code=400, detail="address is required")
    user = auth_mod.user_for_token(request.cookies.get(auth_mod.SESSION_COOKIE))
    known = await manager.get_device(address)
    if known is None or known.status != "connected":
        raise HTTPException(
            status_code=409,
            detail="Device is not connected. Scan and Connect it on the Devices page first.",
        )
    if req.rate not in config.IMU9_SAMPLE_RATES_HZ:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported rate {req.rate}; use one of {list(config.IMU9_SAMPLE_RATES_HZ)}",
        )
    # One active session per address: stop the old one first via /stop or /reset.
    active = _active_tracking_for(address)
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A tracking session ({active.session_id}) is already running for {address}. Stop it first.",
        )
    _prune_tracking_sessions()
    session_id = _new_tracking_id()
    ctrl = session_mod.SessionController(req.rate, continuous=req.continuous)
    ctrl.arm()
    sess = LiveTrackingSession(
        session_id=session_id,
        address=address,
        device_name=known.name or address,
        rate=req.rate,
        continuous=req.continuous,
        controller=ctrl,
        stop_event=asyncio.Event(),
        started_at=datetime.now(timezone.utc).isoformat(),
        user_id=str(user["id"]) if user else None,
        user_name=str(user["full_name"]) if user else None,
        user_email=str(user["email"]) if user else None,
    )
    tracking_sessions[session_id] = sess
    try:
        local_store.start_tracking_record(
            session_id=session_id,
            address=address,
            device=sess.device_name,
            rate_hz=req.rate,
            continuous=req.continuous,
            started_at=sess.started_at,
        )
    except Exception as exc:  # pragma: no cover - disk failure is non-fatal
        log.warning("could not persist tracking session start %s: %s", session_id, exc)
    sess.task = asyncio.create_task(_run_tracking(sess, req.seconds))
    return {"session_id": session_id, "status": _tracking_snapshot(sess)}


@app.get("/api/tracking/sessions")
async def tracking_list():
    return {"sessions": [_tracking_snapshot(s) for s in tracking_sessions.values()]}


@app.get("/api/tracking/{session_id}")
async def tracking_status(session_id: str):
    sess = tracking_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    return _tracking_snapshot(sess)


@app.get("/api/tracking/{session_id}/samples")
async def tracking_samples(session_id: str, limit: int = Query(default=200, ge=1, le=1000)):
    """Recent raw IMU samples, used by the live waveform instead of synthetic data."""
    sess = tracking_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    recent = list(sess.recent_samples)[-limit:]
    return {
        "session_id": session_id,
        "rate_hz": sess.rate,
        "samples": [
            {"t_ms": t, "acc": acc, "gyro": gyro} for t, acc, gyro in recent
        ],
        "count": len(recent),
    }


@app.get("/api/tracking/{session_id}/trajectory")
async def tracking_trajectory(
    session_id: str,
    max_points: int = Query(default=500, ge=2, le=5000),
):
    """Downsampled 3D path for the Three.js frontend (metres, relative to Point A)."""
    import numpy as _np

    sess = tracking_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    pos, kind, _speed_mps = _current_path_for(sess.controller)
    empty = {
        "session_id": session_id,
        "kind": kind,
        "state": sess.controller.state.value,
        "points_m": [],
        "count": 0,
        "distance_cm": None,
        "path_length_cm": None,
    }
    if pos is None:
        return empty
    try:
        arr = _np.asarray(pos, dtype=float)
    except (TypeError, ValueError):
        return empty
    if arr.ndim != 2 or arr.shape[0] < 2 or arr.shape[1] < 3:
        return empty
    arr = arr[:, :3]
    # Inertial reconstruction can produce a non-finite value in pathological
    # windows. JSON cannot represent NaN/Infinity, so drop those rows instead
    # of failing the whole endpoint (which would also freeze the UI poll).
    arr = arr[_np.all(_np.isfinite(arr), axis=1)]
    if len(arr) < 2:
        return empty
    n = len(arr)
    dist = float(_np.linalg.norm(arr[-1] - arr[0]))
    path = float(_np.linalg.norm(_np.diff(arr, axis=0), axis=1).sum())
    if n <= max_points:
        sub = arr
    else:
        # Keep both endpoints while staying within max_points.
        step = max(1, math.ceil((n - 1) / (max_points - 1)))
        indices = list(range(0, n, step))
        if indices[-1] != n - 1:
            indices.append(n - 1)
        sub = arr[indices]
    # The web scene is A-centred; the desktop viewer keeps the configured A offset.
    pts_m = ((sub - arr[0]) / 100.0).tolist()  # cm -> m
    return {
        "session_id": session_id,
        "kind": kind,
        "state": sess.controller.state.value,
        "points_m": [[float(x), float(y), float(z)] for x, y, z in pts_m],
        "count": len(pts_m),
        "distance_cm": dist if math.isfinite(dist) else None,
        "path_length_cm": path if math.isfinite(path) else None,
    }


@app.post("/api/tracking/{session_id}/reset")
async def tracking_reset(session_id: str):
    """NEW button: discard active window and re-arm for the next A->B."""
    sess = tracking_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    ok = sess.controller.reset(discard=True)
    if ok:
        sess.controller.arm()
    return {"session_id": session_id, "reset": ok, "status": _tracking_snapshot(sess)}


@app.post("/api/tracking/{session_id}/stop")
async def tracking_stop(session_id: str):
    sess = tracking_sessions.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Tracking session not found")
    sess.stop_event.set()
    return {"session_id": session_id, "stopped": True, "status": _tracking_snapshot(sess)}


@app.get("/api/sessions")
async def list_processed_sessions(limit: int = Query(default=24, ge=1, le=200)):
    """Processed A->B summaries for the Dashboard (replaces hardcoded rows)."""
    out: list[dict[str, Any]] = []
    try:
        root = config.PROCESSED_DIR
        if root.exists():
            files = sorted(
                root.glob("*/*_trajectory_summary.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for f in files[:limit]:
                try:
                    import json as _json

                    s = _json.loads(f.read_text())
                    out.append(
                        {
                            "session_id": s.get("session_id", f.parent.name),
                            "motion": s.get("motion", "move"),
                            "straight_line_distance_cm": s.get("straight_line_distance_cm"),
                            "trajectory_length_cm": s.get("trajectory_length_cm"),
                            "quality_status": s.get("quality_status"),
                            "movement_duration_s": s.get("movement_duration_s"),
                            "source_session_id": s.get("source_session_id"),
                            "analysed_for": s.get("analysed_for"),
                            "mtime": f.stat().st_mtime,
                        }
                    )
                except Exception:
                    continue
    except Exception as e:
        log.warning("list sessions failed: %s", e)
    return {"sessions": out, "count": len(out)}


# ---------------------------------------------------------------------------
# Local saved output (no database)
# ---------------------------------------------------------------------------
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@app.get("/api/local/sessions")
async def list_local_sessions(limit: int = Query(default=50, ge=1, le=500)):
    """Tracking metadata plus raw captures stored on this server."""
    sessions = local_store.list_local_sessions(limit)
    return {"sessions": sessions, "count": len(sessions)}


@app.get("/api/local/scans")
async def list_local_scans(limit: int = Query(default=50, ge=1, le=500)):
    scans = local_store.load_scans(limit)
    return {"scans": scans, "count": len(scans)}


@app.get("/api/local/sessions/{session_id}")
async def get_local_session(session_id: str):
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    record = local_store.get_local_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Local session not found")
    return record


_LOCAL_RAW_MEDIA = {
    "csv": "text/csv",
    "json": "application/json",
    "bin": "application/octet-stream",
}


@app.get("/api/local/sessions/{session_id}/export")
async def export_local_raw(
    session_id: str,
    format: str = Query(default="raw_csv", pattern="^(raw_csv|raw_json|raw_bin)$"),
):
    """Download a raw capture saved by acquisition.record_session."""
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    fmt = format.removeprefix("raw_")
    path = local_store.raw_artifact_path(session_id, fmt)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"No raw {fmt.upper()} capture exists for session {session_id}",
        )
    return FileResponse(
        path, media_type=_LOCAL_RAW_MEDIA[fmt], filename=path.name
    )


_SESSION_ARTIFACTS = {
    "csv": ("{sid}_trajectory.csv", "text/csv"),
    "json": ("{sid}_trajectory_summary.json", "application/json"),
    "mat": ("{sid}_processed.mat", "application/octet-stream"),
}


@app.get("/api/sessions/{session_id}/export")
async def export_processed_session(
    session_id: str,
    format: str = Query(default="csv", pattern="^(csv|json|mat)$"),
):
    """Download a real artefact produced by the trajectory/analysis pipeline."""
    if not _SESSION_ID_RE.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session id")
    root = config.PROCESSED_DIR.resolve()
    session_dir = (root / session_id).resolve()
    if session_dir.parent != root:
        raise HTTPException(status_code=400, detail="Invalid session id")
    template, media_type = _SESSION_ARTIFACTS[format]
    path = session_dir / template.format(sid=session_id)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No {format.upper()} export exists for session {session_id}",
        )
    return FileResponse(path, media_type=media_type, filename=path.name)
