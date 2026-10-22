"""Event detection, constrained integration, distance/height, validation, export.

Internal units are SI (m, m/s, m/s^2, s); *_cm values are explicit conversions for output.
"""
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid
from scipy.io import savemat
from scipy.ndimage import minimum_filter1d

import config
import processing as proc
from config import Params


def m_to_cm(value_m: float) -> float:
    return value_m * 100.0


# ----------------------------------------------------------------------------- events
@dataclass(frozen=True)
class Segment:
    start: int  # last rest sample before motion (velocity = 0)
    end: int  # first rest sample after motion (velocity = 0)
    bounded_start: bool
    bounded_end: bool


@dataclass(frozen=True)
class Flight:
    takeoff: int
    landing: int
    duration_s: float


def detect_rest(linear_norm: np.ndarray, gyro_norm_dps: np.ndarray, fs: float, params: Params) -> np.ndarray:
    """Rest = gravity-free (world-frame) acceleration ~ 0 and low angular rate over a window.

    |a| alone cannot detect horizontal acceleration (it adds in quadrature to gravity), so the
    orientation-compensated linear acceleration is used.
    """
    still = (linear_norm < params.rest_acc_tol_mps2) & (gyro_norm_dps < params.rest_gyro_dps)
    size = max(1, round(params.rest_window_s * fs))
    return minimum_filter1d(still.astype(np.uint8), size=size, mode="nearest").astype(bool)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(start, stop_exclusive) of every True run."""
    edges = np.diff(mask.astype(np.int8), prepend=0, append=0)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def find_motion_segments(rest: np.ndarray, t_s: np.ndarray, params: Params) -> list[Segment]:
    n = len(rest)
    segments = []
    for run_start, run_stop in _runs(~rest):
        start, end = max(run_start - 1, 0), min(run_stop, n - 1)
        if t_s[end] - t_s[start] >= params.min_motion_s:
            segments.append(Segment(int(start), int(end), run_start > 0, run_stop < n))
    return segments


def detect_flights(acc_norm: np.ndarray, t_s: np.ndarray, fs: float, params: Params) -> list[Flight]:
    """Free-fall: |a| near 0 g (a flying sensor measures ~0 specific force)."""
    runs = _runs(acc_norm < params.flight_acc_max_mps2)
    merge = round(params.flight_merge_gap_s * fs)
    merged: list[list[int]] = []
    for start, stop in runs:
        if merged and start - merged[-1][1] <= merge:
            merged[-1][1] = int(stop)
        else:
            merged.append([int(start), int(stop)])
    flights = []
    n = len(t_s)
    for start, stop in merged:
        start = min(start, n - 1)
        # `stop` is exclusive for slicing; the last in-flight sample is stop-1.
        last = min(stop, n) - 1
        if last <= start:
            continue
        duration = float(t_s[last] - t_s[start])
        if duration >= params.flight_min_s:
            flights.append(Flight(start, min(stop, n), duration))
    return flights


def flight_height_m(flight_time_s: float) -> float:
    """Ballistic peak above take-off, assuming equal take-off/landing height: h = g T^2 / 8."""
    return config.STANDARD_GRAVITY_MPS2 * flight_time_s**2 / 8.0


# ------------------------------------------------------- motion-type classification
def detect_bounces(linear_norm: np.ndarray, t_s: np.ndarray, params: Params) -> list[int]:
    """Impact spikes in gravity-free acceleration (e.g. basketball dribble bounces).

    A bounce is a local maximum of |linear a| above bounce_min_linear_acc_mps2,
    separated by at least bounce_min_gap_s. Returns sample indices.
    """
    n = len(linear_norm)
    if n < 3:
        return []
    min_gap = max(1, round(params.bounce_min_gap_s / max(np.median(np.diff(t_s)), 1e-3))) if n > 1 else 1
    idx: list[int] = []
    for i in range(1, n - 1):
        if (linear_norm[i] >= params.bounce_min_linear_acc_mps2
                and linear_norm[i] >= linear_norm[i - 1]
                and linear_norm[i] > linear_norm[i + 1]
                and (not idx or i - idx[-1] >= min_gap)):
            idx.append(int(i))
    return idx


def classify_movement(peak_speed_mps: float, max_acc_mps2: float,
                      n_flights: int, n_bounces: int,
                      max_bounce_interval_s: float | None,
                      params: Params) -> str:
    """Heuristic label: 'throw' | 'dribble' | 'move'.

    * throw: free-fall flight present, or fast + hard (peak speed and acceleration).
    * dribble: repeated impacts within bounce rhythm (e.g. basketball dribbling).
    * move: any other detected motion. Callers use 'still' when no segment exists.
    Thresholds are Params so they can be tuned per sensor placement.
    """
    if n_flights > 0 or (peak_speed_mps >= params.throw_min_peak_speed_mps
                         and max_acc_mps2 >= params.throw_min_acc_mps2):
        return "throw"
    if (n_bounces >= params.dribble_min_bounces
            and (max_bounce_interval_s is None or max_bounce_interval_s <= params.dribble_max_interval_s)):
        return "dribble"
    return "move"


# --------------------------------------------------------------------- integration
@dataclass(frozen=True)
class Kinematics:
    t_s: np.ndarray
    velocity_mps: np.ndarray
    position_m: np.ndarray
    final_velocity_drift_mps: float
    raw_final_velocity_mps: np.ndarray

    @property
    def closure_error_mps(self) -> float:
        """Backward-compatible alias: raw final velocity magnitude before drift correction."""
        return self.final_velocity_drift_mps

def integrate_constrained(
    linear_acc: np.ndarray,
    t_s: np.ndarray,
    constrain_end: bool = True
) -> Kinematics:

    t = t_s - t_s[0]

    raw_velocity = cumulative_trapezoid(
        linear_acc,
        t,
        axis=0,
        initial=0.0
    )

    raw_final_velocity = raw_velocity[-1].copy()

    if constrain_end and t[-1] > 0:
        velocity = (
            raw_velocity
            - np.outer(t / t[-1], raw_final_velocity)
        )
    else:
        velocity = raw_velocity

    position = cumulative_trapezoid(
        velocity,
        t,
        axis=0,
        initial=0.0
    )

    final_velocity_drift = float(
        np.linalg.norm(raw_final_velocity)
    )

    return Kinematics(
        t,
        velocity,
        position,
        final_velocity_drift,
        raw_final_velocity
    )


@dataclass
class SegmentResult:
    index: int
    start_s: float
    end_s: float
    duration_s: float
    distance_m: float  # horizontal straight-line displacement start->end
    vertical_displacement_m: float  # z(end) - z(start)
    peak_height_m: float  # max z - z(start), constrained integration
    peak_speed_mps: float
    mean_horizontal_speed_mps: float
    final_velocity_drift_mps: float
    flight_time_s: float | None  # longest free-fall inside the segment
    flight_height_m: float | None  # ballistic estimate from flight time
    flags: list[str] = field(default_factory=list)
    motion: str = "move"  # throw | dribble | move (still = no segment)
    n_bounces: int = 0
    n_flights: int = 0

    @property
    def closure_error_mps(self) -> float:
        """Backward-compatible alias for final_velocity_drift_mps."""
        return self.final_velocity_drift_mps

    @property
    def valid(self) -> bool:
        return quality_status(self.flags) != "ERROR"


QUALITY_WARNING_FLAGS = {
    "large_closure_error",
    "data_gap",
    "no_rest_before",
    "no_rest_after",
    "height_inconsistent_with_flight",
}
QUALITY_ERROR_FLAGS = {
    "implausible_speed",
    "too_long_for_integration",
}


def quality_status(flags: list[str] | tuple[str, ...], final_velocity_drift_mps: float | None = None) -> str:
    """Classify IMU quality separately from reference validation."""
    names = set(flags)
    if names & QUALITY_ERROR_FLAGS:
        return "ERROR"
    if names & QUALITY_WARNING_FLAGS:
        return "WARNING"
    return "GOOD"


def summarize_segment(index: int, seg: Segment, kin: Kinematics, t_abs: np.ndarray,
                      flights: list[Flight], gaps: np.ndarray, params: Params,
                      linear_seg: np.ndarray | None = None) -> SegmentResult:
    duration = float(kin.t_s[-1])
    horizontal = float(np.linalg.norm(kin.position_m[-1, :2]))
    inside = [f for f in flights if seg.start <= f.takeoff <= seg.end]
    longest = max(inside, key=lambda f: f.duration_s) if inside else None
    peak_speed = float(np.linalg.norm(kin.velocity_mps, axis=1).max())
    flags = []
    if not seg.bounded_start:
        flags.append("no_rest_before")
    if not seg.bounded_end:
        flags.append("no_rest_after")
    if duration > params.max_motion_s:
        flags.append("too_long_for_integration")
    if gaps[seg.start + 1:seg.end + 1].any():
        flags.append("data_gap")
    if peak_speed > params.max_plausible_speed_mps:
        flags.append("implausible_speed")
    if kin.closure_error_mps > params.max_closure_error_mps:
        flags.append("large_closure_error")
    peak_height = float(kin.position_m[:, 2].max())
    if longest and peak_height > flight_height_m(longest.duration_s) + params.height_mismatch_tol_m:
        flags.append("height_inconsistent_with_flight")
    if linear_seg is not None and len(linear_seg) == len(kin.t_s):
        lin_norm = np.linalg.norm(np.asarray(linear_seg, dtype=float), axis=1)
        bounce_idx = detect_bounces(lin_norm, np.asarray(kin.t_s, dtype=float), params)
        max_acc = float(lin_norm.max()) if len(lin_norm) else 0.0
        t_b = np.asarray(kin.t_s, dtype=float)[bounce_idx] if bounce_idx else np.array([])
        max_interval = float(np.diff(t_b).max()) if len(t_b) >= 2 else None
    else:
        bounce_idx, max_acc, max_interval = [], 0.0, None
    motion = classify_movement(peak_speed, max_acc, len(inside), len(bounce_idx), max_interval, params)
    return SegmentResult(
        index=index,
        start_s=float(t_abs[seg.start]),
        end_s=float(t_abs[seg.end]),
        duration_s=duration,
        distance_m=horizontal,
        vertical_displacement_m=float(kin.position_m[-1, 2]),
        peak_height_m=float(kin.position_m[:, 2].max()),
        peak_speed_mps=peak_speed,
        mean_horizontal_speed_mps=horizontal / duration if duration > 0 else 0.0,
        final_velocity_drift_mps=kin.final_velocity_drift_mps,
        flight_time_s=longest.duration_s if longest else None,
        flight_height_m=flight_height_m(longest.duration_s) if longest else None,
        flags=flags,
        motion=motion,
        n_bounces=len(bounce_idx),
        n_flights=len(inside),
    )


# ------------------------------------------------------------------------- pipeline
@dataclass
class AnalysisResult:
    processed: pd.DataFrame
    calibration: proc.Calibration
    timestamps: proc.TimestampReport
    segments: list[SegmentResult]
    kinematics: dict[int, Kinematics]
    flights: list[Flight]
    sample_rate_hz: float
    params: Params
    gravity_compensation: proc.GravityCompensationReport


def run_pipeline(raw: pd.DataFrame, sample_rate_hz: float, params: Params = Params()) -> AnalysisResult:
    fs = float(sample_rate_hz)
    t_all = (raw[config.TIME_COL].to_numpy() - raw[config.TIME_COL].iloc[0]) / 1000.0
    ts_report = proc.validate_timestamps(t_all, fs, params)
    acc_all, gyro_all = raw[config.ACC_COLS].to_numpy(), raw[config.GYRO_COLS].to_numpy()
    calib = proc.calibrate(acc_all, gyro_all, fs, params)

    # analysis starts at the calibration window so orientation starts from a known still pose
    s = calib.window_start
    t = t_all[s:] - t_all[s]
    acc, gyro = acc_all[s:], gyro_all[s:] - np.array(calib.gyro_bias_dps)
    gaps = proc.gap_mask(t, fs, params)
    dt = np.diff(t, prepend=t[0] - 1.0 / fs)
    dt[gaps] = 1.0 / fs  # unknown motion during a gap: do not integrate across it

    acc_f = proc.lowpass(acc, fs, params)
    q = proc.estimate_orientation(acc, gyro, dt, proc.initial_quaternion(np.array(calib.acc_mean_mps2)),
                                  calib.gravity_mps2, params)
    linear = proc.remove_gravity(proc.rotate_to_world(q, acc_f), calib.gravity_mps2)
    stationary_n = max(1, calib.window_stop - calib.window_start)
    gravity_comp = proc.gravity_compensation_report(linear[:stationary_n], params)

    acc_norm = np.linalg.norm(acc, axis=1)
    rest = detect_rest(np.linalg.norm(linear, axis=1), np.linalg.norm(gyro, axis=1), fs, params)
    flights = detect_flights(acc_norm, t, fs, params)
    segments, results, kinematics = find_motion_segments(rest, t, params), [], {}
    segment_id = np.full(len(t), -1)
    for i, seg in enumerate(segments):
        sl = slice(seg.start, seg.end + 1)
        kin = integrate_constrained(linear[sl], t[sl])
        kinematics[i] = kin
        segment_id[sl] = i
        results.append(summarize_segment(i, seg, kin, t, flights, gaps, params, linear[sl]))

    in_flight = np.zeros(len(t), dtype=bool)
    for f in flights:
        in_flight[f.takeoff:f.landing] = True
    processed = pd.DataFrame({
        "time_s": t, "acc_norm_mps2": acc_norm,
        "lin_acc_x_mps2": linear[:, 0], "lin_acc_y_mps2": linear[:, 1], "lin_acc_z_mps2": linear[:, 2],
        "q_w": q[:, 0], "q_x": q[:, 1], "q_y": q[:, 2], "q_z": q[:, 3],
        "rest": rest, "flight": in_flight, "segment_id": segment_id,
    })
    return AnalysisResult(processed, calib, ts_report, results, kinematics, flights, fs, params, gravity_comp)


def select_segment(result: AnalysisResult, which: str = "auto") -> int | None:
    if not result.segments:
        return None
    if which != "auto":
        index = int(which)
        if not 0 <= index < len(result.segments):
            raise ValueError(f"Segment {index} out of range (0..{len(result.segments) - 1})")
        return index
    pool = [s for s in result.segments if s.valid] or result.segments
    return max(pool, key=lambda s: s.distance_m + s.peak_height_m).index


# ----------------------------------------------------------------- validation / export
def validate_results(measured_cm: float, reference_cm: float | None) -> dict[str, float] | None:
    """Single-trial comparison against an independent reference. No reference -> no claim."""
    if reference_cm is None:
        return None
    error = measured_cm - reference_cm
    return {"absolute_error_cm": error, "percent_error": 100.0 * error / reference_cm if reference_cm else float("nan")}


def build_summary(session_id: str, metadata: dict, result: AnalysisResult, index: int | None,
                  reference_distance_cm: float | None, reference_height_cm: float | None) -> dict:
    seg = result.segments[index] if index is not None else None
    distance_cm = m_to_cm(seg.distance_m) if seg else None
    peak_cm = m_to_cm(seg.peak_height_m) if seg else None
    dist_val = validate_results(distance_cm, reference_distance_cm) if seg else None
    height_val = validate_results(peak_cm, reference_height_cm) if seg else None
    validation_status = "VALIDATED_AGAINST_REFERENCE" if (dist_val or height_val) else "NO_REFERENCE"
    flags = seg.flags if seg else []
    final_velocity_drift = seg.final_velocity_drift_mps if seg else None
    motion = seg.motion if seg else "still"
    return {
        "session_id": session_id,
        "sensor": metadata.get("sensor_model") or "unknown (offline file)",
        "duration_s": result.timestamps.duration_s,
        "segment_index": index,
        "segments_found": len(result.segments),
        "motion": motion,
        "motion_explanation": ("still = no motion segment; move = generic A-to-B; "
                               "throw = flight or fast+hard; dribble = repeated impacts"),
        "n_bounces": seg.n_bounces if seg else 0,
        "n_flights": seg.n_flights if seg else 0,
        "distance_cm": distance_cm,
        "vertical_displacement_cm": m_to_cm(seg.vertical_displacement_m) if seg else None,
        "peak_height_cm": peak_cm,
        "flight_time_s": seg.flight_time_s if seg else None,
        "flight_height_cm": m_to_cm(seg.flight_height_m) if seg and seg.flight_height_m is not None else None,
        "peak_speed_mps": seg.peak_speed_mps if seg else None,
        "final_velocity_drift_mps": final_velocity_drift,
        "closure_error_mps": final_velocity_drift,
        "final_velocity_drift_explanation": "Final velocity drift is the raw end velocity before endpoint drift correction.",
        "mean_horizontal_speed_mps": seg.mean_horizontal_speed_mps if seg else None,
        "segment_duration_s": seg.duration_s if seg else None,
        "quality_status": quality_status(flags, final_velocity_drift),
        "quality_flags": flags,
        "sensor_orientation": "Arbitrary / calibrated",
        "gravity_magnitude_mps2": result.calibration.gravity_mps2,
        "gravity_direction_sensor": result.calibration.gravity_direction_sensor,
        "gravity_compensation_status": result.gravity_compensation.status,
        "mean_stationary_linear_acc_mps2": result.gravity_compensation.mean_linear_acc_mps2,
        "mean_stationary_linear_acc_norm_mps2": result.gravity_compensation.mean_norm_mps2,
        "reference_distance_cm": reference_distance_cm,
        "distance_error_cm": dist_val["absolute_error_cm"] if dist_val else None,
        "distance_error_percent": dist_val["percent_error"] if dist_val else None,
        "reference_height_cm": reference_height_cm,
        "height_error_cm": height_val["absolute_error_cm"] if height_val else None,
        "height_error_percent": height_val["percent_error"] if height_val else None,
        "validation_status": validation_status,
        "calibration": asdict(result.calibration),
        "timestamp_report": asdict(result.timestamps),
        "sample_rate_hz": result.sample_rate_hz,
        "parameters": asdict(result.params),
        "processing_version": config.PROCESSING_VERSION,
        "session_metadata": metadata,
    }


def segments_table(result: AnalysisResult) -> pd.DataFrame:
    rows = [{
        "segment": s.index, "start_s": s.start_s, "end_s": s.end_s, "duration_s": s.duration_s,
        "motion": s.motion, "n_bounces": s.n_bounces, "n_flights": s.n_flights,
        "distance_cm": m_to_cm(s.distance_m), "vertical_displacement_cm": m_to_cm(s.vertical_displacement_m),
        "peak_height_cm": m_to_cm(s.peak_height_m), "peak_speed_mps": s.peak_speed_mps,
        "mean_horizontal_speed_mps": s.mean_horizontal_speed_mps,
        "final_velocity_drift_mps": s.final_velocity_drift_mps,
        "closure_error_mps": s.closure_error_mps,
        "quality_status": quality_status(s.flags, s.final_velocity_drift_mps),
        "flight_time_s": s.flight_time_s,
        "flight_height_cm": m_to_cm(s.flight_height_m) if s.flight_height_m is not None else None,
        "valid": s.valid, "flags": ";".join(s.flags),
    } for s in result.segments]
    return pd.DataFrame(rows)


def export_results(out_dir: Path, session_id: str, result: AnalysisResult, summary: dict) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "processed_csv": out_dir / f"{session_id}_processed.csv",
        "segments_csv": out_dir / f"{session_id}_segments.csv",
        "mat": out_dir / f"{session_id}_processed.mat",
        "summary_json": out_dir / f"{session_id}_summary.json",
    }
    result.processed.to_csv(paths["processed_csv"], index=False)
    segments_table(result).to_csv(paths["segments_csv"], index=False)
    scalars = {k: v for k, v in summary.items() if isinstance(v, (int, float)) and v is not None}
    savemat(paths["mat"], {
        **{c: result.processed[c].to_numpy() for c in result.processed.columns},
        "sample_rate_hz": result.sample_rate_hz, **scalars,
    })
    paths["summary_json"].write_text(json.dumps(summary, indent=2, default=str))
    return paths
