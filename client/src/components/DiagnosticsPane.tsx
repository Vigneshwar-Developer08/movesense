"use client";

import React from "react";
import { UnitType } from "./Header";

interface DiagnosticsPaneProps {
  unit: UnitType;
  unitMultiplier: number;
  position: { x: number; y: number; z: number };
  isStreaming?: boolean;
  /** Live values from GET /api/tracking/{id}; null means "no data yet". */
  liveDistanceM?: number | null;
  livePathM?: number | null;
  liveVelocityMps?: number | null;
  kinematicState?: string | null;
}

export function DiagnosticsPane({
  unit,
  unitMultiplier,
  position,
  isStreaming = false,
  liveDistanceM = null,
  livePathM = null,
  liveVelocityMps = null,
  kinematicState = null,
}: DiagnosticsPaneProps) {
  const formatMetric = (
    value: number | null,
    digits = 3,
    signed = true,
  ): string => {
    if (value === null) return "—";
    const converted = value * unitMultiplier;
    const sign = signed && converted >= 0 ? "+" : "";
    return `${sign}${converted.toFixed(digits)}`;
  };

  // Calculate straight-line 3D Euclidean displacement from Point A (relative path).
  const hasPosition = liveDistanceM !== null || isStreaming;
  const displacementMeters = liveDistanceM ??
    Math.sqrt(
      position.x * position.x + position.y * position.y + position.z * position.z
    );
  const displacement = hasPosition
    ? formatMetric(displacementMeters)
    : "—";

  const pathLength = formatMetric(livePathM);
  const linearVelConverted = formatMetric(liveVelocityMps);

  const stateLabel = kinematicState ?? (isStreaming ? "—" : "Idle");

  return (
    <div className="flex flex-col gap-space-md">
      {/* DISTANCE SECTION: Key separation between Displacement & Cumulative Arc */}
      <div className="bg-surface-container-lowest p-space-md sm:p-space-lg rounded-xl shadow-xs border border-outline-variant/30 flex flex-col gap-space-md">
        <div className="flex items-center justify-between pb-space-xs border-b border-outline-variant/20">
          <span className="font-label-sm text-label-sm uppercase tracking-wider text-secondary font-bold">
            Distance &amp; Kinematics
          </span>
          <span className="material-symbols-outlined text-primary text-[18px]">
            straighten
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-1 gap-space-sm">
          {/* Displacement Card */}
          <div className="p-space-md rounded-xl bg-surface-container-low border border-outline-variant/20 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="font-label-sm text-label-sm text-secondary font-medium">
                Displacement from Point A
              </span>
              <span
                className="material-symbols-outlined text-[15px] text-secondary cursor-help"
                title="Straight-line 3D vector magnitude from initial calibration origin"
              >
                navigation
              </span>
            </div>
            <div className="flex items-baseline gap-space-xs my-space-xs">
              <span className="font-metric-display text-metric-display text-on-surface tracking-tight">
                {displacement}
              </span>
              <span className="font-body-md text-body-md font-semibold text-secondary">
                {unit}
              </span>
            </div>
          </div>

          {/* Trajectory Length Card */}
          <div className="p-space-md rounded-xl bg-surface-container-low border border-outline-variant/20 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="font-label-sm text-label-sm text-secondary font-medium">
                Trajectory Path Length
              </span>
              <span
                className="material-symbols-outlined text-[15px] text-secondary cursor-help"
                title="Integrated cumulative path length traversed"
              >
                timeline
              </span>
            </div>
            <div className="flex items-baseline gap-space-xs my-space-xs">
              <span className="font-metric-display text-metric-display text-primary tracking-tight">
                {pathLength}
              </span>
              <span className="font-body-md text-body-md font-semibold text-secondary">
                {unit}
              </span>
            </div>
          </div>
        </div>

        {/* Kinetic State Badge */}
        <div
          className={`flex items-center justify-between p-space-sm rounded-lg border transition-all ${
            isStreaming
              ? "bg-tertiary/10 text-tertiary border-tertiary/20"
              : "bg-surface-container text-secondary border-outline-variant/30"
          }`}
        >
          <div className="flex items-center gap-space-sm">
            <span
              className={`material-symbols-outlined text-[18px] ${
                isStreaming ? "animate-pulse" : ""
              }`}
            >
              {isStreaming ? "sprint" : "accessibility_new"}
            </span>
            <div className="flex flex-col">
              <span className="font-label-md text-label-md font-bold">
                Kinematic State: {stateLabel}
              </span>
              <span className="font-body-sm text-body-sm text-tertiary">
                Linear Velocity: {linearVelConverted} {unit}/s
              </span>
            </div>
          </div>
          <span
            className={`px-space-xs py-0.5 rounded font-label-sm text-label-sm font-semibold ${
              isStreaming
                ? "bg-surface-container-lowest text-tertiary shadow-xs"
                : "bg-surface-container-high text-secondary"
            }`}
          >
            {isStreaming ? "Streaming" : "Stopped"}
          </span>
        </div>
      </div>

    </div>
  );
}
