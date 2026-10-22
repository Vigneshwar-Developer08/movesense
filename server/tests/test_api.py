"""API-level tests: validation, tracking bookkeeping, and real artefact exports.

All data here is synthetic and no BLE adapter or database is used.
"""
import asyncio
import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import api
import local_store
import session as session_mod


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "DATA_DIR", tmp_path / "local")
    monkeypatch.setattr(api.config, "RAW_DIR", tmp_path / "raw")
    api.manager._devices.clear()
    api.tracking_sessions.clear()
    yield
    api.tracking_sessions.clear()
    api.manager._devices.clear()


@pytest.fixture
def client():
    return TestClient(api.app)


def _register(address: str = "AA:BB:CC:DD:EE:FF", name: str = "Movesense") -> None:
    api.manager._devices[address] = api.ConnectedDevice(
        address=address, name=name, status="connected"
    )


class _FakeController:
    def __init__(self, n: int = 1000, state=session_mod.SessionState.LOCKED):
        x = np.arange(n, dtype=float)
        self.result = SimpleNamespace(
            position_cm=np.column_stack([x, np.zeros(n), np.zeros(n)]),
            flags=["test_flag"],
        )
        self.state = state
        self.completed: list = []
        self.fs = 52
        self.events: list = []
        self.continuous = False
        self.is_holding_still = False

    def live_motion(self) -> str:
        return "move"

    def provisional_trajectory(self):
        return None


def _session(session_id: str, address: str, controller: _FakeController) -> api.LiveTrackingSession:
    return api.LiveTrackingSession(
        session_id=session_id,
        address=address,
        device_name="Movesense",
        rate=52,
        continuous=False,
        controller=controller,
        stop_event=None,
        started_at="2026-01-01T00:00:00",
    )


def test_tracking_start_validates_payload(client):
    assert client.post("/api/tracking/start", json={"address": ""}).status_code == 422
    assert client.post(
        "/api/tracking/start", json={"address": "AA", "seconds": 0}
    ).status_code == 422
    assert client.post(
        "/api/tracking/start", json={"address": "AA", "seconds": 86401}
    ).status_code == 422


def test_tracking_start_rejects_disconnected_device(client):
    response = client.post("/api/tracking/start", json={"address": "AA:BB"})
    assert response.status_code == 409
    assert "not connected" in response.json()["detail"]


def test_tracking_start_rejects_unsupported_rate(client):
    _register("AA:BB")
    response = client.post("/api/tracking/start", json={"address": "AA:BB", "rate": 123})
    assert response.status_code == 400
    assert "Unsupported rate" in response.json()["detail"]


def test_tracking_ids_are_unique_within_one_second(client, monkeypatch):
    async def _noop(sess, seconds):
        return

    monkeypatch.setattr(api, "_run_tracking", _noop)
    _register("AA:BB")
    _register("CC:DD")

    first = client.post("/api/tracking/start", json={"address": "AA:BB"})
    second = client.post("/api/tracking/start", json={"address": "CC:DD"})
    assert first.status_code == 200
    assert second.status_code == 200
    ids = {first.json()["session_id"], second.json()["session_id"]}
    assert len(ids) == 2
    assert all(session_id.startswith("TRK_") for session_id in ids)


def test_disconnect_and_remove_are_blocked_while_tracking(client):
    _register("AA:BB")
    api.tracking_sessions["TRK_active"] = _session("TRK_active", "AA:BB", _FakeController())

    disconnect = client.post("/api/devices/disconnect", json={"address": "AA:BB"})
    remove = client.delete("/api/devices/AA:BB")
    assert disconnect.status_code == 409
    assert remove.status_code == 409


def test_trajectory_downsample_keeps_endpoint_and_true_metrics(client):
    api.tracking_sessions["TRK_test"] = _session("TRK_test", "AA:BB", _FakeController(1000))

    body = client.get("/api/tracking/TRK_test/trajectory?max_points=10").json()
    points = body["points_m"]
    assert len(points) <= 10
    assert points[0] == pytest.approx([0.0, 0.0, 0.0])
    assert points[-1][0] == pytest.approx(9.99)  # 999 cm from Point A, in metres
    assert body["distance_cm"] == pytest.approx(999.0)
    assert body["path_length_cm"] == pytest.approx(999.0)


