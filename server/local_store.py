"""Local file-backed persistence for the single-user lab API.

No database is used.  Raw captures stay in ``config.RAW_DIR``, processed
trajectories stay in ``config.PROCESSED_DIR``, and small pieces of application
state (device registry, tracking-session metadata, scan log) are written as
JSON/JSONL under ``config.LOCAL_DIR``.

Writes are atomic (temporary file + ``os.replace``) so a crash cannot leave a
half-written JSON document.  This is intentionally a small local store, not a
replacement for the optional SQL schema in ``schema.sql``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import config

log = logging.getLogger(__name__)

DATA_DIR = config.LOCAL_DIR
RAW_FORMATS = ("csv", "json", "bin")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file(name: str) -> Path:
    return Path(DATA_DIR) / name


def relative_path(path: Path) -> str:
    """Prefer project-relative paths in persisted metadata."""
    try:
        return path.resolve().relative_to(config.PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


_relative = relative_path


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read local store %s: %s", path, exc)
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_state(name: str, default: Any) -> Any:
    """Read one JSON document from the local state directory."""
    return _read_json(_file(name), default)


def write_state(name: str, payload: Any) -> None:
    """Atomically write one JSON document to the local state directory."""
    _write_json(_file(name), payload)


# ---------------------------------------------------------------------------
# Device registry
# ---------------------------------------------------------------------------
def save_devices(devices: Iterable[dict[str, Any]]) -> None:
    """Persist the known-device registry (metadata only, never a live handle)."""
    _write_json(_file("devices.json"), list(devices))


def load_devices() -> list[dict[str, Any]]:
    data = _read_json(_file("devices.json"), [])
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("address")]


# ---------------------------------------------------------------------------
# Scan history
# ---------------------------------------------------------------------------
def append_scan(record: dict[str, Any]) -> None:
    """Append one scan result as a JSON line."""
    path = _file("scans.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"scanned_at": _now(), **record}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def load_scans(limit: int = 50) -> list[dict[str, Any]]:
    path = _file("scans.jsonl")
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    except OSError as exc:
        log.warning("could not read scan log %s: %s", path, exc)
        return []
    return records[-limit:]


# ---------------------------------------------------------------------------
# Raw capture artefacts
# ---------------------------------------------------------------------------
def raw_artifacts(session_id: str) -> dict[str, str]:
    """Return existing raw capture files for a session, keyed by format."""
    root = Path(config.RAW_DIR)
    out: dict[str, str] = {}
    for fmt in RAW_FORMATS:
        path = root / f"{session_id}.{fmt}"
        if path.is_file():
            out[fmt] = _relative(path)
    return out


def raw_artifact_path(session_id: str, fmt: str) -> Path | None:
    """Resolve a raw capture file without allowing path traversal."""
    if fmt not in RAW_FORMATS:
        return None
    root = Path(config.RAW_DIR).resolve()
    path = (root / f"{session_id}.{fmt}").resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


# ---------------------------------------------------------------------------
# Tracking-session metadata
# ---------------------------------------------------------------------------
def load_tracking_sessions() -> list[dict[str, Any]]:
    data = _read_json(_file("tracking_sessions.json"), [])
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("session_id")]


def _save_tracking_sessions(records: list[dict[str, Any]]) -> None:
    _write_json(_file("tracking_sessions.json"), records)


def _upsert_tracking_session(record: dict[str, Any]) -> None:
    records = load_tracking_sessions()
    for index, existing in enumerate(records):
        if existing.get("session_id") == record.get("session_id"):
            records[index] = record
            break
    else:
        records.append(record)
    _save_tracking_sessions(records)


def _with_artifacts(record: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(record)
    session_id = str(enriched.get("session_id", ""))
    enriched["raw"] = raw_artifacts(session_id) if session_id else {}
    enriched.setdefault("processed", [])
    return enriched


def start_tracking_record(
    *,
    session_id: str,
    address: str,
    device: str,
    rate_hz: int,
    continuous: bool,
    started_at: str | None = None,
) -> dict[str, Any]:
    record = {
        "session_id": session_id,
        "address": address,
        "device": device,
        "rate_hz": rate_hz,
        "continuous": continuous,
        "started_at": started_at or _now(),
        "finished_at": None,
        "status": "starting",
        "sample_count": 0,
        "raw": raw_artifacts(session_id),
        "processed": [],
        "error": None,
    }
    _upsert_tracking_session(record)
    return record


def finish_tracking_record(
    session_id: str,
    *,
    status: str,
    sample_count: int,
    finished_at: str | None = None,
    processed: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    records = load_tracking_sessions()
    record: dict[str, Any] | None = None
    for existing in records:
        if existing.get("session_id") == session_id:
            record = existing
            break
    if record is None:
        record = {
            "session_id": session_id,
            "address": "",
            "device": "",
            "rate_hz": None,
            "continuous": False,
            "started_at": finished_at or _now(),
            "raw": {},
            "processed": [],
        }
        records.append(record)
    record.update(
        {
            "finished_at": finished_at or _now(),
            "status": status,
            "sample_count": int(sample_count),
            "raw": raw_artifacts(session_id),
            "processed": processed if processed is not None else record.get("processed", []),
            "error": error,
        }
    )
    _save_tracking_sessions(records)
    return record


def mark_interrupted_tracking_sessions() -> int:
    """Mark sessions left unfinished by a previous process as interrupted."""
    records = load_tracking_sessions()
    changed = 0
    for record in records:
        if record.get("finished_at"):
            continue
        record["finished_at"] = _now()
        record["status"] = "interrupted"
        record["error"] = record.get("error") or "Server restarted before the session finished"
        record["raw"] = raw_artifacts(str(record.get("session_id", "")))
        changed += 1
    if changed:
        _save_tracking_sessions(records)
    return changed


def get_tracking_session(session_id: str) -> dict[str, Any] | None:
    for record in load_tracking_sessions():
        if record.get("session_id") == session_id:
            return _with_artifacts(record)
    return None


def list_tracking_sessions() -> list[dict[str, Any]]:
    return sorted(
        (_with_artifacts(r) for r in load_tracking_sessions()),
        key=lambda r: str(r.get("started_at", "")),
        reverse=True,
    )


def _raw_only_record(session_id: str) -> dict[str, Any]:
    path = Path(config.RAW_DIR) / f"{session_id}.csv"
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        mtime = ""
    return {
        "session_id": session_id,
        "address": "",
        "device": "",
        "rate_hz": None,
        "continuous": False,
        "started_at": mtime,
        "finished_at": mtime,
        "status": "raw-only",
        "sample_count": None,
        "raw": raw_artifacts(session_id),
        "processed": [],
        "error": None,
    }


def list_local_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """List tracked sessions plus raw captures created outside the web API."""
    by_id = {r["session_id"]: r for r in list_tracking_sessions()}

    raw_root = Path(config.RAW_DIR)
    if raw_root.is_dir():
        try:
            raw_files = sorted(raw_root.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            raw_files = []
        for path in raw_files:
            session_id = path.stem
            if session_id not in by_id:
                by_id[session_id] = _raw_only_record(session_id)
    records = sorted(
        by_id.values(), key=lambda r: str(r.get("started_at", "")), reverse=True
    )
    return records[:limit]


def get_local_session(session_id: str) -> dict[str, Any] | None:
    record = get_tracking_session(session_id)
    if record is not None:
        return record
    if raw_artifacts(session_id):
        return _raw_only_record(session_id)
    return None
