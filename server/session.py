"""One-shot or continuous A-to-B sessions: WAIT -> START at A -> RECORD -> STOP at B -> FINALIZE.

Triggers are motion based (still -> move -> still) and evaluated on the raw IMU only:
  * Baseline : a full still window while waiting (per-axis/norm acc std, gyro std/bias, |a| ~ g). It supplies
               the gravity/gyro-bias calibration and the initial orientation.
  * Start    : |a - a_still| or |gyro - bias| above threshold for trig_start_confirm_s (debounce).
               Point A = the last rest sample before the motion onset (velocity = 0 there),
               refined backward over sub-threshold onset samples buffered while waiting.
  * Stop     : a trailing trig_stop_confirm_s window with low per-axis acc std and low gyro.
               Point B = first sample of that still window (velocity = 0 there),
               refined forward to the last above-noise motion sample inside the window.
  * Motion label: every finalized window is classified as move | throw | dribble
               (see analysis.classify_movement); with no segment the live state is still.
               In continuous mode the controller re-arms after each Point B, so a
               basketball dribble yields one trajectory per still-separated movement
               (or one long 'dribble' trajectory while bouncing never settles).
Limitation: inertial position drifts, so the physical location of A/B is only as good as the
closure-error quality flag says; the triggers detect movement, not geometry.
"""
import enum
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config
import trajectory as traj
from config import Params

log = logging.getLogger(__name__)


class SessionState(enum.Enum):
    IDLE = "IDLE"
    ARMED = "WAITING"
    START_TRIGGERED = "START TRIGGERED"
    RECORDING = "RECORDING"
    STOP_TRIGGERED = "STOP TRIGGERED"
    COMPLETE = "COMPLETE"
    LOCKED = "LOCKED"


S = SessionState
ALLOWED = {
    S.IDLE: {S.ARMED},
    S.ARMED: {S.START_TRIGGERED, S.IDLE},
    S.START_TRIGGERED: {S.RECORDING},
    S.RECORDING: {S.STOP_TRIGGERED, S.ARMED, S.IDLE},  # ARMED = rejected short movement
    S.STOP_TRIGGERED: {S.COMPLETE},
    S.COMPLETE: {S.LOCKED},
    S.LOCKED: {S.IDLE, S.ARMED},  # ARMED = continuous mode re-arms for the next movement
}


def n_samples(seconds: float, fs: float, minimum: int = 1) -> int:
    return max(minimum, int(math.floor(seconds * fs + 0.5)))  # round-half-up (same as MATLAB round)


class SampleManager:
    """Sample identity = timestamp. Rejects duplicates and out-of-order samples (never reorders)."""

    def __init__(self) -> None:
        self.last_ts: float | None = None
        self.accepted = 0
        self.duplicates = 0
        self.out_of_order = 0

    def accept(self, timestamp_ms: float) -> bool:
        if self.last_ts is not None:
            if timestamp_ms == self.last_ts:
                self.duplicates += 1
                return False
            if timestamp_ms < self.last_ts:
                self.out_of_order += 1
                return False
        self.last_ts = timestamp_ms
        self.accepted += 1
        return True


class TriggerManager:
    """Stateless-threshold detectors plus the start debounce counter."""

    def __init__(self, fs: float, params: Params) -> None:
        self.p = params
        self.fs = fs
        self.n_base = n_samples(params.calib_window_s, fs, 2)
        self.n_confirm = n_samples(params.trig_start_confirm_s, fs, 2)
        self.n_stop = n_samples(params.trig_stop_confirm_s, fs, 3)
        self.start_count = 0

    def baseline(self, acc: np.ndarray, gyro: np.ndarray) -> traj.Baseline | None:
        """Baseline from the last n_base samples, or None if they are not a clean still window."""
        acc, gyro = acc[-self.n_base:], gyro[-self.n_base:]
        norm = np.linalg.norm(acc, axis=1)
        p = self.p
        if (norm.std(ddof=1) < p.calib_max_acc_std_mps2
                and acc.std(axis=0, ddof=1).max() < p.calib_max_acc_std_mps2
                and gyro.std(axis=0, ddof=1).max() < p.calib_max_gyro_std_dps
                and np.abs(gyro.mean(axis=0)).max() < p.calib_max_gyro_bias_dps
                and abs(norm.mean() - config.STANDARD_GRAVITY_MPS2) < p.calib_gravity_tol_mps2):
            return traj.Baseline(acc.mean(axis=0), gyro.mean(axis=0), float(norm.mean()),
                                 float(norm.std(ddof=1)), float(gyro.std(axis=0, ddof=1).max()))
        return None

    def update_start(self, acc: np.ndarray, gyro: np.ndarray, base: traj.Baseline) -> bool:
        moving = (np.linalg.norm(acc - base.acc_mean_mps2) > self.p.trig_start_acc_mps2
                  or np.linalg.norm(gyro - base.gyro_bias_dps) > self.p.trig_start_gyro_dps)
        self.start_count = self.start_count + 1 if moving else 0
        return self.start_count >= self.n_confirm

    def window_is_still(self, acc: np.ndarray, gyro: np.ndarray, base: traj.Baseline) -> bool:
        acc, gyro = acc[-self.n_stop:], gyro[-self.n_stop:]
        return bool(acc.std(axis=0, ddof=1).max() < self.p.trig_stop_acc_std_mps2
                    and np.linalg.norm(gyro - base.gyro_bias_dps, axis=1).max() < self.p.trig_stop_gyro_dps)

    def edge_is_motion(self, acc: np.ndarray, gyro: np.ndarray, base: traj.Baseline) -> bool:
        """Sub-threshold motion edge: above the still noise floor, possibly below trigger thresholds.

        Triggers (debounced, loose) cut gentle onset/tail slivers; this edge (tight, un-debounced)
        refines Point A backward and Point B forward over already-buffered samples only.
        """
        return bool(np.linalg.norm(np.asarray(acc) - base.acc_mean_mps2) > self.p.trig_edge_acc_mps2
                    or np.linalg.norm(np.asarray(gyro) - base.gyro_bias_dps) > self.p.trig_edge_gyro_dps)


