"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Sidebar } from "@/components/Sidebar";
import { Header, UnitType } from "@/components/Header";
import { useAuth } from "@/components/AuthProvider";
import { displayName, initials } from "@/lib/auth";
import {
  apiErrorMessage,
  apiFetch,
  downloadArtifact,
  downloadRawArtifact,
  type ArtifactFormat,
  type RawArtifactFormat,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// Types returned by the local API
// ---------------------------------------------------------------------------
interface ProcessedSession {
  session_id: string;
  motion: string;
  straight_line_distance_cm: number | null;
  trajectory_length_cm: number | null;
  quality_status: string | null;
  movement_duration_s: number | null;
  source_session_id: string | null;
  analysed_for?: { id?: string; name?: string; email?: string } | null;
  mtime: number;
}

interface SessionSummary {
  session_id?: string;
  state?: string;
  point_a_cm?: number[] | null;
  point_b_cm?: number[] | null;
  straight_line_distance_cm?: number | null;
  horizontal_distance_cm?: number | null;
  trajectory_length_cm?: number | null;
  horizontal_path_length_cm?: number | null;
  vertical_displacement_cm?: number | null;
  peak_height_cm?: number | null;
  movement_duration_s?: number | null;
  mean_speed_mps?: number | null;
  max_speed_mps?: number | null;
  max_acceleration_mps2?: number | null;
  efficiency?: number | null;
  final_velocity_drift_mps?: number | null;
  motion?: string;
  n_bounces?: number | null;
  quality_status?: string | null;
  quality_flags?: string[];
  gravity_magnitude_mps2?: number | null;
  gravity_compensation_status?: string | null;
  validation_status?: string | null;
  sample_rate_hz?: number | null;
  processing_version?: string | null;
  mode?: string | null;
  source_session_id?: string | null;
  analysed_for?: { id?: string; name?: string; email?: string } | null;
  baseline?: { gyro_bias_dps?: number[] | null } | null;
}

interface LocalSession {
  session_id: string;
  address?: string;
  device?: string;
  rate_hz?: number;
  started_at?: string;
  finished_at?: string | null;
  status?: string;
  sample_count?: number;
  raw?: Record<string, string>;
  processed?: Array<{ session_id: string }>;
}

const MOTION_LABELS: Record<string, string> = {
  move: "Movement",
  throw: "Throw",
  dribble: "Dribble",
  still: "Still",
};

function motionLabel(motion: string | null | undefined): string {
  if (!motion) return "Unclassified";
  return MOTION_LABELS[motion] ?? motion;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins.toString().padStart(2, "0")}:${secs.padStart(4, "0")}`;
}

function formatMetric(
  value: number | null | undefined,
  digits = 2,
  suffix = "",
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}${suffix}`;
}

function formatStamp(value: number | string | null | undefined): {
  date: string;
  time: string;
} {
  if (value === null || value === undefined) return { date: "—", time: "" };
  const when = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(when.getTime())) return { date: "—", time: "" };
  return {
    date: when.toLocaleDateString([], {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }),
    time: when.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
  };
}

function qualityBadge(status: string | null | undefined): string {
  switch (status) {
    case "GOOD":
      return "bg-surface-container-high text-tertiary";
    case "WARNING":
      return "bg-surface-container-high text-secondary";
    case "ERROR":
      return "bg-error-container text-on-error-container";
    default:
      return "bg-surface-container text-secondary";
  }
}

function qualityDot(status: string | null | undefined): string {
  switch (status) {
    case "GOOD":
      return "bg-tertiary-container";
    case "WARNING":
      return "bg-secondary";
    case "ERROR":
      return "bg-error";
    default:
      return "bg-outline";
  }
}

