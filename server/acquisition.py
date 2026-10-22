"""BLE discovery, GSP streaming, raw-data decoding, and raw-data storage."""
import asyncio
import json
import logging
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Callable

import numpy as np
import pandas as pd
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

import config

log = logging.getLogger(__name__)


class SubscribeRejected(RuntimeError):
    """The sensor answered SUBSCRIBE with an error status (deterministic: retrying will not help)."""


def describe_ble_error(error: BaseException, address: str | None = None) -> tuple[str, bool]:
    """Human-readable BLE error plus whether retrying is pointless (fatal). The raw text is always kept."""
    raw = str(error) or error.__class__.__name__
    low = raw.lower()
    # Transient BlueZ/D-Bus contention *before* the fatal adapter checks: the raw
    # text contains "bluez"/"dbus", which must not be mistaken for a missing
    # adapter (e.g. "[org.bluez.Error.InProgress] Operation already in progress").
    if "in progress" in low:
        return raw + " Another Bluetooth operation (scan/connect) is still finishing. Wait a moment and retry.", False
    if (
        isinstance(error, FileNotFoundError)
        or "no bluetooth adapter" in low
        or "adapter not found" in low
        or "no default adapter" in low
        or "no adapters" in low
        or "org.bluez.error.notready" in low
        or "system_bus_socket" in low
        or "failed to connect to socket" in low
        or "service unknown" in low
    ):
        return f"No Bluetooth adapter/service available ({raw}). Turn Bluetooth on (Linux: install and start BlueZ).", True
    if "turned off" in low or "powered off" in low or "bluetooth is off" in low or "poweredoff" in low:
        return f"Bluetooth is turned off ({raw}). Switch it on and retry.", True
    if "permission" in low or "not authorized" in low or "denied" in low or "unauthorized" in low:
        return (f"Bluetooth permission denied ({raw}). Allow Bluetooth for your terminal/Python "
                "(macOS: System Settings > Privacy & Security > Bluetooth).", True)
    hint = ""
    if "not found" in low:
        hint = " Wake the sensor, close other apps connected to it."
        if sys.platform == "darwin" and address and ":" in address:
            hint += " On macOS use the UUID from 'scan --all', not the MAC address."
        return raw + hint, False
    if ("discover" in low and "service" in low) or "device disconnected" in low or "not connected" in low:
        hint = (" Sensor dropped during GATT discovery (link loss, asleep, or stale BlueZ cache)."
                " Keep it <1 m away, wake it (move/tap it), close the phone/nRF app holding it,"
                " then run: bluetoothctl disconnect/remove <MAC>, restart Bluetooth, and retry verify.")
        return raw + "." + hint, False
    return raw + hint, False


# ---------------------------------------------------------------- discovery / verify
@dataclass(frozen=True)
class FoundDevice:
    name: str
    address: str
    rssi: int | None
    advertises_gsp: bool


@dataclass(frozen=True)
class DeviceReport:
    connected: bool
    identity: dict[str, str | None]  # None = characteristic not exposed
    has_gsp_service: bool
    has_heart_rate_service: bool
    service_uuids: list[str]
    gsp_hello: dict[str, str] | None  # None = no/unparsable HELLO response


async def discover_devices(
    timeout_s: float = config.SCAN_TIMEOUT_S, name_filter: str = config.NAME_FILTER
) -> list[FoundDevice]:
    found = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    devices = []
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        advertises_gsp = config.GSP_SERVICE_UUID in (u.lower() for u in adv.service_uuids)
        if name_filter.lower() in name.lower() or advertises_gsp:
            devices.append(FoundDevice(name, device.address, adv.rssi, advertises_gsp))
    return sorted(devices, key=lambda d: d.rssi if d.rssi is not None else -999, reverse=True)


async def _read_text(client: BleakClient, uuid: str) -> str | None:
    try:
        raw = await client.read_gatt_char(uuid)
    except BleakError:
        return None
    return bytes(raw).decode("utf-8", errors="replace").strip("\x00 ")