def test_trajectory_filters_non_finite_points(client):
    ctrl = _FakeController(3)
    ctrl.result.position_cm = np.array(
        [[0.0, 0.0, 0.0], [np.nan, 0.0, 0.0], [100.0, 0.0, 0.0]]
    )
    ctrl.state = session_mod.SessionState.LOCKED
    api.tracking_sessions["TRK_nan"] = _session("TRK_nan", "AA:BB", ctrl)

    response = client.get("/api/tracking/TRK_nan/trajectory")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert all(
        np.isfinite(value) for point in body["points_m"] for value in point
    )
    assert body["points_m"][-1] == pytest.approx([1.0, 0.0, 0.0])


def test_preview_exception_does_not_break_trajectory_endpoint(client):
    class BrokenPreview(_FakeController):
        def provisional_trajectory(self):
            raise RuntimeError("preview failed")

    ctrl = BrokenPreview()
    ctrl.state = session_mod.SessionState.RECORDING
    api.tracking_sessions["TRK_bad_preview"] = _session(
        "TRK_bad_preview", "AA:BB", ctrl
    )

    response = client.get("/api/tracking/TRK_bad_preview/trajectory")
    assert response.status_code == 200
    assert response.json()["points_m"] == []


def test_trajectory_rejects_invalid_max_points(client):
    api.tracking_sessions["TRK_test"] = _session("TRK_test", "AA:BB", _FakeController())
    assert client.get("/api/tracking/TRK_test/trajectory?max_points=1").status_code == 422
    assert client.get("/api/tracking/TRK_test/trajectory?max_points=5001").status_code == 422


def test_samples_endpoint_returns_real_recent_samples(client):
    sess = _session("TRK_test", "AA:BB", _FakeController())
    sess.recent_samples.append((123.0, [0.1, 0.2, 9.8], [1.0, 2.0, 3.0]))
    api.tracking_sessions["TRK_test"] = sess

    body = client.get("/api/tracking/TRK_test/samples?limit=10").json()
    assert body["count"] == 1
    assert body["samples"][0]["t_ms"] == pytest.approx(123.0)
    assert body["samples"][0]["acc"] == pytest.approx([0.1, 0.2, 9.8])


def test_sessions_list_and_export_real_artifacts(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api.config, "PROCESSED_DIR", tmp_path)
    session_id = "MOVE_20260101_001"
    session_dir = tmp_path / session_id
    session_dir.mkdir()
    summary = {
        "session_id": session_id,
        "motion": "throw",
        "straight_line_distance_cm": 245.0,
        "trajectory_length_cm": 260.0,
        "quality_status": "GOOD",
        "movement_duration_s": 1.2,
    }
    (session_dir / f"{session_id}_trajectory_summary.json").write_text(
        json.dumps(summary)
    )
    (session_dir / f"{session_id}_trajectory.csv").write_text("time_s,x_m,y_m,z_m\n0,0,0,0\n")

    listing = client.get("/api/sessions").json()
    assert listing["count"] == 1
    assert listing["sessions"][0]["session_id"] == session_id
    assert listing["sessions"][0]["motion"] == "throw"
    assert listing["sessions"][0]["mtime"] == pytest.approx(
        (session_dir / f"{session_id}_trajectory_summary.json").stat().st_mtime
    )

    csv = client.get(f"/api/sessions/{session_id}/export?format=csv")
    assert csv.status_code == 200
    assert csv.text.startswith("time_s")
    assert csv.headers["content-type"].startswith("text/csv")

    js = client.get(f"/api/sessions/{session_id}/export?format=json")
    assert js.status_code == 200
    assert js.json()["session_id"] == session_id

    missing = client.get(f"/api/sessions/{session_id}/export?format=mat")
    assert missing.status_code == 404


def test_export_rejects_invalid_session_id(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api.config, "PROCESSED_DIR", tmp_path)
    assert client.get("/api/sessions/..%2Fetc/export?format=csv").status_code in (400, 404)
    assert client.get("/api/sessions/bad%20id/export?format=csv").status_code == 400
    assert client.get("/api/sessions/ok/export?format=exe").status_code == 422


