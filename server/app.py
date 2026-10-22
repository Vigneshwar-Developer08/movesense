"""Minimal live viewer (debug/replay): device connection, ping (ms) and the 3D Point A -> Point B trajectory (cm).

    Preferred flow (uses the Next.js frontend for scan/connect/disconnect):
      cd server && uvicorn api:app --host 0.0.0.0 --port 8000
      cd client && npm run dev            # Devices page -> Scan -> Connect, Live Tracking -> Start
      POST /api/tracking/start {"address": "<ADDR>"}   # same detector as below, streamed to the web UI

    Direct viewer (no frontend):
      python app.py --address <ADDR>                         # live sensor, address required (no prompt)
      python app.py --replay data/raw/<file>.csv --start-at-s 0 # recorded data (clearly labelled REPLAY)

Ping = round trip of the documented GSP HELLO command (write -> response notification).
Trajectory: automatic still -> move -> still session. Movement onset is Point A, the next still window is
Point B. While RECORDING the path is PROVISIONAL (end velocity unknown, drifts); it is replaced by the
final, drift-constrained path at Point B. IMU-only distances are estimates and can drift.
"""
import sys

if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required (found {sys.version.split()[0]}). Create the venv with python3.10 or newer.")

import argparse
import asyncio
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import matplotlib
import numpy as np

import acquisition
import analysis
import config
import session as session_mod
import trajectory

S = session_mod.SessionState
PING_STALE_S = 6.0
log = logging.getLogger(__name__)
HINTS = {
    S.IDLE: "Waiting for sensor data.",
    S.ARMED: "Hold the sensor still; movement starts Point A.",
    S.RECORDING: "Moving... hold still at Point B to finish.",
    S.LOCKED: "Locked. Press NEW for another run.",
}


class Feed:
    """Thread-safe bridge: BLE/replay thread -> GUI thread."""

    def __init__(self, mode: str) -> None:
        self.mode = mode  # "live" | "replay"
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.connected = False
        self.status = "Starting..."
        self.device = ""
        self.ping_ms: float | None = None
        self.ping_at = 0.0
        self.finished = False
        self.closed = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop: asyncio.Event | None = None

    def sink(self, ts: float, acc, gyro) -> None:
        self.q.put((float(ts), np.array(acc), np.array(gyro)))

    def event(self, kind: str, value) -> None:
        with self.lock:
            if kind == "status":
                self.status = str(value)
            elif kind == "connected":
                self.connected = bool(value)
            elif kind == "device":
                self.device = str(value)
            elif kind == "ping":
                self.ping_ms, self.ping_at = value, time.monotonic()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(connected=self.connected, status=self.status, device=self.device,
                        ping_ms=self.ping_ms, ping_age=time.monotonic() - self.ping_at)

    def request_stop(self) -> None:
        self.closed = True
        if self.loop is not None and self.stop is not None:
            self.loop.call_soon_threadsafe(self.stop.set)


def start_live(feed: Feed, address: str, rate: int, seconds: float, ping_interval_s: float) -> None:
    session_id = f"LIVE_{datetime.now():%Y%m%d_%H%M%S}"

    def run() -> None:
        async def main() -> None:
            feed.loop, feed.stop = asyncio.get_running_loop(), asyncio.Event()
            try:
                await acquisition.record_session(
                    address, rate, seconds, session_id, "unspecified", "live viewer",
                    sample_sink=feed.sink, stop_event=feed.stop, event_sink=feed.event,
                    ping_interval_s=ping_interval_s)
            except Exception as error:  # shown in the window, never silent
                feed.event("connected", False)
                feed.event("status", f"Error: {error}")
            feed.finished = True

        asyncio.run(main())

    threading.Thread(target=run, daemon=True).start()


def start_replay(feed: Feed, path: Path, sample_rate: int | None, speed: float) -> float:
    frame, meta = acquisition.load_capture(path, sample_rate)
    t = frame[config.TIME_COL].to_numpy()
    acc, gyro = frame[config.ACC_COLS].to_numpy(), frame[config.GYRO_COLS].to_numpy()
    feed.event("device", f"replay: {path.name}")
    feed.event("status", "REPLAY of recorded data (no sensor connected)")

    def run() -> None:
        start, i = time.monotonic(), 0
        while i < len(t) and not feed.closed:
            due = t[0] + (time.monotonic() - start) * 1000.0 * speed
            j = int(np.searchsorted(t, due, side="right"))
            for k in range(i, j):
                feed.q.put((float(t[k]), acc[k], gyro[k]))
            i = j
            time.sleep(0.01)
        feed.finished = True

    threading.Thread(target=run, daemon=True).start()
    return float(meta["sample_rate_hz"])