async def verify_device(
    address: str, timeout_s: float = config.CONNECT_TIMEOUT_S
) -> DeviceReport:
    """Connect, verify GATT services, read identity, then disconnect.

    The first GATT discovery after a scan can fail while BlueZ is still
    settling (the same race the streaming path already retries), so retry the
    whole connect/verify phase a few times before surfacing the error.
    """
    last_error: BaseException | None = None
    for attempt in range(3):
        log.info("Connecting to Movesense... (try %d/3)", attempt + 1)
        state = StreamState(sample_rate_hz=52)
        try:
            async with BleakClient(address, timeout=timeout_s) as client:
                log.info("Connected.")
                service_uuids = sorted(s.uuid.lower() for s in client.services)
                identity = {f: await _read_text(client, u) for f, u in config.DEVICE_INFO_UUIDS.items()}
                has_gsp = config.GSP_SERVICE_UUID in service_uuids
                hello = None
                if has_gsp:
                    hello = await _hello(client, state)
                return DeviceReport(
                    connected=client.is_connected,
                    identity=identity,
                    has_gsp_service=has_gsp,
                    has_heart_rate_service=config.HEART_RATE_SERVICE_UUID in service_uuids,
                    service_uuids=service_uuids,
                    gsp_hello=hello,
                )
        except (BleakError, asyncio.TimeoutError, OSError) as error:
            last_error = error
            if attempt < 2:
                log.info("Connect/verify try %d/3 failed (%s); retrying in 2 s...", attempt + 1, error)
                await asyncio.sleep(2.0)
    if last_error is not None:
        raise last_error
    raise BleakError(f"Could not verify sensor {address}")


# --------------------------------------------------------------------- GSP packets
def parse_hello(packet: bytes) -> dict[str, str] | None:
    """Parse a GSP HELLO response. Tolerates an optional HTTP-200 status prefix because the
    documented layout is ambiguous; raw bytes are always kept in the session metadata."""
    data = packet[2:]
    if data[:2] == b"\xc8\x00":
        data = data[2:]
    # Older gatt_sensordata_app firmware answers with the ASCII string "Hello"
    # instead of the GSP v1 identity block (protocol version + identity fields).
    if data.decode("utf-8", errors="ignore").strip("\x00 ").lower() == "hello":
        return {"message": "Hello", "legacy_firmware": "true"}
    if len(data) < 2:
        return None
    fields = data[1:].split(b"\x00")
    names = ["serial", "product_name", "dfu_mac", "app_name", "app_version"]
    if len(fields) < len(names):
        return None
    parsed = {n: f.decode("utf-8", errors="replace") for n, f in zip(names, fields)}
    parsed["protocol_version"] = str(data[0])
    return parsed


@dataclass
class StreamState:
    """Everything the notification handler accumulates. No BLE objects: easy to test/replay."""

    sample_rate_hz: int
    rows: list[tuple] = field(default_factory=list)
    packets: int = 0
    malformed_packets: int = 0
    missing_samples: int = 0
    gap_events: list[tuple[float, float]] = field(default_factory=list)  # (timestamp_ms, gap_ms)
    last_end_ms: float | None = None
    hello_packet: bytes | None = None
    hello_waiter: asyncio.Future | None = None  # resolved by the next HELLO response (ping)
    hello_waiter_ref: int = 1  # reference code of the in-flight HELLO/ping (pairs response to request)
    statuses: dict[int, int] = field(default_factory=dict)
    pending: dict[int, bytes] = field(default_factory=dict)
    bin_file: BinaryIO | None = None
    sample_sink: Callable[[float, np.ndarray, np.ndarray], None] | None = None  # (timestamp_ms, acc, gyro)


def _complete(body: bytes) -> bool:
    size = config.IMU9_BYTES_PER_SAMPLE
    return len(body) >= 4 + size and (len(body) - 4) % size == 0


def _ingest(state: StreamState, body: bytes) -> None:
    timestamp_ms = struct.unpack_from("<I", body)[0]
    count = (len(body) - 4) // config.IMU9_BYTES_PER_SAMPLE
    values = np.array(struct.unpack_from(f"<{count * 9}f", body, 4))
    acc, gyro, mag = (values[k * count * 3:(k + 1) * count * 3].reshape(count, 3) for k in range(3))
    step_ms = 1000.0 / state.sample_rate_hz
    if state.last_end_ms is not None:
        gap_ms = timestamp_ms - state.last_end_ms
        if gap_ms > config.Params().max_gap_factor * step_ms:
            state.missing_samples += round(gap_ms / step_ms)
            state.gap_events.append((float(timestamp_ms), float(gap_ms)))
    state.last_end_ms = timestamp_ms + count * step_ms
    for i in range(count):
        state.rows.append((timestamp_ms + i * step_ms, *acc[i], *gyro[i], *mag[i]))
        if state.sample_sink is not None:
            state.sample_sink(timestamp_ms + i * step_ms, acc[i], gyro[i])