@dataclass
class TrajectoryBuffer:
    """Samples of the current A-to-B window only. Frozen at the stop trigger."""

    t_ms: list = field(default_factory=list)
    acc: list = field(default_factory=list)
    gyro: list = field(default_factory=list)
    frozen: bool = False

    def append(self, t_ms: float, acc: np.ndarray, gyro: np.ndarray) -> None:
        if self.frozen:
            raise RuntimeError("TrajectoryBuffer is frozen: no samples may follow Point B")
        self.t_ms.append(t_ms)
        self.acc.append(acc)
        self.gyro.append(gyro)

    def freeze(self, keep: int) -> None:
        del self.t_ms[keep:], self.acc[keep:], self.gyro[keep:]
        self.frozen = True

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return np.array(self.t_ms), np.array(self.acc), np.array(self.gyro)


class SessionController:
    def __init__(self, sample_rate_hz: float, params: Params = Params(),
                 output_dir: Path = config.PROCESSED_DIR,
                 point_a_cm: tuple[float, float, float] | None = None,
                 point_b_cm: tuple[float, float, float] | None = None,
                 continuous: bool = False) -> None:
        self.fs = float(sample_rate_hz)
        self.p = params
        self.output_dir = Path(output_dir)
        self.point_a_cm = point_a_cm
        self.point_b_cm = point_b_cm
        self.continuous = continuous  # re-arm after each Point B: every still->move->still makes an A->B
        self.state = S.IDLE
        self.samples = SampleManager()
        self.triggers = TriggerManager(self.fs, params)
        self.events: list[tuple[float | None, str]] = []
        self.completed: list[traj.TrajectoryResult] = []
        self.result: traj.TrajectoryResult | None = None
        self.ignored_samples = 0
        self.rejected_movements = 0
        self._clear_active()

    # ----------------------------------------------------------------------- internals
    def _clear_active(self) -> None:
        maxlen = self.triggers.n_base + self.triggers.n_confirm + 2
        self._ring: deque = deque(maxlen=maxlen)  # (t_ms, acc, gyro) while waiting for a baseline
        self._baseline: traj.Baseline | None = None
        self._buffer = TrajectoryBuffer()
        self._last_t_ms: float | None = None
        self.triggers.start_count = 0
        self.data_gap = False

    def _log(self, message: str, t_ms: float | None = None) -> None:
        self.events.append((t_ms, message))
        log.info(message)

    def _go(self, new: SessionState) -> None:
        if new not in ALLOWED[self.state]:
            raise RuntimeError(f"Illegal transition {self.state.name} -> {new.name}")
        self.state = new

    # ------------------------------------------------------------------ user commands
    def arm(self) -> bool:
        if self.state is not S.IDLE:
            self._log(f"arm ignored: state is {self.state.value}")
            return False
        self._clear_active()
        self._go(S.ARMED)
        self._log("WAITING: keep still for baseline; the first movement starts Point A")
        return True

    def abort(self, reason: str = "user abort") -> bool:
        if self.state not in (S.ARMED, S.RECORDING):
            return False
        self._log(f"session aborted ({reason}); active buffer discarded, no Point B created")
        self._clear_active()
        self._go(S.IDLE)
        return True

    def reset(self, discard: bool = False) -> bool:
        """Explicit new-session command. Active sessions are only dropped with discard=True."""
        if self.state in (S.COMPLETE, S.LOCKED):
            self._clear_active()
            self.state = S.IDLE
            self.result = None  # finished sessions stay in self.completed
            self._log("reset: IDLE (completed sessions kept)")
            return True
        if self.state is S.IDLE:
            return True
        if not discard:
            self._log("reset refused: an active session cannot be finalized without a Stop Trigger; use discard=True")
            return False
        self._clear_active()
        self.state = S.IDLE
        self._log("reset: active session discarded")
        return True

    # ------------------------------------------------------------- hold-still / motion state
    @property
    def baseline_locked(self) -> bool:
        """True once the hold-still baseline is acquired (sensor was held still)."""
        return self._baseline is not None

    @property
    def is_holding_still(self) -> bool:
        """True when the most recent samples look still (hold-still detector output).

        ARMED: checks the waiting ring; RECORDING: checks the trailing buffer window.
        False when no baseline exists yet or no samples have arrived.
        """
        if self._baseline is None:
            return False
        try:
            if self.state is S.ARMED and len(self._ring) >= self.triggers.n_stop:
                acc = np.array([r[1] for r in list(self._ring)[-self.triggers.n_stop:]])
                gyro = np.array([r[2] for r in list(self._ring)[-self.triggers.n_stop:]])
                return self.triggers.window_is_still(acc, gyro, self._baseline)
            if self.state is S.RECORDING and len(self._buffer.t_ms) >= self.triggers.n_stop:
                acc = np.array(self._buffer.acc[-self.triggers.n_stop:])
                gyro = np.array(self._buffer.gyro[-self.triggers.n_stop:])
                return self.triggers.window_is_still(acc, gyro, self._baseline)
        except (ValueError, IndexError):
            return False
        return self.state in (S.ARMED, S.LOCKED)

    def live_motion(self) -> str:
        """Live label for UI: 'idle' | 'waiting-still' | 'still' | 'moving' | last motion.

        * 'waiting-still': armed but no clean still baseline yet -> hold the sensor still.
        * 'still': baseline locked and recent samples are still.
        * 'moving': start debounce tripped or RECORDING (sensor is being moved/thrown).
        * after a finalized window: that window's 'move' | 'throw' | 'dribble' label.
        """
        if self.state is S.IDLE:
            return "idle"
        if self.state is S.ARMED:
            if self._baseline is None:
                return "waiting-still"
            if self.triggers.start_count > 0:
                return "moving"
            return "still" if self.is_holding_still else "moving"
        if self.state in (S.START_TRIGGERED, S.RECORDING, S.STOP_TRIGGERED, S.COMPLETE):
            return "moving"
        if self.state is S.LOCKED:
            if self.result is not None:
                return str(self.result.extra.get("motion", "move"))
            return "still"
        return "still"

    # ----------------------------------------------------------------------- samples
    def feed(self, timestamp_ms: float, acc, gyro) -> SessionState:
        """Offer one IMU sample. Each accepted sample is processed exactly once."""
        if not self.samples.accept(timestamp_ms):
            return self.state
        if self.state in (S.IDLE, S.LOCKED, S.COMPLETE):
            self.ignored_samples += 1  # not part of any trajectory
            return self.state
        acc, gyro = np.asarray(acc, dtype=float), np.asarray(gyro, dtype=float)
        gap = (self._last_t_ms is not None
               and timestamp_ms - self._last_t_ms > self.p.max_gap_factor * 1000.0 / self.fs)
        self._last_t_ms = timestamp_ms
        if self.state is S.ARMED:
            self._on_armed(timestamp_ms, acc, gyro, gap)
        elif self.state is S.RECORDING:
            self._on_recording(timestamp_ms, acc, gyro, gap)
        return self.state

    def _on_armed(self, t_ms: float, acc: np.ndarray, gyro: np.ndarray, gap: bool) -> None:
        if gap:
            self._ring.clear()
            self._baseline = None
            self.triggers.start_count = 0
            self._log("data gap while waiting: baseline restarted", t_ms)
        self._ring.append((t_ms, acc, gyro))
        tm = self.triggers
        if self._baseline is None:
            if len(self._ring) >= tm.n_base:
                ring_acc = np.array([r[1] for r in self._ring])
                ring_gyro = np.array([r[2] for r in self._ring])
                self._baseline = tm.baseline(ring_acc, ring_gyro)
                if self._baseline is not None:
                    self._log(f"baseline locked: g={self._baseline.gravity_mps2:.3f} m/s^2; waiting for motion", t_ms)
            return
        if not tm.update_start(acc, gyro, self._baseline):
            return
        # Start trigger accepted (latched: only evaluated in ARMED, so it can fire once)
        self._go(S.START_TRIGGERED)
        samples = list(self._ring)
        onset = len(samples) - tm.n_confirm  # first moving sample
        first = onset - 1  # last rest sample before the motion onset (velocity = 0 there)
        while first > 0 and tm.edge_is_motion(samples[first - 1][1], samples[first - 1][2], self._baseline):
            first -= 1  # include the sub-threshold onset sliver cut by the start debounce
        for t, a, g in samples[first:]:  # rest sample (Point A) + debounce window
            self._buffer.append(t, a, g)
        self._ring.clear()
        self._log(f"START trigger confirmed: Point A at t={self._buffer.t_ms[0]:.0f} ms", t_ms)
        self._go(S.RECORDING)
        self.data_gap = False

    def _on_recording(self, t_ms: float, acc: np.ndarray, gyro: np.ndarray, gap: bool) -> None:
        self._buffer.append(t_ms, acc, gyro)
        if gap and not self.data_gap:
            self.data_gap = True
            self._log("data interruption during RECORDING: session flagged 'data_gap'", t_ms)
        p, tm = self.p, self.triggers
        t0 = self._buffer.t_ms[0]
        if p.trig_max_session_s is not None and (t_ms - t0) / 1000.0 > p.trig_max_session_s:
            self.abort(f"no stop trigger within {p.trig_max_session_s:.1f} s")
            return
        count = len(self._buffer.t_ms)
        if count < tm.n_stop + 1:
            return
        trail_acc = np.array(self._buffer.acc[-tm.n_stop:])
        trail_gyro = np.array(self._buffer.gyro[-tm.n_stop:])
        if not tm.window_is_still(trail_acc, trail_gyro, self._baseline):
            return
        base_start = count - tm.n_stop  # confirm-window index of Point B
        still_start = base_start
        for k in range(tm.n_stop):  # refine B forward to the last above-noise motion sample
            a_k = np.asarray(self._buffer.acc[base_start + k])
            g_k = np.asarray(self._buffer.gyro[base_start + k])
            if tm.edge_is_motion(a_k, g_k, self._baseline):
                still_start = min(base_start + k + 1, count - 1)
        t_b = self._buffer.t_ms[still_start]
        if (t_b - self._buffer.t_ms[1]) / 1000.0 < p.trig_min_movement_s or (t_b - t0) / 1000.0 < p.trig_min_session_s:
            self.rejected_movements += 1
            self._log("movement too short: rejected; waiting for another movement", t_ms)
            seed = list(zip(self._buffer.t_ms, self._buffer.acc, self._buffer.gyro))[-tm.n_stop:]
            self._clear_active()
            self._ring.extend(seed)
            self._go(S.ARMED)
            return
        self._go(S.STOP_TRIGGERED)
        self._buffer.freeze(still_start + 1)  # latch: nothing after Point B belongs to the trajectory
        self._log(f"STOP trigger confirmed: Point B at t={t_b:.0f} ms", t_ms)
        self._finalize()

    def provisional_trajectory(self) -> traj.TrajectoryResult | None:
        """Live preview while RECORDING (end velocity NOT constrained: drifts, never stored or exported)."""
        if self.state is not S.RECORDING or len(self._buffer.t_ms) < 8:
            return None
        t_ms, acc, gyro = self._buffer.arrays()
        return traj.reconstruct_trajectory("provisional", t_ms, acc, gyro, self._baseline, self.fs, self.p,
                                           constrain_end=False)

    def _finalize(self) -> None:
        t_ms, acc, gyro = self._buffer.arrays()
        session_id = traj.next_session_id(self.output_dir)
        self.result = traj.reconstruct_trajectory(
            session_id, t_ms, acc, gyro, self._baseline, self.fs, self.p,
            point_a_cm=self.point_a_cm, point_b_cm=self.point_b_cm
        )
        if self.data_gap and "data_gap" not in self.result.flags:
            self.result.flags.append("data_gap")
        self.completed.append(self.result)
        self._go(S.COMPLETE)
        motion = self.result.extra.get("motion", "move")
        self._log(f"COMPLETE: {session_id}, motion={motion}, estimated A->B = {self.result.straight_line_cm:.1f} cm, "
                  f"path = {self.result.path_length_cm:.1f} cm")
        self._go(S.LOCKED)
        if self.continuous:
            # Keep sensing: the trailing still window seeds the next baseline, no NEW press needed.
            seed = list(zip(self._buffer.t_ms, self._buffer.acc, self._buffer.gyro))[-self.triggers.n_stop:]
            self._clear_active()
            self._ring.extend(seed)
            self._go(S.ARMED)
            self._log("re-armed (continuous): hold still; the next movement starts a new Point A")
        else:
            self._log("LOCKED: session frozen; press NEW to detect another movement")
