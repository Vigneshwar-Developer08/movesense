"use client";

import React, { useEffect, useState } from "react";

type ChartMode = "linear" | "accel" | "gyro" | "intervals";

export interface TrackingSample {
  t_ms: number;
  acc: number[];
  gyro: number[];
}

interface WaveformChartProps {
  isStreaming?: boolean;
  /** Raw IMU samples returned by GET /api/tracking/{id}/samples. */
  samples?: TrackingSample[];
  liveSampleCount?: number | null;
  liveRateHz?: number | null;
  liveRssiDbm?: number | null;
  startedAt?: string | null;
  qualityFlags?: string[];
}

const GRAVITY = 9.80665;

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(1);
  return `${mins.toString().padStart(2, "0")}:${secs.padStart(4, "0")}`;
}

function toPath(
  values: number[],
  xFor: (index: number) => number,
  yFor: (value: number) => number,
): string {
  return values
    .map((value, index) => `${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`)
    .join(" ");
}

export function WaveformChart({
  isStreaming = false,
  samples = [],
  liveSampleCount = null,
  liveRateHz = null,
  liveRssiDbm = null,
  startedAt = null,
  qualityFlags = [],
}: WaveformChartProps) {
  const [activeMode, setActiveMode] = useState<ChartMode>("accel");
  // Initialize after mount: reading the clock during the prerender is unstable.
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    if (!isStreaming || !startedAt) return;
    const tick = () => setNow(Date.now());
    const initial = setTimeout(tick, 0);
    const interval = setInterval(tick, 250);
    return () => {
      clearTimeout(initial);
      clearInterval(interval);
    };
  }, [isStreaming, startedAt]);

  const startMs = startedAt ? new Date(startedAt).getTime() : null;
  const durationSeconds =
    startMs !== null && now !== null ? Math.max(0, (now - startMs) / 1000) : 0;

  const visible = samples.slice(-220);
  const firstTs = visible[0]?.t_ms ?? 0;
  const lastTs = visible[visible.length - 1]?.t_ms ?? firstTs;

  let series: { label: string; color: string; values: number[] }[];
  if (activeMode === "gyro") {
    series = [
      { label: "Ω_x (°/s)", color: "#ba1a1a", values: visible.map((s) => s.gyro[0] ?? 0) },
      { label: "Ω_y (°/s)", color: "#117e3b", values: visible.map((s) => s.gyro[1] ?? 0) },
      { label: "Ω_z (°/s)", color: "#004ac6", values: visible.map((s) => s.gyro[2] ?? 0) },
    ];
  } else if (activeMode === "linear") {
    series = [
      {
        label: "|a| − g (m/s²)",
        color: "#004ac6",
        values: visible.map(
          (s) => Math.hypot(s.acc[0] ?? 0, s.acc[1] ?? 0, s.acc[2] ?? 0) - GRAVITY,
        ),
      },
    ];
  } else if (activeMode === "intervals") {
    series = [
      {
        label: "Sample interval (ms)",
        color: "#004ac6",
        values: visible.slice(1).map((s, index) => s.t_ms - visible[index].t_ms),
      },
    ];
  } else {
    series = [
      { label: "X accel (m/s²)", color: "#ba1a1a", values: visible.map((s) => s.acc[0] ?? 0) },
      { label: "Y accel (m/s²)", color: "#117e3b", values: visible.map((s) => s.acc[1] ?? 0) },
      { label: "Z accel (m/s²)", color: "#004ac6", values: visible.map((s) => s.acc[2] ?? 0) },
    ];
  }

  const allValues = series.flatMap((line) => line.values);
  const dataMin = allValues.length ? Math.min(...allValues) : 0;
  const dataMax = allValues.length ? Math.max(...allValues) : 1;
  const span = Math.max(dataMax - dataMin, 1e-6);
  const min = dataMin - span * 0.15;
  const max = dataMax + span * 0.15;
  const range = max - min;
  const pointCount = Math.max(...series.map((line) => line.values.length), 0);
  const xFor = (index: number) =>
    pointCount <= 1 ? 500 : (index / (pointCount - 1)) * 1000;
  const yFor = (value: number) => 110 - ((value - min) / range) * 100;

  const primary = series[0];
  const areaPoints =
    primary && primary.values.length >= 2
      ? `0,120 ${toPath(primary.values, xFor, yFor)} 1000,120`
      : "";

  const windowSeconds = Math.max(0, (lastTs - firstTs) / 1000);
  const timeTicks = Array.from({ length: 5 }, (_, index) => {
    const fraction = index / 4;
    return {
      x: fraction * 1000,
      label:
        windowSeconds > 0
          ? `${(windowSeconds * (fraction - 1)).toFixed(1)} s`
          : index === 4
            ? "now"
            : "",
    };
  });

  const observedRate =
    visible.length >= 2 && lastTs > firstTs
      ? ((visible.length - 1) / ((lastTs - firstTs) / 1000)).toFixed(2)
      : liveRateHz !== null
        ? liveRateHz.toFixed(2)
        : "—";

  const qualityLabel = qualityFlags.length
    ? qualityFlags.join(", ")
    : visible.length
      ? "No flags reported"
      : "Waiting for samples";

  return (
    <div className="bg-surface-container-lowest p-space-md sm:p-space-lg rounded-xl shadow-xs border border-outline-variant/30 flex flex-col gap-space-md">
      {/* Chart Header & Mode Tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-space-sm pb-space-xs border-b border-outline-variant/20">
        <div className="flex items-center gap-space-md">
          <div className="flex items-center gap-space-xs">
            <span className="material-symbols-outlined text-[18px] text-primary">
              show_chart
            </span>
            <span className="font-headline-sm text-headline-sm text-on-surface">
              Real-Time Kinematic Waveforms
            </span>
          </div>
          <span
            className={`w-2 h-2 rounded-full ${
              isStreaming ? "bg-tertiary animate-pulse" : "bg-outline"
            }`}
            title={isStreaming ? "Streaming" : "Stopped"}
          />
        </div>

        <div className="flex items-center gap-space-xs flex-wrap">
          <div className="flex items-center bg-surface-container-low rounded-lg p-0.5 border border-outline-variant/30">
            {(
              [
                { id: "linear", label: "Linear Accel" },
                { id: "accel", label: "Accelerometer (3-Axis)" },
                { id: "gyro", label: "Gyroscope (3-Axis)" },
                { id: "intervals", label: "Sample Intervals" },
              ] as const
            ).map((mode) => (
              <button
                key={mode.id}
                onClick={() => setActiveMode(mode.id)}
                className={`px-space-sm py-1 rounded font-label-sm text-label-sm transition-all cursor-pointer ${
                  activeMode === mode.id
                    ? "font-semibold bg-surface-container-lowest text-primary shadow-xs"
                    : "text-secondary hover:text-on-surface hover:bg-surface-container-high"
                }`}
                type="button"
              >
                {mode.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Inline Scientific SVG Time-Series Chart */}
      <div className="relative w-full h-48 bg-surface-container-low rounded-lg p-space-sm overflow-hidden flex flex-col justify-between border border-outline-variant/30">
        <div className="flex items-center justify-between z-10 px-space-xs gap-space-sm">
          <div className="flex items-center gap-space-md font-label-sm text-label-sm flex-wrap">
            {series.map((line) => (
              <div key={line.label} className="flex items-center gap-1.5">
                <span
                  className="w-3 h-0.5"
                  style={{ backgroundColor: line.color }}
                />
                <span className="text-on-surface font-mono">{line.label}</span>
              </div>
            ))}
          </div>
          <span className="font-mono font-label-sm text-label-sm text-secondary hidden sm:inline">
            {(lastTs - firstTs) > 0
              ? `${((lastTs - firstTs) / 1000).toFixed(1)} s window`
              : "No samples yet"}
          </span>
        </div>

        {/* SVG Graph Canvas */}
        <div className="w-full h-32 relative">
          <svg
            className="w-full h-full overflow-visible"
            preserveAspectRatio="none"
            viewBox="0 0 1000 120"
          >
            <defs>
              <linearGradient id="primaryFade" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#004ac6" stopOpacity="0.18" />
                <stop offset="100%" stopColor="#004ac6" stopOpacity="0.0" />
              </linearGradient>
            </defs>

            {[20, 60, 100].map((y, index) => (
              <line
                key={y}
                opacity={index === 1 ? 0.4 : 0.6}
                stroke={index === 1 ? "#737686" : "#c3c6d7"}
                strokeDasharray={index === 1 ? undefined : "3,3"}
                strokeWidth={index === 1 ? 1 : 0.8}
                x1="0"
                x2="1000"
                y1={y}
                y2={y}
              />
            ))}

            {[250, 500, 750].map((x) => (
              <line
                key={x}
                opacity="0.4"
                stroke="#c3c6d7"
                strokeDasharray="2,2"
                strokeWidth="0.8"
                x1={x}
                x2={x}
                y1="0"
                y2="120"
              />
            ))}

            {areaPoints && <polygon fill="url(#primaryFade)" points={areaPoints} />}

            {series.map((line, index) => (
              <polyline
                key={line.label}
                fill="none"
                points={toPath(line.values, xFor, yFor)}
                stroke={line.color}
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={index === 2 ? 2 : 1.8}
              />
            ))}

            {series.map((line) => {
              const values = line.values;
              if (!values.length) return null;
              return (
                <circle
                  key={`${line.label}-cursor`}
                  cx={xFor(values.length - 1)}
                  cy={yFor(values[values.length - 1])}
                  fill={line.color}
                  r="2.5"
                />
              );
            })}
          </svg>

          {visible.length < 2 && (
            <div className="absolute inset-0 flex items-center justify-center text-body-sm text-secondary pointer-events-none">
              {isStreaming
                ? "Waiting for the first IMU samples…"
                : "Start tracking to plot real IMU samples."}
            </div>
          )}
        </div>

        {/* Bottom Timestamps */}
        <div className="flex items-center justify-between font-mono text-[10px] text-secondary px-space-xs border-t border-outline-variant/20 pt-1">
          {timeTicks.map((tick, index) => (
            <span
              key={tick.x}
              className={index === 4 ? "font-bold text-primary" : undefined}
            >
              {tick.label}
            </span>
          ))}
        </div>
      </div>

      {/* Diagnostic Stream Quality Footer */}
      <div className="flex flex-wrap items-center justify-between gap-space-sm pt-space-xs text-body-sm font-mono text-secondary">
        <div className="flex items-center gap-space-md flex-wrap text-xs sm:text-body-sm">
          <span>
            <strong className="text-on-surface">Duration:</strong>{" "}
            {formatDuration(durationSeconds)}
          </span>
          <span>•</span>
          <span>
            <strong className="text-on-surface">Samples:</strong>{" "}
            {(liveSampleCount ?? 0).toLocaleString()}
          </span>
          <span>•</span>
          <span>
            <strong className="text-on-surface">Observed Rate:</strong>{" "}
            {observedRate}
            {liveRateHz !== null ? ` Hz (Nominal: ${liveRateHz} Hz)` : " Hz"}
          </span>
          <span>•</span>
          <span>
            <strong className="text-on-surface">RSSI:</strong>{" "}
            {liveRssiDbm !== null ? `${liveRssiDbm} dBm` : "—"}
          </span>
        </div>

        <div className="flex items-center gap-1.5 font-label-sm text-label-sm text-tertiary font-semibold">
          <span
            className={`w-2 h-2 rounded-full ${
              visible.length ? "bg-tertiary-container animate-pulse" : "bg-outline"
            }`}
          />
          <span>Quality: {qualityLabel}</span>
        </div>
      </div>
    </div>
  );
}
