"use client";

import React, { useState, useCallback, useRef, useEffect } from "react";
import Link from "next/link";
import { Sidebar } from "@/components/Sidebar";
import { Header, UnitType } from "@/components/Header";
import { apiErrorMessage, apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ScannedDevice {
  name: string;
  address: string;
  rssi: number | null;
  advertises_gsp: boolean;
  is_connected: boolean;
}

interface ConnectedDevice {
  address: string;
  name: string;
  serial: string | null;
  model: string | null;
  firmware: string | null;
  manufacturer: string | null;
  rssi: number | null;
  connected_at: string;
  has_gsp: boolean;
  has_hr: boolean;
  gsp_hello: Record<string, string> | null;
  status: string;
}

interface ActiveTrackingSession {
  session_id: string;
  address: string;
  device: string;
  connected: boolean;
  status: string;
  finished: boolean;
  started_at: string;
}

type ScanState = "idle" | "scanning" | "done" | "error";
type ConnectState = "idle" | "connecting" | "error";

const SCAN_TIMEOUT_S = 8;

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function DevicesPage() {
  const [currentUnit, setCurrentUnit] = useState<UnitType>("m");
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  // Scan state
  const [scanState, setScanState] = useState<ScanState>("idle");
  const [scannedDevices, setScannedDevices] = useState<ScannedDevice[]>([]);
  const [scanError, setScanError] = useState<string | null>(null);

  // Connected devices
  const [connectedDevices, setConnectedDevices] = useState<ConnectedDevice[]>([]);
  const [activeSessions, setActiveSessions] = useState<ActiveTrackingSession[]>([]);
  const [connectingAddress, setConnectingAddress] = useState<string | null>(null);
  const [connectState, setConnectState] = useState<ConnectState>("idle");

  // Notification toast
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);
  const toastTimeout = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback((message: string, type: "success" | "error" | "info" = "info") => {
    if (toastTimeout.current) clearTimeout(toastTimeout.current);
    setToast({ message, type });
    toastTimeout.current = setTimeout(() => setToast(null), 4000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimeout.current) clearTimeout(toastTimeout.current);
    };
  }, []);

  const fetchConnected = useCallback(async () => {
    try {
      const data = await apiFetch<{ devices: ConnectedDevice[] }>("/api/devices");
      setConnectedDevices(data.devices);
    } catch (err) {
      showToast(apiErrorMessage(err, "Could not reach the backend."), "error");
    }
  }, [showToast]);

  // Fetch devices and any running live-tracking sessions on mount.
  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      apiFetch<{ devices: ConnectedDevice[] }>("/api/devices"),
      apiFetch<{ sessions: ActiveTrackingSession[] }>("/api/tracking/sessions"),
    ]).then(([deviceResult, sessionResult]) => {
      if (cancelled) return;
      if (deviceResult.status === "fulfilled") {
        setConnectedDevices(deviceResult.value.devices);
      }
      if (sessionResult.status === "fulfilled") {
        setActiveSessions(
          (sessionResult.value.sessions ?? []).filter((session) => !session.finished),
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ----- Scan -----
  const handleScan = useCallback(async () => {
    setScanState("scanning");
    setScanError(null);
    setScannedDevices([]);
    try {
      const data = await apiFetch<{ devices: ScannedDevice[]; count: number }>(
        "/api/devices/scan",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ timeout_s: SCAN_TIMEOUT_S }),
        },
      );
      setScannedDevices(data.devices);
      setScanState("done");
      showToast(
        `Found ${data.count} device${data.count !== 1 ? "s" : ""}`,
        data.count > 0 ? "success" : "info",
      );
    } catch (err) {
      const message = apiErrorMessage(err, "BLE scan failed.");
      setScanError(message);
      setScanState("error");
      showToast(message, "error");
    }
  }, [showToast]);

  // ----- Connect -----
  const handleConnect = useCallback(
    async (address: string) => {
      setConnectingAddress(address);
      setConnectState("connecting");
      try {
        const data = await apiFetch<{ device?: { name?: string } }>(
          "/api/devices/connect",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ address }),
          },
        );
        showToast(`Connected to ${data.device?.name || address}`, "success");
        await fetchConnected();
        // Update scan list to reflect connection
        setScannedDevices((prev) =>
          prev.map((d) => (d.address === address ? { ...d, is_connected: true } : d))
        );
      } catch (err) {
        const message = apiErrorMessage(err, "Connection failed.");
        showToast(message, "error");
      } finally {
        setConnectingAddress(null);
        setConnectState("idle");
      }
    },
    [fetchConnected, showToast]
  );

  // ----- Disconnect -----
  const handleDisconnect = useCallback(
    async (address: string) => {
      try {
        await apiFetch<unknown>("/api/devices/disconnect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ address }),
        });
        showToast("Device disconnected", "info");
        await fetchConnected();
        setScannedDevices((prev) =>
          prev.map((d) => (d.address === address ? { ...d, is_connected: false } : d))
        );
      } catch (err) {
        showToast(apiErrorMessage(err, "Disconnect failed."), "error");
      }
    },
    [fetchConnected, showToast]
  );

  // ----- Remove -----
  const handleRemove = useCallback(
    async (address: string) => {
      try {
        await apiFetch<unknown>(
          `/api/devices/${encodeURIComponent(address)}`,
          { method: "DELETE" },
        );
        showToast("Device removed", "info");
        await fetchConnected();
        setScannedDevices((prev) =>
          prev.map((d) =>
            d.address === address ? { ...d, is_connected: false } : d,
          ),
        );
      } catch (err) {
        showToast(apiErrorMessage(err, "Remove failed."), "error");
      }
    },
    [fetchConnected, showToast]
  );

  // ----- Stop active live-tracking session -----
  const handleStopTracking = useCallback(
    async (sessionId: string) => {
      try {
        await apiFetch<unknown>(
          `/api/tracking/${encodeURIComponent(sessionId)}/stop`,
          { method: "POST" },
        );
        setActiveSessions((prev) =>
          prev.filter((session) => session.session_id !== sessionId),
        );
        showToast("Stopping live tracking session...", "info");
        await fetchConnected();
      } catch (err) {
        showToast(
          apiErrorMessage(err, "Could not stop the tracking session."),
          "error",
        );
      }
    },
    [fetchConnected, showToast],
  );

  // ----- RSSI signal icon -----
  const rssiIcon = (rssi: number | null) => {
    if (rssi === null) return "signal_cellular_0_bar";
    if (rssi >= -50) return "signal_cellular_alt";
    if (rssi >= -70) return "signal_cellular_alt_2_bar";
    if (rssi >= -85) return "signal_cellular_alt_1_bar";
    return "signal_cellular_0_bar";
  };

  const rssiLabel = (rssi: number | null) => {
    if (rssi === null) return "N/A";
    if (rssi >= -50) return "Excellent";
    if (rssi >= -70) return "Good";
    if (rssi >= -85) return "Fair";
    return "Weak";
  };

  const rssiColor = (rssi: number | null) => {
    if (rssi === null) return "text-outline";
    if (rssi >= -50) return "text-tertiary";
    if (rssi >= -70) return "text-tertiary";
    if (rssi >= -85) return "text-secondary";
    return "text-error";
  };

  return (
    <div className="min-h-screen bg-background text-on-surface flex flex-col font-body-md antialiased selection:bg-primary-container selection:text-white">
      <Sidebar
        activeModule="devices"
        isOpen={isMobileNavOpen}
        onClose={() => setIsMobileNavOpen(false)}
      />

      <div className="lg:pl-64 flex flex-col min-h-screen">
        <Header
          currentUnit={currentUnit}
          onUnitChange={setCurrentUnit}
          onOpenMobileMenu={() => setIsMobileNavOpen(true)}
          deviceConnected={connectedDevices.some((d) => d.status === "connected")}
          deviceName={
            connectedDevices.find((d) => d.status === "connected")?.name ??
            undefined
          }
        />

        <main className="w-full pt-18 px-space-md sm:px-gutter-desktop pb-margin-desktop bg-background flex flex-col gap-space-lg">
          {/* Page Header */}
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md pt-space-xs">
            <div className="flex flex-col gap-space-xs">
              <div className="flex items-center gap-space-sm">
                <h1 className="font-display-lg text-display-lg text-on-surface tracking-tight">
                  Device Management
                </h1>
                <span className="px-2.5 py-0.5 rounded-full bg-surface-container text-primary font-label-sm text-label-sm font-semibold">
                  BLE / Movesense
                </span>
              </div>
              <p className="font-body-md text-body-md text-on-surface-variant">
                Scan, connect, and manage Movesense IMU sensors over Bluetooth Low Energy
              </p>
            </div>

            <div className="flex items-center gap-space-sm self-start md:self-auto">
              <button
                onClick={handleScan}
                disabled={scanState === "scanning"}
                className={`inline-flex items-center gap-space-xs px-4 py-2.5 rounded-lg transition-all shadow-sm font-label-md text-label-md font-semibold cursor-pointer disabled:opacity-60 disabled:cursor-wait ${
                  scanState === "scanning"
                    ? "bg-surface-container-high text-on-surface"
                    : "bg-primary-container hover:bg-primary text-on-primary"
                }`}
                type="button"
              >
                <span
                  className={`material-symbols-outlined text-[18px] ${
                    scanState === "scanning" ? "animate-spin" : ""
                  }`}
                >
                  {scanState === "scanning" ? "progress_activity" : "bluetooth_searching"}
                </span>
                <span>{scanState === "scanning" ? "Scanning..." : "Scan for Devices"}</span>
              </button>
            </div>
          </div>

          {/* Live tracking owns the BLE link: explain why Scan/Connect is busy */}
          {activeSessions.length > 0 && (
            <div className="rounded-xl border border-primary/30 bg-primary-fixed p-space-md sm:p-space-lg flex flex-col sm:flex-row sm:items-center justify-between gap-space-md">
              <div className="flex items-start gap-space-sm">
                <span className="material-symbols-outlined text-[22px] text-primary shrink-0">
                  sensors
                </span>
                <div>
                  <div className="font-label-md text-label-md text-on-surface font-semibold">
                    Live tracking is using{" "}
                    {activeSessions[0].device || activeSessions[0].address}
                  </div>
                  <div className="font-body-sm text-body-sm text-on-surface-variant mt-0.5">
                    Session {activeSessions[0].session_id} holds the BLE link, so the
                    sensor stops advertising and Scan will not list it. Stop the
                    session before connecting it again.
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-space-xs shrink-0">
                <Link
                  href="/live-tracking"
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-container-lowest text-primary border border-outline-variant/30 font-label-md text-label-md cursor-pointer"
                >
                  <span className="material-symbols-outlined text-[16px]">
                    open_in_new
                  </span>
                  Open Live Tracking
                </Link>
                <button
                  onClick={() => handleStopTracking(activeSessions[0].session_id)}
                  className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-error-container text-error border border-error/20 font-label-md text-label-md cursor-pointer"
                  type="button"
                >
                  <span className="material-symbols-outlined text-[16px]">
                    stop_circle
                  </span>
                  Stop Session
                </button>
              </div>
            </div>
          )}

          {/* Connected Devices Summary */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-space-md">
            {/* Metric: Connected */}
            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Connected Sensors
                </span>
                <span className="material-symbols-outlined text-[20px] text-tertiary">
                  bluetooth_connected
                </span>
              </div>
              <div className="my-space-sm">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {connectedDevices.filter((d) => d.status === "connected").length}
                </span>
              </div>
              <div className="flex items-center pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                <span className="w-2 h-2 rounded-full bg-tertiary-container animate-pulse mr-2"></span>
                Active BLE links
              </div>
            </div>

            {/* Metric: Scanned */}
            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Discovered Nearby
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  sensors
                </span>
              </div>
              <div className="my-space-sm">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {scannedDevices.length}
                </span>
              </div>
              <div className="flex items-center pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                <span className="font-label-sm text-label-sm text-outline">
                  {scanState === "done" ? "Last scan complete" : scanState === "scanning" ? "Scanning..." : "No scan yet"}
                </span>
              </div>
            </div>
          </div>

          {/* Scan Results Section */}
          <div className="bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 overflow-hidden flex flex-col">
            <div className="p-space-lg flex flex-col lg:flex-row lg:items-center justify-between gap-space-md border-b border-outline-variant/20">
              <div className="flex items-center gap-space-md">
                <h2 className="font-headline-md text-headline-md text-on-surface">
                  Discovered Devices
                </h2>
                <span className="px-2.5 py-0.5 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm font-semibold">
                  {scannedDevices.length} Found
                </span>
              </div>
              {scanState === "scanning" && (
                <div className="flex items-center gap-space-sm text-primary">
                  <span className="material-symbols-outlined text-[18px] animate-spin">
                    progress_activity
                  </span>
                  <span className="font-label-md text-label-md">
                    Scanning Bluetooth neighborhood (~{SCAN_TIMEOUT_S}s)...
                  </span>
                </div>
              )}
            </div>

            {/* Scan Error Banner */}
            {scanError && (
              <div className="mx-space-lg mt-space-md p-space-md rounded-lg bg-error-container border border-error/20 flex items-start gap-space-sm">
                <span className="material-symbols-outlined text-[20px] text-on-error-container shrink-0 mt-0.5">
                  error
                </span>
                <div>
                  <div className="font-label-md text-label-md text-on-error-container font-semibold">
                    Scan Failed
                  </div>
                  <div className="font-body-sm text-body-sm text-on-error-container mt-0.5">
                    {scanError}
                  </div>
                </div>
              </div>
            )}

            {/* Empty state */}
            {scanState === "idle" && scannedDevices.length === 0 && (
              <div className="p-12 flex flex-col items-center justify-center text-center gap-space-md">
                <div className="w-16 h-16 rounded-2xl bg-surface-container flex items-center justify-center">
                  <span className="material-symbols-outlined text-[32px] text-outline">
                    bluetooth_searching
                  </span>
                </div>
                <div>
                  <div className="font-headline-sm text-headline-sm text-on-surface">
                    No Devices Discovered
                  </div>
                  <div className="font-body-md text-body-md text-on-surface-variant mt-1">
                    Click &quot;Scan for Devices&quot; to discover nearby Movesense sensors
                  </div>
                </div>
                <button
                  onClick={handleScan}
                  className="inline-flex items-center gap-space-xs px-4 py-2 rounded-lg bg-primary-container hover:bg-primary text-on-primary transition-all shadow-sm font-label-md text-label-md font-semibold cursor-pointer mt-2"
                  type="button"
                >
                  <span className="material-symbols-outlined text-[18px]">bluetooth_searching</span>
                  <span>Start Scan</span>
                </button>
              </div>
            )}

            {/* No matching devices after a completed scan */}
            {scanState === "done" && scannedDevices.length === 0 && (
              <div className="p-12 flex flex-col items-center justify-center text-center gap-space-md">
                <div className="w-16 h-16 rounded-2xl bg-surface-container flex items-center justify-center">
                  <span className="material-symbols-outlined text-[32px] text-outline">
                    bluetooth_disabled
                  </span>
                </div>
                <div>
                  <div className="font-headline-sm text-headline-sm text-on-surface">
                    No Matching Devices
                  </div>
                  <div className="font-body-md text-body-md text-on-surface-variant mt-1">
                    Wake the sensor, close other apps using it, and scan again.
                  </div>
                </div>
                <button
                  onClick={handleScan}
                  className="inline-flex items-center gap-space-xs px-4 py-2 rounded-lg bg-primary-container hover:bg-primary text-on-primary transition-all shadow-sm font-label-md text-label-md font-semibold cursor-pointer mt-2"
                  type="button"
                >
                  <span className="material-symbols-outlined text-[18px]">bluetooth_searching</span>
                  <span>Scan Again</span>
                </button>
              </div>
            )}

            {/* Scanning skeleton */}
            {scanState === "scanning" && scannedDevices.length === 0 && (
              <div className="p-space-lg flex flex-col gap-space-md">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="animate-pulse flex items-center gap-space-md p-space-md rounded-xl bg-surface-container-low border border-outline-variant/10">
                    <div className="w-12 h-12 rounded-xl bg-surface-container-high"></div>
                    <div className="flex-1 flex flex-col gap-2">
                      <div className="h-4 w-40 rounded bg-surface-container-high"></div>
                      <div className="h-3 w-56 rounded bg-surface-container"></div>
                    </div>
                    <div className="w-24 h-8 rounded-lg bg-surface-container-high"></div>
                  </div>
                ))}
              </div>
            )}

            {/* Device list */}
            {scannedDevices.length > 0 && (
              <div className="p-space-lg flex flex-col gap-space-sm">
                {scannedDevices.map((device) => {
                  const isThisConnecting = connectingAddress === device.address && connectState === "connecting";
                  return (
                    <div
                      key={device.address}
                      className={`flex items-center gap-space-md p-space-md rounded-xl border transition-all ${
                        device.is_connected
                          ? "bg-surface-container-low border-tertiary/30 shadow-sm"
                          : "bg-surface-container-lowest border-outline-variant/20 hover:border-primary/30 hover:shadow-xs"
                      }`}
                    >
                      {/* Device icon */}
                      <div
                        className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${
                          device.is_connected
                            ? "bg-tertiary-container"
                            : "bg-surface-container"
                        }`}
                      >
                        <span
                          className={`material-symbols-outlined text-[24px] ${
                            device.is_connected ? "text-on-tertiary" : "text-secondary"
                          }`}
                        >
                          {device.is_connected ? "bluetooth_connected" : "sensors"}
                        </span>
                      </div>

                      {/* Device info */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-space-sm flex-wrap">
                          <span className="font-headline-sm text-headline-sm text-on-surface truncate">
                            {device.name || "Unknown Device"}
                          </span>
                          {device.advertises_gsp && (
                            <span className="px-1.5 py-0.5 rounded bg-primary-fixed text-primary font-label-sm text-label-sm text-[10px] font-bold uppercase tracking-wider">
                              GSP
                            </span>
                          )}
                          {device.is_connected && (
                            <span className="px-1.5 py-0.5 rounded bg-tertiary-container text-tertiary font-label-sm text-label-sm text-[10px] font-bold uppercase tracking-wider flex items-center gap-0.5">
                              <span className="w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse"></span>
                              Connected
                            </span>
                          )}
                        </div>
                        <div className="font-metric-code text-[12px] text-on-surface-variant mt-0.5">
                          {device.address}
                        </div>
                        <div className="flex items-center gap-space-md mt-1">
                          <span className={`flex items-center gap-0.5 font-label-sm text-label-sm ${rssiColor(device.rssi)}`}>
                            <span className="material-symbols-outlined text-[14px]">
                              {rssiIcon(device.rssi)}
                            </span>
                            {device.rssi !== null ? `${device.rssi} dBm` : "—"} · {rssiLabel(device.rssi)}
                          </span>
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex items-center gap-space-xs shrink-0">
                        {device.is_connected ? (
                          <button
                            onClick={() => handleDisconnect(device.address)}
                            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-container-lowest hover:bg-error-container text-secondary hover:text-on-error-container transition-all border border-outline-variant/30 font-label-md text-label-md cursor-pointer"
                            type="button"
                          >
                            <span className="material-symbols-outlined text-[16px]">
                              bluetooth_disabled
                            </span>
                            Disconnect
                          </button>
                        ) : (
                          <button
                            onClick={() => handleConnect(device.address)}
                            disabled={isThisConnecting}
                            className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg transition-all font-label-md text-label-md cursor-pointer disabled:cursor-wait ${
                              isThisConnecting
                                ? "bg-surface-container-high text-on-surface opacity-70"
                                : "bg-primary-container hover:bg-primary text-on-primary shadow-xs"
                            }`}
                            type="button"
                          >
                            <span
                              className={`material-symbols-outlined text-[16px] ${
                                isThisConnecting ? "animate-spin" : ""
                              }`}
                            >
                              {isThisConnecting ? "progress_activity" : "bluetooth"}
                            </span>
                            {isThisConnecting ? "Connecting..." : "Connect"}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Connected Devices Detail Table */}
          {connectedDevices.length > 0 && (
            <div className="bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 overflow-hidden flex flex-col">
              <div className="p-space-lg flex items-center justify-between border-b border-outline-variant/20">
                <div className="flex items-center gap-space-md">
                  <h2 className="font-headline-md text-headline-md text-on-surface">
                    Registered Sensors
                  </h2>
                  <span className="px-2.5 py-0.5 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm font-semibold">
                    {connectedDevices.length} Total
                  </span>
                </div>
              </div>

              <div className="overflow-x-auto w-full">
                <table className="w-full text-left font-body-sm text-body-sm">
                  <thead className="bg-surface-container-low text-outline uppercase font-label-sm text-label-sm border-b border-outline-variant/20">
                    <tr>
                      <th className="py-3 px-space-lg">Device</th>
                      <th className="py-3 px-space-md">Address</th>
                      <th className="py-3 px-space-md">Serial</th>
                      <th className="py-3 px-space-md">Firmware</th>
                      <th className="py-3 px-space-md">Services</th>
                      <th className="py-3 px-space-md text-center">Status</th>
                      <th className="py-3 px-space-lg text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-outline-variant/20 text-on-surface">
                    {connectedDevices.map((device) => (
                      <tr
                        key={device.address}
                        className="hover:bg-surface-container-low/50 transition-colors"
                      >
                        <td className="py-3.5 px-space-lg">
                          <div className="flex items-center gap-space-sm">
                            <span
                              className={`w-2 h-2 rounded-full shrink-0 ${
                                device.status === "connected"
                                  ? "bg-tertiary animate-pulse"
                                  : "bg-secondary"
                              }`}
                            ></span>
                            <div>
                              <span className="font-headline-sm text-headline-sm text-on-surface">
                                {device.name || device.model || "Movesense"}
                              </span>
                              {device.manufacturer && (
                                <div className="font-label-sm text-label-sm text-on-surface-variant">
                                  {device.manufacturer}
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="py-3.5 px-space-md font-metric-code text-[12px] text-on-surface-variant">
                          {device.address}
                        </td>
                        <td className="py-3.5 px-space-md font-metric-code text-[12px] text-on-surface">
                          {device.serial || "—"}
                        </td>
                        <td className="py-3.5 px-space-md font-metric-code text-[12px] text-on-surface-variant">
                          {device.firmware || "—"}
                        </td>
                        <td className="py-3.5 px-space-md">
                          <div className="flex items-center gap-1">
                            {device.has_gsp && (
                              <span className="px-1.5 py-0.5 rounded bg-primary-fixed text-primary font-label-sm text-[10px] font-bold">
                                GSP
                              </span>
                            )}
                            {device.has_hr && (
                              <span className="px-1.5 py-0.5 rounded bg-error-container text-on-error-container font-label-sm text-[10px] font-bold">
                                HR
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="py-3.5 px-space-md text-center">
                          <span
                            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${
                              device.status === "connected"
                                ? "bg-tertiary-fixed text-tertiary"
                                : device.status === "error"
                                ? "bg-error-container text-on-error-container"
                                : "bg-surface-container-high text-secondary"
                            }`}
                          >
                            {device.status === "connected" && (
                              <span className="w-1.5 h-1.5 rounded-full bg-tertiary animate-pulse"></span>
                            )}
                            {device.status.charAt(0).toUpperCase() + device.status.slice(1)}
                          </span>
                        </td>
                        <td className="py-3.5 px-space-lg text-right">
                          <div className="flex items-center justify-end gap-1.5">
                            {device.status === "connected" ? (
                              <button
                                onClick={() => handleDisconnect(device.address)}
                                className="p-1.5 rounded-lg hover:bg-error-container text-secondary hover:text-on-error-container transition-colors cursor-pointer"
                                title="Disconnect"
                                type="button"
                              >
                                <span className="material-symbols-outlined text-[18px]">
                                  bluetooth_disabled
                                </span>
                              </button>
                            ) : (
                              <button
                                onClick={() => handleConnect(device.address)}
                                className="p-1.5 rounded-lg hover:bg-surface-container text-primary transition-colors cursor-pointer"
                                title="Reconnect"
                                type="button"
                              >
                                <span className="material-symbols-outlined text-[18px]">
                                  bluetooth
                                </span>
                              </button>
                            )}
                            <button
                              onClick={() => handleRemove(device.address)}
                              className="p-1.5 rounded-lg hover:bg-error-container text-secondary hover:text-on-error-container transition-colors cursor-pointer"
                              title="Remove from registry"
                              type="button"
                            >
                              <span className="material-symbols-outlined text-[18px]">
                                delete
                              </span>
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="p-space-md bg-surface-container-low flex items-center justify-between text-outline font-body-sm text-body-sm border-t border-outline-variant/20">
                <span>
                  {connectedDevices.filter((d) => d.status === "connected").length} active connection{connectedDevices.filter((d) => d.status === "connected").length !== 1 ? "s" : ""} · {connectedDevices.length} registered
                </span>
                <span className="font-metric-code text-[11px] text-outline">
                  Movesense BLE Device Manager
                </span>
              </div>
            </div>
          )}
        </main>
      </div>

      {/* Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-6 right-6 z-50 flex items-center gap-space-sm px-4 py-3 rounded-xl shadow-lg border backdrop-blur-sm transition-all animate-[slideUp_0.3s_ease-out] ${
            toast.type === "success"
              ? "bg-tertiary-container/95 border-tertiary/30 text-on-tertiary-container"
              : toast.type === "error"
              ? "bg-error-container/95 border-error/30 text-on-error-container"
              : "bg-surface-container/95 border-outline-variant/30 text-on-surface"
          }`}
        >
          <span className="material-symbols-outlined text-[20px]">
            {toast.type === "success" ? "check_circle" : toast.type === "error" ? "error" : "info"}
          </span>
          <span className="font-label-md text-label-md">{toast.message}</span>
          <button
            onClick={() => setToast(null)}
            className="ml-2 p-0.5 rounded hover:bg-black/10 transition-colors cursor-pointer"
            type="button"
          >
            <span className="material-symbols-outlined text-[16px]">close</span>
          </button>
        </div>
      )}
    </div>
  );
}
