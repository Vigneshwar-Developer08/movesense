"""One-shot session / trigger tests on SYNTHETIC streams (see tests/synth.py)."""
import numpy as np
import pytest

import acquisition
import config
import session as sm
import trajectory
from config import Params
from tests.synth import FS, STEP_MS, feed_all, push, still, stream, two_metre_push

S = sm.SessionState


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def ctrl(tmp_path):
    return sm.SessionController(FS, output_dir=tmp_path)


def test_idle_until_armed_and_samples_ignored(ctrl, rng):
    t, a, g = stream(still(2, rng), two_metre_push(rng), still(2, rng))
    feed_all(ctrl, t, a, g)
    assert ctrl.state is S.IDLE and ctrl.result is None and ctrl.ignored_samples == len(t)


def test_full_session_distance_state_and_lock(ctrl, rng):
    ctrl.arm()
    t, a, g = stream(still(3, rng), two_metre_push(rng), still(2, rng))
    feed_all(ctrl, t, a, g)
    assert ctrl.state is S.LOCKED
    r = ctrl.result
    assert r.straight_line_cm == pytest.approx(200.0, rel=0.08)
    assert r.path_length_cm >= r.straight_line_cm - 1e-6
    assert r.point_a_cm == (0.0, 20.0, 40.0) and r.session_id.startswith("MOVE_")
    assert r.closure_error_mps < 0.5 and "large_closure_error" not in r.flags
    assert r.t_s[-1] == pytest.approx(r.duration_s)
    assert trajectory.summary_dict(r, {}, None, None)["distance_source"] == "IMU-only inertial estimate; subject to drift"


def _flip_180_about_x(vectors: np.ndarray) -> np.ndarray:
    """Mount the sensor upside down (180 deg about sensor X): [x, y, z] -> [x, -y, -z]."""
    flipped = vectors.copy()
    flipped[:, 1:] *= -1.0
    return flipped


def test_upside_down_mount_session_distance(ctrl, rng):
    """Flipped mounting (head upside down) must measure the same distance: no axis is assumed vertical."""
    ctrl.arm()
    t, a, g = stream(still(3, rng), two_metre_push(rng), still(2, rng))
    feed_all(ctrl, t, _flip_180_about_x(a), _flip_180_about_x(g))
    assert ctrl.state is S.LOCKED
    r = ctrl.result
    assert r.straight_line_cm == pytest.approx(200.0, rel=0.1)
    assert r.closure_error_mps < 0.5 and "large_closure_error" not in r.flags


def test_one_shot_no_second_trajectory_until_reset(ctrl, rng):
    ctrl.arm()
    feed_all(ctrl, *stream(still(3, rng), two_metre_push(rng), still(2, rng)))
    first, n_points = ctrl.result, len(ctrl.result.t_s)
    t, a, g = stream(two_metre_push(rng), still(2, rng), t0_ms=1e6)  # another movement while LOCKED
    feed_all(ctrl, t, a, g)
    assert ctrl.result is first and len(first.t_s) == n_points and len(ctrl.completed) == 1
    assert ctrl.reset() and ctrl.state is S.IDLE and ctrl.completed == [first]
    assert ctrl.arm()
    feed_all(ctrl, *stream(still(3, rng), two_metre_push(rng), still(2, rng), t0_ms=2e6))
    assert len(ctrl.completed) == 2 and ctrl.completed[1].session_id != first.session_id


def test_duplicate_and_out_of_order_samples_rejected(ctrl, rng):
    ctrl.arm()
    t, a, g = stream(still(3, rng))
    for i in range(len(t)):
        ctrl.feed(t[i], a[i], g[i])
        ctrl.feed(t[i], a[i], g[i])  # duplicate packet
    ctrl.feed(t[10], a[10], g[10])  # out of order
    assert ctrl.samples.duplicates == len(t) and ctrl.samples.out_of_order == 1
    assert ctrl.samples.accepted == len(t)


def test_very_short_movement_rejected_then_real_movement_accepted(ctrl, rng):
    ctrl.arm()
    bump = push([3.0] * 4 + [-3.0] * 4, rng)
    feed_all(ctrl, *stream(still(3, rng), bump, still(3, rng)))
    assert ctrl.state is S.ARMED and ctrl.result is None and ctrl.rejected_movements == 1
    feed_all(ctrl, *stream(still(2, rng), two_metre_push(rng), still(2, rng), t0_ms=1e6))  # fresh still baseline first
    assert ctrl.state is S.LOCKED and ctrl.result.straight_line_cm == pytest.approx(200.0, rel=0.1)


