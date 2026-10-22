"""Analysis tests on SYNTHETIC kinematics with known ground truth."""
import numpy as np
import pandas as pd
import pytest

import analysis
import config
from config import Params

FS = 52.0
G = config.STANDARD_GRAVITY_MPS2


def _synthetic_raw(accel_profile_x, flight_s=0.0, rest_s=2.5, tilt_free=True):
    """Sensor flat (Z up). Optional constant-acceleration profile along world X; optional free fall."""
    rest = np.tile([0, 0, G], (int(rest_s * FS), 1))
    motion = np.array([[a, 0, G] for a in accel_profile_x]).reshape(-1, 3)
    fall = np.zeros((int(flight_s * FS), 3))
    acc = np.vstack([rest, motion, fall, rest]) if flight_s else np.vstack([rest, motion, rest])
    t_ms = np.arange(len(acc)) / FS * 1000.0
    frame = pd.DataFrame(acc, columns=config.ACC_COLS)
    frame[config.GYRO_COLS] = 0.0
    frame[config.MAG_COLS] = 0.0
    frame.insert(0, config.TIME_COL, t_ms)
    frame["HeartRate"], frame["SensorID"] = np.nan, "synthetic"
    return frame


def test_unit_conversion():
    assert analysis.m_to_cm(1.234) == pytest.approx(123.4)


def test_flight_height_formula():
    assert analysis.flight_height_m(0.5) == pytest.approx(G * 0.25 / 8)


def test_straight_line_distance_known_displacement():
    # +2 m/s^2 for 1 s then -2 m/s^2 for 1 s -> exactly 2 m
    profile = [2.0] * int(FS) + [-2.0] * int(FS)
    result = analysis.run_pipeline(_synthetic_raw(profile), FS)
    seg = result.segments[analysis.select_segment(result)]
    assert 100 * seg.distance_m == pytest.approx(200.0, rel=0.06)
    assert seg.valid


def test_constant_accelerometer_bias_is_removed_by_constrained_integration():
    profile = [2.3] * int(FS) + [-1.7] * int(FS)  # same motion plus +0.3 m/s^2 bias
    result = analysis.run_pipeline(_synthetic_raw(profile), FS)
    seg = result.segments[analysis.select_segment(result)]
    assert 100 * seg.distance_m == pytest.approx(200.0, rel=0.08)


def test_free_fall_flight_detected_and_height():
    raw = _synthetic_raw([2.0] * 10 + [-2.0] * 10, flight_s=0.5)
    result = analysis.run_pipeline(raw, FS)
    assert len(result.flights) == 1
    assert result.flights[0].duration_s == pytest.approx(0.5, abs=0.04)
    h_cm = 100 * analysis.flight_height_m(result.flights[0].duration_s)
    assert h_cm == pytest.approx(100 * G * 0.25 / 8, rel=0.15)


def test_stationary_recording_has_no_motion_segments():
    result = analysis.run_pipeline(_synthetic_raw([]), FS)
    assert result.segments == []
    assert analysis.select_segment(result) is None


def test_gap_does_not_corrupt_distance():
    # drop 6 samples mid-motion: trapezoid integration spans the gap (linear
    # interpolation across missing samples), so the distance stays sane and flagged
    profile = [2.0] * int(FS) + [-2.0] * int(FS)
    raw = _synthetic_raw(profile)
    mid = len(raw) // 2
    raw = raw.drop(range(mid, mid + 6)).reset_index(drop=True)
    result = analysis.run_pipeline(raw, FS)
    seg = result.segments[analysis.select_segment(result)]
    assert 100 * seg.distance_m == pytest.approx(200.0, rel=0.1)
    assert "data_gap" in seg.flags


def test_validation_requires_reference():
    assert analysis.validate_results(190.0, None) is None
    v = analysis.validate_results(190.0, 200.0)
    assert v["absolute_error_cm"] == pytest.approx(-10.0) and v["percent_error"] == pytest.approx(-5.0)
