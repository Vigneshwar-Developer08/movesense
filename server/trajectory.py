"""A-to-B trajectory reconstruction for one finalized recording window, plus export.

World frame: right-handed, Z up (opposite gravity), origin = Point A (the rest sample just
before motion onset). Yaw is arbitrary because the magnetometer is not used, so X/Y are only
meaningful relative to each other (set Params.align_x_to_b to put Point B on +X).
"""
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import analysis
import config
import processing as proc
from config import Params


@dataclass(frozen=True)
class Baseline:
    """Stationary pre-trigger calibration (measured, never assumed)."""

    acc_mean_mps2: np.ndarray
    gyro_bias_dps: np.ndarray
    gravity_mps2: float
    acc_std_mps2: float
    gyro_std_dps: float

    def as_dict(self) -> dict:
        return {
            "acc_mean_mps2": self.acc_mean_mps2.tolist(),
            "gyro_bias_dps": self.gyro_bias_dps.tolist(),
            "gravity_mps2": self.gravity_mps2,
            "gravity_deviation_mps2": self.gravity_mps2 - config.STANDARD_GRAVITY_MPS2,
            "gravity_direction_sensor": (self.acc_mean_mps2 / np.linalg.norm(self.acc_mean_mps2)).tolist(),
            "acc_std_mps2": self.acc_std_mps2,
            "gyro_std_dps": self.gyro_std_dps,
        }


@dataclass(frozen=True)
class TrajectoryResult:
    session_id: str
    timestamp_ms: np.ndarray
    t_s: np.ndarray
    position_cm: np.ndarray  # (n,3), row 0 = Point A = (0,0,0)
    velocity_mps: np.ndarray
    point_a_cm: tuple[float, float, float]
    point_b_cm: tuple[float, float, float]
    estimated_point_b_cm: tuple[float, float, float]
    endpoint_error_m: float | None
    straight_line_cm: float  # 3-D |B - A|
    horizontal_distance_cm: float  # XY |B - A|
    path_length_cm: float  # sum of consecutive 3-D steps
    horizontal_path_length_cm: float
    displacement_cm: tuple[float, float, float]  # B - A
    vertical_displacement_cm: float
    peak_height_cm: float  # max Z above Point A
    duration_s: float
    mean_speed_mps: float  # path length / duration
    max_speed_mps: float
    max_acceleration_mps2: float
    efficiency: float  # straight-line / path length
    final_velocity_drift_mps: float  # raw |v(B)| before drift removal: quality indicator
    flight_time_s: float | None
    flight_height_cm: float | None
    flags: list[str]
    baseline: dict
    params: dict
    extra: dict = field(default_factory=dict)

    @property
    def closure_error_mps(self) -> float:
        """Backward-compatible alias for final_velocity_drift_mps."""
        return self.final_velocity_drift_mps