class LiveView:
    def __init__(self, plt, feed: Feed, ctrl: session_mod.SessionController, replay_start_ms: float | None = None) -> None:
        from matplotlib.widgets import Button

        self.feed, self.ctrl, self.replay_start_ms = feed, ctrl, replay_start_ms
        if ctrl.state is S.IDLE:
            ctrl.arm()
        self.exported, self.saved_dir = False, None
        self.exported_ids: set[str] = set()
        self.half, self._prov, self._prov_at = 50.0, None, 0.0
        fig = self.fig = plt.figure(figsize=(11.5, 6.8))
        fig.canvas.manager.set_window_title("Movesense trajectory") if fig.canvas.manager else None
        self.ax = fig.add_axes([0.02, 0.04, 0.62, 0.84], projection="3d")
        self.t_conn = fig.text(0.025, 0.945, "", fontsize=14, fontweight="bold", va="center")
        self.t_ping = fig.text(0.50, 0.945, "", fontsize=14, fontweight="bold", va="center")
        fig.text(0.68, 0.86, "State", fontsize=10, color="#666")
        self.t_state = fig.text(0.68, 0.80, "", fontsize=20, fontweight="bold")
        self.t_hint = fig.text(0.68, 0.75, "", fontsize=10, color="#555", wrap=True)
        fig.text(0.68, 0.64, "Estimated A to B (straight line)", fontsize=10, color="#666")
        self.t_dist = fig.text(0.68, 0.55, "", fontsize=34, fontweight="bold")
        fig.text(0.68, 0.46, "Point B  (X, Y, Z)", fontsize=10, color="#666")
        self.t_b = fig.text(0.68, 0.41, "", fontsize=15)
        self.t_warn = fig.text(0.68, 0.33, "", fontsize=10, color="#c1121f", wrap=True)
        self.t_foot = fig.text(0.02, 0.012, "", fontsize=8, color="#666")
        self.btn_reset = Button(fig.add_axes([0.75, 0.10, 0.20, 0.07]), "NEW")
        self.btn_reset.on_clicked(lambda _e: self.reset())
        fig.canvas.mpl_connect("key_press_event", lambda e: self.reset() if e.key in ("n", "r") else None)
        fig.canvas.mpl_connect("close_event", lambda _e: feed.request_stop())

        ax = self.ax
        (self.path,) = ax.plot([np.nan], [np.nan], [np.nan], color="#1f77b4", lw=3, zorder=2)  # ONE line object, updated in place
        (self.pa,) = ax.plot([np.nan], [np.nan], [np.nan], "o", ms=11, color="#1a8c4d", mec="white", label="Point A")
        (self.pb,) = ax.plot([np.nan], [np.nan], [np.nan], "o", ms=11, color="#d62f2f", mec="white", label="Point B")
        ax.set(xlabel="X (cm)", ylabel="Y (cm)", zlabel="Z (cm)")
        ax.set_box_aspect((1, 1, 1))
        ax.legend(loc="upper left")
        ax.set_title("Point A to Point B trajectory (cm)")
        self._limits(np.zeros(3), 50.0)

    # ------------------------------------------------------------------ helpers
    def reset(self) -> None:
        if self.ctrl.reset(discard=True):
            self.exported, self._prov = False, None
            self.saved_dir = None
            self.ctrl.arm()

    def _limits(self, centre: np.ndarray, half: float) -> None:
        """Cube limits => identical scale on X, Y, Z (no geometric distortion)."""
        self.ax.set_xlim(centre[0] - half, centre[0] + half)
        self.ax.set_ylim(centre[1] - half, centre[1] + half)
        self.ax.set_zlim(centre[2] - half, centre[2] + half)

    def _drain(self) -> None:
        for _ in range(4000):
            try:
                ts, acc, gyro = self.feed.q.get_nowait()
            except queue.Empty:
                return
            if self.replay_start_ms is not None and ts < self.replay_start_ms:
                continue
            self.ctrl.feed(ts, acc, gyro)

    def _current_path(self) -> tuple[np.ndarray | None, str]:
        st = self.ctrl.state
        if self.ctrl.result is not None and (st is S.LOCKED or (st is S.ARMED and self.ctrl.continuous)):
            return self.ctrl.result.position_cm, "final"
        if st is S.RECORDING:
            if time.monotonic() - self._prov_at > 0.25:
                prov = self.ctrl.provisional_trajectory()
                self._prov = prov.position_cm if prov is not None else self._prov
                self._prov_at = time.monotonic()
            return self._prov, "provisional"
        return None, ""

    def _export_once(self) -> None:
        for result in self.ctrl.completed:
            if result.session_id in self.exported_ids:
                continue
            try:
                context = {"mode": self.feed.mode, "sample_rate_hz": self.ctrl.fs, "viewer": "app.py",
                           "events": [{"t_ms": t, "message": m} for t, m in self.ctrl.events]}
                summary = trajectory.summary_dict(result, context, None, None)
                paths = trajectory.export_trajectory(result, summary, config.PROCESSED_DIR)
                self.exported_ids.add(result.session_id)
                self.exported, self.saved_dir = True, paths["summary_json"].parent
            except OSError as exc:
                log.warning("could not export %s: %s", result.session_id, exc)

    # ------------------------------------------------------------------- update
    def update(self, _frame=None):
        self._drain()
        snap, st = self.feed.snapshot(), self.ctrl.state
        # connection + ping
        if self.feed.mode == "replay":
            self.t_conn.set(text="REPLAY (no sensor): recorded data", color="#1f77b4")
            self.t_ping.set(text="Ping  n/a", color="#888")
        else:
            busy = "onnecting" in snap["status"]
            if snap["connected"]:
                self.t_conn.set(text=f"●  Connected   {snap['device']}", color="#1a8c4d")
            else:
                self.t_conn.set(text=f"●  {snap['status'] if busy or snap['status'] else 'Disconnected'}",
                                color="#d98e04" if busy else "#c1121f")
            ping = snap["ping_ms"]
            if ping is None or snap["ping_age"] > PING_STALE_S or not snap["connected"]:
                self.t_ping.set(text="Ping  — ms", color="#888")
            else:
                self.t_ping.set(text=f"Ping  {ping:.0f} ms", color="#1a8c4d" if ping < 50 else "#d98e04" if ping < 150 else "#c1121f")
        # trajectory
        pos, kind = self._current_path()
        if pos is not None and len(pos) > 1:
            self.path.set_data_3d(pos[:, 0], pos[:, 1], pos[:, 2])
            self.pa.set_data_3d(pos[:1, 0], pos[:1, 1], pos[:1, 2])
            self.pb.set_data_3d(pos[-1:, 0], pos[-1:, 1], pos[-1:, 2])
            centre = (pos.max(axis=0) + pos.min(axis=0)) / 2
            self.half = max(self.half, 0.6 * float(np.ptp(pos, axis=0).max()))  # grows only: no jumping view
            self._limits(centre, self.half)
            dist = float(np.linalg.norm(pos[-1] - pos[0]))
            self.t_dist.set(text=f"{dist:.1f} cm" + ("" if kind == "final" else "*"))
            self.t_b.set(text="({:.0f}, {:.0f}, {:.0f}) cm".format(*pos[-1]) if kind != "final"
                         else "({:.1f}, {:.1f}, {:.1f}) cm".format(*pos[-1]))
        elif st in (S.IDLE, S.ARMED):
            for artist in (self.path, self.pa, self.pb):
                artist.set_data_3d(np.array([np.nan]), np.array([np.nan]), np.array([np.nan]))
            self.t_dist.set(text="— cm")
            self.t_b.set(text="—")
        if st is S.LOCKED or (st is S.ARMED and self.ctrl.completed):
            self._export_once()
        result = self.ctrl.result if (st is S.LOCKED or (st is S.ARMED and self.ctrl.completed)) else None
        motion_live = self.ctrl.live_motion()
        flags = result.flags if result is not None else []
        if result is not None:
            status = analysis.quality_status(flags, result.final_velocity_drift_mps)
            reason = ", ".join(flags) or "none"
            endpoint = (f"\nEndpoint Position Error: {result.endpoint_error_m:.2f} m"
                        if result.endpoint_error_m is not None else "")
            efficiency = f"{result.efficiency:.2f}" if result.efficiency == result.efficiency else "n/a"
            flight = (f"\nFlight: {result.flight_time_s:.2f} s -> {result.flight_height_cm:.1f} cm"
                      if result.flight_time_s is not None and result.flight_height_cm is not None else "")
            motion = result.extra.get("motion", "move")
            self.t_warn.set(text=(
                f"Trajectory: LIVE (IMU estimate; see Quality)\nMotion: {motion} (live: {motion_live}) | "
                f"Trajectories: {len(self.ctrl.completed)}\nSensor Orientation: Arbitrary / calibrated\n"
                f"Gravity: {result.baseline['gravity_mps2']:.2f} m/s²\n"
                f"Gravity Compensation: {result.extra.get('gravity_compensation', {}).get('status', 'n/a')}\n"
                f"--- Report ---\n"
                f"A->B straight: {result.straight_line_cm:.1f} cm | Path: {result.path_length_cm:.1f} cm\n"
                f"Horizontal: {result.horizontal_distance_cm:.1f} cm | "
                f"Vertical: {result.vertical_displacement_cm:+.1f} cm | Peak: {result.peak_height_cm:.1f} cm\n"
                f"Duration: {result.duration_s:.2f} s | Mean speed: {result.mean_speed_mps:.2f} m/s | "
                f"Max speed: {result.max_speed_mps:.2f} m/s\n"
                f"Efficiency: {efficiency}{flight}\n"
                f"IMU Quality: {status}\nReason: {reason}\n"
                f"Final Velocity Drift: {result.final_velocity_drift_mps:.2f} m/s"
                f"{endpoint}\nReference Validation: NO REFERENCE"
            ))
        else:
            live = self.ctrl.live_motion()
            hold = "holding still" if self.ctrl.is_holding_still else ("move the sensor" if live == "moving" else "hold still")
            self.t_warn.set(text=(f"Motion: {live} — {hold}"
                                  + ("*" + " provisional: drifts until Point B" if st is S.RECORDING else "")))
        state_label = "WAITING FOR STILL" if st is S.ARMED else st.value
        self.t_state.set(text=state_label, color={S.LOCKED: "#1a8c4d", S.RECORDING: "#d62f2f", S.ARMED: "#d98e04"}.get(st, "#444"))
        self.t_hint.set(text=HINTS.get(st, ""))
        self.t_foot.set(text=f"Saved to {self.saved_dir}" if self.saved_dir else "")
        return ()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group()
    src.add_argument("--address", help="sensor BLE address (live); required unless --replay is used")
    src.add_argument("--replay", type=Path, help="recorded .csv/.bin to replay (no sensor)")
    p.add_argument("--rate", type=int, default=52, choices=config.IMU9_SAMPLE_RATES_HZ)
    p.add_argument("--seconds", type=float, default=3600.0, help="maximum live session length")
    p.add_argument("--ping-interval", type=float, default=2.0, help="seconds between ping measurements (0 = off)")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    p.add_argument("--start-at-s", type=float, default=0.0, help="replay only: begin automatic detection this many seconds into the recording")
    p.add_argument("--headless-png", type=Path, help="replay only: process without a window and save a screenshot")
    p.add_argument("--continuous", action="store_true", help="every still->move->still makes an A->B (dribble/multiple throws)")
    args = p.parse_args()
    if not args.address and not args.replay:
        p.error("no address given. Connect via the Next.js frontend (Devices page: Scan -> Connect), "
                "then rerun with --address <ADDR>, or start tracking from /live-tracking "
                "(backend: uvicorn api:app --host 0.0.0.0 --port 8000).")
    if args.headless_png and not args.replay:
        p.error("--headless-png needs --replay")
    if args.speed <= 0:
        p.error("--speed must be greater than zero")
    if args.start_at_s < 0:
        p.error("--start-at-s must be zero or greater")
    if args.headless_png:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # after backend selection

    feed = Feed("replay" if args.replay else "live")
    rate, start_ms = args.rate, None
    if args.replay:
        rate = int(start_replay(feed, args.replay, args.rate if args.replay.suffix == ".bin" else None, args.speed))
        if args.start_at_s > 0:
            first = acquisition.load_capture(args.replay, rate if args.replay.suffix == ".bin" else None)[0][config.TIME_COL].iloc[0]
            start_ms = float(first) + args.start_at_s * 1000.0
    else:
        start_live(feed, args.address, rate, args.seconds, args.ping_interval)
    ctrl = session_mod.SessionController(rate, continuous=args.continuous)
    view = LiveView(plt, feed, ctrl, start_ms)

    if args.headless_png:
        while not ((ctrl.state is S.LOCKED and not args.continuous) or (feed.finished and feed.q.empty())):
            view.update()
            time.sleep(0.01)
        view.update()
        view.fig.savefig(args.headless_png, dpi=120)
        print(f"state={ctrl.state.value} saved={args.headless_png}")
        return 0

    from matplotlib.animation import FuncAnimation
    _anim = FuncAnimation(view.fig, view.update, interval=100, cache_frame_data=False)  # keep a reference
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