def handle_packet(state: StreamState, packet: bytes) -> None:
    """Process one GSP notification (response or data)."""
    if len(packet) < 2:
        return
    code, reference = packet[0], packet[1]
    if code == config.RESP_COMMAND:
        if reference == config.HELLO_REFERENCE or reference == state.hello_waiter_ref:
            state.hello_packet = packet
            if state.hello_waiter is not None and not state.hello_waiter.done():
                state.hello_waiter.set_result(None)
        elif len(packet) >= 4:
            state.statuses[reference] = struct.unpack_from("<H", packet, 2)[0]
        return
    if code not in (config.RESP_DATA, config.RESP_DATA_PART2):
        log.warning("Unexpected GSP packet type %d", code)
        return
    state.packets += 1
    if state.bin_file is not None:
        state.bin_file.write(struct.pack("<H", len(packet)) + packet)
    body = packet[2:]
    if code == config.RESP_DATA_PART2:
        if reference not in state.pending:
            state.malformed_packets += 1
            return
        body = state.pending.pop(reference) + body
    elif not _complete(body):
        state.pending[reference] = body  # wait for DATA_PART2
        return
    if _complete(body):
        _ingest(state, body)
    else:
        state.malformed_packets += 1


async def _hello(client: BleakClient, state: StreamState) -> dict[str, str] | None:
    await client.start_notify(config.GSP_NOTIFY_UUID, lambda _h, d: handle_packet(state, bytes(d)))
    try:
        await client.write_gatt_char(
            config.GSP_WRITE_UUID, bytes([config.CMD_HELLO, config.HELLO_REFERENCE]), response=True
        )
        for _ in range(20):
            if state.hello_packet is not None:
                break
            await asyncio.sleep(0.1)
    finally:
        await client.stop_notify(config.GSP_NOTIFY_UUID)
    return parse_hello(state.hello_packet) if state.hello_packet else None


# ----------------------------------------------------------------------- recording
def _emit(sink: Callable[[str, object], None] | None, kind: str, value: object) -> None:
    if sink is not None:
        sink(kind, value)


async def _ping_loop(client: BleakClient, state: StreamState, sink, interval_s: float) -> None:
    """Ping = round trip of the documented GSP HELLO command (write -> response notification), in ms.

    Each ping uses a rotating reference code (101..150) so a late response to an
    earlier ping cannot resolve the next ping's waiter and fake a measurement.
    """
    loop = asyncio.get_running_loop()
    seq = 0
    while True:
        await asyncio.sleep(interval_s)
        ref = 101 + (seq % 50)
        seq += 1
        state.hello_waiter_ref = ref
        state.hello_waiter = loop.create_future()
        waiter = state.hello_waiter
        started = time.perf_counter()
        try:
            await client.write_gatt_char(config.GSP_WRITE_UUID, bytes([config.CMD_HELLO, ref]), response=True)
            await asyncio.wait_for(asyncio.shield(waiter), timeout=2.0)
            if state.hello_waiter is waiter:
                _emit(sink, "ping", (time.perf_counter() - started) * 1000.0)
        except asyncio.TimeoutError:
            _emit(sink, "ping", None)
        except BleakError:
            if state.hello_waiter is waiter:
                state.hello_waiter = None
            return
        finally:
            if state.hello_waiter is waiter:
                state.hello_waiter = None


async def _wait_end(disconnected: asyncio.Event, stop_event: asyncio.Event | None, timeout: float) -> bool:
    """Wait for deadline, stop request, or disconnect. True only if the connection was lost."""
    waiters = [asyncio.ensure_future(disconnected.wait())]
    if stop_event is not None:
        waiters.append(asyncio.ensure_future(stop_event.wait()))
    _, pending = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return disconnected.is_set()


