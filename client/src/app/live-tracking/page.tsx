"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Header, UnitType } from "@/components/Header";
import { ThreeTrajectoryScene } from "@/components/ThreeTrajectoryScene";
import { DiagnosticsPane } from "@/components/DiagnosticsPane";
import {
  WaveformChart,
  type TrackingSample,
} from "@/components/WaveformChart";
import { ExportModal } from "@/components/ExportModal";
import { apiErrorMessage, apiFetch, downloadArtifact } from "@/lib/api";

interface TrackingStatus {
  session_id: string;
  address: string;
  device: string;
  rate_hz: number;
  state: string;
  motion: string;
  holding_still: boolean;
  connected: boolean;
  status: string;
  ping_ms: number | null;
  kind: string;
  distance_cm: number | null;
  path_length_cm: number | null;
  speed_mps: number | null;
  point_b_cm: number[] | null;
  n_trajectories: number;
  sample_count: number;
  finished: boolean;
  last_acc: [number, number, number] | null;
  last_gyro: [number, number, number] | null;
  last_ts_ms: number | null;
  quality_flags: string[];
  started_at: string;
  finished_at?: string;
  processed_session_ids?: string[];
}

interface ConnectedDevice {
  address: string;
  name: string;
  status: string;
  rssi: number | null;
}

interface TrackingStartResponse {
  session_id: string;
  status: TrackingStatus;
}

interface SamplesResponse {
  samples: TrackingSample[];
}

// Next keeps this page mounted (off-screen) across client-side navigation, so
// its effects are torn down on hide and re-run on show with the previous
// `stopped`/`sessionId` state intact. Remembering which sessions already
// triggered the "open the report" redirect stops the page from bouncing
// straight back to /report when the user returns to Live Tracking.
const autoRedirectedSessions = new Set<string>();