def reconstruct_trajectory(session_id: str, t_ms: np.ndarray, acc: np.ndarray, gyro: np.ndarray,
                           baseline: Baseline, fs: float, params: Params,
                           constrain_end: bool = True,
                           point_a_cm: tuple[float, float, float] | None = None,
                           point_b_cm: tuple[float, float, float] | None = None) -> TrajectoryResult:
    """Process exactly the accepted window [Point A .. Point B], once."""
    t_s = (t_ms - t_ms[0]) / 1000.0
    gaps = proc.gap_mask(t_s, fs, params)
    dt = np.diff(t_s, prepend=t_s[0] - 1.0 / fs)
    dt[gaps] = 1.0 / fs  # never integrate across a data gap
    q = proc.estimate_orientation(acc, gyro - baseline.gyro_bias_dps, dt,
                                  proc.initial_quaternion(baseline.acc_mean_mps2),
                                  baseline.gravity_mps2, params)
    linear = proc.remove_gravity(proc.rotate_to_world(q, proc.lowpass(acc, fs, params)), baseline.gravity_mps2)
    q0 = proc.initial_quaternion(baseline.acc_mean_mps2)
    baseline_linear = proc.remove_gravity(
        proc.rotate_to_world(q0[None, :], np.asarray(baseline.acc_mean_mps2, dtype=float)[None, :]),
        baseline.gravity_mps2,
    )
    gravity_comp = proc.gravity_compensation_report(baseline_linear, params)
    kin = analysis.integrate_constrained(linear, t_s, constrain_end)

    flights = analysis.detect_flights(np.linalg.norm(acc, axis=1), t_s, fs, params)
    seg = analysis.summarize_segment(0, analysis.Segment(0, len(t_s) - 1, True, True), kin, t_s, flights, gaps, params,
                                     linear)

    position, velocity = kin.position_m.copy(), kin.velocity_mps.copy()
    if params.align_x_to_b and np.hypot(*position[-1, :2]) > 1e-9:
        theta = -math.atan2(position[-1, 1], position[-1, 0])
        rot = np.array([[math.cos(theta), -math.sin(theta)], [math.sin(theta), math.cos(theta)]])
        position[:, :2], velocity[:, :2] = position[:, :2] @ rot.T, velocity[:, :2] @ rot.T
    start_cm = tuple(float(v) for v in (point_a_cm or config.DEFAULT_POINT_A_CM))
    position_cm = analysis.m_to_cm(position) + np.array(start_cm)
    estimated_b_cm = tuple(float(v) for v in position_cm[-1])
    target_b_cm = tuple(float(v) for v in point_b_cm) if point_b_cm is not None else estimated_b_cm
    endpoint_error_m = float(np.linalg.norm(position_cm[-1] - np.array(target_b_cm)) / 100.0) if point_b_cm is not None else None

    steps = np.diff(position_cm, axis=0)
    path = float(np.linalg.norm(steps, axis=1).sum())
    straight = float(np.linalg.norm(np.array(target_b_cm) - np.array(start_cm)))
    duration = float(t_s[-1])
    speed = np.linalg.norm(velocity, axis=1)
    return TrajectoryResult(
        session_id=session_id, timestamp_ms=t_ms, t_s=t_s, position_cm=position_cm, velocity_mps=velocity,
        point_a_cm=start_cm, point_b_cm=target_b_cm, estimated_point_b_cm=estimated_b_cm,
        endpoint_error_m=endpoint_error_m,
        straight_line_cm=straight, horizontal_distance_cm=float(np.linalg.norm(np.array(target_b_cm)[:2] - np.array(start_cm)[:2])),
        path_length_cm=path, horizontal_path_length_cm=float(np.linalg.norm(steps[:, :2], axis=1).sum()),
        displacement_cm=tuple(float(v) for v in position_cm[-1] - np.array(start_cm)),
        vertical_displacement_cm=float(position_cm[-1, 2] - start_cm[2]), peak_height_cm=float(position_cm[:, 2].max() - start_cm[2]),
        duration_s=duration, mean_speed_mps=path / 100.0 / duration if duration > 0 else 0.0,
        max_speed_mps=float(speed.max()), max_acceleration_mps2=float(np.linalg.norm(linear, axis=1).max()),
        efficiency=straight / path if path > 0 else float("nan"), final_velocity_drift_mps=kin.final_velocity_drift_mps,
        flight_time_s=seg.flight_time_s,
        flight_height_cm=analysis.m_to_cm(seg.flight_height_m) if seg.flight_height_m is not None else None,
        flags=seg.flags, baseline=baseline.as_dict(), params=asdict(params),
        extra={"motion": seg.motion, "n_bounces": seg.n_bounces, "n_flights": seg.n_flights,
               "motion_explanation": ("move = generic A-to-B; throw = flight or fast+hard; "
                                      "dribble = repeated impacts"),
               "gravity_compensation": {
            "status": gravity_comp.status,
            "mean_linear_acc_mps2": gravity_comp.mean_linear_acc_mps2,
            "mean_norm_mps2": gravity_comp.mean_norm_mps2,
        }},
    )


# ---------------------------------------------------------------------------- export
_ISSUED_IDS: set[str] = set()


def next_session_id(out_dir: Path, today: date | None = None) -> str:
    """MOVE_YYYYMMDD_NNN, never reused (checks issued ids and existing session folders)."""
    stamp = (today or date.today()).strftime("%Y%m%d")
    n = 1
    while True:
        sid = f"MOVE_{stamp}_{n:03d}"
        if sid not in _ISSUED_IDS and not (Path(out_dir) / sid).exists():
            _ISSUED_IDS.add(sid)
            return sid
        n += 1