const UNIT_MULTIPLIERS: Record<UnitType, number> = {
  m: 1,
  cm: 100,
  in: 39.3701,
  ft: 3.28084,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ReportPage() {
  const { user, loading: authLoading } = useAuth();
  const [currentUnit, setCurrentUnit] = useState<UnitType>("m");
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  const [sessions, setSessions] = useState<ProcessedSession[]>([]);
  const [localSessions, setLocalSessions] = useState<LocalSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deviceConnected, setDeviceConnected] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);

  const [searchTerm, setSearchTerm] = useState("");
  const [qualityFilter, setQualityFilter] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<Record<string, SessionSummary>>({});
  const [summaryErrors, setSummaryErrors] = useState<Record<string, string>>({});

  const [downloading, setDownloading] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [massInput, setMassInput] = useState("1");

  const multiplier = UNIT_MULTIPLIERS[currentUnit];

  const formatDistance = useCallback(
    (cm: number | null | undefined): string => {
      if (cm === null || cm === undefined || !Number.isFinite(cm)) return "—";
      return `${((cm / 100) * multiplier).toFixed(2)}`;
    },
    [multiplier],
  );

  // Fetch reports, raw captures, and device state. State updates happen in the
  // promise callback so the effect body itself stays side-effect free.
  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      apiFetch<{ sessions: ProcessedSession[] }>("/api/sessions?limit=200"),
      apiFetch<{ sessions: LocalSession[] }>("/api/local/sessions?limit=200"),
      apiFetch<{ connected_count: number }>("/api/devices"),
    ]).then(([processedResult, localResult, deviceResult]) => {
      if (cancelled) return;

      if (processedResult.status === "rejected") {
        setError(
          apiErrorMessage(
            processedResult.reason,
            "Could not load processed reports from the backend.",
          ),
        );
        setSessions([]);
      } else {
        setSessions(processedResult.value.sessions);
        setError(null);
      }

      setLocalSessions(
        localResult.status === "fulfilled" ? localResult.value.sessions : [],
      );
      setDeviceConnected(
        deviceResult.status === "fulfilled"
          ? deviceResult.value.connected_count > 0
          : false,
      );
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [refreshToken]);

  // Only show reports owned by the signed-in user. Reports from before user
  // tagging existed have no owner and remain visible as local legacy data.
  const ownedSessions = useMemo(() => {
    if (!user) return sessions;
    return sessions.filter((session) => {
      const owner = session.analysed_for;
      return !owner?.id || owner.id === user.id;
    });
  }, [sessions, user]);

  // Selection is derived: an explicit click wins, then a ?session= link from
  // Live Tracking, then the newest report.
  const requestedId = useMemo(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("session");
  }, []);

  const activeId =
    selectedId && ownedSessions.some((session) => session.session_id === selectedId)
      ? selectedId
      : requestedId &&
          ownedSessions.some((session) => session.session_id === requestedId)
        ? requestedId
        : ownedSessions[0]?.session_id ?? null;

  const summary = activeId ? summaries[activeId] ?? null : null;
  const summaryError = activeId ? summaryErrors[activeId] ?? null : null;
  const summaryLoading = Boolean(activeId) && !summary && !summaryError;

  // Lazily load (and cache) the full summary for the active report.
  useEffect(() => {
    if (!activeId || summaries[activeId] || summaryErrors[activeId]) return;
    let cancelled = false;
    apiFetch<SessionSummary>(
      `/api/sessions/${encodeURIComponent(activeId)}/export?format=json`,
    )
      .then((data) => {
        if (cancelled) return;
        setSummaries((current) => ({ ...current, [activeId]: data }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setSummaryErrors((current) => ({
          ...current,
          [activeId]: apiErrorMessage(err, "Could not load the processed summary."),
        }));
      });
    return () => {
      cancelled = true;
    };
  }, [activeId, summaries, summaryErrors]);

  const handleRefresh = () => {
    setLoading(true);
    setRefreshToken((token) => token + 1);
  };

  const filteredSessions = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return ownedSessions.filter((session) => {
      if (
        qualityFilter !== "all" &&
        (session.quality_status ?? "unknown").toUpperCase() !==
          qualityFilter.toUpperCase()
      ) {
        return false;
      }
      if (!term) return true;
      return (
        session.session_id.toLowerCase().includes(term) ||
        (session.source_session_id ?? "").toLowerCase().includes(term) ||
        (session.motion ?? "").toLowerCase().includes(term) ||
        (session.quality_status ?? "").toLowerCase().includes(term)
      );
    });
  }, [ownedSessions, searchTerm, qualityFilter]);

  const selectedSession =
    ownedSessions.find((session) => session.session_id === activeId) ?? null;

  const rawSession = useMemo(() => {
    const sourceId = summary?.source_session_id ?? selectedSession?.source_session_id;
    if (sourceId) {
      const match = localSessions.find((item) => item.session_id === sourceId);
      if (match) return match;
    }
    return localSessions.find((item) => item.session_id === activeId) ?? null;
  }, [summary, selectedSession, localSessions, activeId]);

  const massKg = Number(massInput);
  const movingMassKg = Number.isFinite(massKg) && massKg > 0 ? massKg : 1;
  const peakAcceleration = summary?.max_acceleration_mps2 ?? null;
  const peakForceN =
    peakAcceleration !== null &&
    peakAcceleration !== undefined &&
    Number.isFinite(peakAcceleration)
      ? peakAcceleration * movingMassKg
      : null;

  const goodCount = ownedSessions.filter(
    (session) => (session.quality_status ?? "").toUpperCase() === "GOOD",
  ).length;

  const activeDistanceCm =
    summary?.straight_line_distance_cm ??
    selectedSession?.straight_line_distance_cm ??
    null;

  const handleDownload = async (
    kind: "processed" | "raw",
    id: string,
    format: ArtifactFormat | RawArtifactFormat,
  ) => {
    const key = `${kind}:${id}:${format}`;
    setDownloading(key);
    setDownloadError(null);
    try {
      if (kind === "processed") {
        await downloadArtifact(id, format as ArtifactFormat);
      } else {
        await downloadRawArtifact(id, format as RawArtifactFormat);
      }
    } catch (err) {
      setDownloadError(apiErrorMessage(err, "Could not download this file."));
    } finally {
      setDownloading(null);
    }
  };

  const summaryQuality =
    summary?.quality_status ?? selectedSession?.quality_status ?? null;
  const summaryDate = formatStamp(selectedSession?.mtime);

  return (
    <div className="min-h-screen bg-background text-on-surface flex flex-col font-body-md antialiased selection:bg-primary-container selection:text-white">
      <Sidebar
        activeModule="report"
        isOpen={isMobileNavOpen}
        onClose={() => setIsMobileNavOpen(false)}
      />

      <div className="lg:pl-64 flex flex-col min-h-screen">
        <Header
          currentUnit={currentUnit}
          onUnitChange={setCurrentUnit}
          onOpenMobileMenu={() => setIsMobileNavOpen(true)}
          deviceConnected={deviceConnected}
        />

        <main className="w-full pt-18 px-space-md sm:px-gutter-desktop pb-margin-desktop bg-background flex flex-col gap-space-lg">
          {/* Page header */}
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md pt-space-xs">
            <div className="flex flex-col gap-space-xs">
              <div className="flex items-center gap-space-sm flex-wrap">
                <h1 className="font-display-lg text-display-lg text-on-surface tracking-tight">
                  Analysis Report
                </h1>
                <span className="px-2 py-0.5 rounded bg-surface-container-high text-primary font-label-sm text-label-sm font-semibold">
                  Processed A→B
                </span>
              </div>

              <div className="flex items-center gap-space-sm w-fit rounded-lg bg-surface-container-lowest border border-outline-variant/30 px-space-sm py-1.5 shadow-xs">
                <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-on-primary font-label-sm text-label-sm font-semibold">
                  {authLoading ? "…" : initials(user)}
                </div>
                <div className="flex flex-col leading-tight">
                  <span className="font-label-md text-label-md font-semibold text-on-surface">
                    {authLoading ? "Loading…" : displayName(user)}
                  </span>
                  <span className="text-[11px] text-secondary">
                    {authLoading
                      ? "Checking session"
                      : user?.email ?? "Local account"}
                  </span>
                </div>
              </div>

              <p className="font-body-sm text-body-sm text-on-surface-variant">
                Distance from Point A to Point B, estimated movement force and
                quality for every live tracking run.
              </p>
            </div>

            <div className="flex items-center gap-space-sm self-start md:self-auto flex-wrap">
              <button
                onClick={handleRefresh}
                disabled={loading}
                className="inline-flex items-center gap-space-xs px-3.5 py-2 rounded-lg bg-surface-container-lowest hover:bg-surface-container-low text-on-surface transition-all shadow-xs border border-outline-variant/30 font-label-md text-label-md cursor-pointer disabled:opacity-50 disabled:cursor-wait"
                type="button"
              >
                <span className="material-symbols-outlined text-[18px] text-secondary">
                  refresh
                </span>
                <span>{loading ? "Refreshing…" : "Refresh"}</span>
              </button>
              <Link
                href="/live-tracking"
                className="inline-flex items-center gap-space-xs px-4 py-2 rounded-lg bg-primary-container hover:bg-primary text-on-primary transition-all shadow-sm font-label-md text-label-md font-semibold cursor-pointer"
              >
                <span className="material-symbols-outlined text-[18px]">
                  play_arrow
                </span>
                <span>Live Tracking</span>
              </Link>
            </div>
          </div>

          {/* Summary metrics */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-space-md">
            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Analysed Sessions
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  summarize
                </span>
              </div>
              <div className="my-space-sm flex items-baseline gap-space-xs">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {ownedSessions.length}
                </span>
                <span className="font-body-md text-body-md text-on-surface-variant font-medium">
                  reports
                </span>
              </div>
              <div className="pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                Processed trajectory summaries
              </div>
            </div>

            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Good Quality
                </span>
                <span className="material-symbols-outlined text-[20px] text-tertiary">
                  check_circle
                </span>
              </div>
              <div className="my-space-sm flex items-baseline gap-space-xs">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {goodCount}
                </span>
                <span className="font-body-md text-body-md text-on-surface-variant font-medium">
                  / {ownedSessions.length}
                </span>
              </div>
              <div className="pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                Passed IMU quality assessment
              </div>
            </div>

            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Point A → B
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  straighten
                </span>
              </div>
              <div className="my-space-sm flex items-baseline gap-space-xs">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {activeDistanceCm === null
                    ? "—"
                    : formatDistance(activeDistanceCm)}
                </span>
                <span className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                  {currentUnit}
                </span>
              </div>
              <div className="pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20 truncate">
                {selectedSession ? selectedSession.session_id : "No report selected"}
              </div>
            </div>

            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Peak Force (est.)
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  bolt
                </span>
              </div>
              <div className="my-space-sm flex items-baseline gap-space-xs">
                <span className="font-metric-display text-metric-display text-on-surface">
                  {formatMetric(peakForceN, 1)}
                </span>
                <span className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                  N
                </span>
              </div>
              <div className="pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                mass × peak acceleration · {movingMassKg} kg
              </div>
            </div>
          </div>

          {error && (
            <div className="rounded-lg border border-error/30 bg-error-container/40 px-space-md py-2 text-body-sm text-on-error-container">
              {error}
            </div>
          )}
          {downloadError && (
            <div className="rounded-lg border border-error/30 bg-error-container/40 px-space-md py-2 text-body-sm text-on-error-container">
              {downloadError}
            </div>
          )}

          {/* Report index + selected report detail */}
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-space-lg items-start">
            {/* Index */}
            <div className="xl:col-span-5 bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 overflow-hidden flex flex-col">
              <div className="p-space-lg border-b border-outline-variant/20 flex flex-col gap-space-md">
                <div className="flex items-center justify-between gap-space-sm">
                  <h2 className="font-headline-md text-headline-md text-on-surface">
                    Report Index
                  </h2>
                  <span className="px-2.5 py-0.5 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm font-semibold">
                    {filteredSessions.length} / {ownedSessions.length}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-space-sm">
                  <div className="relative flex items-center flex-1 min-w-40">
                    <span className="material-symbols-outlined absolute left-2.5 text-[18px] text-outline pointer-events-none">
                      search
                    </span>
                    <input
                      value={searchTerm}
                      onChange={(event) => setSearchTerm(event.target.value)}
                      className="pl-8 pr-3 py-1.5 w-full rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40 border border-outline-variant/20 transition-all"
                      placeholder="Filter session, motion, quality…"
                      type="text"
                      aria-label="Filter reports"
                    />
                  </div>
                  <select
                    value={qualityFilter}
                    onChange={(event) => setQualityFilter(event.target.value)}
                    className="px-2.5 py-1.5 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm border border-outline-variant/20"
                    aria-label="Filter by quality"
                  >
                    <option value="all">All quality</option>
                    <option value="good">Good</option>
                    <option value="warning">Warning</option>
                    <option value="error">Error</option>
                  </select>
                </div>
              </div>

              <div className="max-h-[640px] overflow-y-auto divide-y divide-outline-variant/20">
                {loading && (
                  <p className="py-10 px-space-lg text-center text-secondary font-body-sm text-body-sm">
                    Loading analysed reports…
                  </p>
                )}
                {!loading && filteredSessions.length === 0 && (
                  <div className="py-10 px-space-lg text-center text-secondary font-body-sm text-body-sm flex flex-col items-center gap-space-sm">
                    <span className="material-symbols-outlined text-[28px] text-outline">
                      lab_profile
                    </span>
                    <span>
                      No processed reports match this filter. Finish a live
                      tracking run or clear the filters.
                    </span>
                  </div>
                )}
                {!loading &&
                  filteredSessions.map((session) => {
                    const stamp = formatStamp(session.mtime);
                    const isSelected = session.session_id === activeId;
                    return (
                      <button
                        key={session.session_id}
                        onClick={() => setSelectedId(session.session_id)}
                        className={`w-full text-left px-space-lg py-3.5 transition-colors cursor-pointer flex items-center justify-between gap-space-md ${
                          isSelected
                            ? "bg-primary/5 border-l-2 border-primary"
                            : "hover:bg-surface-container-low/60 border-l-2 border-transparent"
                        }`}
                        type="button"
                      >
                        <div className="flex items-start gap-space-sm min-w-0">
                          <span
                            className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${qualityDot(
                              session.quality_status,
                            )}`}
                          ></span>
                          <div className="min-w-0">
                            <div className="font-metric-code text-[13px] font-semibold text-on-surface truncate">
                              {session.session_id}
                            </div>
                            <div className="font-body-sm text-body-sm text-on-surface-variant truncate">
                              {motionLabel(session.motion)} · {stamp.date}{" "}
                              {stamp.time}
                            </div>
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="font-metric-code text-[13px] text-primary font-semibold">
                            {formatDistance(session.trajectory_length_cm)}{" "}
                            {currentUnit}
                          </div>
                          <div
                            className={`inline-flex mt-0.5 px-1.5 py-0.5 rounded font-label-sm text-[10px] font-semibold ${qualityBadge(
                              session.quality_status,
                            )}`}
                          >
                            {session.quality_status ?? "UNKNOWN"}
                          </div>
                        </div>
                      </button>
                    );
                  })}
              </div>
            </div>

            {/* Detail */}
            <div className="xl:col-span-7 bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 overflow-hidden">
              {!selectedSession && !loading && (
                <div className="p-space-lg py-16 flex flex-col items-center gap-space-sm text-center text-secondary font-body-sm text-body-sm">
                  <span className="material-symbols-outlined text-[34px] text-outline">
                    lab_profile
                  </span>
                  <span>Select a report to inspect its analysed data.</span>
                </div>
              )}

              {selectedSession && (
                <div className="flex flex-col">
                  {/* Detail header */}
                  <div className="p-space-lg border-b border-outline-variant/20 flex flex-col lg:flex-row lg:items-start justify-between gap-space-md">
                    <div className="flex flex-col gap-space-xs min-w-0">
                      <div className="flex items-center gap-space-sm flex-wrap">
                        <h2 className="font-headline-md text-headline-md text-on-surface font-metric-code">
                          {selectedSession.session_id}
                        </h2>
                        <span
                          className={`px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold ${qualityBadge(
                            summaryQuality,
                          )}`}
                        >
                          {summaryQuality ?? "UNKNOWN"}
                        </span>
                      </div>
                      <div className="flex items-center gap-space-xs flex-wrap font-body-sm text-body-sm text-on-surface-variant">
                        <span className="font-semibold text-on-surface">
                          {summary?.analysed_for?.name ??
                            (authLoading ? "…" : displayName(user))}
                        </span>
                        <span className="text-secondary">•</span>
                        <span>{motionLabel(summary?.motion ?? selectedSession.motion)}</span>
                        <span className="text-secondary">•</span>
                        <span>
                          {summaryDate.date} {summaryDate.time}
                        </span>
                        {selectedSession.source_session_id && (
                          <>
                            <span className="text-secondary">•</span>
                            <span className="font-metric-code text-[11px]">
                              raw: {selectedSession.source_session_id}
                            </span>
                          </>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-space-xs flex-wrap shrink-0">
                      {(
                        [
                          { id: "csv", label: "CSV" },
                          { id: "json", label: "JSON" },
                          { id: "mat", label: "MAT" },
                        ] as const
                      ).map((option) => {
                        const key = `processed:${selectedSession.session_id}:${option.id}`;
                        return (
                          <button
                            key={option.id}
                            onClick={() =>
                              void handleDownload(
                                "processed",
                                selectedSession.session_id,
                                option.id,
                              )
                            }
                            disabled={downloading !== null}
                            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-container-low hover:bg-surface-container text-on-surface font-label-sm text-label-sm border border-outline-variant/20 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-wait"
                            type="button"
                            title={`Download processed ${option.label}`}
                          >
                            <span className="material-symbols-outlined text-[15px] text-secondary">
                              {downloading === key ? "hourglass_top" : "download"}
                            </span>
                            {option.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {summaryLoading && (
                    <p className="py-16 px-space-lg text-center text-secondary font-body-sm text-body-sm">
                      Loading analysed summary…
                    </p>
                  )}

                  {!summaryLoading && summaryError && (
                    <p className="m-space-lg rounded-lg border border-error/30 bg-error-container/40 px-space-md py-2 text-body-sm text-on-error-container">
                      {summaryError}
                    </p>
                  )}

                  {!summaryLoading && summary && (
                    <div className="p-space-lg flex flex-col gap-space-lg">
                      {/* Headline analysis: A→B distance + estimated force */}
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-space-md">
                        <div className="rounded-xl bg-primary/5 border border-primary/25 p-space-md flex flex-col gap-1">
                          <div className="flex items-center justify-between text-primary">
                            <span className="font-label-sm text-[11px] uppercase tracking-wider font-semibold">
                              Point A → Point B
                            </span>
                            <span className="material-symbols-outlined text-[18px]">
                              straighten
                            </span>
                          </div>
                          <div className="font-metric-code text-[30px] font-semibold text-on-surface leading-tight">
                            {formatDistance(summary.straight_line_distance_cm)}{" "}
                            <span className="text-[14px] font-normal text-secondary">
                              {currentUnit}
                            </span>
                          </div>
                          <span className="text-[11px] text-secondary">
                            Straight-line displacement covered during the
                            movement.
                          </span>
                        </div>

                        <div className="rounded-xl bg-surface-container-low border border-outline-variant/25 p-space-md flex flex-col gap-1">
                          <div className="flex items-center justify-between text-primary">
                            <span className="font-label-sm text-[11px] uppercase tracking-wider font-semibold">
                              Peak Force (estimated)
                            </span>
                            <span className="material-symbols-outlined text-[18px]">
                              bolt
                            </span>
                          </div>
                          <div className="font-metric-code text-[30px] font-semibold text-on-surface leading-tight">
                            {formatMetric(peakForceN, 1)}{" "}
                            <span className="text-[14px] font-normal text-secondary">
                              N
                            </span>
                          </div>
                          <div className="flex items-center gap-space-xs flex-wrap text-[11px] text-secondary">
                            <label htmlFor="moving-mass">Moving mass</label>
                            <input
                              id="moving-mass"
                              type="number"
                              min="0.001"
                              step="0.01"
                              value={massInput}
                              onChange={(event) =>
                                setMassInput(event.target.value)
                              }
                              className="w-20 px-2 py-0.5 rounded bg-surface-container-lowest border border-outline-variant/30 font-metric-code text-[11px] text-on-surface"
                            />
                            <span>kg</span>
                            <span className="text-outline">
                              · peak accel{" "}
                              {formatMetric(summary.max_acceleration_mps2, 1)}{" "}
                              m/s²
                            </span>
                          </div>
                          <span className="text-[10px] text-outline">
                            F = mass × gravity-compensated peak acceleration.
                            Adjust the mass for the thrown object or body
                            segment.
                          </span>
                        </div>
                      </div>

                      {/* Primary distance metrics */}
                      <section className="flex flex-col gap-space-sm">
                        <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
                          Distance &amp; Displacement
                        </h3>
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-space-sm">
                          {[
                            {
                              label: "Path length",
                              value: formatDistance(summary.trajectory_length_cm),
                              suffix: currentUnit,
                              hint: "Arc length",
                            },
                            {
                              label: "Horizontal",
                              value: formatDistance(summary.horizontal_distance_cm),
                              suffix: currentUnit,
                              hint: "Ground plane",
                            },
                            {
                              label: "Vertical",
                              value: formatDistance(
                                summary.vertical_displacement_cm,
                              ),
                              suffix: currentUnit,
                              hint: "Height change",
                            },
                            {
                              label: "Peak height",
                              value: formatDistance(summary.peak_height_cm),
                              suffix: currentUnit,
                              hint: "Above Point A",
                            },
                            {
                              label: "Efficiency",
                              value: formatMetric(
                                summary.efficiency !== null &&
                                  summary.efficiency !== undefined
                                  ? summary.efficiency * 100
                                  : null,
                                1,
                              ),
                              suffix: "%",
                              hint: "Displacement / path",
                            },
                          ].map((metric) => (
                            <div
                              key={metric.label}
                              className="rounded-lg bg-surface-container-low border border-outline-variant/20 p-space-md flex flex-col gap-0.5"
                            >
                              <span className="font-label-sm text-[11px] uppercase tracking-wide text-on-surface-variant font-semibold">
                                {metric.label}
                              </span>
                              <span className="font-metric-code text-[20px] font-semibold text-on-surface">
                                {metric.value}{" "}
                                <span className="text-[12px] font-normal text-secondary">
                                  {metric.suffix}
                                </span>
                              </span>
                              <span className="text-[10px] text-outline">
                                {metric.hint}
                              </span>
                            </div>
                          ))}
                        </div>
                      </section>

                      {/* Motion metrics */}
                      <section className="flex flex-col gap-space-sm">
                        <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
                          Motion Analysis
                        </h3>
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-space-sm">
                          {[
                            {
                              label: "Duration",
                              value: formatDuration(summary.movement_duration_s),
                              suffix: "",
                            },
                            {
                              label: "Mean speed",
                              value: formatMetric(summary.mean_speed_mps, 2),
                              suffix: "m/s",
                            },
                            {
                              label: "Max speed",
                              value: formatMetric(summary.max_speed_mps, 2),
                              suffix: "m/s",
                            },
                            {
                              label: "Max acceleration",
                              value: formatMetric(
                                summary.max_acceleration_mps2,
                                1,
                              ),
                              suffix: "m/s²",
                            },
                            {
                              label: "Final velocity drift",
                              value: formatMetric(
                                summary.final_velocity_drift_mps,
                                2,
                              ),
                              suffix: "m/s",
                            },
                            {
                              label: "Bounces",
                              value: formatMetric(summary.n_bounces, 0),
                              suffix: "",
                            },
                          ].map((metric) => (
                            <div
                              key={metric.label}
                              className="rounded-lg bg-surface-container-low border border-outline-variant/20 p-space-md flex flex-col gap-0.5"
                            >
                              <span className="font-label-sm text-[11px] uppercase tracking-wide text-on-surface-variant font-semibold">
                                {metric.label}
                              </span>
                              <span className="font-metric-code text-[18px] font-semibold text-on-surface">
                                {metric.value}{" "}
                                {metric.suffix && (
                                  <span className="text-[11px] font-normal text-secondary">
                                    {metric.suffix}
                                  </span>
                                )}
                              </span>
                            </div>
                          ))}
                        </div>
                      </section>

                      {/* Quality flags */}
                      <section className="flex flex-col gap-space-sm">
                        <div className="flex items-center justify-between">
                          <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
                            Quality Assessment
                          </h3>
                          <span className="font-metric-code text-[11px] text-secondary">
                            validation: {summary.validation_status ?? "—"}
                          </span>
                        </div>
                        {summary.quality_flags && summary.quality_flags.length > 0 ? (
                          <div className="flex flex-wrap gap-space-xs">
                            {summary.quality_flags.map((flag) => (
                              <span
                                key={flag}
                                className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-error-container/40 text-on-error-container font-metric-code text-[11px] border border-error/20"
                              >
                                <span className="material-symbols-outlined text-[13px]">
                                  warning
                                </span>
                                {flag}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="rounded-lg bg-surface-container-low border border-outline-variant/20 px-space-md py-2 font-body-sm text-body-sm text-on-surface-variant inline-flex items-center gap-1.5">
                            <span className="material-symbols-outlined text-[16px] text-tertiary">
                              check_circle
                            </span>
                            No quality flags raised for this run.
                          </p>
                        )}
                      </section>

                      {/* Pipeline / sensor */}
                      <section className="flex flex-col gap-space-sm">
                        <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
                          Sensor &amp; Pipeline
                        </h3>
                        <div className="rounded-lg bg-surface-container-low border border-outline-variant/20 p-space-md grid grid-cols-2 lg:grid-cols-4 gap-space-md">
                          <div>
                            <div className="text-[10px] uppercase text-outline font-semibold">
                              Sample rate
                            </div>
                            <div className="font-metric-code text-[13px] text-on-surface">
                              {summary.sample_rate_hz ?? "—"} Hz
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] uppercase text-outline font-semibold">
                              Processing
                            </div>
                            <div className="font-metric-code text-[13px] text-on-surface">
                              v{summary.processing_version ?? "—"}
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] uppercase text-outline font-semibold">
                              Gravity
                            </div>
                            <div className="font-metric-code text-[13px] text-on-surface">
                              {formatMetric(summary.gravity_magnitude_mps2, 2)} m/s²
                            </div>
                          </div>
                          <div>
                            <div className="text-[10px] uppercase text-outline font-semibold">
                              Compensation
                            </div>
                            <div className="font-metric-code text-[13px] text-on-surface">
                              {summary.gravity_compensation_status ?? "—"}
                            </div>
                          </div>
                          <div className="col-span-2 lg:col-span-4">
                            <div className="text-[10px] uppercase text-outline font-semibold">
                              Point A → B
                            </div>
                            <div className="font-metric-code text-[11px] text-on-surface-variant break-all">
                              {summary.point_a_cm
                                ? `A [${summary.point_a_cm
                                    .map((v) => v.toFixed(1))
                                    .join(", ")}] cm`
                                : "A —"}{" "}
                              →{" "}
                              {summary.point_b_cm
                                ? `B [${summary.point_b_cm
                                    .map((v) => v.toFixed(1))
                                    .join(", ")}] cm`
                                : "B —"}
                            </div>
                          </div>
                        </div>
                      </section>

                      {/* Raw capture linked to this processed report */}
                      {rawSession && rawSession.raw && (
                        <section className="flex flex-col gap-space-sm">
                          <div className="flex items-center justify-between flex-wrap gap-space-sm">
                            <h3 className="font-label-sm text-label-sm uppercase tracking-wider text-outline font-semibold">
                              Raw Capture · {rawSession.session_id}
                            </h3>
                            <span className="font-metric-code text-[11px] text-outline">
                              {rawSession.sample_count ?? "—"} samples ·{" "}
                              {rawSession.rate_hz ?? "—"} Hz
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-space-xs">
                            {(
                              [
                                { id: "raw_csv", label: "Raw CSV" },
                                { id: "raw_json", label: "Raw JSON" },
                                { id: "raw_bin", label: "Raw BIN" },
                              ] as const
                            )
                              .filter((option) =>
                                Boolean(
                                  rawSession.raw?.[
                                    option.id.replace("raw_", "")
                                  ],
                                ),
                              )
                              .map((option) => {
                              const key = `raw:${rawSession.session_id}:${option.id}`;
                              return (
                                <button
                                  key={option.id}
                                  onClick={() =>
                                    void handleDownload(
                                      "raw",
                                      rawSession.session_id,
                                      option.id,
                                    )
                                  }
                                  disabled={downloading !== null}
                                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-surface-container-low hover:bg-surface-container text-on-surface font-label-sm text-label-sm border border-outline-variant/20 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-wait"
                                  type="button"
                                >
                                  <span className="material-symbols-outlined text-[15px] text-secondary">
                                    {downloading === key
                                      ? "hourglass_top"
                                      : "download"}
                                  </span>
                                  {option.label}
                                </button>
                              );
                            })}
                          </div>
                        </section>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