export default function LiveTrackingPage() {
  const router = useRouter();
  const [currentUnit, setCurrentUnit] = useState<UnitType>("m");
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isExportOpen, setIsExportOpen] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sessionSaved, setSessionSaved] = useState(false);
  const [currentPos, setCurrentPos] = useState({ x: 0, y: 0, z: 0 });

  // Backend-driven tracking (Devices page owns scan/connect/disconnect)
  const [devices, setDevices] = useState<ConnectedDevice[]>([]);
  const [selectedAddress, setSelectedAddress] = useState<string>("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [trackStatus, setTrackStatus] = useState<TrackingStatus | null>(null);
  const [livePoints, setLivePoints] = useState<Array<[number, number, number]>>([]);
  const [waveSamples, setWaveSamples] = useState<TrackingSample[]>([]);
  const [trackError, setTrackError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [processedSessionId, setProcessedSessionId] = useState<string | null>(null);

  // Unit conversion multiplier relative to meters
  const unitMultipliers: Record<UnitType, number> = {
    m: 1,
    cm: 100,
    in: 39.3701,
    ft: 3.28084,
  };

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const [deviceResult, sessionResult] = await Promise.allSettled([
        apiFetch<{ devices: ConnectedDevice[] }>("/api/devices"),
        apiFetch<{ sessions: TrackingStatus[] }>("/api/tracking/sessions"),
      ]);
      if (cancelled) return;

      let connected: ConnectedDevice[] = [];
      if (deviceResult.status === "fulfilled") {
        connected = deviceResult.value.devices
          .filter((device) => device.status === "connected")
          .map((device) => ({
            address: device.address,
            name: device.name,
            status: device.status,
            rssi: device.rssi ?? null,
          }));
        setDevices(connected);
      } else {
        setTrackError(
          apiErrorMessage(
            deviceResult.reason,
            "Backend is offline; live tracking is unavailable.",
          ),
        );
      }

      // The backend keeps a tracking session alive across navigation/refresh.
      // Adopt it instead of losing its id and leaving the sensor locked with
      // "already running" errors and no way to stop it.
      let active: TrackingStatus | null = null;
      if (sessionResult.status === "fulfilled") {
        const running = (sessionResult.value.sessions ?? []).filter(
          (session) => !session.finished,
        );
        running.sort((a, b) =>
          (b.started_at ?? "").localeCompare(a.started_at ?? ""),
        );
        active = running[0] ?? null;
      }

      if (active) {
        setSessionId(active.session_id);
        setTrackStatus(active);
        setIsStreaming(true);
        setStopped(false);
        setSelectedAddress(active.address);
        setDevices((prev) =>
          prev.some((device) => device.address === active.address)
            ? prev
            : [
                {
                  address: active.address,
                  name: active.device || active.address,
                  status: "connected",
                  rssi: null,
                },
                ...prev,
              ],
        );
        return;
      }

      setSelectedAddress((prev) => prev || connected[0]?.address || "");
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const pollSession = useCallback(async (sid: string) => {
    const base = `/api/tracking/${encodeURIComponent(sid)}`;
    // Poll independently: a transient trajectory/waveform failure must not
    // prevent the other views (and especially the 3D path) from updating.
    const [statusResult, trajectoryResult, samplesResult] =
      await Promise.allSettled([
        apiFetch<TrackingStatus>(base),
        apiFetch<{ points_m: Array<[number, number, number]> }>(
          `${base}/trajectory?max_points=400`,
        ),
        apiFetch<SamplesResponse>(`${base}/samples?limit=240`),
      ]);

    if (statusResult.status === "rejected") throw statusResult.reason;
    const status = statusResult.value;
    setTrackStatus(status);

    if (trajectoryResult.status === "fulfilled") {
      const points = trajectoryResult.value.points_m;
      if (Array.isArray(points) && points.length >= 2) {
        setLivePoints(points);
        const tip = points[points.length - 1];
        // Scene coordinates (Y up), matching ThreeTrajectoryScene's mapping.
        setCurrentPos({ x: tip[0], y: tip[2], z: -tip[1] });
      }
    }
    if (samplesResult.status === "fulfilled") {
      const samples = samplesResult.value.samples;
      if (Array.isArray(samples)) {
        setWaveSamples(samples);
      }
    }
    if (status.finished) {
      setIsStreaming(false);
      setStopped(true);
    }
  }, []);

  useEffect(() => {
    if (!sessionId || !isStreaming) return;
    const tick = () => {
      void pollSession(sessionId).catch((err: unknown) => {
        setTrackError(apiErrorMessage(err, "Lost contact with the tracking session."));
        setIsStreaming(false);
      });
    };
    const initial = setTimeout(tick, 0);
    const interval = setInterval(tick, 500);
    return () => {
      clearTimeout(initial);
      clearInterval(interval);
    };
  }, [sessionId, isStreaming, pollSession]);

  // When a run ends (manual Stop or automatic Point B), wait for the backend
  // to finish exporting, then open the analysed report automatically. The
  // `autoRedirectedSessions` guard makes this fire at most once per session:
  // Next keeps this page mounted off-screen, so returning to it later re-runs
  // this effect with the preserved `stopped` state and would otherwise bounce
  // the user straight back to /report.
  useEffect(() => {
    if (!stopped || !sessionId) return;
    if (autoRedirectedSessions.has(sessionId)) return;
    let cancelled = false;
    let attempts = 0;

    const openReport = (reportId: string | null) => {
      autoRedirectedSessions.add(sessionId);
      if (reportId) setProcessedSessionId(reportId);
      router.replace(
        reportId
          ? `/report?session=${encodeURIComponent(reportId)}`
          : "/report",
      );
    };

    const check = async () => {
      try {
        const status = await apiFetch<TrackingStatus>(
          `/api/tracking/${encodeURIComponent(sessionId)}`,
        );
        if (cancelled) return;
        if (status.finished) {
          openReport((status.processed_session_ids ?? [])[0] ?? null);
          return;
        }
      } catch {
        // Session metadata may still be finalizing; retry below.
      }
      if (cancelled) return;
      if (attempts < 30) {
        attempts += 1;
        setTimeout(check, 500);
      } else {
        openReport(null);
      }
    };

    void check();
    return () => {
      cancelled = true;
    };
  }, [stopped, sessionId, router]);

  const handleStartTracking = useCallback(async () => {
    if (!selectedAddress) {
      setTrackError("No connected device. Scan and Connect one on the Devices page first.");
      return;
    }
    setStarting(true);
    setTrackError(null);
    setTrackStatus(null);
    setLivePoints([]);
    setWaveSamples([]);
    setCurrentPos({ x: 0, y: 0, z: 0 });
    setStopped(false);
    setSessionSaved(false);
    setProcessedSessionId(null);
    try {
      const data = await apiFetch<TrackingStartResponse>("/api/tracking/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          address: selectedAddress,
          rate: 52,
          continuous: false,
        }),
      });
      setSessionId(data.session_id);
      setTrackStatus(data.status);
      setIsStreaming(true);
    } catch (err) {
      setTrackError(apiErrorMessage(err, "Failed to start tracking."));
    } finally {
      setStarting(false);
    }
  }, [selectedAddress]);

  const handleStopTrackingToggle = async () => {
    if (!isStreaming) {
      // A stopped backend session cannot resume; begin a new A->B session.
      await handleStartTracking();
      return;
    }
    if (!sessionId) return;
    const confirmed = window.confirm(
      "Stop current trajectory session and finalize biomechanics records?",
    );
    if (!confirmed) return;
    setStopping(true);
    try {
      await apiFetch<unknown>(`/api/tracking/${encodeURIComponent(sessionId)}/stop`, {
        method: "POST",
      });
      setIsStreaming(false);
      setStopped(true);
      try {
        await pollSession(sessionId);
      } catch {
        // Stop succeeded; keep the last rendered frame if the final poll fails.
      }
    } catch (err) {
      setTrackError(apiErrorMessage(err, "Could not stop the tracking session."));
    } finally {
      setStopping(false);
    }
  };

  const handleNewRun = async () => {
    if (!sessionId || !isStreaming) return;
    try {
      await apiFetch<unknown>(
        `/api/tracking/${encodeURIComponent(sessionId)}/reset`,
        { method: "POST" },
      );
      setLivePoints([]);
      setWaveSamples([]);
      setCurrentPos({ x: 0, y: 0, z: 0 });
      await pollSession(sessionId);
    } catch (err) {
      setTrackError(apiErrorMessage(err, "Could not reset the tracking session."));
    }
  };

  const handleSaveSession = async () => {
    if (!sessionId) return;
    try {
      await downloadArtifact(sessionId, "json");
      setSessionSaved(true);
      setTimeout(() => setSessionSaved(false), 3000);
    } catch (err) {
      setTrackError(
        apiErrorMessage(
          err,
          "The summary is not available yet. Stop tracking to finalize it.",
        ),
      );
    }
  };

  const deviceConnected = trackStatus
    ? trackStatus.connected
    : devices.length > 0;
  const statusLabel = trackStatus
    ? `${trackStatus.state}${trackStatus.kind === "provisional" ? "*" : ""} · ${trackStatus.motion}`
    : isStreaming
      ? "Connecting…"
      : stopped || sessionId
        ? "Session stopped"
        : "Idle";
  const liveHint = (() => {
    if (!trackStatus) return isStreaming ? "Connecting to the sensor…" : null;
    if (trackStatus.finished) {
      return "Session ended. Start a new session to capture another A→B movement.";
    }
    switch (trackStatus.state) {
      case "WAITING":
        return trackStatus.motion === "waiting-still"
          ? "Hold the sensor still until the baseline locks (about 3 seconds), then make one movement."
          : "Baseline locked. Move the sensor once to set Point A.";
      case "START TRIGGERED":
      case "RECORDING":
        return "Recording movement. Hold still at Point B to finalize the trajectory.";
      case "STOP TRIGGERED":
      case "COMPLETE":
        return "Point B detected. Finalizing the trajectory…";
      case "LOCKED":
        return "Point B locked. The 3D path is ready.";
      default:
        return null;
    }
  })();
  const sessionTag = sessionId ? `#${sessionId}` : "No active session";
  const deviceLabel =
    trackStatus?.device ||
    devices.find((d) => d.address === selectedAddress)?.name ||
    "No sensor selected";
  const selectedRssi =
    devices.find((d) => d.address === (trackStatus?.address || selectedAddress))
      ?.rssi ?? null;

  return (
    <div className="min-h-screen bg-background text-on-surface flex flex-col font-body-md antialiased selection:bg-primary-container selection:text-white">
      {/* Left Sidebar */}
      <Sidebar
        activeModule="live-tracking"
        isOpen={isMobileNavOpen}
        onClose={() => setIsMobileNavOpen(false)}
      />

      {/* Main Content Area */}
      <div className="lg:pl-64 flex flex-col min-h-screen">
        {/* Top Fixed Header */}
        <Header
          currentUnit={currentUnit}
          onUnitChange={setCurrentUnit}
          onOpenMobileMenu={() => setIsMobileNavOpen(true)}
          deviceConnected={deviceConnected}
        />

        {/* Main Workspace Body */}
        <main className="relative pt-18 pb-12 w-full px-space-md sm:px-gutter-desktop min-h-[calc(100vh-3.5rem)] flex flex-col gap-space-lg">
          {/* Top Page Header & Live Session Status Strip */}
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md bg-surface-container-lowest p-space-md sm:p-space-lg rounded-xl shadow-xs border border-outline-variant/30">
            <div className="flex flex-col gap-space-xs">
              <div className="flex items-center gap-space-sm flex-wrap">
                <div className="flex items-center gap-2">
                  <Link
                    href="/dashboard"
                    className="p-1 rounded-md text-secondary hover:text-on-surface hover:bg-surface-container-low transition-colors"
                    title="Back to Dashboard"
                  >
                    <span className="material-symbols-outlined text-[20px]">
                      arrow_back
                    </span>
                  </Link>
                  <h1 className="font-headline-lg text-headline-lg text-on-surface">
                    Live Tracking
                  </h1>
                </div>
                <span
                  className={`inline-flex items-center gap-1.5 px-space-sm py-0.5 rounded-full font-label-sm text-label-sm ${
                    isStreaming
                      ? "bg-surface-container-low text-tertiary"
                      : "bg-surface-container text-secondary"
                  }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${
                      isStreaming
                        ? "bg-tertiary-container animate-ping"
                        : "bg-secondary"
                    }`}
                  ></span>
                  {statusLabel}
                </span>
              </div>

              <div className="flex items-center gap-space-xs font-body-sm text-body-sm text-on-surface-variant flex-wrap pl-8 sm:pl-0">
                <span className="text-tertiary flex items-center gap-1">
                  <span className="material-symbols-outlined text-[14px]">
                    cloud_download
                  </span>{" "}
                  {trackStatus?.connected ? "BLE connected" : "BLE offline"}
                </span>
                <span className="text-secondary">•</span>
                <span>{deviceLabel}</span>
                <span className="text-secondary">•</span>
                <span>{sessionTag}</span>
                {trackStatus?.ping_ms !== null && trackStatus?.ping_ms !== undefined && (
                  <>
                    <span className="text-secondary">•</span>
                    <span>Ping {Math.round(trackStatus.ping_ms)} ms</span>
                  </>
                )}
              </div>

              {/* Device picker + backend status (same card, no style change) */}
              <div className="flex items-center gap-space-xs font-body-sm text-body-sm text-on-surface-variant flex-wrap pl-8 sm:pl-0">
                <span className="text-secondary">Sensor:</span>
                {devices.length > 0 ? (
                  <select
                    value={selectedAddress}
                    onChange={(e) => setSelectedAddress(e.target.value)}
                    disabled={isStreaming}
                    className="px-2 py-1 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm border border-outline-variant/20 disabled:opacity-60"
                    aria-label="Connected sensor"
                  >
                    {devices.map((d) => (
                      <option key={d.address} value={d.address}>
                        {d.name} · {d.address}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Link href="/devices" className="text-primary underline">
                    No connected sensor — Scan & Connect on the Devices page
                  </Link>
                )}
                {!sessionId && !starting && devices.length > 0 && (
                  <button
                    onClick={handleStartTracking}
                    className="px-2 py-1 rounded-lg bg-surface-container-low text-primary font-label-sm text-label-sm border border-outline-variant/20 cursor-pointer"
                    type="button"
                  >
                    Start backend session
                  </button>
                )}
                {starting && <span className="text-secondary">Starting…</span>}
                {trackError && <span className="text-error">{trackError}</span>}
              </div>

              {liveHint && (
                <div className="flex items-start gap-2 px-space-sm py-1.5 rounded-lg bg-surface-container-low border border-outline-variant/20 font-body-sm text-body-sm text-on-surface-variant">
                  <span className="material-symbols-outlined text-primary text-[16px] leading-5">
                    info
                  </span>
                  <span>{liveHint}</span>
                </div>
              )}
            </div>

            {/* Stepper Top Bar Quick Controls */}
            <div className="flex items-center gap-space-sm self-start md:self-auto flex-wrap">
              <Link
                href={
                  processedSessionId
                    ? `/report?session=${encodeURIComponent(processedSessionId)}`
                    : "/report"
                }
                className="inline-flex items-center gap-1.5 px-space-md py-1.5 rounded-lg bg-surface-container-low text-primary hover:bg-surface-container transition-colors font-label-md text-label-md font-semibold cursor-pointer border border-outline-variant/20"
                title="Open the analysed report for this run"
              >
                <span className="material-symbols-outlined text-[16px]">
                  summarize
                </span>
                View Report
              </Link>

              <button
                onClick={() => setIsExportOpen(true)}
                className="inline-flex items-center gap-1.5 px-space-md py-1.5 rounded-lg bg-surface-container-lowest text-on-surface hover:bg-surface-container-low transition-colors shadow-xs border border-outline-variant/30 font-label-md text-label-md cursor-pointer"
                type="button"
              >
                <span className="material-symbols-outlined text-[16px] text-secondary">
                  file_download
                </span>
                Export Data
              </button>

              <button
                onClick={handleSaveSession}
                disabled={!sessionId || isStreaming}
                className="inline-flex items-center gap-1.5 px-space-md py-1.5 rounded-lg bg-surface-container-low text-secondary hover:text-on-surface hover:bg-surface-container transition-colors font-label-md text-label-md cursor-pointer border border-outline-variant/20 disabled:opacity-40 disabled:cursor-not-allowed"
                type="button"
                title={
                  isStreaming
                    ? "Stop tracking to finalize the summary"
                    : "Download the processed summary JSON"
                }
              >
                <span className="material-symbols-outlined text-[16px]">
                  save
                </span>
                {sessionSaved ? "Saved!" : "Save Summary"}
              </button>

              <button
                onClick={handleNewRun}
                disabled={!sessionId || !isStreaming}
                className="inline-flex items-center gap-1.5 px-space-md py-1.5 rounded-lg bg-surface-container-low text-secondary hover:text-on-surface hover:bg-surface-container transition-colors font-label-md text-label-md cursor-pointer border border-outline-variant/20 disabled:opacity-40 disabled:cursor-not-allowed"
                type="button"
                title="NEW: discard and re-arm for the next A-to-B"
              >
                <span className="material-symbols-outlined text-[16px]">
                  restart_alt
                </span>
                New Run
              </button>

              <button
                id="btn-stop-tracking"
                onClick={handleStopTrackingToggle}
                disabled={starting || stopping}
                className={`inline-flex items-center gap-1.5 px-space-md py-1.5 rounded-lg transition-colors font-label-md text-label-md font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-wait ${
                  isStreaming
                    ? "bg-error-container text-error hover:bg-error/15 border border-error/20"
                    : "bg-surface-container text-secondary hover:text-on-surface"
                }`}
                type="button"
              >
                <span className="material-symbols-outlined text-[16px]">
                  {isStreaming ? "stop_circle" : "play_circle"}
                </span>
                {starting
                  ? "Starting…"
                  : stopping
                    ? "Stopping…"
                    : isStreaming
                      ? "Stop Tracking"
                      : sessionId
                        ? "Start New Session"
                        : "Start Tracking"}
              </button>
            </div>
          </div>

          {/* Main Center Grid: 70% Viewport Left & 30% Diagnostics Right */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-lg items-start">
            {/* LEFT 70% Trajectory 3D Stage (lg:col-span-8) */}
            <div className="lg:col-span-8">
              <ThreeTrajectoryScene
                unit={currentUnit}
                unitMultiplier={unitMultipliers[currentUnit]}
                onPositionUpdate={setCurrentPos}
                isStreaming={isStreaming}
                livePoints={livePoints}
              />
            </div>

            {/* RIGHT 30% Scientific Measurement Pane (lg:col-span-4) */}
            <div className="lg:col-span-4">
              <DiagnosticsPane
                unit={currentUnit}
                unitMultiplier={unitMultipliers[currentUnit]}
                position={currentPos}
                isStreaming={isStreaming}
                liveDistanceM={trackStatus?.distance_cm != null ? trackStatus.distance_cm / 100 : null}
                livePathM={trackStatus?.path_length_cm != null ? trackStatus.path_length_cm / 100 : null}
                liveVelocityMps={trackStatus?.speed_mps ?? null}
                kinematicState={trackStatus?.motion ?? null}
              />
            </div>
          </div>

          {/* Bottom Kinematic Time-Series Visualization Strip */}
          <WaveformChart
            isStreaming={isStreaming}
            samples={waveSamples}
            liveSampleCount={trackStatus?.sample_count ?? null}
            liveRateHz={trackStatus?.rate_hz ?? null}
            liveRssiDbm={selectedRssi}
            startedAt={trackStatus?.started_at ?? null}
            qualityFlags={trackStatus?.quality_flags ?? []}
          />
        </main>
      </div>

      {/* Export Data Modal */}
      <ExportModal
        isOpen={isExportOpen}
        onClose={() => setIsExportOpen(false)}
        sessionId={sessionId}
      />
    </div>
  );
}
