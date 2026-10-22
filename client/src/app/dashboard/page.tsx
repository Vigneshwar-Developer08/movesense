"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Header, UnitType } from "@/components/Header";
import { ExportModal } from "@/components/ExportModal";
import { apiErrorMessage, apiFetch } from "@/lib/api";

interface ApiSession {
  session_id: string;
  motion: string;
  straight_line_distance_cm: number | null;
  trajectory_length_cm: number | null;
  quality_status: string | null;
  movement_duration_s: number | null;
  mtime: number;
}

interface SessionRecord {
  id: string;
  sessionId: string;
  protocol: string;
  subjectName: string;
  subjectNote: string;
  time: string;
  date: string;
  device: string;
  duration: string;
  trajectoryDist: number | null; // meters
  displacementDist: number | null; // meters
  quality: string;
  activeColor: string;
  mtimeMs: number;
}

const MOTION_LABELS: Record<string, string> = {
  move: "Movement",
  throw: "Throw",
  dribble: "Dribble",
  still: "Still",
};

function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins.toString().padStart(2, "0")}:${secs.padStart(4, "0")}`;
}

function toRecord(session: ApiSession): SessionRecord {
  const when = new Date(session.mtime * 1000);
  const quality = session.quality_status ?? "UNKNOWN";
  return {
    id: session.session_id,
    sessionId: session.session_id,
    protocol: MOTION_LABELS[session.motion] ?? session.motion,
    subjectName: "—",
    subjectNote: "Processed A→B summary",
    time: when.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
    date: when.toLocaleDateString([], {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }),
    device: "Movesense IMU",
    duration: formatDuration(session.movement_duration_s),
    trajectoryDist:
      session.trajectory_length_cm === null
        ? null
        : session.trajectory_length_cm / 100,
    displacementDist:
      session.straight_line_distance_cm === null
        ? null
        : session.straight_line_distance_cm / 100,
    quality,
    activeColor:
      quality === "GOOD"
        ? "bg-primary"
        : quality === "WARNING"
          ? "bg-secondary"
          : "bg-error",
    mtimeMs: session.mtime * 1000,
  };
}

export default function DashboardPage() {
  const router = useRouter();
  const [currentUnit, setCurrentUnit] = useState<UnitType>("m");
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isExportOpen, setIsExportOpen] = useState(false);
  const [exportSessionId, setExportSessionId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [dateFilter, setDateFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deviceConnected, setDeviceConnected] = useState(false);
  const [nowMs, setNowMs] = useState<number | null>(null);

  useEffect(() => {
    // Read the clock after mount so server and client renders stay identical.
    const timer = setTimeout(() => setNowMs(Date.now()), 0);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch<{ sessions: ApiSession[] }>("/api/sessions?limit=100"),
      apiFetch<{ connected_count: number }>("/api/devices"),
    ])
      .then(([sessionData, deviceData]) => {
        if (cancelled) return;
        setSessions(sessionData.sessions.map(toRecord));
        setDeviceConnected(deviceData.connected_count > 0);
        setError(null);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            apiErrorMessage(err, "Could not load sessions from the backend."),
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Unit conversion multiplier relative to meters
  const unitMultipliers: Record<UnitType, number> = {
    m: 1,
    cm: 100,
    in: 39.3701,
    ft: 3.28084,
  };

  const multiplier = unitMultipliers[currentUnit];

  const formatDistance = (valMeters: number | null) => {
    if (valMeters === null) return "—";
    return (valMeters * multiplier).toFixed(2);
  };

  const openExport = (sessionId?: string) => {
    setExportSessionId(sessionId ?? null);
    setIsExportOpen(true);
  };

  const closeExport = () => {
    setIsExportOpen(false);
    setExportSessionId(null);
  };

  const filteredSessions = useMemo(() => {
    const term = searchTerm.toLowerCase();
    let yesterdayStartMs = 0;
    if (nowMs !== null) {
      const todayStart = new Date(nowMs);
      todayStart.setHours(0, 0, 0, 0);
      yesterdayStartMs = todayStart.getTime() - 24 * 60 * 60 * 1000;
    }

    return sessions.filter((s) => {
      const matchesTerm =
        !term ||
        s.protocol.toLowerCase().includes(term) ||
        s.subjectName.toLowerCase().includes(term) ||
        s.sessionId.toLowerCase().includes(term) ||
        s.quality.toLowerCase().includes(term);
      if (!matchesTerm) return false;
      if (dateFilter === "today" && nowMs !== null) {
        return s.mtimeMs >= yesterdayStartMs && s.mtimeMs <= nowMs;
      }
      return true;
    });
  }, [sessions, searchTerm, dateFilter, nowMs]);

  const PAGE_SIZE = 10;
  const totalPages = Math.max(1, Math.ceil(filteredSessions.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const visibleSessions = filteredSessions.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );

  const totalPathMeters = sessions.reduce(
    (sum, session) => sum + (session.trajectoryDist ?? 0),
    0,
  );
  const weekStartMs =
    nowMs === null ? 0 : nowMs - 7 * 24 * 60 * 60 * 1000;
  const thisWeekCount =
    nowMs === null
      ? 0
      : sessions.filter((s) => s.mtimeMs >= weekStartMs).length;

  const weeklyData = useMemo(() => {
    if (nowMs === null) {
      return Array.from({ length: 7 }, (_, index) => ({
        day: "",
        trials: 0,
        dist: 0,
        avg: 0,
        isToday: index === 6,
        heightPct: 0,
        avgPct: 0,
      }));
    }
    const now = new Date(nowMs);
    now.setHours(0, 0, 0, 0);
    const days = Array.from({ length: 7 }, (_, index) => {
      const date = new Date(now);
      date.setDate(date.getDate() - (6 - index));
      const next = new Date(date);
      next.setDate(next.getDate() + 1);
      const daySessions = sessions.filter(
        (s) => s.mtimeMs >= date.getTime() && s.mtimeMs < next.getTime(),
      );
      const dist = daySessions.reduce(
        (sum, s) => sum + (s.trajectoryDist ?? 0),
        0,
      );
      return {
        day: date.toLocaleDateString([], { weekday: "short" }),
        trials: daySessions.length,
        dist,
        avg: daySessions.length ? dist / daySessions.length : 0,
        isToday: date.getTime() === now.getTime(),
      };
    });
    const maxDist = Math.max(...days.map((d) => d.dist), 0);
    const maxAvg = Math.max(...days.map((d) => d.avg), 0);
    return days.map((d) => ({
      ...d,
      heightPct: maxDist > 0 ? Math.max(2, (d.dist / maxDist) * 100) : 0,
      avgPct: maxAvg > 0 ? Math.max(2, (d.avg / maxAvg) * 100) : 0,
    }));
  }, [sessions, nowMs]);

  const peakDay = weeklyData.reduce(
    (best, day) => (day.dist > best.dist ? day : best),
    weeklyData[0],
  );

  return (
    <div className="min-h-screen bg-background text-on-surface flex flex-col font-body-md antialiased selection:bg-primary-container selection:text-white">
      {/* Sidebar Navigation */}
      <Sidebar
        activeModule="dashboard"
        isOpen={isMobileNavOpen}
        onClose={() => setIsMobileNavOpen(false)}
      />

      {/* Main Content Area */}
      <div className="lg:pl-64 flex flex-col min-h-screen">
        {/* Top Header */}
        <Header
          currentUnit={currentUnit}
          onUnitChange={setCurrentUnit}
          onOpenMobileMenu={() => setIsMobileNavOpen(true)}
          deviceConnected={deviceConnected}
        />

        {/* Workspace Body */}
        <main className="w-full pt-18 px-space-md sm:px-gutter-desktop pb-margin-desktop bg-background flex flex-col gap-space-lg">
          {/* Top Welcome & Protocol Initiation Header */}
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-space-md pt-space-xs">
            <div className="flex flex-col gap-space-xs">
              <div className="flex items-center gap-space-sm flex-wrap">
                <h1 className="font-display-lg text-display-lg text-on-surface tracking-tight">
                  Motion Capture Workspace
                </h1>
              </div>
            </div>

            <div className="flex items-center gap-space-sm self-start md:self-auto flex-wrap">
              <button
                onClick={() => router.push("/devices")}
                className="inline-flex items-center gap-space-xs px-3.5 py-2 rounded-lg bg-surface-container-lowest hover:bg-surface-container-low text-on-surface transition-all shadow-xs border border-outline-variant/30 font-label-md text-label-md cursor-pointer"
                type="button"
              >
                <span className="material-symbols-outlined text-[18px] text-secondary">
                  sensors
                </span>
                <span>Discover Devices</span>
              </button>

              <Link
                href="/live-tracking"
                className="inline-flex items-center gap-space-xs px-4 py-2 rounded-lg bg-primary-container hover:bg-primary text-on-primary transition-all shadow-sm font-label-md text-label-md font-semibold cursor-pointer"
              >
                <span className="material-symbols-outlined text-[18px]">
                  play_arrow
                </span>
                <span>Start Session</span>
              </Link>
            </div>
          </div>


          {/* Key Summary Metrics Grid (4 Metric Tiles) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-space-md">
            {/* Metric 1: Total Recorded Sessions */}
            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Recorded Motion Trials
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  analytics
                </span>
              </div>
              <div className="my-space-sm">
                <div className="flex items-baseline gap-space-xs">
                  <span className="font-metric-display text-metric-display text-on-surface">
                    {sessions.length}
                  </span>
                  <span className="font-body-md text-body-md text-on-surface-variant font-medium">
                    sessions
                  </span>
                </div>
              </div>
              <div className="flex items-center justify-between pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                <span className="inline-flex items-center text-tertiary font-metric-code text-[11px] font-semibold">
                  <span className="material-symbols-outlined text-[14px]">
                    calendar_today
                  </span>{" "}
                  {thisWeekCount} in last 7 days
                </span>
                <span className="font-label-sm text-label-sm text-outline">
                  Processed summaries
                </span>
              </div>
            </div>

            {/* Metric 2: Total Trajectory Path Tracked */}
            <div className="bg-surface-container-lowest rounded-xl p-space-lg shadow-xs border border-outline-variant/30 flex flex-col justify-between">
              <div className="flex items-center justify-between text-outline">
                <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant font-semibold">
                  Total Trajectory Tracked
                </span>
                <span className="material-symbols-outlined text-[20px] text-primary">
                  route
                </span>
              </div>
              <div className="my-space-sm">
                <div className="flex items-baseline gap-space-xs">
                  <span className="font-metric-display text-metric-display text-on-surface">
                    {formatDistance(totalPathMeters)}
                  </span>
                  <span className="font-headline-sm text-headline-sm text-on-surface font-semibold">
                    {currentUnit}
                  </span>
                </div>
              </div>
              <div className="flex items-center justify-between pt-space-xs text-on-surface-variant font-body-sm text-body-sm border-t border-outline-variant/20">
                <span className="text-on-surface-variant">
                  Cumulative arc path
                </span>
                <span className="font-metric-code text-metric-code text-[11px] text-primary font-semibold">
                  6-DOF Integration
                </span>
              </div>
            </div>
          </div>

          {/* Primary Laboratory Distance Analysis Component */}
          <div className="bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 p-space-lg flex flex-col gap-space-md">
            <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-space-md">
              <div>
                <div className="flex items-center gap-space-sm flex-wrap">
                  <span className="font-headline-md text-headline-md text-on-surface">
                    Daily Tracking Report
                  </span>
                  <span className="px-2 py-0.5 rounded bg-surface-container-high text-primary font-label-sm text-label-sm font-semibold">
                    Last 7 Days
                  </span>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-space-md bg-surface-container-low p-space-sm rounded-lg border border-outline-variant/20">
                <div className="flex items-center gap-space-xs">
                  <span className="w-3.5 h-3.5 rounded bg-primary"></span>
                  <span className="font-label-md text-label-md text-on-surface font-medium">
                    Tracked Distance ({currentUnit})
                  </span>
                </div>
                <div className="flex items-center gap-space-xs">
                  <span className="w-3.5 h-3.5 rounded bg-surface-tint opacity-60"></span>
                  <span className="font-label-md text-label-md text-on-surface font-medium">
                    Avg Session Distance
                  </span>
                </div>
              </div>
            </div>

            <div className="p-space-sm rounded-lg bg-surface-container-low flex items-start gap-space-sm border border-outline-variant/20">
              <span className="material-symbols-outlined text-primary text-[20px] shrink-0 mt-0.5">
                insights
              </span>
              <div className="text-body-sm font-body-sm text-on-surface-variant">
                <strong className="text-on-surface">
                  Weekly Activity Summary:
                </strong>{" "}
                {peakDay && peakDay.dist > 0
                  ? `Peak trajectory volume recorded on ${peakDay.day} (${formatDistance(peakDay.dist)} ${currentUnit} across ${peakDay.trials} trial${peakDay.trials === 1 ? "" : "s"}).`
                  : "No processed A→B sessions in the last 7 days. Run a live tracking session to populate this report."}
              </div>
            </div>

            {/* Interactive Bar Chart Canvas */}
            <div className="relative w-full pt-4 pb-2">
              <div className="relative w-full h-72">
                {/* Horizontal reference grid lines */}
                <div className="absolute inset-0 flex flex-col justify-between pointer-events-none text-outline text-[11px] font-metric-code">
                  <div className="flex items-center justify-between">
                    <span className="w-12">
                      {formatDistance(40)} {currentUnit}
                    </span>
                    <div className="w-full h-px bg-surface-container-highest"></div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="w-12">
                      {formatDistance(30)} {currentUnit}
                    </span>
                    <div className="w-full h-px bg-surface-container-highest"></div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="w-12">
                      {formatDistance(20)} {currentUnit}
                    </span>
                    <div className="w-full h-px bg-surface-container-highest"></div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="w-12">
                      {formatDistance(10)} {currentUnit}
                    </span>
                    <div className="w-full h-px bg-surface-container-highest"></div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="w-12">0 {currentUnit}</span>
                    <div className="w-full h-px bg-surface-container-high"></div>
                  </div>
                </div>

                {/* Bars */}
                <div className="absolute inset-0 pl-14 pr-4 flex items-end justify-between gap-2 sm:gap-4 z-10">
                  {weeklyData.map((d, idx) => (
                    <div
                      key={idx}
                      className="group flex-1 flex flex-col items-center h-full justify-end cursor-pointer"
                    >
                      <div className="w-full flex items-end justify-center gap-1 sm:gap-2 h-full pb-1">
                        {/* Primary distance bar */}
                        <div
                          className="relative w-1/2 max-w-[28px] rounded-t transition-all duration-200 group-hover:brightness-110 flex justify-center bg-primary"
                          style={{ height: `${d.heightPct}%` }}
                        >
                          {d.dist > 0 && (
                            <span className="absolute -top-6 font-metric-code text-[11px] text-on-surface font-semibold group-hover:text-primary whitespace-nowrap">
                              {formatDistance(d.dist)}
                              {currentUnit}
                            </span>
                          )}
                        </div>

                        {/* Avg distance bar */}
                        <div
                          className="relative w-1/2 max-w-[28px] rounded-t transition-all duration-200 group-hover:opacity-90 flex justify-center bg-surface-tint opacity-60"
                          style={{ height: `${d.avgPct}%` }}
                        >
                          {d.avg > 0 && (
                            <span className="absolute -top-6 font-metric-code text-[11px] text-on-surface-variant font-medium whitespace-nowrap hidden group-hover:block">
                              {formatDistance(d.avg)}
                              {currentUnit}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Day Label */}
                      <div className="w-full text-center mt-2">
                        <div
                          className={`font-label-md text-label-md font-semibold truncate ${
                            d.isToday ? "text-primary font-bold" : "text-on-surface"
                          }`}
                        >
                          {d.day}
                        </div>
                        <div className="font-metric-code text-[10px] text-outline">
                          {typeof d.trials === "number"
                            ? `${d.trials} trials`
                            : d.trials}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="flex flex-col sm:flex-row items-center justify-between text-on-surface-variant font-body-sm text-body-sm pt-space-xs border-t border-outline-variant/20 gap-2">
              <div className="flex items-center gap-space-sm">
                <span className="w-2 h-2 rounded-full bg-tertiary"></span>
                <span>
                  {thisWeekCount} sessions and{" "}
                  <strong>{formatDistance(totalPathMeters)} {currentUnit}</strong>{" "}
                  of estimated trajectory in the last 7 days.
                </span>
              </div>
              <span className="font-metric-code text-[11px] text-outline">
                Movesense 3D Trajectory Analytics Kernel
              </span>
            </div>
          </div>

          {/* Recent Sessions Table Component */}
          <div
            id="sessions"
            className="bg-surface-container-lowest rounded-xl shadow-xs border border-outline-variant/30 overflow-hidden flex flex-col"
          >
            {/* Table Toolbar & Filters */}
            <div className="p-space-lg flex flex-col lg:flex-row lg:items-center justify-between gap-space-md border-b border-outline-variant/20">
              <div className="flex items-center gap-space-md">
                <h2 className="font-headline-md text-headline-md text-on-surface">
                  Recent Tracking Sessions
                </h2>
                <span className="px-2.5 py-0.5 rounded-full bg-surface-container text-on-surface font-label-sm text-label-sm font-semibold">
                  {filteredSessions.length} Filtered / {sessions.length} Total
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-space-sm">
                {/* Search Filter */}
                <div className="relative flex items-center">
                  <span className="material-symbols-outlined absolute left-2.5 text-[18px] text-outline pointer-events-none">
                    search
                  </span>
                  <input
                    value={searchTerm}
                    onChange={(e) => {
                      setSearchTerm(e.target.value);
                      setPage(1);
                    }}
                    className="pl-8 pr-3 py-1.5 rounded-lg bg-surface-container-low text-on-surface font-body-sm text-body-sm focus:outline-none focus:ring-2 focus:ring-primary/40 border border-outline-variant/20 w-56 transition-all"
                    placeholder="Filter trial, subject ID..."
                    type="text"
                  />
                </div>

                {/* Date Filter Dropdown */}
                <button
                  onClick={() => {
                    setDateFilter((prev) =>
                      prev === "all" ? "today" : "all"
                    );
                    setPage(1);
                  }}
                  className="flex items-center bg-surface-container-low rounded-lg px-2.5 py-1.5 gap-space-xs text-on-surface font-body-sm text-body-sm cursor-pointer hover:bg-surface-container border border-outline-variant/20 transition-colors"
                  type="button"
                >
                  <span className="material-symbols-outlined text-[18px] text-secondary">
                    calendar_today
                  </span>
                  <span>{dateFilter === "all" ? "All Cycles" : "Today & Yesterday"}</span>
                  <span className="material-symbols-outlined text-[16px] text-outline">
                    expand_more
                  </span>
                </button>

                <button
                  onClick={() => openExport()}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-surface-container-low hover:bg-surface-container text-on-surface font-label-md text-label-md border border-outline-variant/20 transition-colors cursor-pointer"
                  type="button"
                >
                  <span className="material-symbols-outlined text-[16px] text-secondary">
                    file_download
                  </span>
                  Export
                </button>
              </div>
            </div>

            {/* Telemetry Table Content */}
            <div className="overflow-x-auto w-full">
              <table className="w-full text-left font-body-sm text-body-sm">
                <thead className="bg-surface-container-low text-outline uppercase font-label-sm text-label-sm border-b border-outline-variant/20">
                  <tr>
                    <th className="py-3 px-space-lg">Session ID &amp; Protocol</th>
                    <th className="py-3 px-space-md">Subject / ID</th>
                    <th className="py-3 px-space-md">Timestamp</th>
                    <th className="py-3 px-space-md">Sensor Hardware</th>
                    <th className="py-3 px-space-md">Duration</th>
                    <th className="py-3 px-space-md text-right">
                      Trajectory vs Disp
                    </th>
                    <th className="py-3 px-space-md text-center">Quality</th>
                    <th className="py-3 px-space-lg text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/20 text-on-surface">
                  {loading && (
                    <tr>
                      <td colSpan={8} className="py-8 px-space-lg text-center text-secondary">
                        Loading processed sessions…
                      </td>
                    </tr>
                  )}
                  {!loading && error && (
                    <tr>
                      <td colSpan={8} className="py-8 px-space-lg text-center text-on-error-container bg-error-container/30">
                        {error}
                      </td>
                    </tr>
                  )}
                  {!loading && !error && visibleSessions.length === 0 && (
                    <tr>
                      <td colSpan={8} className="py-8 px-space-lg text-center text-secondary">
                        No processed sessions match this filter. Run a live
                        tracking session or clear the filters.
                      </td>
                    </tr>
                  )}
                  {visibleSessions.map((row) => (
                    <tr
                      key={row.id}
                      className="hover:bg-surface-container-low/50 transition-colors"
                    >
                      <td className="py-3.5 px-space-lg">
                        <div className="flex items-center gap-space-sm">
                          <span
                            className={`w-2 h-2 rounded-full ${row.activeColor} shrink-0`}
                          ></span>
                          <div>
                            <span className="font-headline-sm text-headline-sm text-on-surface">
                              {row.sessionId}
                            </span>
                            <div className="font-label-sm text-label-sm text-on-surface-variant">
                              {row.protocol}
                            </div>
                          </div>
                        </div>
                      </td>

                      <td className="py-3.5 px-space-md">
                        <div className="font-body-md text-body-md text-on-surface font-medium">
                          {row.subjectName}
                        </div>
                        <div className="text-[11px] text-outline font-metric-code">
                          {row.subjectNote}
                        </div>
                      </td>

                      <td className="py-3.5 px-space-md font-metric-code text-on-surface-variant text-[12px]">
                        <div>{row.time}</div>
                        <div className="text-[11px] text-outline">{row.date}</div>
                      </td>

                      <td className="py-3.5 px-space-md">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-container text-on-surface font-label-sm text-label-sm border border-outline-variant/20">
                          <span className="w-1.5 h-1.5 rounded-full bg-tertiary"></span>{" "}
                          {row.device}
                        </span>
                      </td>

                      <td className="py-3.5 px-space-md font-metric-code text-on-surface">
                        {row.duration}
                      </td>

                      <td className="py-3.5 px-space-md text-right font-metric-code">
                        <div className="text-primary font-semibold">
                          {formatDistance(row.trajectoryDist)} {currentUnit}
                        </div>
                        <div className="text-outline text-[11px]">
                          Δr: {formatDistance(row.displacementDist)} {currentUnit}
                        </div>
                      </td>

                      <td className="py-3.5 px-space-md text-center">
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-surface-container-high text-tertiary font-label-sm text-label-sm font-semibold">
                          {row.quality}
                        </span>
                      </td>

                      <td className="py-3.5 px-space-lg text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => router.push("/live-tracking")}
                            className="p-1.5 rounded-lg hover:bg-surface-container text-primary transition-colors cursor-pointer"
                            title="Open Live Tracking"
                            type="button"
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              sensors
                            </span>
                          </button>
                          <button
                            onClick={() => openExport(row.sessionId)}
                            className="p-1.5 rounded-lg hover:bg-surface-container text-secondary transition-colors cursor-pointer"
                            title="Export this session"
                            type="button"
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              download
                            </span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Table Footer Pagination & Status */}
            <div className="p-space-md bg-surface-container-low flex flex-col sm:flex-row items-center justify-between gap-space-sm text-outline font-body-sm text-body-sm border-t border-outline-variant/20">
              <span>
                Showing{" "}
                {filteredSessions.length === 0
                  ? 0
                  : (currentPage - 1) * PAGE_SIZE + 1}
                –{Math.min(currentPage * PAGE_SIZE, filteredSessions.length)} of{" "}
                {filteredSessions.length} filtered sessions
              </span>
              <div className="flex items-center gap-space-xs">
                <button
                  onClick={() => setPage((prev) => Math.max(1, prev - 1))}
                  className="px-2.5 py-1 rounded bg-surface-container-lowest text-on-surface font-label-sm text-label-sm hover:bg-surface-container transition-colors disabled:opacity-40 border border-outline-variant/20 cursor-pointer"
                  disabled={currentPage <= 1}
                  type="button"
                >
                  Previous
                </button>
                <span className="px-2 py-1 font-metric-code text-on-surface font-semibold text-[12px]">
                  Page {currentPage} / {totalPages}
                </span>
                <button
                  onClick={() =>
                    setPage((prev) => Math.min(totalPages, prev + 1))
                  }
                  className="px-2.5 py-1 rounded bg-surface-container-lowest text-on-surface font-label-sm text-label-sm hover:bg-surface-container transition-colors disabled:opacity-40 border border-outline-variant/20 cursor-pointer"
                  disabled={currentPage >= totalPages}
                  type="button"
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        </main>
      </div>

      {/* Export Modal */}
      <ExportModal
        isOpen={isExportOpen}
        onClose={closeExport}
        sessionId={exportSessionId}
      />
    </div>
  );
}