async def _stream_once(
    address: str, state: StreamState, deadline: float, reference: int, events: list[str],
    stop_event: asyncio.Event | None = None, event_sink=None, ping_interval_s: float = 0.0,
) -> None:
    loop = asyncio.get_running_loop()
    _emit(event_sink, "status", "Connecting...")
    # Fresh state for this attempt: a half-packet (DATA without PART2) from a
    # dropped connection must not poison the new one, and a dangling ping
    # waiter from the previous connection must not capture its responses.
    state.pending.clear()
    if state.hello_waiter is not None and not state.hello_waiter.done():
        state.hello_waiter.cancel()
    state.hello_waiter, state.hello_waiter_ref = None, config.HELLO_REFERENCE
    device = await BleakScanner.find_device_by_address(address, timeout=config.SCAN_TIMEOUT_S)
    if device is None:
        raise BleakError(f"Sensor {address} not found")
    try:
        stop = getattr(BleakScanner, "stop", None)
        if callable(stop):
            # Newer bleak exposes an instance method; find_device_by_address
            # already cleans up its own scan, so ignore that TypeError. Older
            # versions expose a class-level stop that must be awaited.
            try:
                await stop()
            except TypeError:
                pass
    except Exception:
        pass
    # Inner connect retry: Bleak can fail GATT discovery on the first try after
    # a scan (BlueZ still resolving services / bonded encryption settling) while
    # a plain `bluetoothctl connect` succeeds. Retry the connect phase a few
    # times with a settle delay before giving up this stream attempt (the outer
    # record_session loop then does a fresh scan + reconnect with backoff).
    last_error: BaseException | None = None
    for conn_try in range(3):
        disconnected = asyncio.Event()
        log.info("Connecting to Movesense... (try %d/3)", conn_try + 1)
        try:
            # NOTE: pass the address string (not the cached BLEDevice) so BlueZ
            # resolves a fresh connection each attempt; stale device objects
            # cause repeat "failed to discover services" failures.
            async with BleakClient(
                address, timeout=config.CONNECT_TIMEOUT_S, disconnected_callback=lambda _c: disconnected.set()
            ) as client:
                log.info("Connected.")
                await asyncio.sleep(0.3)  # let BlueZ finish service resolution on bonded links
                # BlueZ can report "connected" then fail GATT discovery (sensor slept /
                # link lost). Fail fast here instead of hanging on start_notify with an
                # empty service list.
                try:
                    if hasattr(client, "get_services"):
                        await client.get_services()
                except BleakError as svc_error:
                    raise BleakError("failed to discover services, device disconnected"
                                     f" (GATT discovery failed: {svc_error}; wake sensor, keep <1 m, clear BlueZ cache)")
                if not client.is_connected:
                    raise BleakError("failed to discover services, device disconnected"
                                     " (link lost before service discovery)")
                # BleakGATTServiceCollection (bleak>=2) has no len(): materialize
                # once; list() works for both it and the list-based fakes.
                discovered = list(client.services)
                if len(discovered) == 0:
                    raise BleakError("failed to discover services, device disconnected"
                                     " (empty GATT table; sensor asleep or stale BlueZ cache)")
                if config.GSP_SERVICE_UUID not in {s.uuid.lower() for s in discovered}:
                    raise SubscribeRejected(
                        "GSP service not found on this sensor (DFU mode or firmware without"
                        " GATT SensorData; check verify output and flash a GSP app)")
                _emit(event_sink, "device", device.name or address)
                _emit(event_sink, "connected", True)
                _emit(event_sink, "status", "Connected")
                events.append(f"{datetime.now().isoformat(timespec='seconds')} connected")
                await client.start_notify(config.GSP_NOTIFY_UUID, lambda _h, d: handle_packet(state, bytes(d)))
                await client.write_gatt_char(
                    config.GSP_WRITE_UUID, bytes([config.CMD_HELLO, config.HELLO_REFERENCE]), response=True
                )
                await asyncio.sleep(0.5)
                path = config.IMU9_PATH.format(rate=state.sample_rate_hz).encode()
                await client.write_gatt_char(
                    config.GSP_WRITE_UUID, bytes([config.CMD_SUBSCRIBE, reference]) + path, response=True
                )
                status = None
                for _ in range(30):  # up to ~3 s: BlueZ + slow advertising can exceed 0.7 s
                    await asyncio.sleep(0.1)
                    status = state.statuses.get(reference)
                    if status is not None:
                        break
                if status is not None and not 200 <= status < 300:
                    raise SubscribeRejected(f"SUBSCRIBE {path.decode()} rejected by sensor (HTTP {status}); this firmware may not offer that stream/rate")
                if status is None:
                    log.warning("No SUBSCRIBE response seen; continuing (data may still arrive).")
                log.info("Streaming IMU data...")
                ping_task = asyncio.ensure_future(_ping_loop(client, state, event_sink, ping_interval_s)) if ping_interval_s > 0 else None
                try:
                    if await _wait_end(disconnected, stop_event, max(0.0, deadline - loop.time())):
                        events.append(f"{datetime.now().isoformat(timespec='seconds')} connection lost")
                        log.warning("Connection lost.")
                        _emit(event_sink, "connected", False)
                        _emit(event_sink, "ping", None)
                        _emit(event_sink, "status", "Connection lost")
                finally:
                    if ping_task is not None:
                        ping_task.cancel()
                    if client.is_connected:
                        try:
                            await client.write_gatt_char(
                                config.GSP_WRITE_UUID, bytes([config.CMD_UNSUBSCRIBE, reference]), response=True
                            )
                            await client.stop_notify(config.GSP_NOTIFY_UUID)
                        except BleakError as error:
                            log.warning("Unsubscribe failed: %s", error)
                return  # streaming phase done (deadline/stop/disconnect handled); outer loop rescans if needed
        except SubscribeRejected:
            raise  # deterministic: no point retrying connects
        except (BleakError, asyncio.TimeoutError, OSError) as error:
            last_error = error
            if conn_try < 2:
                log.info("Connect try %d/3 failed (%s); retrying in 2 s...", conn_try + 1, error)
                await asyncio.sleep(2.0)
                continue
            raise