def test_stationary_after_arming_creates_nothing(ctrl, rng):
    ctrl.arm()
    feed_all(ctrl, *stream(still(10, rng)))
    assert ctrl.state is S.ARMED and ctrl.result is None  # stop trigger ignored before start


def test_baseline_rejects_shaking_with_constant_acceleration_magnitude(ctrl):
    n = ctrl.triggers.n_base
    angle = np.tile(np.array([-0.12, 0.12]), n // 2 + 1)[:n]
    acc = np.column_stack([
        config.STANDARD_GRAVITY_MPS2 * np.sin(angle),
        np.zeros(n),
        config.STANDARD_GRAVITY_MPS2 * np.cos(angle),
    ])
    gyro = np.zeros_like(acc)
    assert np.linalg.norm(acc, axis=1).std(ddof=1) < ctrl.p.calib_max_acc_std_mps2
    assert ctrl.triggers.baseline(acc, gyro) is None


def test_no_stop_trigger_stays_recording_until_abort(ctrl, rng):
    ctrl.arm()
    shaking = push([3.0, -3.0] * 80, rng)  # never settles
    feed_all(ctrl, *stream(still(3, rng), shaking))
    assert ctrl.state is S.RECORDING and ctrl.result is None
    assert ctrl.reset(discard=False) is False and ctrl.state is S.RECORDING
    assert ctrl.abort() and ctrl.state is S.IDLE and ctrl.result is None


def test_timeout_aborts_without_fabricating_point_b(tmp_path, rng):
    c = sm.SessionController(FS, Params(trig_max_session_s=1.0), tmp_path)
    c.arm()
    feed_all(c, *stream(still(3, rng), push([3.0, -3.0] * 80, rng)))
    assert c.state is S.IDLE and c.result is None and any("no stop trigger" in m for _, m in c.events)


def test_gap_while_armed_restarts_baseline_and_gap_while_recording_is_flagged(ctrl, rng):
    ctrl.arm()
    t, a, g = stream(still(3, rng))
    feed_all(ctrl, t, a, g)
    assert ctrl._baseline is not None
    ctrl.feed(t[-1] + 20 * STEP_MS, a[-1], g[-1])  # 20 samples missing
    assert ctrl._baseline is None
    # recording gap
    c2 = sm.SessionController(FS, output_dir=ctrl.output_dir)
    c2.arm()
    t, a, g = stream(still(3, rng), two_metre_push(rng), still(2, rng))
    keep = np.ones(len(t), bool)
    keep[3 * 52 + 40:3 * 52 + 46] = False  # drop 6 samples mid-movement
    feed_all(c2, t[keep], a[keep], g[keep])
    assert c2.state is S.LOCKED and "data_gap" in c2.result.flags


def test_arm_only_from_idle_and_illegal_transition_raises(ctrl):
    assert ctrl.arm() and ctrl.arm() is False
    with pytest.raises(RuntimeError):
        ctrl._go(S.COMPLETE)


def test_buffer_frozen_after_stop():
    buf = sm.TrajectoryBuffer()
    buf.append(0.0, np.zeros(3), np.zeros(3))
    buf.append(1.0, np.zeros(3), np.zeros(3))
    buf.freeze(1)
    assert len(buf.t_ms) == 1
    with pytest.raises(RuntimeError):
        buf.append(2.0, np.zeros(3), np.zeros(3))


def test_session_ids_unique(tmp_path):
    ids = {trajectory.next_session_id(tmp_path) for _ in range(5)}
    assert len(ids) == 5


def test_stream_sink_feeds_controller_once_per_sample(ctrl, rng):
    import struct
    ctrl.arm()
    state = acquisition.StreamState(int(FS), sample_sink=lambda ts, a, g: ctrl.feed(ts, a, g))
    t, acc, gyro = stream(still(3, rng))
    for i in range(0, len(t) - 3, 3):
        body = struct.pack("<I", int(round(t[i]))) + struct.pack(
            "<27f", *acc[i:i + 3].ravel(), *gyro[i:i + 3].ravel(), *np.zeros(9))
        acquisition.handle_packet(state, bytes([config.RESP_DATA, 42]) + body)
    assert ctrl.samples.accepted == len(state.rows) and ctrl._baseline is not None
