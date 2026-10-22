"""Movesense HR+/HR2 CLI: scan | verify | record | analyze | trajectory | track | compare."""
import sys

if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required (found {sys.version.split()[0]}). Create the venv with python3.10 or newer.")

import argparse
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from bleak.exc import BleakError

import acquisition
import analysis
import config
import processing
import session as session_mod
import trajectory
import visualization


def _session_id(prefix: str = "S") -> str:
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}"


def _target_points_from_args(args) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None]:
    start = (args.start_x_cm, args.start_y_cm, args.start_z_cm)
    end = (args.end_x_cm, args.end_y_cm, args.end_z_cm)
    point_a = tuple(float(v) for v in start) if any(v is not None for v in start) else None
    point_b = tuple(float(v) for v in end) if any(v is not None for v in end) else None
    if point_a is not None and any(v is None for v in start):
        raise ValueError("start point requires all of --start-x-cm, --start-y-cm and --start-z-cm")
    if point_b is not None and any(v is None for v in end):
        raise ValueError("end point requires all of --end-x-cm, --end-y-cm and --end-z-cm")
    return point_a, point_b


async def cmd_scan(args) -> int:
    devices = await acquisition.discover_devices(args.scan_seconds, "" if args.all else config.NAME_FILTER)
    if not devices:
        what = "any BLE device" if args.all else f"a device matching '{config.NAME_FILTER}'"
        logging.error("No %s found. Wake the sensor, close other apps using it, or run 'scan --all' "
                      "to list every BLE device. You can still connect directly with its MAC: python app.py", what)
        return 1
    for d in devices:
        print(f"{d.name or '<no name>':<24} {d.address}  RSSI={d.rssi}  GSP_advertised={d.advertises_gsp}")
    return 0


async def cmd_verify(args) -> int:
    report = await acquisition.verify_device(args.address)
    print(f"Connected           : {report.connected}")
    for field, value in report.identity.items():
        print(f"{field:<20}: {value if value is not None else 'not exposed over BLE'}")
    print(f"GSP service present : {report.has_gsp_service}")
    print(f"GSP HELLO           : {report.gsp_hello if report.gsp_hello else 'no/unparsable response'}")
    print(f"Heart-rate service  : {report.has_heart_rate_service}")
    return 0


async def cmd_record(args) -> int:
    session = args.session or _session_id()
    print(f"Hold the sensor still for ~2 s, then perform the movement, then rest. Recording {args.seconds:.0f} s (Ctrl+C stops early).")
    try:
        path = await acquisition.record_session(
            args.address, args.rate, args.seconds, session, args.placement, args.experiment)
    except asyncio.CancelledError:
        print("Stopped by user; partial data was saved.")
        return 0
    if path is None:
        print("No samples were recorded (see the error above).")
        return 1
    if args.analyze:
        args.input, args.sample_rate = path, args.rate
        return cmd_analyze(args)
    print(f"Next: python main.py analyze --input {path}")
    return 0


