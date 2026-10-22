"""Protocol-level FAKE Bluetooth device (never hardware data): exercises connect, HELLO, SUBSCRIBE, streaming,
ping, disconnect/reconnect, stop request and error paths of acquisition.record_session."""
import asyncio
import json
import struct

import numpy as np
import pytest

import acquisition
import app
import config

G = config.STANDARD_GRAVITY_MPS2


class FakeDevice:
    name, address = "Movesense FAKE", "AA:BB:CC:DD:EE:FF"


class Link:
    """Shared fake-sensor state across reconnects (the sensor clock keeps running)."""

    def __init__(self, drop_after_packets=None, subscribe_status=200):
        self.ts_ms = 10_000
        self.connects = 0
        self.drop_after_packets = drop_after_packets
        self.subscribe_status = subscribe_status
        self.found = True


def make_fakes(link: Link):
    class FakeScanner:
        @staticmethod
        async def find_device_by_address(address, timeout=10.0):
            return FakeDevice() if link.found else None

    class Svc:
        def __init__(self, uuid):
            self.uuid = uuid

    class FakeClient:
        def __init__(self, device, timeout=None, disconnected_callback=None):
            self.cb, self.is_connected, self.task = None, False, None
            self.on_disc = disconnected_callback
            self.services = [Svc(config.GSP_SERVICE_UUID)]

        async def __aenter__(self):
            link.connects += 1
            self.is_connected = True
            return self

        async def __aexit__(self, *exc):
            self.is_connected = False
            if self.task:
                self.task.cancel()

        async def start_notify(self, uuid, cb):
            self.cb = cb

        async def stop_notify(self, uuid):
            return None

        async def read_gatt_char(self, uuid):
            return b"fake"

        async def write_gatt_char(self, uuid, data, response=True):
            cmd, ref = data[0], data[1]
            loop = asyncio.get_running_loop()
            if cmd == config.CMD_HELLO:
                body = b"\x01" + b"SER1\x00Movesense HR+\x00AA\x00GattApp\x001.0\x00"
                loop.call_later(0.01, self.cb, None, bytearray([1, ref]) + body)
            elif cmd == config.CMD_SUBSCRIBE:
                loop.call_later(0.01, self.cb, None, bytearray([1, ref]) + struct.pack("<H", link.subscribe_status))
                if link.subscribe_status == 200:
                    self.task = asyncio.ensure_future(self.stream(ref))
            elif cmd == config.CMD_UNSUBSCRIBE and self.task:
                self.task.cancel()

        async def stream(self, ref):
            sent = 0
            while True:
                await asyncio.sleep(0.077)
                acc = [0.0, 0.0, G] * 4
                body = struct.pack("<I", link.ts_ms) + struct.pack("<36f", *acc, *([0.0] * 24))
                self.cb(None, bytearray([config.RESP_DATA, ref]) + body)
                link.ts_ms += 77
                sent += 1
                if link.drop_after_packets and sent == link.drop_after_packets:
                    self.is_connected = False
                    self.on_disc(self)
                    return

    return FakeScanner, FakeClient


def run_session(monkeypatch, tmp_path, link, seconds, stop_after=None, ping=0.15):
    scanner, client = make_fakes(link)
    monkeypatch.setattr(acquisition, "BleakScanner", scanner)
    monkeypatch.setattr(acquisition, "BleakClient", client)
    monkeypatch.setattr(config, "MAX_RECONNECTS", 2)
    feed = app.Feed("live")
    got = []

    async def main():
        stop = asyncio.Event()
        if stop_after:
            asyncio.get_running_loop().call_later(stop_after, stop.set)
        try:
            return await acquisition.record_session(
                "AA:BB:CC:DD:EE:FF", 52, seconds, "T1", "chest", "mock", raw_dir=tmp_path,
                sample_sink=lambda ts, a, g: got.append(ts), stop_event=stop, event_sink=feed.event, ping_interval_s=ping)
        except Exception as error:
            feed.event("status", f"Error: {error}")
            raise

    return asyncio.run(main()), feed, got


def test_connect_stream_ping_stop_and_save(monkeypatch, tmp_path):
    link = Link()
    path, feed, got = run_session(monkeypatch, tmp_path, link, seconds=30, stop_after=2.4)
    meta = json.loads(path.with_suffix(".json").read_text())
    assert link.connects == 1 and len(got) >= 20 and np.all(np.diff(got) > 0)
    assert meta["sensor_model"] == "Movesense HR+" and meta["sensor_serial"] == "SER1"
    assert meta["missing_samples_detected"] == 0 and meta["samples"] == len(got)
    snap = feed.snapshot()
    assert snap["device"] == "Movesense FAKE" and snap["connected"] is False and snap["status"] == "Disconnected"
    assert snap["ping_ms"] is not None and 5 < snap["ping_ms"] < 200  # a ping was measured


def test_disconnect_triggers_reconnect_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr(asyncio, "sleep", asyncio.sleep)  # keep real timing; reconnect waits 1 s
    link = Link(drop_after_packets=8)
    path, feed, got = run_session(monkeypatch, tmp_path, link, seconds=30, stop_after=3.2)
    meta = json.loads(path.with_suffix(".json").read_text())
    assert link.connects >= 2
    assert any("connection lost" in e for e in meta["connection_events"])
    assert len(got) > 8  # data resumed after reconnect


def test_sensor_not_found_is_reported_not_silent(monkeypatch, tmp_path):
    link = Link()
    link.found = False
    path, feed, got = run_session(monkeypatch, tmp_path, link, seconds=2.5)
    status = feed.snapshot()["status"]
    assert got == [] and status.startswith("Disconnected") and "not found" in status  # the reason stays visible


def test_subscribe_rejected_is_reported(monkeypatch, tmp_path):
    link = Link(subscribe_status=404)
    path, feed, got = run_session(monkeypatch, tmp_path, link, seconds=2.0)
    assert path is None and got == [] and link.connects == 1  # no pointless retries, no empty files
    assert "rejected" in feed.snapshot()["status"] and list(tmp_path.iterdir()) == []


def test_error_descriptions_and_fatal_flags():
    msg, fatal = acquisition.describe_ble_error(FileNotFoundError(2, "No such file or directory"))
    assert fatal and "No Bluetooth adapter" in msg
    assert acquisition.describe_ble_error(OSError("Bluetooth device is turned off"))[1]
    assert acquisition.describe_ble_error(OSError("access denied by user"))[1]
    msg, fatal = acquisition.describe_ble_error(acquisition.BleakError("Sensor AA not found"), "AA:BB")
    assert not fatal and "Wake the sensor" in msg


def test_fatal_adapter_error_stops_retrying_and_saves_nothing(monkeypatch, tmp_path):
    class Broken:
        @staticmethod
        async def find_device_by_address(address, timeout=10.0):
            raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(acquisition, "BleakScanner", Broken)
    feed = app.Feed("live")
    path = asyncio.run(acquisition.record_session("AA", 52, 20, "T9", "x", "y", raw_dir=tmp_path, event_sink=feed.event))
    assert path is None and list(tmp_path.iterdir()) == []  # no empty files left behind
    assert "No Bluetooth adapter" in feed.snapshot()["status"]
