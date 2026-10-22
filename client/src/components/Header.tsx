"use client";

import React, { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { displayName, initials } from "@/lib/auth";

export type UnitType = "m" | "cm" | "in" | "ft";

interface HeaderProps {
  currentUnit: UnitType;
  onUnitChange: (unit: UnitType) => void;
  onOpenMobileMenu?: () => void;
  deviceConnected?: boolean;
  deviceName?: string;
}

export function Header({
  currentUnit,
  onUnitChange,
  onOpenMobileMenu,
  deviceConnected = false,
  deviceName,
}: HeaderProps) {
  const units: UnitType[] = ["m", "cm", "in", "ft"];
  const { user, loading, signOut } = useAuth();
  const [signingOut, setSigningOut] = useState(false);

  const handleSignOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await signOut();
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <header className="fixed top-0 left-0 lg:left-64 right-0 h-14 bg-surface-container-lowest z-40 shadow-[0_1px_8px_rgba(0,0,0,0.04)] border-b border-outline-variant/30 px-space-md sm:px-gutter-desktop flex items-center justify-between">
      {/* Left: Mobile Toggle & Breadcrumbs */}
      <div className="flex items-center gap-space-sm sm:gap-space-md">
        <button
          onClick={onOpenMobileMenu}
          className="lg:hidden p-1.5 rounded-lg text-secondary hover:text-on-surface hover:bg-surface-container-low cursor-pointer"
          type="button"
          aria-label="Open Navigation"
        >
          <span className="material-symbols-outlined text-[20px]">menu</span>
        </button>


      </div>

      {/* Right: Device Indicator, Unit Selector, User Profile */}
      <div className="flex items-center gap-space-sm sm:gap-space-lg">
        {/* Device Status Pill */}
        <div className="flex items-center gap-space-sm px-2.5 sm:px-space-md py-1 rounded-full bg-surface-container-low border border-outline-variant/20">
          <span
            className={`w-2 h-2 rounded-full ${
              deviceConnected
                ? "bg-tertiary-container animate-pulse"
                : "bg-error"
            }`}
          ></span>
          <span className="font-label-sm text-label-sm text-tertiary font-semibold hidden md:inline">
            {deviceConnected ? deviceName ?? "Movesense" : "No sensor"}
          </span>
          <span className="font-label-sm text-label-sm text-secondary">
            {deviceConnected ? "· Connected" : "· Standby"}
          </span>
        </div>

        {/* Unit Selector */}
        <div className="flex items-center p-0.5 rounded-lg bg-surface-container-low border border-outline-variant/30">
          {units.map((unit) => {
            const isSelected = currentUnit === unit;
            return (
              <button
                key={unit}
                onClick={() => onUnitChange(unit)}
                className={`px-2 sm:px-space-sm py-0.5 rounded transition-all font-label-sm text-label-sm cursor-pointer ${
                  isSelected
                    ? "bg-surface-container-lowest shadow-[0_1px_3px_rgba(23,43,77,0.08)] font-semibold text-primary"
                    : "text-secondary hover:text-on-surface hover:bg-surface-container-high"
                }`}
                type="button"
              >
                {unit}
              </button>
            );
          })}
        </div>

        {/* User Profile */}
        <div className="flex items-center gap-space-sm pl-space-xs sm:pl-space-sm border-l border-outline-variant/20">
          <div className="hidden lg:flex flex-col items-end leading-tight max-w-[150px]">
            <span className="font-label-sm text-label-sm font-semibold text-on-surface truncate w-full text-right">
              {loading ? "…" : displayName(user)}
            </span>
            <span className="text-[11px] text-secondary truncate w-full text-right">
              {loading ? "Checking session" : user?.email ?? "Not signed in"}
            </span>
          </div>
          <div
            className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-on-primary shadow-[0_1px_4px_rgba(0,74,198,0.2)] font-label-sm text-label-sm font-semibold"
            title={displayName(user)}
          >
            {loading ? (
              <span className="material-symbols-outlined text-[18px]">person</span>
            ) : (
              initials(user)
            )}
          </div>
          <button
            onClick={handleSignOut}
            disabled={signingOut}
            className="p-1.5 rounded-lg text-secondary hover:text-on-surface hover:bg-surface-container-low disabled:opacity-50 cursor-pointer disabled:cursor-wait"
            type="button"
            title="Sign out"
            aria-label="Sign out"
          >
            <span className="material-symbols-outlined text-[18px]">
              {signingOut ? "hourglass_top" : "logout"}
            </span>
          </button>
        </div>
      </div>
    </header>
  );
}
