"""Packet handling tests with SYNTHETIC GSP packets (format from the Movesense GSP docs)."""
import struct

import pytest

import acquisition
import config

RATE = 52


def _data_packet(ts_ms, n=4, code=config.RESP_DATA, ref=42, acc_z=9.8):
    acc = [0.0, 0.0, acc_z] * n
    gyro = [0.0] * (3 * n)
    mag = [0.0] * (3 * n)
    return bytes([code, ref]) + struct.pack("<I", ts_ms) + struct.pack(f"<{9 * n}f", *acc, *gyro, *mag)


def test_data_packet_decoding():
    state = acquisition.StreamState(RATE)
    acquisition.handle_packet(state, _data_packet(1000))
    assert len(state.rows) == 4
    assert state.rows[1][0] - state.rows[0][0] == pytest.approx(1000 / RATE)
    assert state.rows[0][3] == pytest.approx(9.8, abs=1e-5)  # Accel_Z


def test_missing_samples_detected():
    state = acquisition.StreamState(RATE)
    acquisition.handle_packet(state, _data_packet(1000))
    acquisition.handle_packet(state, _data_packet(1000 + 77 + 5 * 19))  # ~5 samples lost
    assert state.missing_samples >= 4 and len(state.gap_events) == 1


def test_split_packet_is_reassembled():
    full = _data_packet(2000)
    state = acquisition.StreamState(RATE)
    body = full[2:]
    cut = 4 + 36 * 2 + 7  # not a multiple of the sample size -> must wait for PART2
    acquisition.handle_packet(state, bytes([config.RESP_DATA, 42]) + body[:cut])
    assert state.rows == []
    acquisition.handle_packet(state, bytes([config.RESP_DATA_PART2, 42]) + body[cut:])
    assert len(state.rows) == 4 and state.malformed_packets == 0


def test_orphan_part2_counted_as_malformed():
    state = acquisition.StreamState(RATE)
    acquisition.handle_packet(state, bytes([config.RESP_DATA_PART2, 42]) + b"\x00" * 10)
    assert state.malformed_packets == 1


def test_subscribe_status_recorded():
    state = acquisition.StreamState(RATE)
    acquisition.handle_packet(state, bytes([config.RESP_COMMAND, 42]) + struct.pack("<H", 200))
    assert state.statuses[42] == 200


def test_hello_parsing_with_and_without_status_prefix():
    payload = bytes([1]) + b"SER123\x00Movesense HR+\x00AA:BB\x00GattApp\x001.2.3\x00"
    for packet in (bytes([1, 1]) + payload, bytes([1, 1]) + b"\xc8\x00" + payload):
        parsed = acquisition.parse_hello(packet)
        assert parsed["serial"] == "SER123" and parsed["product_name"] == "Movesense HR+"
        assert parsed["app_version"] == "1.2.3"
    assert acquisition.parse_hello(bytes([1, 1])) is None


def test_hello_parsing_legacy_hello_reply():
    # Older gatt_sensordata_app firmware answers with ASCII "Hello".
    for packet in (b"\x01\x01Hello", b"\x01\x01\xc8\x00Hello"):
        parsed = acquisition.parse_hello(packet)
        assert parsed == {"message": "Hello", "legacy_firmware": "true"}


def test_in_progress_bluez_error_is_retryable_not_fatal():
    message, fatal = acquisition.describe_ble_error(
        Exception("[org.bluez.Error.InProgress] Operation already in progress")
    )
    assert fatal is False
    assert "retry" in message.lower()


def test_missing_adapter_error_is_fatal():
    _message, fatal = acquisition.describe_ble_error(
        FileNotFoundError("No Bluetooth adapter/service available")
    )
    assert fatal is True


def test_bin_roundtrip_and_truncated_tail(tmp_path):
    packets = [_data_packet(1000 + i * 77) for i in range(3)]
    raw = b"".join(struct.pack("<H", len(p)) + p for p in packets)
    path = tmp_path / "cap.bin"
    path.write_bytes(raw + b"\x96\x00\x01")  # truncated final frame
    frame, meta = acquisition.load_capture(path, RATE)
    assert len(frame) == 12 and meta["sample_rate_hz"] == RATE


def test_bin_requires_sample_rate(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"")
    with pytest.raises(ValueError):
        acquisition.load_capture(tmp_path / "x.bin")


def test_unsupported_rate_rejected():
    import asyncio
    with pytest.raises(ValueError):
        asyncio.run(acquisition.record_session("AA", 100, 1, "t", "x", "y"))
