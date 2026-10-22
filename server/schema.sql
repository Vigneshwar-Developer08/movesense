-- Movesense 3D Trajectory Studio — PostgreSQL Schema (OPTIONAL / NOT ACTIVE)
-- Version: 1.1.0
-- Run: psql -U <user> -d movesense -f schema.sql
--
-- Scope / assumptions:
--   * This file is a future persistence proposal only. The current API does not
--     connect to a database: it stores devices, scan history, and tracking
--     metadata as local JSON under `data/local`, raw captures under `data/raw`,
--     and processed artefacts under `data/processed` (see `local_store.py`).
--   * The API has no authentication or user accounts. Consequently there is no
--     users/auth table here; `sessions.subject_name` is free-text metadata.
--   * Intended deployment is a single-user lab workstation. Add auth, tenancy,
--     and row-level security before exposing the API on an untrusted network.
--   * `raw_samples` is partitioned by month/day in production. The DEFAULT
--     partition below prevents inserts from failing, but old partitions are
--     not archived automatically.
--   * A recording session can contain multiple A->B movements (continuous
--     mode), so trajectories are uniquely identified by
--     (session_id, movement_index).

-- =============================================================================
-- Extensions
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid() on PostgreSQL < 13

-- =============================================================================
-- DEVICES — BLE sensor registry
-- =============================================================================
CREATE TABLE IF NOT EXISTS devices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    address         VARCHAR(64) NOT NULL UNIQUE,           -- BLE MAC (Linux) or UUID (macOS)
    name            VARCHAR(128),                           -- e.g. "Movesense HR2"
    serial          VARCHAR(128),                           -- from GATT Device Info
    model           VARCHAR(128),                           -- model string
    firmware        VARCHAR(64),                            -- firmware revision
    manufacturer    VARCHAR(128),                           -- manufacturer string
    has_gsp         BOOLEAN NOT NULL DEFAULT FALSE,        -- supports GSP (GATT SensorData Protocol)
    has_hr          BOOLEAN NOT NULL DEFAULT FALSE,        -- supports Heart Rate service
    gsp_hello       JSONB,                                  -- parsed HELLO response payload
    status          VARCHAR(24) NOT NULL DEFAULT 'disconnected'
                    CHECK (status IN ('connected', 'disconnected', 'error')),
    last_rssi       SMALLINT CHECK (last_rssi BETWEEN -127 AND 20),  -- dBm
    last_seen_at    TIMESTAMPTZ,                            -- last scan/connection timestamp
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_devices_status ON devices (status);
-- The UNIQUE constraint on address already provides its index.

