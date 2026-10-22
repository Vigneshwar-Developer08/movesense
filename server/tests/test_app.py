"""Ping, connection display, provisional path and viewer tests. Signals are SYNTHETIC; the fake BLE client only
exercises ping TIMING logic (it is never presented as sensor data)."""
import asyncio

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

import acquisition  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import session as sm  # noqa: E402
from tests.synth import FS, STEP_MS, push, still, stream, two_metre_push  # noqa: E402

S = sm.SessionState


class FakeClient:
    """Answers a HELLO write with a HELLO response notification after a fixed delay."""

    def __init__(self, state, delay_s):
        self.state, self.delay_s = state, delay_s

    async def write_gatt_char(self, uuid, data, response=True):
        async def reply():
            await asyncio.sleep(self.delay_s)
            acquisition.handle_packet(self.state, bytes([config.RESP_COMMAND, config.HELLO_REFERENCE, 0xC8, 0x00]) + b"\x01A\x00B\x00C\x00D\x00E\x00")
        asyncio.get_running_loop().create_task(reply())


def test_ping_measures_round_trip_in_ms():
    pings = []

    async def run():
        state = acquisition.StreamState(sample_rate_hz=52)
        task = asyncio.ensure_future(acquisition._ping_loop(FakeClient(state, 0.04), state, lambda k, v: pings.append(v) if k == "ping" else None, 0.05))
        await asyncio.sleep(0.4)
        task.cancel()

    asyncio.run(run())
    assert len(pings) >= 3 and all(40 <= p <= 120 for p in pings)


def test_ping_timeout_reports_none():
    pings = []

    class Silent(FakeClient):
        async def write_gatt_char(self, *a, **k):
            return None

    async def run():
        state = acquisition.StreamState(sample_rate_hz=52)
        task = asyncio.ensure_future(acquisition._ping_loop(Silent(state, 0), state, lambda k, v: pings.append(v), 0.01))
        await asyncio.sleep(2.3)
        task.cancel()

    asyncio.run(run())
    assert pings == [None]


def test_provisional_only_while_recording_and_never_stored(tmp_path):
    rng = np.random.default_rng(3)
    c = sm.SessionController(FS, output_dir=tmp_path)
    assert c.provisional_trajectory() is None
    c.arm()
    t, a, g = stream(still(3, rng), two_metre_push(rng), still(2, rng))
    seen = []
    for ts, acc, gyr in zip(t, a, g):
        c.feed(ts, acc, gyr)
        if c.state is S.RECORDING:
            prov = c.provisional_trajectory()
            if prov is not None:
                seen.append(prov)
    assert seen and c.state is S.LOCKED and c.provisional_trajectory() is None
    assert len(c.completed) == 1 and c.result.session_id != "provisional"


def _view(tmp_path, monkeypatch, mode="live"):
    monkeypatch.setattr(config, "PROCESSED_DIR", tmp_path)
    feed = app.Feed(mode)
    ctrl = sm.SessionController(FS, output_dir=tmp_path)
    return feed, ctrl, app.LiveView(plt, feed, ctrl)


def test_viewer_connection_ping_and_full_session(tmp_path, monkeypatch):
    feed, ctrl, view = _view(tmp_path, monkeypatch)
    view.update()
    assert "Starting" in view.t_conn.get_text() or "●" in view.t_conn.get_text()
    assert not hasattr(view, "btn_arm")
    feed.event("device", "Movesense TEST"); feed.event("connected", True); feed.event("ping", 23.4)
    view.update()
    assert "Connected" in view.t_conn.get_text() and "Movesense TEST" in view.t_conn.get_text()
    assert view.t_ping.get_text() == "Ping  23 ms"
    feed.event("connected", False); feed.event("status", "Connection lost")
    view.update()
    assert "Connection lost" in view.t_conn.get_text() and view.t_ping.get_text() == "Ping  — ms"
    # Automatic still-move-still detection from the viewer startup
    feed.event("connected", True)
    assert ctrl.state is S.ARMED
    assert view.btn_reset.label.get_text() == "NEW"
    rng = np.random.default_rng(5)
    t, a, g = stream(still(3, rng), two_metre_push(rng), still(2, rng))
    for ts, acc, gyr in zip(t, a, g):
        feed.sink(ts, acc, gyr)
    view.update()
    assert ctrl.state is S.LOCKED and view.t_state.get_text() == "LOCKED"
    assert "IMU estimate" in view.t_warn.get_text()
    assert float(view.t_dist.get_text().split()[0]) == pytest.approx(200.0, rel=0.1)
    assert len(view.ax.lines) == 3  # path + A + B: one path line only, no axis triad
    view.fig.canvas.draw()
    assert view.exported and any(tmp_path.glob("MOVE_*/MOVE_*_trajectory.csv"))
    ctrl.result.flags[:] = ["large_closure_error", "height_inconsistent_with_flight"]
    view.update()
    view.fig.canvas.draw()
    warning_bbox = view.t_warn.get_window_extent(view.fig.canvas.get_renderer())
    assert warning_bbox.x1 <= view.fig.bbox.x1
    assert warning_bbox.height > view.t_warn.get_fontsize()
    hint_bbox = view.t_hint.get_window_extent(view.fig.canvas.get_renderer())
    assert hint_bbox.x1 <= view.fig.bbox.x1


def test_viewer_new_starts_another_automatic_session(tmp_path, monkeypatch):
    feed, ctrl, view = _view(tmp_path, monkeypatch)
    assert ctrl.state is S.ARMED
    rng = np.random.default_rng(6)
    for ts, acc, gyr in zip(*stream(still(3, rng), two_metre_push(rng), still(2, rng))):
        feed.sink(ts, acc, gyr)
    view.update()
    assert ctrl.state is S.LOCKED
    view.reset()
    view.update()
    assert ctrl.state is S.ARMED and view.t_dist.get_text() == "— cm" and len(ctrl.completed) == 1
    rng = np.random.default_rng(7)
    for ts, acc, gyro in zip(*stream(still(3, rng), two_metre_push(rng), still(2, rng), t0_ms=1e6)):
        feed.sink(ts, acc, gyro)
    view.update()
    assert ctrl.state is S.LOCKED and len(ctrl.completed) == 2