def cmd_analyze(args) -> int:
    raw, metadata = acquisition.load_capture(Path(args.input), args.sample_rate)
    rate = metadata["sample_rate_hz"]
    session = metadata.get("session_id") or args.session or Path(args.input).stem
    print(f"Processing samples... ({len(raw)} @ {rate} Hz)")
    try:
        result = analysis.run_pipeline(raw, rate)
    except processing.CalibrationError as error:
        print(f"Calibration failed: {error}")
        return 2
    c = result.calibration
    print(f"Calibration complete. gravity={c.gravity_mps2:.3f} m/s² (dev {c.gravity_deviation_mps2:+.3f}), "
          f"gyro bias={tuple(round(v, 2) for v in c.gyro_bias_dps)} deg/s")
    ts = result.timestamps
    if ts.n_gaps:
        print(f"WARNING: {ts.n_gaps} timestamp gaps (~{ts.missing_samples_est} missing samples).")
    index = analysis.select_segment(result, args.segment)
    summary = analysis.build_summary(session, metadata, result, index,
                                     args.reference_distance_cm, args.reference_height_cm)
    out_dir = config.PROCESSED_DIR / session
    paths = analysis.export_results(out_dir, session, result, summary)
    paths["signals_png"] = visualization.plot_signals(result, index, out_dir / f"{session}_signals.png")
    paths["dashboard_png"] = visualization.plot_dashboard(summary, result, index, out_dir / f"{session}_dashboard.png")
    n_not_error = sum(s.valid for s in result.segments)
    print(f"Segments: {len(result.segments)} found, {n_not_error} without ERROR quality.")
    if index is None:
        print("No motion segment detected.")
    else:
        print(f"Segment {index}: Distance = {summary['distance_cm']:.1f} cm | "
              f"Vertical displacement = {summary['vertical_displacement_cm']:.1f} cm | "
              f"Peak height = {summary['peak_height_cm']:.1f} cm")
        if summary["flight_time_s"]:
            print(f"Flight: {summary['flight_time_s']:.2f} s -> ballistic height {summary['flight_height_cm']:.1f} cm")
        print(f"IMU Quality: {summary['quality_status']} | Flags: {', '.join(summary['quality_flags']) or 'none'} | "
              f"Reference Validation: {summary['validation_status'].replace('_', ' ')}")
        if summary["distance_error_cm"] is not None:
            print(f"Distance error vs reference: {summary['distance_error_cm']:+.1f} cm ({summary['distance_error_percent']:+.1f} %)")
        if summary["height_error_cm"] is not None:
            print(f"Height error vs reference: {summary['height_error_cm']:+.1f} cm ({summary['height_error_percent']:+.1f} %)")
    print(f"Outputs in {out_dir}")
    return 0


# ------------------------------------------------------------ one-shot A->B trajectory
def _finish_trajectory(ctrl, context: dict, args) -> int:
    results = list(ctrl.completed) if getattr(args, "continuous", False) else ([ctrl.result] if ctrl.result else [])
    if not results:
        result = ctrl.result
        if result is None:
            print(f"No complete A->B session (final state: {ctrl.state.value}, motion={ctrl.live_motion()}).")
            for t_ms, message in ctrl.events[-4:]:
                print(f"  - {message}")
            print("Hold the sensor still ~2 s, make ONE movement, then hold still ~1 s.")
            return 3
        results = [result]
    context.update(
        events=[{"t_ms": t, "message": m} for t, m in ctrl.events],
        duplicates_rejected=ctrl.samples.duplicates, out_of_order_rejected=ctrl.samples.out_of_order,
        movements_rejected_as_too_short=ctrl.rejected_movements,
    )
    code = 0
    for result in results:
        summary = trajectory.summary_dict(result, context, args.reference_distance_cm, args.reference_path_cm)
        paths = trajectory.export_trajectory(result, summary, config.PROCESSED_DIR)
        out_dir = paths["summary_json"].parent
        paths["png"] = visualization.plot_trajectory(summary, result, out_dir / f"{result.session_id}_trajectory.png")
        paths["png_3d"] = visualization.plot_trajectory(summary, result, out_dir / f"{result.session_id}_trajectory_3d.png", view="3d")
        paths["xyz_png"] = visualization.plot_xyz_time(result, out_dir / f"{result.session_id}_xyz_time.png")
        if args.show and result is results[-1]:
            visualization.plot_trajectory(summary, result, None, view="3d", show=True)
        print(f"Session {result.session_id} COMPLETE and LOCKED. Motion: {summary.get('motion', 'move')}.")
        print(f"Estimated A->B straight-line = {summary['straight_line_distance_cm']:.1f} cm | "
              f"Trajectory length = {summary['trajectory_length_cm']:.1f} cm | "
              f"dX = {summary['displacement_x_cm']:.1f} cm, dY = {summary['displacement_y_cm']:.1f} cm")
        print(f"Vertical displacement = {summary['vertical_displacement_cm']:.1f} cm | "
              f"Peak height = {summary['peak_height_cm']:.1f} cm | Duration = {summary['movement_duration_s']:.2f} s")
        print(f"Motion = {summary.get('motion', 'move')} (bounces={summary.get('n_bounces', 0)}) | "
              f"Final velocity drift = {summary['final_velocity_drift_mps']:.2f} m/s | IMU Quality: {summary['quality_status']} | "
              f"Flags: {', '.join(summary['quality_flags']) or 'none'}")
        if summary["endpoint_error_m"] is not None:
            print(f"Endpoint position error = {summary['endpoint_error_cm']:.1f} cm")
        print(f"Reference Validation: {summary['validation_status'].replace('_', ' ')}")
        if summary["distance_error_cm"] is not None:
            print(f"A->B error vs reference: {summary['distance_error_cm']:+.1f} cm ({summary['distance_error_percent']:+.1f} %)")
        if summary["path_error_cm"] is not None:
            print(f"Path error vs reference: {summary['path_error_cm']:+.1f} cm ({summary['path_error_percent']:+.1f} %)")
        print(f"Outputs in {paths['png'].parent}")
    return code