def raw_dataframe(state: StreamState, sensor_id: str) -> pd.DataFrame:
    columns = [config.TIME_COL, *config.ACC_COLS, *config.GYRO_COLS, *config.MAG_COLS]
    frame = pd.DataFrame(state.rows, columns=columns)
    frame["HeartRate"] = np.nan  # not part of the IMU9 stream: explicitly unavailable
    frame["SensorID"] = sensor_id
    return frame


async def record_session(
    address: str,
    sample_rate_hz: int,
    duration_s: float,
    session_id: str,
    placement: str,
    experiment: str,
    raw_dir: Path = config.RAW_DIR,
    sample_sink: Callable[[float, np.ndarray, np.ndarray], None] | None = None,
    stop_event: asyncio.Event | None = None,
    event_sink: Callable[[str, object], None] | None = None,
    ping_interval_s: float = 0.0,
) -> Path | None:
    """Record /Meas/IMU9/{rate} for duration_s (Ctrl+C stops early). Returns the raw CSV path, or None if no samples arrived."""
    if sample_rate_hz not in config.IMU9_SAMPLE_RATES_HZ:
        raise ValueError(f"Unsupported rate {sample_rate_hz}; documented: {config.IMU9_SAMPLE_RATES_HZ}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_path = raw_dir / f"{session_id}.csv"
    state = StreamState(sample_rate_hz=sample_rate_hz, sample_sink=sample_sink)
    events: list[str] = []
    started = datetime.now()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_s
    last_error: str | None = None
    with (raw_dir / f"{session_id}.bin").open("wb") as bin_file:
        state.bin_file = bin_file
        try:
            attempt = 0
            while loop.time() < deadline and attempt <= config.MAX_RECONNECTS and not (stop_event and stop_event.is_set()):
                reference = (config.SUBSCRIBE_REFERENCE + attempt) % 255 + 1
                try:
                    await _stream_once(address, state, deadline, reference, events, stop_event, event_sink, ping_interval_s)
                except SubscribeRejected as error:
                    log.error("%s", error)
                    last_error = str(error)
                    events.append(f"{datetime.now().isoformat(timespec='seconds')} error: {error}")
                    break  # deterministic failure: do not retry
                except (BleakError, asyncio.TimeoutError, OSError) as error:
                    message, fatal = describe_ble_error(error, address)
                    log.warning("BLE error: %s", message)
                    last_error = f"BLE error: {message}"
                    _emit(event_sink, "connected", False)
                    _emit(event_sink, "status", last_error)
                    events.append(f"{datetime.now().isoformat(timespec='seconds')} error: {message}")
                    if fatal:
                        break  # retrying cannot fix a missing/disabled adapter or a denied permission
                if loop.time() < deadline and not (stop_event and stop_event.is_set()):
                    attempt += 1
                    log.info("Reconnecting (%d/%d)...", attempt, config.MAX_RECONNECTS)
                    _emit(event_sink, "status", f"Reconnecting ({attempt}/{config.MAX_RECONNECTS})...")
                    await asyncio.sleep(min(1.0 * attempt, 5.0))
        finally:
            _emit(event_sink, "connected", False)
            _emit(event_sink, "status", f"Disconnected ({last_error[:110]})" if last_error else "Disconnected")
            hello = parse_hello(state.hello_packet) if state.hello_packet else None
            sensor_id = (hello or {}).get("serial") or address
            if state.rows:
                raw_dataframe(state, sensor_id).to_csv(csv_path, index=False)
            metadata = {
                "session_id": session_id,
                "date_time": started.isoformat(timespec="seconds"),
                "sensor_model": (hello or {}).get("product_name"),
                "sensor_serial": (hello or {}).get("serial"),
                "firmware": " ".join(filter(None, [(hello or {}).get("app_name"), (hello or {}).get("app_version")])) or None,
                "hello_raw_hex": state.hello_packet.hex() if state.hello_packet else None,
                "ble_address": address,
                "stream_path": config.IMU9_PATH.format(rate=sample_rate_hz),
                "sample_rate_hz": sample_rate_hz,
                "sensor_placement": placement,
                "experiment_type": experiment,
                "units": config.UNITS,
                "heart_rate": "unavailable (not part of the IMU9 stream)",
                "samples": len(state.rows),
                "packets": state.packets,
                "malformed_packets": state.malformed_packets,
                "missing_samples_detected": state.missing_samples,
                "connection_events": events,
                "processing_version": config.PROCESSING_VERSION,
            }
            if state.rows:
                csv_path.with_suffix(".json").write_text(json.dumps(metadata, indent=2))
                log.info("Saved %d samples (%d missing) to %s", len(state.rows), state.missing_samples, csv_path)
            else:
                (raw_dir / f"{session_id}.bin").unlink(missing_ok=True)
                log.warning("No samples received; nothing saved. %s", last_error or "")
    return csv_path if state.rows else None


# ----------------------------------------------------------------------- loading
def load_capture(path: Path, sample_rate_hz: int | None = None) -> tuple[pd.DataFrame, dict]:
    """Load a raw CSV (with optional JSON sidecar) or a framed .bin GSP capture."""
    path = Path(path)
    sidecar = path.with_suffix(".json")
    metadata = json.loads(sidecar.read_text()) if sidecar.exists() else {}
    rate = sample_rate_hz or metadata.get("sample_rate_hz")
    if path.suffix == ".csv":
        frame = pd.read_csv(path)
        missing = [c for c in config.RAW_COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")
        metadata.setdefault("sample_rate_hz", rate)
    elif path.suffix == ".bin":
        if rate is None:
            raise ValueError("A .bin capture has no embedded sample rate; pass --sample-rate.")
        state = StreamState(sample_rate_hz=int(rate))
        raw = path.read_bytes()
        offset = 0
        while offset + 2 <= len(raw):
            (length,) = struct.unpack_from("<H", raw, offset)
            offset += 2
            if offset + length > len(raw):
                log.warning("Capture ends mid-packet; ignoring %d trailing bytes.", len(raw) - offset)
                break
            handle_packet(state, raw[offset:offset + length])
            offset += length
        frame = raw_dataframe(state, metadata.get("sensor_serial") or "unknown")
        metadata.update(sample_rate_hz=int(rate), missing_samples_detected=state.missing_samples,
                        malformed_packets=state.malformed_packets)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    if frame.empty:
        raise ValueError(f"{path.name}: no IMU samples found")
    if metadata.get("sample_rate_hz") is None:
        raise ValueError("Sample rate unknown; pass --sample-rate.")
    return frame, metadata


def save_raw_csv(frame: pd.DataFrame, metadata: dict, path: Path) -> Path:
    """Write a raw CSV plus JSON sidecar (so MATLAB/Python can replay the same dataset)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    path.with_suffix(".json").write_text(json.dumps(metadata, indent=2, default=str))
    return path
