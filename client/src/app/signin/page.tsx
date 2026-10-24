"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { signIn } from "@/lib/auth";

export default function SignInPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setIsLoading(true);

    try {
      await signIn(email, password, rememberMe);
      await refresh();
      const next = new URLSearchParams(window.location.search).get("next");
      const destination =
        next && next.startsWith("/") && !next.startsWith("//")
          ? next
          : "/dashboard";
      router.replace(destination);
      router.refresh();
    } catch (err: unknown) {
      const msg =
        err instanceof Error ? err.message : "Sign in failed. Please try again.";
      setFormError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background font-body-md text-body-md text-on-surface antialiased flex items-center justify-center p-gutter selection:bg-primary-container selection:text-white">
      <main className="w-full max-w-md">
        <div className="flex flex-col w-full">
          <div className="relative w-full overflow-hidden rounded-xl bg-surface-container-lowest shadow-xl border border-outline-variant/30">
            {/* Ambient Accent */}
            <div className="absolute inset-x-0 top-0 h-1.5 bg-gradient-to-r from-primary via-primary-container to-surface-tint"></div>
            <div className="absolute -right-16 -top-16 w-48 h-48 rounded-full bg-primary/5 pointer-events-none blur-2xl"></div>
            <div className="absolute -left-16 -bottom-16 w-48 h-48 rounded-full bg-primary/5 pointer-events-none blur-2xl"></div>

            <div className="p-margin-desktop sm:p-gutter-desktop flex flex-col gap-space-xl">
              {/* Brand Header */}
              <header className="flex flex-col items-center text-center">
                <div className="relative mb-space-md flex items-center justify-center">
                  <div className="w-12 h-12 rounded-lg bg-surface-container-low flex items-center justify-center text-primary shadow-xs border border-outline-variant/20">
                    <svg
                      className="w-7 h-7"
                      fill="none"
                      stroke="currentColor"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="1.75"
                      viewBox="0 0 28 28"
                    >
                      <path
                        className="stroke-primary"
                        d="M14 3v11m0 0l9 5.5M14 14l-9 5.5"
                      ></path>
                      <circle
                        className="fill-primary stroke-primary"
                        cx="14"
                        cy="3"
                        r="2"
                      ></circle>
                      <circle
                        className="fill-primary stroke-primary"
                        cx="23"
                        cy="19.5"
                        r="2"
                      ></circle>
                      <circle
                        className="fill-primary stroke-primary"
                        cx="5"
                        cy="19.5"
                        r="2"
                      ></circle>
                      <circle
                        className="fill-white stroke-primary"
                        cx="14"
                        cy="14"
                        r="1.5"
                      ></circle>
                      <path
                        className="stroke-primary/40 stroke-dasharray-[2,2]"
                        d="M8 8.5c3-2 9-2 12 0"
                      ></path>
                    </svg>
                  </div>
                </div>

                <h1 className="font-headline-md text-headline-md text-on-surface tracking-tight">
                  Movesense 3D Trajectory
                </h1>
                <p className="font-body-sm text-body-sm text-secondary mt-1 max-w-xs">
                  Precision Biomechanical Motion &amp; IMU Telemetry Platform
                </p>
              </header>

              {/* Status Notifications */}
              {formError && (
                <div className="rounded-lg bg-error-container p-3 text-body-sm text-on-error-container border border-error/20 flex items-start gap-2">
                  <span className="material-symbols-outlined text-[18px] text-error shrink-0 mt-0.5">
                    error
                  </span>
                  <span>{formError}</span>
                </div>
              )}

              {/* Sign In Form */}
              <form onSubmit={handleSubmit} className="flex flex-col gap-space-lg">
                {/* Email Input Field */}
                <div className="flex flex-col gap-1.5">
                  <label
                    className="font-label-md text-label-md text-on-surface-variant flex items-center gap-1.5"
                    htmlFor="email"
                  >
                    <span className="material-symbols-outlined text-[15px] text-secondary">
                      mail
                    </span>{" "}
                    Email
                  </label>
                  <div className="relative flex items-center">
                    <input
                      className="w-full h-9 px-space-md pr-10 bg-surface-container-low text-on-surface font-body-md text-body-md placeholder:text-outline rounded-lg focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40 focus:shadow-sm border border-outline-variant/30 transition-all duration-150"
                      id="email"
                      name="email"
                      autoComplete="username"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@lab.example"
                      required
                      type="email"
                    />
                    <span className="material-symbols-outlined absolute right-space-md text-[18px] text-outline pointer-events-none">
                      alternate_email
                    </span>
                  </div>
                </div>

                {/* Password Field */}
                <div className="flex flex-col gap-1.5">
                  <label
                    className="font-label-md text-label-md text-on-surface-variant flex items-center gap-1.5"
                    htmlFor="password"
                  >
                    <span className="material-symbols-outlined text-[15px] text-secondary">
                      lock
                    </span>{" "}
                    Password
                  </label>
                  <div className="relative flex items-center">
                    <input
                      className="w-full h-9 pl-space-md pr-10 bg-surface-container-low text-on-surface font-body-md text-body-md placeholder:text-outline rounded-lg focus:outline-none focus:bg-surface-container-lowest focus:ring-2 focus:ring-primary/40 focus:shadow-sm border border-outline-variant/30 transition-all duration-150 tracking-wider"
                      id="password"
                      name="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter password"
                      minLength={6}
                      required
                      type={showPassword ? "text" : "password"}
                    />
                    <button
                      aria-label="Toggle password visibility"
                      className="absolute right-2.5 flex items-center justify-center text-outline hover:text-on-surface transition-colors p-1 cursor-pointer"
                      onClick={() => setShowPassword(!showPassword)}
                      type="button"
                    >
                      <span className="material-symbols-outlined text-[18px]">
                        {showPassword ? "visibility_off" : "visibility"}
                      </span>
                    </button>
                  </div>
                </div>

                {/* Remember Me Checkbox */}
                <div className="flex items-center justify-between pt-1">
                  <label className="flex items-center gap-2.5 cursor-pointer select-none group">
                    <input
                      checked={rememberMe}
                      onChange={(e) => setRememberMe(e.target.checked)}
                      className="w-4 h-4 rounded bg-surface-container-low text-primary focus:ring-0 focus:outline-none cursor-pointer accent-primary"
                      id="rememberMe"
                      name="rememberMe"
                      type="checkbox"
                    />
                    <span className="font-body-sm text-body-sm text-on-surface-variant group-hover:text-on-surface transition-colors">
                      Remember me
                    </span>
                  </label>
                </div>

                {/* Supabase authentication security badge */}
                <div className="flex items-center gap-2 rounded-lg bg-surface-container-low px-3 py-2 text-[11px] text-secondary border border-outline-variant/20">
                  <span className="material-symbols-outlined text-[15px] text-primary">
                    verified_user
                  </span>
                  <span>Direct Supabase authentication.</span>
                </div>

                {/* Submit Button */}
                <button
                  className="w-full h-10 mt-1 rounded-lg bg-primary text-on-primary font-headline-sm text-headline-sm tracking-normal flex items-center justify-center gap-2 shadow-md hover:bg-on-primary-fixed-variant active:scale-[0.99] transition-all duration-150 cursor-pointer disabled:opacity-70"
                  type="submit"
                  disabled={isLoading}
                >
                  {isLoading ? (
                    <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                  ) : (
                    <>
                      <span className="material-symbols-outlined text-[19px]">
                        login
                      </span>
                      <span>Sign In</span>
                    </>
                  )}
                </button>
              </form>

              {/* Sign Up Link */}
              <div className="text-center font-body-sm text-body-sm text-secondary">
                Don&apos;t have an account?{" "}
                <Link
                  href="/signup"
                  className="text-primary font-semibold hover:text-on-primary-fixed-variant transition-colors"
                >
                  Sign Up
                </Link>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
