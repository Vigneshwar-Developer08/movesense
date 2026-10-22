"""Timestamp validation, calibration, filtering, orientation, gravity removal.

Frames and signs:
  * Sensor frame: Movesense local X/Y/Z; no axis is assumed to be vertical.
  * World frame: right-handed, X/Y horizontal, Z upward.
  * Physical gravitational acceleration in world coordinates is [0, 0, -g].
  * A stationary accelerometer reports +g specific force, opposite physical gravity.

The initial attitude maps the measured stationary accelerometer vector onto world +Z.
Yaw (heading) is arbitrary because the magnetometer is not used; gravity only fixes pitch
and roll. Horizontal axes are therefore relative unless an external heading reference is added.
"""
import logging
from dataclasses import dataclass
from math import sqrt

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

import config
from config import Params


class CalibrationError(RuntimeError):
    pass


log = logging.getLogger(__name__)
WORLD_UP = np.array([0.0, 0.0, 1.0])


# ------------------------------------------------------------------------ timestamps
@dataclass(frozen=True)
class TimestampReport:
    n_samples: int
    duration_s: float
    median_dt_s: float
    n_gaps: int
    missing_samples_est: int
    max_gap_s: float
    non_monotonic: int


def gap_mask(t_s: np.ndarray, sample_rate_hz: float, params: Params) -> np.ndarray:
    """True at samples that follow a gap larger than max_gap_factor * nominal interval."""
    mask = np.zeros(len(t_s), dtype=bool)
    mask[1:] = np.diff(t_s) > params.max_gap_factor / sample_rate_hz
    return mask


def validate_timestamps(t_s: np.ndarray, sample_rate_hz: float, params: Params) -> TimestampReport:
    dt = np.diff(t_s)
    gaps = gap_mask(t_s, sample_rate_hz, params)[1:]
    nominal = 1.0 / sample_rate_hz
    return TimestampReport(
        n_samples=len(t_s),
        duration_s=float(t_s[-1] - t_s[0]),
        median_dt_s=float(np.median(dt)),
        n_gaps=int(gaps.sum()),
        missing_samples_est=int(np.round(dt[gaps] / nominal - 1).sum()) if gaps.any() else 0,
        max_gap_s=float(dt.max()),
        non_monotonic=int((dt <= 0).sum()),
    )


# -------------------------------------------------------------------------- calibration
@dataclass(frozen=True)
class Calibration:
    window_start: int
    window_stop: int
    gyro_bias_dps: tuple[float, float, float]
    acc_mean_mps2: tuple[float, float, float]
    gravity_mps2: float  # measured |a| while still (absorbs accelerometer scale/bias)
    gravity_deviation_mps2: float  # measured - standard gravity
    acc_std_mps2: float
    gyro_std_dps: float
    status: str

    @property
    def gravity_direction_sensor(self) -> tuple[float, float, float]:
        """Unit direction of stationary +g specific force in the sensor frame."""
        a = np.array(self.acc_mean_mps2, dtype=float)
        n = np.linalg.norm(a)
        return tuple(float(v) for v in (a / n)) if n > 0 else (float("nan"), float("nan"), float("nan"))


def find_still_window(acc: np.ndarray, gyro: np.ndarray, fs: float, params: Params) -> tuple[int, int] | None:
    n_win = max(2, round(params.calib_window_s * fs))
    if len(acc) < n_win:
        return None
    acc_norm = pd.Series(np.linalg.norm(acc, axis=1))
    acc_std = acc_norm.rolling(n_win).std().to_numpy()
    # Per-axis motion check: shaking can keep |a| almost constant while the
    # sensor rotates, which would otherwise pass as a "still" window.
    acc_axis_std = np.max(
        [pd.Series(acc[:, k]).rolling(n_win).std().to_numpy() for k in range(3)],
        axis=0,
    )
    acc_mean = acc_norm.rolling(n_win).mean().to_numpy()
    gyro_std = np.max([pd.Series(gyro[:, k]).rolling(n_win).std().to_numpy() for k in range(3)], axis=0)
    gyro_mean = np.max([np.abs(pd.Series(gyro[:, k]).rolling(n_win).mean().to_numpy()) for k in range(3)], axis=0)
    with np.errstate(invalid="ignore"):
        ok = (
            (acc_std < params.calib_max_acc_std_mps2)
            & (acc_axis_std < params.calib_max_acc_std_mps2)
            & (gyro_std < params.calib_max_gyro_std_dps)
            & (gyro_mean < params.calib_max_gyro_bias_dps)
            & (np.abs(acc_mean - config.STANDARD_GRAVITY_MPS2) < params.calib_gravity_tol_mps2)
        )
    hits = np.flatnonzero(ok)
    if hits.size == 0:
        return None
    return int(hits[0] - n_win + 1), int(hits[0] + 1)


