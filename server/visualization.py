"""Static engineering plots: signal overview and summary dashboard (all lengths in cm)."""
from pathlib import Path

import matplotlib

DEFAULT_BACKEND = matplotlib.get_backend()  # remembered so --show can open a real window
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analysis import m_to_cm


def _shade(ax, color: str, label: str, t_s: np.ndarray, mask: np.ndarray) -> None:
    edges = np.diff(mask.astype(int), prepend=0, append=0)
    for k, (a, b) in enumerate(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))):
        ax.axvspan(t_s[a], t_s[min(b, len(t_s) - 1)], color=color, alpha=0.25, label=label if k == 0 else None)


def plot_signals(result: AnalysisResult, index: int | None, out_path: Path) -> Path:
    df = result.processed
    t = df["time_s"].to_numpy()
    fig, axes = plt.subplots(4, 1, figsize=(11, 10))
    ax = axes[0]
    ax.plot(t, df["acc_norm_mps2"], lw=0.7, color="#1f4e79", label="|a| raw")
    ax.axhline(result.calibration.gravity_mps2, color="gray", lw=0.8, ls="--", label="gravity (calibrated)")
    _shade(ax, "#f4a261", "motion segments", t, df["segment_id"].to_numpy() >= 0)
    _shade(ax, "#2a9d8f", "free-fall", t, df["flight"].to_numpy())
    if index is not None:
        seg = result.segments[index]
        ax.axvspan(seg.start_s, seg.end_s, facecolor="none", edgecolor="red", lw=1.5, label="selected")
    ax.set(title="Raw acceleration magnitude and detected events", xlabel="Time (s)", ylabel="Acceleration (m/s²)")
    ax.legend(loc="upper right", fontsize=8, ncol=4)
    if index is None:
        for a in axes[1:]:
            a.text(0.5, 0.5, "No motion segment detected", ha="center", transform=a.transAxes)
            a.set_axis_off()
    else:
        seg, kin = result.segments[index], result.kinematics[index]
        i0 = int(np.searchsorted(t, seg.start_s))
        i1 = i0 + len(kin.t_s)
        lin = df[["lin_acc_x_mps2", "lin_acc_y_mps2", "lin_acc_z_mps2"]].to_numpy()[i0:i1]
        axes[1].plot(kin.t_s, np.linalg.norm(lin[:, :2], axis=1), label="horizontal", color="#1f4e79")
        axes[1].plot(kin.t_s, lin[:, 2], label="vertical (Z up)", color="#c1121f")
        axes[1].set(title=f"Linear acceleration, segment {index} (gravity removed)", xlabel="Time (s)", ylabel="Acceleration (m/s²)")
        axes[2].plot(kin.t_s, np.linalg.norm(kin.velocity_mps[:, :2], axis=1), label="horizontal", color="#1f4e79")
        axes[2].plot(kin.t_s, kin.velocity_mps[:, 2], label="vertical", color="#c1121f")
        axes[2].set(title="Velocity (zero at both rest points)", xlabel="Time (s)", ylabel="Velocity (m/s)")
        axes[3].plot(kin.t_s, m_to_cm(np.linalg.norm(kin.position_m[:, :2], axis=1)), label="horizontal distance", color="#1f4e79")
        axes[3].plot(kin.t_s, m_to_cm(kin.position_m[:, 2]), label="vertical displacement", color="#c1121f")
        axes[3].set(title="Displacement relative to start", xlabel="Time (s)", ylabel="Displacement (cm)")
        for a in axes[1:]:
            a.axhline(0, color="gray", lw=0.6)
            a.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def plot_dashboard(summary: dict, result: AnalysisResult, index: int | None, out_path: Path) -> Path:
    fig = plt.figure(figsize=(11, 6))
    left = fig.add_axes([0.03, 0.05, 0.42, 0.88])
    left.set_axis_off()

    def text(y, s, **kw):
        left.text(0.02, y, s, transform=left.transAxes, **kw)

    def fmt(v, unit, digits=1):
        return "—" if v is None else f"{v:.{digits}f} {unit}"

    text(0.95, "MOVESENSE MOTION ANALYSIS", fontsize=16, fontweight="bold")
    text(0.88, f"Sensor: {summary['sensor']}", fontsize=10, color="#444")
    text(0.83, f"Session: {summary['session_id']}   Duration: {summary['duration_s']:.1f} s", fontsize=10, color="#444")
    text(0.78, f"Segment {summary['segment_index']} of {summary['segments_found']}", fontsize=10, color="#444")
    text(0.68, "Distance (horizontal, A→B)", fontsize=10, color="#666")
    text(0.58, fmt(summary["distance_cm"], "cm"), fontsize=34, fontweight="bold")
    text(0.48, "Vertical displacement", fontsize=10, color="#666")
    text(0.42, fmt(summary["vertical_displacement_cm"], "cm"), fontsize=18)
    text(0.34, "Peak height (integration) | flight height (ballistic)", fontsize=10, color="#666")
    text(0.28, f"{fmt(summary['peak_height_cm'], 'cm')}  |  {fmt(summary['flight_height_cm'], 'cm')}", fontsize=18)
    text(0.23, f"Peak speed {fmt(summary['peak_speed_mps'], 'm/s', 2)}   Mean horiz. speed {fmt(summary['mean_horizontal_speed_mps'], 'm/s', 2)}", fontsize=10)
    text(0.18, f"Orientation: {summary.get('sensor_orientation', 'Arbitrary / calibrated')}", fontsize=10)
    text(0.14, f"Gravity: {summary.get('gravity_magnitude_mps2', 0):.2f} m/s²   Compensation: {summary.get('gravity_compensation_status', 'n/a')}", fontsize=10)
    flags = ", ".join(summary["quality_flags"]) or "none"
    text(0.09, f"IMU Quality: {summary['quality_status']} ({flags})", fontsize=10,
         color="#c1121f" if summary["quality_status"] == "ERROR" else "#d98e04" if summary["quality_status"] == "WARNING" else "#2a7f3f")
    text(0.04, f"Reference Validation: {summary['validation_status'].replace('_', ' ')}", fontsize=10, fontweight="bold",
         color="#2a7f3f" if summary["validation_status"] == "VALIDATED_AGAINST_REFERENCE" else "#666")

    right = fig.add_axes([0.54, 0.12, 0.43, 0.78])
    if index is None:
        right.text(0.5, 0.5, "No motion segment detected", ha="center", transform=right.transAxes)
    else:
        kin = result.kinematics[index]
        right.plot(kin.t_s, m_to_cm(np.linalg.norm(kin.position_m[:, :2], axis=1)), color="#1f4e79", label="horizontal distance")
        right.plot(kin.t_s, m_to_cm(kin.position_m[:, 2]), color="#c1121f", label="vertical displacement")
        right.axhline(0, color="gray", lw=0.6)
        right.legend()
    right.set(title="Displacement relative to start (constrained integration)", xlabel="Time (s)", ylabel="Displacement (cm)")
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