def test_health_reports_ok(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "bluetooth_adapter" in body


def test_tracking_start_persists_local_metadata(client, monkeypatch):
    async def _noop(sess, seconds):
        return

    monkeypatch.setattr(api, "_run_tracking", _noop)
    _register("AA:BB", "Sensor One")

    response = client.post("/api/tracking/start", json={"address": "AA:BB"})
    assert response.status_code == 200
    session_id = response.json()["session_id"]

    record = local_store.get_tracking_session(session_id)
    assert record is not None
    assert record["address"] == "AA:BB"
    assert record["device"] == "Sensor One"
    assert record["rate_hz"] == 52
    assert record["status"] == "starting"
    assert record["raw"] == {}


def test_device_registry_restores_as_disconnected(client):
    local_store.save_devices(
        [
            {
                "address": "AA:BB",
                "name": "Movesense",
                "status": "connected",
                "connected_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert asyncio.run(api.manager.restore()) == 1

    body = client.get("/api/devices").json()
    assert body["total_count"] == 1
    assert body["connected_count"] == 0
    assert body["devices"][0]["status"] == "disconnected"


def test_local_sessions_list_and_raw_export(client):
    raw_dir = api.config.RAW_DIR
    raw_dir.mkdir(parents=True)
    session_id = "TRK_20260101_001"
    (raw_dir / f"{session_id}.csv").write_text("t_ms,ax\n0,1\n")
    (raw_dir / f"{session_id}.json").write_text('{"session_id": "TRK_20260101_001"}')
    (raw_dir / f"{session_id}.bin").write_bytes(b"\x00\x01")

    listing = client.get("/api/local/sessions").json()
    assert listing["count"] == 1
    record = listing["sessions"][0]
    assert record["session_id"] == session_id
    assert record["status"] == "raw-only"
    assert set(record["raw"]) == {"csv", "json", "bin"}

    detail = client.get(f"/api/local/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["session_id"] == session_id

    csv = client.get(f"/api/local/sessions/{session_id}/export?format=raw_csv")
    assert csv.status_code == 200
    assert csv.text.startswith("t_ms")
    assert csv.headers["content-type"].startswith("text/csv")

    binary = client.get(f"/api/local/sessions/{session_id}/export?format=raw_bin")
    assert binary.status_code == 200
    assert binary.content == b"\x00\x01"

    assert client.get(
        f"/api/local/sessions/{session_id}/export?format=raw_json"
    ).status_code == 200
    assert client.get(
        "/api/local/sessions/TRK_missing/export?format=raw_csv"
    ).status_code == 404
    assert client.get(
        "/api/local/sessions/bad%20id/export?format=raw_csv"
    ).status_code == 400


def test_last_speed_reads_newest_velocity_sample():
    result = SimpleNamespace(
        velocity_mps=np.array([[0.0, 0.0, 0.0], [0.3, 0.4, 0.0]])
    )
    assert api._last_speed_mps(result) == pytest.approx(0.5)
    assert api._last_speed_mps(
        SimpleNamespace(velocity_mps=np.array([0.3, 0.4, 0.0]))
    ) == pytest.approx(0.5)
    # Non-finite samples must not leak NaN/Infinity into the JSON snapshot.
    assert api._last_speed_mps(
        SimpleNamespace(velocity_mps=np.array([[np.nan, 0.0, 0.0]]))
    ) is None
    assert api._last_speed_mps(SimpleNamespace()) is None


def test_connect_reverifies_stale_registry_flag(client, monkeypatch):
    """A persisted "connected" flag is not a live link and must not block Connect."""
    _register("AA:BB")

    async def _fail(*_args, **_kwargs):
        raise RuntimeError("failed to discover services, device disconnected")

    monkeypatch.setattr(api.acquisition, "verify_device", _fail)
    response = client.post("/api/devices/connect", json={"address": "AA:BB"})
    assert response.status_code == 500
    assert api.manager._devices["AA:BB"].status == "disconnected"


def test_connect_accepts_device_held_by_active_tracking(client, monkeypatch):
    _register("AA:BB")
    api.tracking_sessions["TRK_active"] = _session(
        "TRK_active", "AA:BB", _FakeController()
    )

    async def _must_not_verify(*_args, **_kwargs):
        raise AssertionError("an active tracking link must not be re-verified")

    monkeypatch.setattr(api.acquisition, "verify_device", _must_not_verify)
    response = client.post("/api/devices/connect", json={"address": "AA:BB"})
    assert response.status_code == 200
    assert response.json()["tracking_session_id"] == "TRK_active"


def test_scan_includes_device_held_by_active_tracking(client, monkeypatch):
    async def _no_advertisements(**_kwargs):
        return []

    monkeypatch.setattr(api.acquisition, "discover_devices", _no_advertisements)
    _register("AA:BB")
    api.tracking_sessions["TRK_active"] = _session(
        "TRK_active", "AA:BB", _FakeController()
    )

    response = client.post("/api/devices/scan", json={"timeout_s": 1})
    assert response.status_code == 200
    devices = response.json()["devices"]
    assert [device["address"] for device in devices] == ["AA:BB"]
    assert devices[0]["is_connected"] is True
    assert devices[0]["tracking_session_id"] == "TRK_active"