def calibrate(acc: np.ndarray, gyro: np.ndarray, fs: float, params: Params) -> Calibration:
    window = find_still_window(acc, gyro, fs, params)
    if window is None:
        raise CalibrationError(
            f"No stationary window of {params.calib_window_s:.1f} s found. Keep the sensor still "
            "for ~2 s at the start of the recording."
        )
    start, stop = window
    acc_w, gyro_w = acc[start:stop], gyro[start:stop]
    acc_norm = np.linalg.norm(acc_w, axis=1)
    gravity = float(acc_norm.mean())
    cal = Calibration(
        window_start=start,
        window_stop=stop,
        gyro_bias_dps=tuple(float(v) for v in gyro_w.mean(axis=0)),
        acc_mean_mps2=tuple(float(v) for v in acc_w.mean(axis=0)),
        gravity_mps2=gravity,
        gravity_deviation_mps2=gravity - config.STANDARD_GRAVITY_MPS2,
        acc_std_mps2=float(acc_norm.std()),
        gyro_std_dps=float(gyro_w.std(axis=0).max()),
        status="OK",
    )
    log.info("[CAL] Acc mean = %s", np.array2string(np.array(cal.acc_mean_mps2), precision=4))
    log.info("[CAL] Acc magnitude = %.4f m/s^2", cal.gravity_mps2)
    log.info("[CAL] Gyro bias = %s", np.array2string(np.array(cal.gyro_bias_dps), precision=4))
    log.info("[CAL] Initial gravity direction = %s", np.array2string(np.array(cal.gravity_direction_sensor), precision=4))
    log.info("[CAL] Initial orientation initialized")
    return cal


# ----------------------------------------------------------------------------- filtering
def lowpass(x: np.ndarray, fs: float, params: Params) -> np.ndarray:
    """Zero-phase Butterworth low-pass along axis 0 (offline analysis only)."""
    nyquist = fs / 2.0
    if not 0 < params.lowpass_cutoff_hz < nyquist:
        raise ValueError(f"Cutoff {params.lowpass_cutoff_hz} Hz must be below Nyquist {nyquist} Hz")
    b, a = butter(params.filter_order, params.lowpass_cutoff_hz / nyquist)
    padlen = min(3 * (params.filter_order + 1), x.shape[0] - 1)  # scipy default, capped for short windows
    return filtfilt(b, a, x, axis=0, padlen=padlen)


# ------------------------------------------------------------------------- orientation
def normalize_quaternion(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q)
    if n <= 0:
        raise ValueError("zero-length quaternion")
    return q / n


def quaternion_from_two_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Quaternion (w,x,y,z) rotating normalized source onto normalized target.

    Handles aligned, nearly opposite and zero-length inputs without relying on any sensor axis
    being upright. For opposite vectors, a stable perpendicular axis is chosen.
    """
    src = np.asarray(source, dtype=float)
    dst = np.asarray(target, dtype=float)
    ns, nt = np.linalg.norm(src), np.linalg.norm(dst)
    if ns <= 1e-12 or nt <= 1e-12:
        raise ValueError("cannot build quaternion from a zero-length vector")
    src, dst = src / ns, dst / nt
    dot = float(np.clip(np.dot(src, dst), -1.0, 1.0))
    if dot > 1.0 - 1e-10:
        return np.array([1.0, 0.0, 0.0, 0.0])
    if dot < -1.0 + 1e-10:
        basis = np.array([1.0, 0.0, 0.0]) if abs(src[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        axis = np.cross(src, basis)
        axis = axis / np.linalg.norm(axis)
        return np.array([0.0, *axis])
    axis = np.cross(src, dst)
    q = np.array([1.0 + dot, axis[0], axis[1], axis[2]])
    return normalize_quaternion(q)


def initial_quaternion(acc_mean: np.ndarray) -> np.ndarray:
    """Initial sensor->world attitude from stationary accelerometer data.

    Stationary acceleration is treated as +g specific force, so it should rotate onto world
    +Z. This works for upright, upside-down, sideways, and arbitrary initial orientations.
    """
    return quaternion_from_two_vectors(acc_mean, WORLD_UP)


def _rotate_world_to_sensor(q: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Rotate world-frame vectors into sensor frame using inverse sensor->world quaternions."""
    inv = q.copy()
    inv[:, 1:] *= -1
    return rotate_to_world(inv, vectors)


