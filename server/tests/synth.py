"""SYNTHETIC IMU streams for tests only (known physics, seeded noise). Never real sensor data."""
import numpy as np

import config

FS = 52.0
G = config.STANDARD_GRAVITY_MPS2
STEP_MS = 1000.0 / FS


def still(seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(round(seconds * FS))
    return np.column_stack([rng.normal(0, 0.02, n), rng.normal(0, 0.02, n), G + rng.normal(0, 0.02, n)])


def push(accel_x: list[float], rng: np.random.Generator) -> np.ndarray:
    """Sensor flat, accelerating along world X by the given profile (m/s^2)."""
    a = still(len(accel_x) / FS, rng)
    a[:, 0] += np.asarray(accel_x)
    return a


def two_metre_push(rng) -> np.ndarray:
    """One sine period of acceleration, A = pi m/s^2, T = 2 s: v returns to 0 and x(T) = A T^2 / (2 pi) = 2.00 m.

    (Piecewise-constant acceleration is avoided on purpose: an IMU cannot tell constant
    acceleration plateaus from rest by variance alone, and real movements are not like that.)
    """
    n = int(2 * FS)
    t = np.arange(n) / FS
    return push(list(np.pi * np.sin(2 * np.pi * t / 2.0)), rng)


def stream(*parts: np.ndarray, t0_ms: float = 5000.0):
    acc = np.vstack(parts)
    t = t0_ms + np.arange(len(acc)) * STEP_MS
    gyro = np.random.default_rng(1).normal(0, 0.2, acc.shape)
    return t, acc, gyro


def feed_all(ctrl, t, acc, gyro) -> None:
    for ts, a, g in zip(t, acc, gyro):
        ctrl.feed(ts, a, g)
