"""Tests for the local JSON/JSONL persistence layer (no database)."""
import json

import config
import local_store


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store, "DATA_DIR", tmp_path / "local")
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")
    return tmp_path / "local", tmp_path / "raw"


def test_device_registry_roundtrip(tmp_path, monkeypatch):
    data_dir, _ = _isolate(tmp_path, monkeypatch)
    devices = [
        {
            "address": "AA:BB",
            "name": "Movesense",
            "status": "connected",
            "connected_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    local_store.save_devices(devices)

    assert json.loads((data_dir / "devices.json").read_text()) == devices
    assert local_store.load_devices() == devices


def test_tracking_session_lifecycle_links_raw_and_processed(tmp_path, monkeypatch):
    _, raw_dir = _isolate(tmp_path, monkeypatch)
    raw_dir.mkdir(parents=True)
    (raw_dir / "TRK_1.csv").write_text("t_ms,ax\n0,1\n")
    (raw_dir / "TRK_1.bin").write_bytes(b"\x00")

    started = local_store.start_tracking_record(
        session_id="TRK_1",
        address="AA:BB",
        device="Movesense",
        rate_hz=52,
        continuous=False,
        started_at="2026-01-01T00:00:00+00:00",
    )
    assert started["status"] == "starting"
    assert set(started["raw"]) == {"csv", "bin"}
    assert started["raw"]["csv"].endswith("TRK_1.csv")

    finished = local_store.finish_tracking_record(
        "TRK_1",
        status="Stopped",
        sample_count=100,
        finished_at="2026-01-01T00:01:00+00:00",
        processed=[{"session_id": "MOVE_1", "trajectory_csv": "processed/MOVE_1/x.csv"}],
    )
    assert finished is not None
    assert finished["status"] == "Stopped"
    assert finished["sample_count"] == 100
    assert finished["processed"][0]["session_id"] == "MOVE_1"

    record = local_store.get_tracking_session("TRK_1")
    assert record is not None
    assert record["finished_at"] == "2026-01-01T00:01:00+00:00"
    assert set(record["raw"]) == {"csv", "bin"}


def test_unfinished_sessions_are_marked_interrupted(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    local_store.start_tracking_record(
        session_id="TRK_1",
        address="AA:BB",
        device="Movesense",
        rate_hz=52,
        continuous=False,
    )

    assert local_store.mark_interrupted_tracking_sessions() == 1

    record = local_store.get_tracking_session("TRK_1")
    assert record is not None
    assert record["status"] == "interrupted"
    assert record["finished_at"]
    assert "restarted" in record["error"]


def test_raw_artifact_lookup_blocks_traversal(tmp_path, monkeypatch):
    _, raw_dir = _isolate(tmp_path, monkeypatch)
    raw_dir.mkdir(parents=True)
    (raw_dir / "TRK_1.csv").write_text("ok")
    (tmp_path / "secret.csv").write_text("no")

    assert local_store.raw_artifact_path("TRK_1", "csv") == (raw_dir / "TRK_1.csv").resolve()
    assert local_store.raw_artifact_path("../secret", "csv") is None
    assert local_store.raw_artifact_path("TRK_1", "exe") is None


def test_list_local_sessions_includes_raw_only_captures(tmp_path, monkeypatch):
    _, raw_dir = _isolate(tmp_path, monkeypatch)
    raw_dir.mkdir(parents=True)
    (raw_dir / "LIVE_1.csv").write_text("t_ms,ax\n0,1\n")
    (raw_dir / "LIVE_1.json").write_text("{}")

    records = local_store.list_local_sessions()

    assert len(records) == 1
    assert records[0]["session_id"] == "LIVE_1"
    assert records[0]["status"] == "raw-only"
    assert set(records[0]["raw"]) == {"csv", "json"}


def test_scan_log_appends_json_lines(tmp_path, monkeypatch):
    data_dir, _ = _isolate(tmp_path, monkeypatch)

    local_store.append_scan({"count": 1, "devices": [{"address": "AA:BB"}]})
    local_store.append_scan({"count": 0, "devices": []})

    lines = (data_dir / "scans.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["devices"][0]["address"] == "AA:BB"
    assert local_store.load_scans(limit=1)[0]["count"] == 0