def estimate_orientation(
    acc: np.ndarray, gyro_dps: np.ndarray, dt_s: np.ndarray, q0: np.ndarray, gravity: float, params: Params
) -> np.ndarray:
    """Madgwick-style IMU filter (accelerometer + gyroscope). Returns sensor->world quaternions.

    The accelerometer correction is applied only while | |a| - g | < acc_gate, so impacts and
    free fall do not corrupt the tilt estimate (gyroscope-only propagation there). The correction
    compares normalized +g specific-force directions; it does not assume a particular sensor axis.
    """
    n = len(acc)
    gyro = np.deg2rad(gyro_dps)
    out = np.empty((n, 4))
    w, x, y, z = (float(v) for v in q0)
    beta, gate = params.madgwick_beta, params.acc_gate_mps2
    for i in range(n):
        gx, gy, gz = gyro[i]
        dw = 0.5 * (-x * gx - y * gy - z * gz)
        dx = 0.5 * (w * gx + y * gz - z * gy)
        dy = 0.5 * (w * gy - x * gz + z * gx)
        dz = 0.5 * (w * gz + x * gy - y * gx)
        ax, ay, az = acc[i]
        norm = sqrt(ax * ax + ay * ay + az * az)
        if norm > 0.0 and abs(norm - gravity) < gate:
            measured = np.array([ax / norm, ay / norm, az / norm])
            q_now = normalize_quaternion(np.array([w, x, y, z]))
            expected = _rotate_world_to_sensor(q_now[None, :], WORLD_UP[None, :])[0]
            correction = quaternion_from_two_vectors(expected, measured)
            angle = 2.0 * np.arctan2(np.linalg.norm(correction[1:]), abs(correction[0]))
            if angle > 1e-12:
                # Small corrective angular velocity in sensor frame. This keeps the update
                # continuous through upside-down poses and avoids branchy axis sign handling.
                axis = correction[1:] / np.linalg.norm(correction[1:])
                gx, gy, gz = gx + 2.0 * beta * axis[0] * angle, gy + 2.0 * beta * axis[1] * angle, gz + 2.0 * beta * axis[2] * angle
                dw = 0.5 * (-x * gx - y * gy - z * gz)
                dx = 0.5 * (w * gx + y * gz - z * gy)
                dy = 0.5 * (w * gy - x * gz + z * gx)
                dz = 0.5 * (w * gz + x * gy - y * gx)
        h = dt_s[i]
        w, x, y, z = w + dw * h, x + dx * h, y + dy * h, z + dz * h
        qn = sqrt(w * w + x * x + y * y + z * z)
        w, x, y, z = w / qn, x / qn, y / qn, z / qn
        out[i] = (w, x, y, z)
    return out


def rotate_to_world(q: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Rotate sensor-frame vectors into the world frame using quaternions (w,x,y,z)."""
    w, x, y, z = q.T
    rot = np.empty((len(q), 3, 3))
    rot[:, 0] = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1)
    rot[:, 1] = np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1)
    rot[:, 2] = np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1)
    return np.einsum("nij,nj->ni", rot, vectors)


def remove_gravity(world_acc: np.ndarray, gravity: float) -> np.ndarray:
    """Linear acceleration from world-frame accelerometer specific force.

    A stationary accelerometer reports +g along world +Z after orientation compensation, so
    subtract [0, 0, g]. The result should be approximately [0, 0, 0] for any still mounting.
    """
    return world_acc - np.array([0.0, 0.0, gravity])


@dataclass(frozen=True)
class GravityCompensationReport:
    mean_linear_acc_mps2: tuple[float, float, float]
    mean_norm_mps2: float
    status: str


def gravity_compensation_report(linear_acc: np.ndarray, params: Params) -> GravityCompensationReport:
    mean = np.mean(linear_acc, axis=0)
    norm = float(np.linalg.norm(mean))
    status = "OK" if norm <= params.gravity_comp_mean_tol_mps2 else "WARNING"
    log.info("[ATT] Mean stationary linear acceleration = %s", np.array2string(mean, precision=4))
    if status != "OK":
        log.warning("GRAVITY COMPENSATION WARNING")
    return GravityCompensationReport(tuple(float(v) for v in mean), norm, status)