def summary_dict(result: TrajectoryResult, context: dict, reference_distance_cm: float | None,
                 reference_path_cm: float | None) -> dict:
    dist_val = analysis.validate_results(result.straight_line_cm, reference_distance_cm)
    path_val = analysis.validate_results(result.path_length_cm, reference_path_cm)
    validation_status = "VALIDATED_AGAINST_REFERENCE" if (dist_val or path_val) else "NO_REFERENCE"
    return {
        "session_id": result.session_id,
        "state": "LOCKED",
        "point_a_cm": result.point_a_cm,
        "point_b_cm": result.point_b_cm,
        "estimated_point_b_cm": result.estimated_point_b_cm,
        "endpoint_error_m": result.endpoint_error_m,
        "endpoint_error_cm": result.endpoint_error_m * 100.0 if result.endpoint_error_m is not None else None,
        "straight_line_distance_cm": result.straight_line_cm,
        "distance_source": "IMU-only inertial estimate; subject to drift",
        "horizontal_distance_cm": result.horizontal_distance_cm,
        "trajectory_length_cm": result.path_length_cm,
        "horizontal_path_length_cm": result.horizontal_path_length_cm,
        "displacement_x_cm": result.displacement_cm[0],
        "displacement_y_cm": result.displacement_cm[1],
        "vertical_displacement_cm": result.vertical_displacement_cm,
        "peak_height_cm": result.peak_height_cm,
        "movement_duration_s": result.duration_s,
        "mean_speed_mps": result.mean_speed_mps,
        "max_speed_mps": result.max_speed_mps,
        "max_acceleration_mps2": result.max_acceleration_mps2,
        "efficiency": result.efficiency,
        "final_velocity_drift_mps": result.final_velocity_drift_mps,
        "closure_error_mps": result.final_velocity_drift_mps,
        "final_velocity_drift_explanation": "Final velocity drift is the raw end velocity before endpoint drift correction.",
        "flight_time_s": result.flight_time_s,
        "flight_height_cm": result.flight_height_cm,
        "motion": result.extra.get("motion", "move"),
        "n_bounces": result.extra.get("n_bounces", 0),
        "n_flights": result.extra.get("n_flights", 0),
        "motion_explanation": result.extra.get("motion_explanation", ""),
        "quality_status": analysis.quality_status(result.flags, result.final_velocity_drift_mps),
        "quality_flags": result.flags,
        "sensor_orientation": "Arbitrary / calibrated",
        "gravity_magnitude_mps2": result.baseline["gravity_mps2"],
        "gravity_direction_sensor": result.baseline["gravity_direction_sensor"],
        "gravity_compensation_status": result.extra.get("gravity_compensation", {}).get("status"),
        "mean_stationary_linear_acc_mps2": result.extra.get("gravity_compensation", {}).get("mean_linear_acc_mps2"),
        "mean_stationary_linear_acc_norm_mps2": result.extra.get("gravity_compensation", {}).get("mean_norm_mps2"),
        "reference_distance_cm": reference_distance_cm,
        "distance_error_cm": dist_val["absolute_error_cm"] if dist_val else None,
        "distance_error_percent": dist_val["percent_error"] if dist_val else None,
        "reference_path_cm": reference_path_cm,
        "path_error_cm": path_val["absolute_error_cm"] if path_val else None,
        "path_error_percent": path_val["percent_error"] if path_val else None,
        "validation_status": validation_status,
        "baseline": result.baseline,
        "parameters": result.params,
        "processing_version": config.PROCESSING_VERSION,
        **context,
    }


def export_trajectory(result: TrajectoryResult, summary: dict, out_root: Path) -> dict[str, Path]:
    out_dir = Path(out_root) / result.session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    cumulative = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(result.position_cm, axis=0), axis=1))])
    frame = pd.DataFrame({
        "sample_index": np.arange(len(result.t_s)), "timestamp_ms": result.timestamp_ms, "time_s": result.t_s,
        "x_cm": result.position_cm[:, 0], "y_cm": result.position_cm[:, 1], "z_cm": result.position_cm[:, 2],
        "cumulative_path_cm": cumulative,
        "vx_mps": result.velocity_mps[:, 0], "vy_mps": result.velocity_mps[:, 1], "vz_mps": result.velocity_mps[:, 2],
    })
    paths = {"trajectory_csv": out_dir / f"{result.session_id}_trajectory.csv",
             "summary_json": out_dir / f"{result.session_id}_trajectory_summary.json"}
    frame.to_csv(paths["trajectory_csv"], index=False)
    paths["summary_json"].write_text(json.dumps(summary, indent=2, default=str))
    return paths
