"""Constants and analysis parameters.

Protocol/hardware facts carry their documentation source. Everything else is a tunable
project parameter; the values used are written into every session summary.
"""
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Vercel (and other function-as-a-service runtimes) mount the deployment
# read-only; only the system temp directory is writable. Detect that and keep
# every generated file there instead of inside the checked-out ``data/`` tree.
ON_SERVERLESS = bool(
    os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
)
_DATA_ROOT_OVERRIDE = os.environ.get("MOVESENSE_DATA_DIR")

if _DATA_ROOT_OVERRIDE:
    DATA_ROOT = Path(_DATA_ROOT_OVERRIDE).expanduser()
elif ON_SERVERLESS:
    DATA_ROOT = Path(tempfile.gettempdir()) / "movesense"
else:
    DATA_ROOT = PROJECT_ROOT / "data"

RAW_DIR = DATA_ROOT / "raw"
PROCESSED_DIR = DATA_ROOT / "processed"
LOCAL_DIR = DATA_ROOT / "local"

# Force Secure session cookies when the API is served over HTTPS behind a proxy
# that does not forward ``X-Forwarded-Proto``. Normally the request scheme is
# detected per request instead, so local HTTP development still works.
SECURE_COOKIES = os.environ.get("MOVESENSE_SECURE_COOKIES") == "1"