-- =============================================================================
-- SESSIONS — motion capture recording sessions
-- =============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_num     SERIAL UNIQUE,
    session_id      VARCHAR(128) NOT NULL UNIQUE,           -- e.g. "LIVE_20261016_104215"
    device_id       UUID REFERENCES devices(id) ON DELETE SET NULL,
    protocol        VARCHAR(256),                           -- e.g. "Gait Analysis (Linear 10m walkway)"
    subject_name    VARCHAR(256),
    subject_note    TEXT,
    sample_rate_hz  INTEGER NOT NULL DEFAULT 52
                    CHECK (sample_rate_hz > 0 AND sample_rate_hz <= 1000),
    duration_s      DOUBLE PRECISION CHECK (duration_s IS NULL OR duration_s >= 0),
    status          VARCHAR(24) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'completed', 'error', 'discarded')),
    metadata        JSONB NOT NULL DEFAULT '{}',            -- freeform session context
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sessions_time_order CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_sessions_device ON sessions (device_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions (status);

-- =============================================================================
-- TRAJECTORIES — computed A->B movement results per recording session
-- =============================================================================
CREATE TABLE IF NOT EXISTS trajectories (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    movement_index          INTEGER NOT NULL DEFAULT 0
                            CHECK (movement_index >= 0),    -- 0,1,... within a continuous session
    straight_line_cm        DOUBLE PRECISION CHECK (straight_line_cm IS NULL OR straight_line_cm >= 0),
    path_length_cm          DOUBLE PRECISION CHECK (path_length_cm IS NULL OR path_length_cm >= 0),
    horizontal_distance_cm  DOUBLE PRECISION,
    vertical_displacement_cm DOUBLE PRECISION,
    peak_height_cm          DOUBLE PRECISION CHECK (peak_height_cm IS NULL OR peak_height_cm >= 0),
    duration_s              DOUBLE PRECISION CHECK (duration_s IS NULL OR duration_s >= 0),
    mean_speed_mps          DOUBLE PRECISION CHECK (mean_speed_mps IS NULL OR mean_speed_mps >= 0),
    max_speed_mps           DOUBLE PRECISION CHECK (max_speed_mps IS NULL OR max_speed_mps >= 0),
    efficiency              DOUBLE PRECISION CHECK (efficiency IS NULL OR efficiency >= 0),
    flight_time_s           DOUBLE PRECISION CHECK (flight_time_s IS NULL OR flight_time_s >= 0),
    flight_height_cm        DOUBLE PRECISION CHECK (flight_height_cm IS NULL OR flight_height_cm >= 0),
    endpoint_error_m        DOUBLE PRECISION CHECK (endpoint_error_m IS NULL OR endpoint_error_m >= 0),
    final_velocity_drift_mps DOUBLE PRECISION CHECK (final_velocity_drift_mps IS NULL OR final_velocity_drift_mps >= 0),
    quality_status          VARCHAR(32)
                            CHECK (quality_status IS NULL OR quality_status IN ('GOOD', 'WARNING', 'ERROR')),
    quality_flags           TEXT[] NOT NULL DEFAULT '{}',
    motion_type             VARCHAR(32)
                            CHECK (motion_type IS NULL OR motion_type IN ('move', 'throw', 'dribble', 'still')),
    position_data           JSONB,                          -- [[x,y,z],...] trajectory points (cm)
    baseline                JSONB,                          -- calibration snapshot
    extra                   JSONB NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT trajectories_session_movement_unique UNIQUE (session_id, movement_index)
);

CREATE INDEX IF NOT EXISTS idx_trajectories_session ON trajectories (session_id);
CREATE INDEX IF NOT EXISTS idx_trajectories_quality ON trajectories (quality_status);
CREATE INDEX IF NOT EXISTS idx_trajectories_motion ON trajectories (motion_type);

-- =============================================================================
-- RAW_SAMPLES — optional raw IMU sample storage (high volume, partitioned by date)
-- =============================================================================
CREATE TABLE IF NOT EXISTS raw_samples (
    id              BIGSERIAL,
    session_id      UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    timestamp_ms    DOUBLE PRECISION NOT NULL CHECK (timestamp_ms >= 0),
    accel_x         REAL NOT NULL,
    accel_y         REAL NOT NULL,
    accel_z         REAL NOT NULL,
    gyro_x          REAL NOT NULL,
    gyro_y          REAL NOT NULL,
    gyro_z          REAL NOT NULL,
    mag_x           REAL,
    mag_y           REAL,
    mag_z           REAL,
    heart_rate      REAL CHECK (heart_rate IS NULL OR (heart_rate >= 0 AND heart_rate <= 300)),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Default partition (create dated partitions as needed)
CREATE TABLE IF NOT EXISTS raw_samples_default PARTITION OF raw_samples DEFAULT;

-- Composite index supports per-session ordered reads. A unique
-- (session_id, timestamp_ms) constraint is not possible without the partition
-- key, so duplicate timestamps are rejected by the acquisition layer.
CREATE INDEX IF NOT EXISTS idx_raw_samples_session_ts ON raw_samples (session_id, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_raw_samples_ts ON raw_samples (timestamp_ms);

-- =============================================================================
-- SCAN_LOG — audit log of BLE scans
-- =============================================================================
CREATE TABLE IF NOT EXISTS scan_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_duration_s DOUBLE PRECISION CHECK (scan_duration_s IS NULL OR scan_duration_s >= 0),
    devices_found   INTEGER NOT NULL DEFAULT 0 CHECK (devices_found >= 0),
    results         JSONB,                                  -- full scan results snapshot
    scanned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scan_log_scanned ON scan_log (scanned_at DESC);

-- =============================================================================
-- Trigger: auto-update `updated_at` timestamps
-- =============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_devices_updated ON devices;
CREATE TRIGGER trg_devices_updated
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_sessions_updated ON sessions;
CREATE TRIGGER trg_sessions_updated
    BEFORE UPDATE ON sessions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Views
-- =============================================================================
CREATE OR REPLACE VIEW v_session_summary AS
SELECT
    s.id,
    s.session_num,
    s.session_id,
    s.protocol,
    s.subject_name,
    s.subject_note,
    s.sample_rate_hz,
    s.duration_s,
    s.status AS session_status,
    s.started_at,
    s.ended_at,
    d.name AS device_name,
    d.address AS device_address,
    d.serial AS device_serial,
    t.movement_index,
    t.straight_line_cm,
    t.path_length_cm,
    t.quality_status,
    t.quality_flags,
    t.motion_type
FROM sessions s
LEFT JOIN devices d ON s.device_id = d.id
LEFT JOIN trajectories t ON t.session_id = s.id
ORDER BY s.started_at DESC, t.movement_index ASC;
