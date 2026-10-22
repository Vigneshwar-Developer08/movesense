"""Unit tests. All signals here are SYNTHETIC, generated from known physics, never real data."""
import numpy as np
import pytest

import config
import processing as proc
from config import Params

FS = 52.0
G = config.STANDARD_GRAVITY_MPS2


def test_initial_quaternion_aligns_gravity_to_z():
    acc = np.array([[2.0, -1.5, 9.5]])
    q = proc.initial_quaternion(acc[0])[None, :]
    world = proc.rotate_to_world(q, acc)[0]
    assert world[:2] == pytest.approx([0, 0], abs=1e-9)
    assert world[2] == pytest.approx(np.linalg.norm(acc[0]))


@pytest.mark.parametrize("source", [
    [0, 0, 1],
    [0, 0, -1],
    [0, 1, 0],
    [0, -1, 0],
    [1, 0, 0],
    [-1, 0, 0],
])
def test_quaternion_from_two_vectors_maps_axis_to_world_up(source):
    q = proc.quaternion_from_two_vectors(np.array(source, dtype=float), np.array([0.0, 0.0, 1.0]))
    world = proc.rotate_to_world(q[None, :], np.array(source, dtype=float)[None, :])[0]
    assert world == pytest.approx([0, 0, np.linalg.norm(source)], abs=1e-9)


def test_quaternion_from_two_vectors_rejects_zero_vector():
    with pytest.raises(ValueError):
        proc.quaternion_from_two_vectors(np.zeros(3), np.array([0.0, 0.0, 1.0]))


def test_orientation_static_tilt_gives_zero_linear_acceleration():
    acc = np.tile([2.0, -1.5, 9.5], (300, 1))
    g = np.linalg.norm(acc[0])
    q = proc.estimate_orientation(acc, np.zeros((300, 3)), np.full(300, 1 / FS),
                                  proc.initial_quaternion(acc[0]), g, Params())
    lin = proc.remove_gravity(proc.rotate_to_world(q, acc), g)
    assert np.abs(lin).max() < 1e-6


def _quat_from_axis_angle(axis, degrees):
    axis = np.asarray(axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    half = np.deg2rad(degrees) / 2.0
    return np.array([np.cos(half), *(np.sin(half) * axis)])


def _world_to_sensor(q_sensor_to_world, vectors):
    q = q_sensor_to_world.copy()[None, :]
    q[:, 1:] *= -1.0
    return proc.rotate_to_world(q, vectors)


@pytest.mark.parametrize("q_mount", [
    np.array([1.0, 0.0, 0.0, 0.0]),     # identity
    _quat_from_axis_angle([1, 0, 0], 180),
    _quat_from_axis_angle([0, 1, 0], 180),
    _quat_from_axis_angle([1, 0, 0], 90),
    _quat_from_axis_angle([0, 1, 0], 90),
])
def test_flipped_mounts_recover_world_gravity_and_horizontal_magnitude(q_mount):
    physical_linear_world = np.array([[1.0, 0.0, 0.0]])
    specific_world = physical_linear_world + np.array([[0.0, 0.0, G]])
    acc_sensor = _world_to_sensor(q_mount, specific_world)
    still_sensor = _world_to_sensor(q_mount, np.array([[0.0, 0.0, G]]))[0]
    q0 = proc.initial_quaternion(still_sensor)
    recovered = proc.remove_gravity(proc.rotate_to_world(q0[None, :], acc_sensor), G)[0]
    stationary = proc.remove_gravity(proc.rotate_to_world(q0[None, :], still_sensor[None, :]), G)[0]
    assert stationary == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    assert recovered[2] == pytest.approx(0.0, abs=1e-9)
    assert np.linalg.norm(recovered[:2]) == pytest.approx(np.linalg.norm(physical_linear_world[0, :2]), abs=1e-9)


def test_yaw_is_not_observable_from_gravity_alone():
    q_yaw_180 = _quat_from_axis_angle([0, 0, 1], 180)
    physical_linear_world = np.array([[1.0, 0.0, 0.0]])
    specific_world = physical_linear_world + np.array([[0.0, 0.0, G]])
    acc_sensor = _world_to_sensor(q_yaw_180, specific_world)
    q0 = proc.initial_quaternion(np.array([0.0, 0.0, G]))
    recovered = proc.remove_gravity(proc.rotate_to_world(q0[None, :], acc_sensor), G)[0]
    assert recovered[:2] == pytest.approx([-1.0, 0.0], abs=1e-9)


def test_orientation_tracks_gyro_rotation():
    # rotate 90 deg about X in 1 s while accelerometer gate rejects (|a|=0) -> gyro-only propagation
    n = int(FS)
    gyro = np.tile([90.0, 0.0, 0.0], (n, 1))
    q = proc.estimate_orientation(np.zeros((n, 3)), gyro, np.full(n, 1 / FS),
                                  np.array([1.0, 0, 0, 0]), G, Params())
    angle = 2 * np.arccos(abs(q[-1, 0]))
    assert np.degrees(angle) == pytest.approx(90.0, abs=1.0)


def test_calibration_recovers_gyro_bias_and_gravity():
    rng = np.random.default_rng(0)
    n = int(3 * FS)
    acc = np.tile([0.2, -0.3, 9.85], (n, 1)) + rng.normal(0, 0.03, (n, 3))
    gyro = np.array([1.4, -2.5, 0.3]) + rng.normal(0, 0.1, (n, 3))
    cal = proc.calibrate(acc, gyro, FS, Params())
    assert cal.gyro_bias_dps == pytest.approx((1.4, -2.5, 0.3), abs=0.1)
    assert cal.gravity_mps2 == pytest.approx(np.linalg.norm([0.2, -0.3, 9.85]), abs=0.05)


def test_calibration_fails_without_stationary_window():
    t = np.arange(int(5 * FS)) / FS
    acc = np.stack([3 * np.sin(2 * np.pi * t), np.zeros_like(t), 9.8 + 2 * np.cos(2 * np.pi * t)], axis=1)
    with pytest.raises(proc.CalibrationError):
        proc.calibrate(acc, np.zeros_like(acc), FS, Params())


def test_calibration_rejects_rotation_with_constant_acc_magnitude():
    # A shaking sensor can keep |a| ~= g while its axes move a lot; |a| std alone
    # would wrongly accept this as a still window.
    t = np.arange(int(5 * FS)) / FS
    acc = np.stack([G * np.cos(2 * np.pi * 2 * t), G * np.sin(2 * np.pi * 2 * t), np.zeros_like(t)], axis=1)
    with pytest.raises(proc.CalibrationError):
        proc.calibrate(acc, np.zeros_like(acc), FS, Params())


def test_lowpass_attenuates_high_frequency_and_validates_cutoff():
    t = np.arange(400) / FS
    x = np.sin(2 * np.pi * 2 * t) + np.sin(2 * np.pi * 24 * t)
    y = proc.lowpass(x[:, None], FS, Params())[:, 0]
    assert np.std(y - np.sin(2 * np.pi * 2 * t)) < 0.15
    with pytest.raises(ValueError):
        proc.lowpass(x[:, None], FS, Params(lowpass_cutoff_hz=30.0))


def test_timestamp_gap_detection():
    t = np.arange(100) / FS
    t = np.delete(t, range(40, 45))  # 5 missing samples
    report = proc.validate_timestamps(t, FS, Params())
    assert report.n_gaps == 1 and report.missing_samples_est == 5 and report.non_monotonic == 0