def _ensure_runtime_data() -> None:
    """Create the writable data directories and seed bundled sample output.

    On a serverless cold start the sample trajectories committed under
    ``data/processed`` are copied to the writable directory so the dashboard
    and report pages have something to show.
    """
    for folder in (RAW_DIR, PROCESSED_DIR, LOCAL_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
    if not ON_SERVERLESS:
        return
    bundled = PROJECT_ROOT / "data" / "processed"
    try:
        if bundled.is_dir() and not any(PROCESSED_DIR.iterdir()):
            shutil.copytree(bundled, PROCESSED_DIR, dirs_exist_ok=True)
    except OSError:
        pass


_ensure_runtime_data()

PROCESSING_VERSION = "0.2.0"

# --- GATT SensorData Protocol (GSP) -------------------------------------------------
# https://www.movesense.com/docs/esw/gatt_sensordata_protocol/
GSP_SERVICE_UUID = "34802252-7185-4d5d-b431-630e7050e8f0"
GSP_WRITE_UUID = "34800001-7185-4d5d-b431-630e7050e8f0"
GSP_NOTIFY_UUID = "34800002-7185-4d5d-b431-630e7050e8f0"
CMD_HELLO, CMD_SUBSCRIBE, CMD_UNSUBSCRIBE = 0, 1, 2
RESP_COMMAND, RESP_DATA, RESP_DATA_PART2 = 1, 2, 3

# --- Sensor API ---------------------------------------------------------------------
# https://www.movesense.com/docs/esw/api_reference/  (/Meas/IMU9/{SampleRate})
IMU9_PATH = "/Meas/IMU9/{rate}"
IMU9_SAMPLE_RATES_HZ = (13, 26, 52, 104, 208)
# IMU9 payload: uint32 timestamp [ms] + n*3 float32 acc [m/s^2], n*3 gyro [deg/s],
# n*3 magn [uT]. Layout confirmed on real HR+ captures (all packets decoded cleanly).
IMU9_BYTES_PER_SAMPLE = 36

# --- Bluetooth SIG standard assigned numbers ----------------------------------------
HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
DEVICE_INFO_UUIDS = {
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
    "model": "00002a24-0000-1000-8000-00805f9b34fb",
    "serial": "00002a25-0000-1000-8000-00805f9b34fb",
    "firmware": "00002a26-0000-1000-8000-00805f9b34fb",
}

# --- Acquisition settings -------------------------------------------------------------
SCAN_TIMEOUT_S = 8.0
CONNECT_TIMEOUT_S = 15.0
NAME_FILTER = "movesense"  # assumption: confirm against your scan output
MAX_RECONNECTS = 5
HELLO_REFERENCE = 1
SUBSCRIBE_REFERENCE = 42

# --- World-frame origin -------------------------------------------------------------
# Point A (trajectory start) defaults to this offset so the X/Y/Z distance
# readouts start at 0/20/40 cm. Explicit --start-x/y/z-cm still overrides it.
DEFAULT_POINT_A_CM = (0.0, 20.0, 40.0)

# --- Data schema ----------------------------------------------------------------------
TIME_COL = "Timestamp_ms"
ACC_COLS = ["Accel_X", "Accel_Y", "Accel_Z"]
GYRO_COLS = ["Gyro_X", "Gyro_Y", "Gyro_Z"]
MAG_COLS = ["Mag_X", "Mag_Y", "Mag_Z"]
RAW_COLUMNS = [TIME_COL, *ACC_COLS, *GYRO_COLS, *MAG_COLS, "HeartRate", "SensorID"]
UNITS = {"time": "ms", "acc": "m/s^2", "gyro": "deg/s", "magn": "uT", "heart_rate": "bpm"}

STANDARD_GRAVITY_MPS2 = 9.80665  # CGPM standard gravity (used for ballistic equations)


@dataclass(frozen=True)
class Params:
    # calibration (stationary window)
    calib_window_s: float = 1.5
    calib_max_acc_std_mps2: float = 0.15
    calib_max_gyro_std_dps: float = 1.0
    calib_max_gyro_bias_dps: float = 10.0
    calib_gravity_tol_mps2: float = 0.5
    gravity_comp_mean_tol_mps2: float = 0.25
    # filtering: zero-phase Butterworth low-pass on acceleration (offline, non-causal)
    lowpass_cutoff_hz: float = 15.0
    filter_order: int = 4
    # orientation (Madgwick IMU, accelerometer + gyroscope only)
    madgwick_beta: float = 0.02
    acc_gate_mps2: float = 1.5  # accelerometer correction only if | |a| - g | < gate
    # rest / motion segmentation
    rest_acc_tol_mps2: float = 0.6
    rest_gyro_dps: float = 20.0
    rest_window_s: float = 0.3
    min_motion_s: float = 0.2
    max_motion_s: float = 8.0  # longer segments are flagged (integration drift)
    # flight (free-fall) detection
    flight_acc_max_mps2: float = 4.0
    flight_min_s: float = 0.08
    flight_merge_gap_s: float = 0.04
    # sanity limits
    max_plausible_speed_mps: float = 25.0
    max_closure_error_mps: float = 0.5  # raw |v(end)| before drift removal; larger = IMU quality warning
    height_mismatch_tol_m: float = 1.0  # integrated peak vs ballistic flight height
    max_gap_factor: float = 1.5
    # motion-type classification (heuristic labels for still / move / throw / dribble)
    throw_min_peak_speed_mps: float = 4.0  # fast segment -> throw candidate
    throw_min_acc_mps2: float = 8.0  # fast + hard acceleration -> throw
    bounce_min_linear_acc_mps2: float = 2.5  # impact spike in gravity-free |a| counts as a bounce
    bounce_min_gap_s: float = 0.15  # minimum separation between two bounces
    dribble_min_bounces: int = 3  # >= this many impacts inside one segment -> dribble
    dribble_max_interval_s: float = 1.2  # max gap between consecutive bounces for a dribble
    # motion-based one-shot triggers (still -> move -> still), see session.py
    trig_start_acc_mps2: float = 1.0  # |a - a_still_mean| above this = moving
    trig_start_gyro_dps: float = 30.0  # |gyro - bias| above this = moving
    trig_start_confirm_s: float = 0.06  # movement must persist this long (debounce)
    trig_stop_confirm_s: float = 0.5  # stillness must persist this long (debounce)
    trig_stop_acc_std_mps2: float = 0.25  # per-axis std over the confirm window
    trig_stop_gyro_dps: float = 15.0  # max |gyro - bias| over the confirm window
    trig_edge_acc_mps2: float = 0.3  # sub-threshold motion edge for A/B refinement (still noise ~0.05)
    trig_edge_gyro_dps: float = 5.0  # same for angular rate (still |gyro - bias| ~0.5 max)
    trig_min_movement_s: float = 0.3  # shorter movements are rejected, system re-arms
    trig_min_session_s: float = 0.6
    trig_max_session_s: float | None = None  # None: never time out; set to abort (no fabricated B)
    align_x_to_b: bool = False  # rotate XY so Point B lies on +X (yaw is otherwise arbitrary)
