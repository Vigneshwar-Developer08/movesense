"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";

interface SidebarProps {
  activeModule?: string;
  onSelectModule?: (module: string) => void;
  isOpen?: boolean;
  onClose?: () => void;
}

export function Sidebar({
  activeModule,
  onSelectModule,
  isOpen = false,
  onClose,
}: SidebarProps) {
  const pathname = usePathname();
  const { signOut } = useAuth();

  const navItems = [
    {
      id: "dashboard",
      label: "Dashboard",
      icon: "dashboard",
      href: "/dashboard",
    },
    {
      id: "live-tracking",
      label: "Live Tracking",
      icon: "radar",
      href: "/live-tracking",
    },
    {
      id: "devices",
      label: "Devices",
      icon: "sensors",
      href: "/devices",
    },
    {
      id: "report",
      label: "Report",
      icon: "summarize",
      href: "/report",
    },
  ];

  return (
    <>
      {/* Mobile Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 backdrop-blur-xs z-40 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`fixed left-0 top-0 h-full w-64 bg-surface-container-lowest z-50 flex flex-col justify-between shadow-[0_1px_8px_rgba(0,0,0,0.04)] border-r border-outline-variant/30 transition-transform duration-200 lg:translate-x-0 ${
          isOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex flex-col">
          {/* Logo Header */}
          <div className="h-14 px-gutter flex items-center justify-between bg-surface-container-lowest border-b border-outline-variant/20">
            <Link
              href="/dashboard"
              className="flex items-center gap-space-sm hover:opacity-90 transition-opacity"
            >
              <div className="w-8 h-8 rounded-lg bg-primary-container flex items-center justify-center text-on-primary shadow-xs">
                <span className="material-symbols-outlined text-[18px]">
                  view_in_ar
                </span>
              </div>
              <div className="flex flex-col justify-center leading-none">
                <span className="font-headline-sm text-headline-sm text-on-surface font-semibold tracking-tight">
                  Movesense 3D
                </span>
                <span className="font-label-sm text-label-sm text-on-surface-variant font-medium mt-0.5">
                  Trajectory Studio
                </span>
              </div>
            </Link>

            {/* Close Button on Mobile */}
            <button
              onClick={onClose}
              className="lg:hidden p-1 rounded-md text-secondary hover:text-on-surface hover:bg-surface-container-low cursor-pointer"
              type="button"
            >
              <span className="material-symbols-outlined text-[20px]">
                close
              </span>
            </button>
          </div>

          {/* Section Title */}
          <div className="px-gutter pt-space-lg pb-space-xs">
            <span className="font-label-sm text-label-sm uppercase text-outline tracking-wider font-semibold">
              Clinical Modules
            </span>
          </div>

          {/* Navigation Items */}
          <nav className="flex flex-col gap-space-xs px-space-sm mt-space-xs">
            {navItems.map((item) => {
              const isCurrent =
                activeModule === item.id ||
                (item.href === "/dashboard"
                  ? pathname === "/dashboard" || pathname === "/"
                  : item.href === pathname);

              return (
                <Link
                  key={item.id}
                  href={item.href || "#"}
                  onClick={() => {
                    onSelectModule?.(item.id);
                    onClose?.();
                  }}
                  className={`flex items-center gap-space-md px-space-md py-space-sm rounded-lg transition-all text-left w-full cursor-pointer ${
                    isCurrent
                      ? "bg-primary-container text-on-primary font-medium shadow-xs"
                      : "text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface"
                  }`}
                >
                  <span
                    className={`material-symbols-outlined text-[20px] ${
                      isCurrent ? "text-on-primary" : "text-secondary"
                    }`}
                  >
                    {item.icon}
                  </span>
                  <span className="font-label-md text-label-md">
                    {item.label}
                  </span>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Footer Section: Pipeline Badge */}
        <div className="p-space-md flex flex-col gap-space-sm border-t border-outline-variant/20">
          <div className="p-space-md bg-surface-container-low rounded-xl border border-outline-variant/20">
            <div className="flex items-center justify-between">
              <span className="font-label-sm text-label-sm text-outline uppercase font-semibold">
                Inertial Pipeline
              </span>
              <span className="font-metric-code text-metric-code text-tertiary font-bold">
                v0.2.0
              </span>
            </div>
            <div className="font-body-sm text-body-sm text-on-surface-variant mt-space-xs flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-tertiary-container animate-pulse"></span>
              IMU9 · 52 Hz default · 208 Hz max
            </div>
          </div>

          <div className="flex items-center justify-between px-space-sm pt-1">
            <button
              onClick={() => {
                onClose?.();
                void signOut();
              }}
              className="text-xs text-secondary hover:text-primary transition-colors flex items-center gap-1 cursor-pointer"
              type="button"
            >
              <span className="material-symbols-outlined text-[15px]">
                logout
              </span>
              Sign Out
            </button>
            <span className="text-[10px] text-outline font-mono">Local backend</span>
          </div>
        </div>
      </aside>
    </>
  );
}