def cmd_trajectory(args) -> int:
    """Replay a recording through automatic still -> movement -> still detection."""
    path = Path(args.input)
    raw, metadata = acquisition.load_capture(path, args.sample_rate)
    rate = metadata["sample_rate_hz"]
    if path.suffix == ".bin" and not (config.RAW_DIR / f"{path.stem}.csv").exists():
        acquisition.save_raw_csv(raw, metadata, config.RAW_DIR / f"{path.stem}.csv")
    t_ms = raw[config.TIME_COL].to_numpy()
    acc, gyro = raw[config.ACC_COLS].to_numpy(), raw[config.GYRO_COLS].to_numpy()
    params = config.Params(align_x_to_b=args.align_x_to_b)
    point_a_cm, point_b_cm = _target_points_from_args(args)
    ctrl = session_mod.SessionController(rate, params, point_a_cm=point_a_cm, point_b_cm=point_b_cm,
                                         continuous=args.continuous)
    start_at_ms = t_ms[0] + args.start_at_s * 1000.0
    print(f"Replaying {len(t_ms)} samples @ {rate} Hz; automatic detection starts at t={args.start_at_s:.1f} s."
          + (" Continuous: every still->move->still makes an A->B." if args.continuous else ""))
    for ts, a, g in zip(t_ms, acc, gyro):
        if ts < start_at_ms:
            continue
        if ctrl.state is session_mod.SessionState.IDLE and not ctrl.completed:
            ctrl.arm()
        if ctrl.feed(ts, a, g) is session_mod.SessionState.LOCKED and not args.continuous:
            break
    context = {"input": str(path), "sample_rate_hz": rate, "start_at_s": args.start_at_s, "mode": "replay",
               "continuous": args.continuous}
    return _finish_trajectory(ctrl, context, args)


async def cmd_track(args) -> int:
    """Live tracking: automatic still -> movement -> still; --continuous keeps sensing after each Point B."""
    params = config.Params(align_x_to_b=args.align_x_to_b)
    point_a_cm, point_b_cm = _target_points_from_args(args)
    ctrl = session_mod.SessionController(args.rate, params, point_a_cm=point_a_cm, point_b_cm=point_b_cm,
                                         continuous=args.continuous)
    stop = asyncio.Event()
    shown = {"state": None, "t0": None, "motion": None}
    ctrl.arm()
    print("Automatic detection active: keep still for calibration, move from Point A, then hold still at Point B."
          + (" Continuous: every movement is saved." if args.continuous else ""))

    def sink(ts, acc, gyro) -> None:
        shown["t0"] = shown["t0"] if shown["t0"] is not None else ts
        state = ctrl.feed(ts, acc, gyro)
        motion = ctrl.live_motion()
        if state is not shown["state"] or motion != shown["motion"]:
            shown["state"], shown["motion"] = state, motion
            n = len(ctrl.completed)
            print(f"[{(ts - shown['t0']) / 1000:7.2f} s] {state.value} motion={motion} trajectories={n}")
        if state is session_mod.SessionState.LOCKED and not args.continuous:
            stop.set()

    session = args.session or _session_id("TRK")
    csv_path = await acquisition.record_session(
        args.address, args.rate, args.seconds, session, args.placement, args.experiment,
        sample_sink=sink, stop_event=stop)
    context = {"input": str(csv_path) if csv_path else None, "sample_rate_hz": args.rate, "mode": "live"}
    return _finish_trajectory(ctrl, context, args)