def _plot_3d(ax, pos: np.ndarray) -> None:
    """Reference-style 3D path: ONE line, Point A/B markers, equal scaling on all axes (cm)."""
    ax.plot(pos[:, 0], pos[:, 1], pos[:, 2], color="#1f77b4", lw=3, zorder=2)
    ax.scatter(*pos[0], s=130, color="#1a8c4d", edgecolor="white", depthshade=False, label="Point A")
    ax.scatter(*pos[-1], s=130, color="#d62f2f", edgecolor="white", depthshade=False, label="Point B")
    ax.plot(*zip(pos[0], pos[-1]), color="gray", lw=0.9, ls=":")
    centre = (pos.max(axis=0) + pos.min(axis=0)) / 2
    half = 0.6 * max(np.ptp(pos, axis=0).max(), 1.0)
    ax.set_xlim(centre[0] - half, centre[0] + half)
    ax.set_ylim(centre[1] - half, centre[1] + half)
    ax.set_zlim(centre[2] - half, centre[2] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.set(xlabel="Horizontal X (cm)", ylabel="Horizontal Y (cm)", zlabel="Vertical Z (cm)")
    ax.legend(loc="upper left")


def plot_trajectory(summary: dict, result, out_path: Path | None, view: str = "xy", show: bool = False) -> Path | None:
    """Trajectory dashboard. view='xy' (default) or '3d' (reference style); show=True opens a rotatable window."""
    if show:
        plt.switch_backend(DEFAULT_BACKEND)
    xy = result.position_cm[:, :2]
    fig = plt.figure(figsize=(12, 6.5))
    if view == "3d":
        ax = fig.add_axes([0.02, 0.06, 0.6, 0.86], projection="3d")
        _plot_3d(ax, result.position_cm)
        ax.set_title("Reconstructed A to B trajectory")
    else:
        ax = fig.add_axes([0.06, 0.1, 0.56, 0.8])
        ax.plot(xy[:, 0], xy[:, 1], color="#1f4e79", lw=2.2, solid_capstyle="round", zorder=2)
        ax.scatter(*xy[0], s=150, color="#2a9d8f", zorder=3, edgecolor="white")
        ax.scatter(*xy[-1], s=150, color="#c1121f", zorder=3, edgecolor="white")
        span = max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]), 1.0)
        off = 0.04 * span
        ax.text(xy[0, 0] + off, xy[0, 1] - 1.6 * off, f"A  ({xy[0, 0]:.1f}, {xy[0, 1]:.1f})", fontsize=12, fontweight="bold", color="#2a9d8f", va="top")
        ax.text(xy[-1, 0] + off, xy[-1, 1] - 0.4 * off, f"B  ({xy[-1, 0]:.1f}, {xy[-1, 1]:.1f})", fontsize=12,
                fontweight="bold", color="#c1121f", va="top")
        for frac in (0.25, 0.5, 0.75):  # direction indicators (a few arrows, not per-sample markers)
            i = min(int(frac * (len(xy) - 1)), len(xy) - 2)
            if np.linalg.norm(xy[i + 1] - xy[i]) > 1e-9:
                ax.annotate("", xy=xy[i + 1], xytext=xy[i], arrowprops=dict(arrowstyle="-|>", color="#1f4e79", lw=1.6, mutation_scale=16))
        if summary["straight_line_distance_cm"] > 0:
            ax.plot([xy[0, 0], xy[-1, 0]], [xy[0, 1], xy[-1, 1]], color="gray", lw=0.9, ls=":", zorder=1)
        ax.margins(0.2)
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=0.35)
        ax.set(xlabel="X (cm)", ylabel="Y (cm)", title="MOVESENSE TRAJECTORY ANALYSIS: Point A → Point B")
        ax.title.set_fontweight("bold")

    panel = fig.add_axes([0.66, 0.1, 0.32, 0.8])
    panel.set_axis_off()
    s = summary
    lines = [
        ("Session", s["session_id"]),
        ("State", s["state"]),
        ("Start Point A", "({:.1f}, {:.1f}, {:.1f}) cm".format(*s["point_a_cm"])),
        ("End Point B", "({:.1f}, {:.1f}, {:.1f}) cm".format(*s["point_b_cm"])),
        ("Sensor Orientation", s.get("sensor_orientation", "Arbitrary / calibrated")),
        ("Gravity", f"{s.get('gravity_magnitude_mps2', 0):.2f} m/s²"),
        ("Gravity Compensation", s.get("gravity_compensation_status") or "n/a"),
        ("Estimated IMU endpoint", "({:.1f}, {:.1f}, {:.1f}) cm".format(*s["estimated_point_b_cm"])),
        ("Estimated straight-line A→B", f"{s['straight_line_distance_cm']:.1f} cm"),
        ("Estimated trajectory length", f"{s['trajectory_length_cm']:.1f} cm"),
        ("Displacement X", f"{s['displacement_x_cm']:.1f} cm"),
        ("Displacement Y", f"{s['displacement_y_cm']:.1f} cm"),
        ("Vertical displacement", f"{s['vertical_displacement_cm']:.1f} cm"),
        ("Peak height", f"{s['peak_height_cm']:.1f} cm"),
        ("Movement duration", f"{s['movement_duration_s']:.2f} s"),
        ("Max speed", f"{s['max_speed_mps']:.2f} m/s"),
        ("Final velocity drift", f"{s['final_velocity_drift_mps']:.2f} m/s"),
        ("Endpoint position error", "—" if s["endpoint_error_cm"] is None else f"{s['endpoint_error_cm']:.1f} cm"),
    ]
    y = 0.97
    panel.text(0, y, "SUMMARY", fontsize=13, fontweight="bold", transform=panel.transAxes)
    for label, value in lines:
        y -= 0.066
        panel.text(0, y, label, fontsize=9.5, color="#666", transform=panel.transAxes)
        panel.text(0.50, y, value, fontsize=10, fontweight="bold", transform=panel.transAxes)
    flags = ", ".join(s["quality_flags"]) or "none"
    panel.text(0, y - 0.09, f"IMU Quality: {s['quality_status']} ({flags})", fontsize=10,
               color="#c1121f" if s["quality_status"] == "ERROR" else "#d98e04" if s["quality_status"] == "WARNING" else "#2a7f3f",
               transform=panel.transAxes)
    panel.text(0, y - 0.16, f"Reference Validation: {s['validation_status'].replace('_', ' ')}", fontsize=10, fontweight="bold",
               color="#2a7f3f" if s["validation_status"] == "VALIDATED_AGAINST_REFERENCE" else "#666", transform=panel.transAxes)
    if s["distance_error_cm"] is not None:
        panel.text(0, y - 0.23, f"A→B error vs reference: {s['distance_error_cm']:+.1f} cm ({s['distance_error_percent']:+.1f} %)", fontsize=9.5, transform=panel.transAxes)
    if out_path is not None:
        fig.savefig(out_path, dpi=130)
    if show:
        plt.show()
    plt.close(fig)
    return out_path


def plot_xyz_time(result, out_path: Path, flight_note: str = "") -> Path:
    """X, Y, Z position (cm) versus time, relative to Point A: shows which axis carries the motion/drift."""
    fig, axes = plt.subplots(3, 1, figsize=(9, 7), sharex=True)
    for ax, k, name, colour in zip(axes, range(3), ("X", "Y", "Z"), ("#e67e22", "#8e44ad", "#2c3e50")):
        ax.plot(result.t_s, result.position_cm[:, k], color=colour, lw=2)
        ax.axhline(0, color="gray", lw=0.6)
        ax.plot(result.t_s[0], result.position_cm[0, k], "o", color="#1a8c4d", ms=9, label="Point A")
        ax.plot(result.t_s[-1], result.position_cm[-1, k], "o", color="#d62f2f", ms=9, label="Point B")
        ax.set_ylabel(f"{name} position (cm)")
        ax.grid(True, alpha=0.35)
    axes[0].legend(loc="upper left")
    axes[0].set_title(f"{result.session_id}: X, Y, Z position relative to Point A {flight_note}")
    axes[-1].set_xlabel("Time since Point A (s)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path
