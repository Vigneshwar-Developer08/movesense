# Movesense HR+/HR2 Trajectory Project

One-shot A-to-B trajectory (distance, height, 3D path) from Movesense IMU9 data. Python + MATLAB.
Lengths are in cm, speeds in m/s. IMU quality and reference validation are reported separately.

## Setup (Python 3.10+)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps + pytest/matplotlib/uvicorn
pytest                      # synthetic signals + API/local-storage tests
```

`requirements.txt` is the lean runtime set installed by Vercel; `requirements-dev.txt`
adds the test runner, the matplotlib replay viewer, and uvicorn.

## Web frontend flow (scan / connect / disconnect / live tracking via Next.js)
From the repository root, `vercel dev -L` starts both services with `/api/*`
routed to FastAPI. To run them separately instead:
```bash
cd server && uvicorn api:app --host 0.0.0.0 --port 8000
cd client && npm run dev            # http://localhost:3000
```
Set `NEXT_PUBLIC_API_URL=http://localhost:8000` for the separate-server flow;
when the app is deployed as one Vercel project, requests stay same-origin and
no URL is needed.
- Devices page: Scan -> Connect / Disconnect / Remove (owns all BLE device management).
- Live Tracking page: pick the connected sensor -> Start backend session
  (`POST /api/tracking/start`), polls `GET /api/tracking/{id}` +
  `GET /api/tracking/{id}/trajectory` for the 3D path, NEW re-arms
  (`POST /api/tracking/{id}/reset`), Stop ends BLE streaming
  (`POST /api/tracking/{id}/stop`).
- Dashboard "Discover Devices" routes to the Devices page.
- Report page (`/report`): signed-in user's analysed A→B summaries — straight-line
  distance, path/speed and an estimated peak force (mass × peak dynamic
  acceleration) — plus per-session CSV/JSON/MAT and raw CSV/JSON/BIN downloads.
  Stopping a live run (or automatic Point B) opens its report automatically.
- `GET /api/sessions` lists exported A->B summaries for the dashboard and report page.

## Local accounts and sessions
- The first visit redirects to `/signin`; create a workstation account at `/signup`.
  There is no external auth service.
- `POST /api/auth/signup`, `POST /api/auth/signin`, `POST /api/auth/signout`, and
  `GET /api/auth/me` back the forms. The browser stores an HttpOnly
  `movesense_session` cookie; the server keeps only token digests and PBKDF2
  password hashes in `data/local/users.json` / `sessions.json`.
- Sign out from the header. Delete those two files to remove all local accounts.
  Raw captures remain on disk and are not user-scoped.
- New live-tracking runs record the signed-in user in their processed summary.
  The Report page shows the current user's reports (plus legacy untagged local
  reports) and hides reports owned by another local account.

## Local output storage (current, no database)
The API saves its output on the server filesystem. No SQL connection is used
and `schema.sql` is only an optional future-proposal file.
On Vercel the deployment is read-only, so the API writes to `/tmp/movesense`
(override with `MOVESENSE_DATA_DIR`) and seeds the bundled sample trajectories
there. That directory is ephemeral; see the root `README.md` for the full
serverless caveats.
- `data/raw/{session_id}.csv`, `.json`, `.bin` — raw IMU capture written by
  `acquisition.record_session` (this happens even when no A->B trajectory completes).
- `data/processed/{MOVE_id}/` — trajectory CSV and summary JSON written by
  `trajectory.export_trajectory`.
- `data/local/devices.json` — known-device registry. After a restart devices are
  restored as `disconnected` because the BLE connection itself cannot be restored.
- `data/local/tracking_sessions.json` — live-session metadata, raw artefact links,
  processed trajectory links, status, and sample counts. Sessions left unfinished
  by a restart are marked `interrupted`.
- `data/local/scans.jsonl` — successful BLE scan results.
- `GET /api/local/sessions` lists saved sessions (including raw-only captures).
- `GET /api/local/sessions/{id}` returns one saved-session record.
- `GET /api/local/sessions/{id}/export?format=raw_csv|raw_json|raw_bin` downloads
  a raw capture. Processed artefacts continue to use `/api/sessions/{id}/export`.

## Live viewer (debug/replay matplotlib window: connection, ping in ms, 3D A-to-B trajectory in cm)
```bash
python app.py --address <ADDR>                                   # live sensor: address required (no prompt; prefer the frontend flow above)
python app.py --replay data/raw/<file>.csv --start-at-s 0 --speed 5 # recorded data, labelled REPLAY (no sensor)
python app.py --replay data/raw/prior_hrplus_live_capture.bin --start-at-s 0 --speed 100 --headless-png data/processed/replay.png # save a replay plot without opening a window
```
- Ping = round trip of the GSP HELLO command, measured every 2 s (`--ping-interval`).
- While RECORDING the path is provisional (marked `*`); the final drift-constrained path replaces it at Point B.
- `--headless-png` saves the live viewer as a PNG for replay runs without requiring a GUI backend.
- No ARM action is needed: keep the sensor still for a stable baseline, move once to start Point A, then hold still to mark Point B.
- Distances are IMU-only estimates. They drift with sensor/orientation errors and cannot be guaranteed accurate without an external reference or sensor; inspect IMU Quality and quality flags before trusting them.
- If no window opens: `pip install PyQt6` and run with `MPLBACKEND=QtAgg`.

## Python commands
```bash
python main.py scan
python main.py verify --address <ADDR>
python main.py record --address <ADDR> --seconds 30 --placement chest --experiment throw
python main.py track  --address <ADDR> --show          # automatic live still -> move -> still
python main.py track  --address <ADDR> --continuous      # keep sensing: every still->move->still makes an A->B (dribble, repeated throws)
python main.py trajectory --input data/raw/<file>.csv --start-at-s 0 --end-x-cm 500 --end-y-cm 0 --end-z-cm 0 --show
python main.py trajectory --input data/raw/<file>.csv --continuous  # all movements in one recording
python main.py analyze --input data/raw/<file>.csv     # all motion segments
python main.py compare --session-dir data/processed/<MOVE_id>
```
Protocol: keep the sensor still for a stable baseline (~1.5 s), make ONE movement, then hold still (~0.5 s). Movement onset is Point A; the still trigger is Point B.
Hold-still / motion state: `ctrl.live_motion()` reports `waiting-still | still | moving | move | throw | dribble`
and `ctrl.is_holding_still` reports the hold-still detector. Every finalized window is labelled
`move` (generic A-to-B), `throw` (free-fall flight, or fast + hard), or `dribble` (repeated impacts,
e.g. basketball dribbling) — see `motion` / `n_bounces` in each summary. Thresholds are tunable in `Params`
(`throw_min_peak_speed_mps`, `bounce_min_linear_acc_mps2`, `dribble_min_bounces`, ...).

## Frames and gravity compensation
- Sensor frame: Movesense local X/Y/Z. The sensor may be upright, upside down, sideways, or otherwise mounted before startup.
- World frame: X/Y are horizontal reference axes and Z is upward.
- Physical gravity is `[0, 0, -g]` in the world frame.
- A stationary accelerometer measures `+g` specific force, opposite physical gravity. During calibration the measured mean `g_sensor = mean(acc)` is rotated onto world `[0, 0, +1]`.
- The pipeline is: `a_sensor -> R(q) a_sensor -> a_world_specific -> a_world_linear = a_world_specific - [0, 0, g]`.
- Gravity fixes roll and pitch, but not yaw. Without magnetometer or an external heading reference, a pre-mounted yaw rotation such as 180 degrees around world Z is not physically observable from gravity alone. `align_x_to_b` can rotate the final XY trajectory for presentation/reference alignment; it is not used for gravity compensation.

## MATLAB (base MATLAB, no toolboxes)
```matlab
addpath('matlab'); testTrajectory
runTrajectorySession('data/raw/<file>.csv','SampleRate',52,'SessionId','<MOVE_id>', ...
    'OutDir','data/processed','View','3d','Visible','on');
```
Use the same SessionId/OutDir as the Python run, then run `main.py compare`.

## Known limits
- Inertial position drifts; check `final_velocity_drift_mps` and the quality flags before trusting a result. `closure_error_mps` is kept as a backward-compatible alias.
- Heading (X/Y direction) is arbitrary: no magnetometer. Z is up.
- BLE recording/tracking code is untested on hardware; MATLAB code was tested in GNU Octave, not MATLAB.
- `data/raw/prior_hrplus_live_capture.*` is a real HR+ capture of handling, not a measured A-to-B test.