def cmd_compare(args) -> int:
    """Python vs MATLAB for one session folder (MATLAB output: <id>_matlab_trajectory.csv/.json)."""
    folder = Path(args.session_dir)
    sid = folder.name
    py = pd.read_csv(folder / f"{sid}_trajectory.csv")
    ml = pd.read_csv(folder / f"{sid}_matlab_trajectory.csv")
    py_s = json.loads((folder / f"{sid}_trajectory_summary.json").read_text())
    ml_s = json.loads((folder / f"{sid}_matlab_summary.json").read_text())
    rows = [(f"Final {axis.upper()} (cm)", py[f"{axis}_cm"].iloc[-1], ml[f"{axis}_cm"].iloc[-1]) for axis in "xyz"]
    for label, key in [("Trajectory length (cm)", "trajectory_length_cm"), ("A-to-B distance (cm)", "straight_line_distance_cm"),
                       ("Displacement X (cm)", "displacement_x_cm"), ("Displacement Y (cm)", "displacement_y_cm"),
                       ("Vertical displacement (cm)", "vertical_displacement_cm"), ("Peak height (cm)", "peak_height_cm"),
                       ("Duration (s)", "movement_duration_s")]:
        rows.append((label, py_s[key], ml_s[key]))
    print(f"{'Parameter':<28}{'Python':>12}{'MATLAB':>12}{'Abs error':>12}{'Rel error':>11}")
    for label, a, b in rows:
        rel = f"{100 * abs(a - b) / abs(a):.3f} %" if a else "n/a"
        print(f"{label:<28}{a:>12.3f}{b:>12.3f}{abs(a - b):>12.4f}{rel:>11}")
    if len(py) == len(ml):
        diff = np.abs(py[["x_cm", "y_cm", "z_cm"]].to_numpy() - ml[["x_cm", "y_cm", "z_cm"]].to_numpy())
        print(f"Samples: {len(py)} (equal). Max |position difference| = {diff.max():.4f} cm")
    else:
        print(f"WARNING: sample counts differ (Python {len(py)}, MATLAB {len(ml)}): trigger windows disagree; investigate.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan", help="list nearby Movesense devices")
    s.add_argument("--scan-seconds", type=float, default=config.SCAN_TIMEOUT_S)
    s.add_argument("--all", action="store_true", help="list every BLE device, not only names containing 'movesense'")
    v = sub.add_parser("verify", help="connect and report identity/services")
    v.add_argument("--address", required=True)
    r = sub.add_parser("record", help="record raw IMU9 data")
    r.add_argument("--address", required=True)
    r.add_argument("--seconds", type=float, default=30.0)
    r.add_argument("--rate", type=int, default=52, choices=config.IMU9_SAMPLE_RATES_HZ)
    r.add_argument("--placement", default="unspecified")
    r.add_argument("--experiment", default="unspecified")
    r.add_argument("--session")
    r.add_argument("--analyze", action="store_true")
    for sp in (r,):
        sp.add_argument("--segment", default="auto")
        sp.add_argument("--reference-distance-cm", type=float)
        sp.add_argument("--reference-height-cm", type=float)
    a = sub.add_parser("analyze", help="process a raw .csv or .bin capture")
    a.add_argument("--input", required=True)
    a.add_argument("--sample-rate", type=int)
    a.add_argument("--session")
    a.add_argument("--segment", default="auto", help="'auto' or a segment index")
    a.add_argument("--reference-distance-cm", type=float)
    a.add_argument("--reference-height-cm", type=float)
    t = sub.add_parser("trajectory", help="replay a recording through the one-shot A->B state machine")
    t.add_argument("--input", required=True)
    t.add_argument("--sample-rate", type=int)
    t.add_argument("--start-at-s", type=float, default=0.0, help="begin automatic detection this many seconds into the recording")
    t.add_argument("--align-x-to-b", action="store_true")
    t.add_argument("--start-x-cm", "--start-x", dest="start_x_cm", type=float)
    t.add_argument("--start-y-cm", "--start-y", dest="start_y_cm", type=float)
    t.add_argument("--start-z-cm", "--start-z", dest="start_z_cm", type=float)
    t.add_argument("--end-x-cm", "--end-x", dest="end_x_cm", type=float)
    t.add_argument("--end-y-cm", "--end-y", dest="end_y_cm", type=float)
    t.add_argument("--end-z-cm", "--end-z", dest="end_z_cm", type=float)
    t.add_argument("--show", action="store_true", help="open a rotatable 3D window (needs a GUI backend)")
    t.add_argument("--reference-distance-cm", type=float, help="measured straight A-to-B distance")
    t.add_argument("--reference-path-cm", type=float, help="measured path length")
    t.add_argument("--continuous", action="store_true", help="every still->move->still makes an A->B (dribble/multiple throws)")
    k = sub.add_parser("track", help="live one-shot A->B tracking over BLE")
    k.add_argument("--address", required=True)
    k.add_argument("--seconds", type=float, default=120.0, help="maximum session time")
    k.add_argument("--rate", type=int, default=52, choices=config.IMU9_SAMPLE_RATES_HZ)
    k.add_argument("--placement", default="unspecified")
    k.add_argument("--experiment", default="unspecified")
    k.add_argument("--session")
    k.add_argument("--align-x-to-b", action="store_true")
    k.add_argument("--start-x-cm", "--start-x", dest="start_x_cm", type=float)
    k.add_argument("--start-y-cm", "--start-y", dest="start_y_cm", type=float)
    k.add_argument("--start-z-cm", "--start-z", dest="start_z_cm", type=float)
    k.add_argument("--end-x-cm", "--end-x", dest="end_x_cm", type=float)
    k.add_argument("--end-y-cm", "--end-y", dest="end_y_cm", type=float)
    k.add_argument("--end-z-cm", "--end-z", dest="end_z_cm", type=float)
    k.add_argument("--show", action="store_true", help="open a rotatable 3D window at the end")
    k.add_argument("--reference-distance-cm", type=float)
    k.add_argument("--reference-path-cm", type=float)
    k.add_argument("--continuous", action="store_true", help="keep sensing after each Point B (dribble/multiple throws)")
    c = sub.add_parser("compare", help="Python vs MATLAB results for a session folder")
    c.add_argument("--session-dir", required=True)
    return p


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    handlers = {"scan": cmd_scan, "verify": cmd_verify, "record": cmd_record, "track": cmd_track}
    try:
        sync = {"analyze": cmd_analyze, "trajectory": cmd_trajectory, "compare": cmd_compare}
        if args.command in sync:
            return sync[args.command](args)
        return asyncio.run(handlers[args.command](args))
    except KeyboardInterrupt:
        print("Stopped by user.")
        return 130
    except (BleakError, OSError) as error:
        if args.command in ("scan", "verify", "record", "track"):
            print(f"Error: {acquisition.describe_ble_error(error, getattr(args, 'address', None))[0]}")
        else:
            print(f"Error: {error}")
        return 1
    except ValueError as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
