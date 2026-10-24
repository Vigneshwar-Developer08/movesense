"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { signUp } from "@/lib/auth";

export default function SignUpPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const getPasswordStrength = (val: string) => {
    if (!val || val.length < 4) {
      return { label: "Minimum 8 characters", color: "text-error" };
    } else if (val.length < 8) {
      return { label: "Too short (need 8+ chars)", color: "text-secondary" };
    } else {
      return {
        label: "Strong (8+ chars)",
        color: "text-tertiary font-semibold",
      };
    }
  };

  const strength = getPasswordStrength(password);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    if (password !== confirmPassword) {
      setFormError("Passwords do not match.");
      return;
    }
    if (password.length < 8) {
      setFormError("Password must be at least 8 characters.");
      return;
    }

    setIsLoading(true);
    try {
      await signUp(fullName, email, password);
      await refresh();
      router.replace("/dashboard");
      router.refresh();
    } catch (err: unknown) {
      const msg =
        err instanceof Error
          ? err.message
          : "Could not create the account. Please try again.";
      setFormError(msg);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background font-body-md text-body-md text-on-surface antialiased flex items-center justify-center p-gutter selection:bg-primary-container selection:text-white">
      <main className="w-full max-w-md">
        <div className="flex flex-col w-full">
          <div className="relative bg-surface-container-lowest rounded-xl shadow-xl border border-outline-variant/30 overflow-hidden p-6 sm:p-8 flex flex-col gap-6">
            <div className="absolute -top-12 -right-12 w-48 h-48 bg-primary-fixed/30 rounded-full blur-3xl pointer-events-none"></div>
            <div className="absolute -bottom-10 -left-10 w-40 h-40 bg-surface-container/60 rounded-full blur-2xl pointer-events-none"></div>

            {/* Brand Header */}
            <header className="relative flex flex-col gap-2.5">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-on-primary shadow-xs">
                  <span
                    className="material-symbols-outlined text-[20px]"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    view_in_ar
                  </span>
                </div>
                <span className="font-headline-sm text-headline-sm tracking-tight text-on-surface">
                  Movesense 3D Trajectory
                </span>
              </div>
              <div>
                <h1 className="font-headline-md text-headline-md text-on-surface tracking-tight">
                  Create an Account
                </h1>
                <p className="font-body-sm text-body-sm text-secondary mt-0.5">
                  Enter your details below to create an account directly in Supabase.
                </p>
              </div>
            </header>

            {/* Sign Up Form */}
            <form onSubmit={handleSubmit} className="relative flex flex-col gap-4">
              {/* Full Name */}
              <div className="flex flex-col gap-1.5">
                <label
                  className="font-label-md text-label-md text-secondary"
                  htmlFor="full-name"
                >
                  Full Name
                </label>
                <div className="relative flex items-center">
                  <span className="material-symbols-outlined absolute left-3 text-secondary text-[18px]">
                    badge
                  </span>
                  <input
                    className="w-full h-10 pl-9 pr-3 rounded-lg bg-surface-container-low text-on-surface font-body-md text-body-md border border-outline-variant/30 focus:bg-surface-container-lowest focus:outline-none focus:ring-2 focus:ring-primary/40 focus:shadow-sm transition-all"
                    id="full-name"
                    name="fullName"
                    autoComplete="name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="e.g. Dr. Kristina Lindqvist"
                    required
                    type="text"
                  />
                </div>
              </div>

              {/* Organization Email */}
              <div className="flex flex-col gap-1.5">
                <label
                  className="font-label-md text-label-md text-secondary"
                  htmlFor="org-email"
                >
                  Email
                </label>
                <div className="relative flex items-center">
                  <span className="material-symbols-outlined absolute left-3 text-secondary text-[18px]">
                    mail
                  </span>
                  <input
                    className="w-full h-10 pl-9 pr-3 rounded-lg bg-surface-container-low text-on-surface font-body-md text-body-md border border-outline-variant/30 focus:bg-surface-container-lowest focus:outline-none focus:ring-2 focus:ring-primary/40 focus:shadow-sm transition-all"
                    id="org-email"
                    name="email"
                    autoComplete="username"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="k.lindqvist@karolinska.se"
                    required
                    type="email"
                  />
                </div>
              </div>

              {/* Passwords */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <label
                    className="font-label-md text-label-md text-secondary"
                    htmlFor="password"
                  >
                    Password
                  </label>
                  <div className="relative flex items-center">
                    <input
                      className="w-full h-10 px-3 pr-8 rounded-lg bg-surface-container-low text-on-surface font-body-md text-body-md border border-outline-variant/30 focus:bg-surface-container-lowest focus:outline-none focus:ring-2 focus:ring-primary/40 focus:shadow-sm transition-all"
                      id="password"
                      name="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="At least 8 chars"
                      autoComplete="new-password"
                      minLength={8}
                      required
                      type={showPassword ? "text" : "password"}
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-2 text-outline hover:text-on-surface p-1 cursor-pointer"
                      aria-label="Toggle password visibility"
                    >
                      <span className="material-symbols-outlined text-[16px]">
                        {showPassword ? "visibility_off" : "visibility"}
                      </span>
                    </button>
                  </div>
                </div>

                <div className="flex flex-col gap-1.5">
                  <label
                    className="font-label-md text-label-md text-secondary"
                    htmlFor="confirm-password"
                  >
                    Confirm Password
                  </label>
                  <div className="relative flex items-center">
                    <input
                      className="w-full h-10 px-3 rounded-lg bg-surface-container-low text-on-surface font-body-md text-body-md border border-outline-variant/30 focus:bg-surface-container-lowest focus:outline-none focus:ring-2 focus:ring-primary/40 focus:shadow-sm transition-all"
                      id="confirm-password"
                      name="confirmPassword"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="Repeat password"
                      autoComplete="new-password"
                      minLength={8}
                      required
                      type={showPassword ? "text" : "password"}
                    />
                  </div>
                </div>
              </div>

              {/* Password strength feedback */}
              {password && (
                <div className="flex items-center justify-between px-1 text-xs font-mono">
                  <span className="text-secondary text-[11px]">
                    Security Grade:
                  </span>
                  <span className={`text-[11px] ${strength.color}`}>
                    {strength.label}
                  </span>
                </div>
              )}

              {/* Form Error */}
              {formError && (
                <div className="rounded-lg bg-error-container p-3 text-body-sm text-on-error-container border border-error/20 flex items-start gap-2">
                  <span className="material-symbols-outlined text-[18px] text-error shrink-0 mt-0.5">
                    error
                  </span>
                  <span>{formError}</span>
                </div>
              )}

              <div className="flex items-center gap-2 rounded-lg bg-surface-container-low px-3 py-2 text-[11px] text-secondary border border-outline-variant/20">
                <span className="material-symbols-outlined text-[15px] text-primary">
                  cloud_done
                </span>
                <span>
                  Direct account creation with Supabase.
                </span>
              </div>

              <button
                className="w-full h-11 rounded-lg bg-primary text-on-primary font-headline-sm text-headline-sm tracking-normal flex items-center justify-center gap-2 shadow-md hover:bg-on-primary-fixed-variant active:scale-[0.99] transition-all mt-1 cursor-pointer disabled:opacity-70"
                type="submit"
                disabled={isLoading}
              >
                {isLoading ? (
                  <span className="inline-block w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
                ) : (
                  <>
                    <span className="material-symbols-outlined text-[20px]">
                      how_to_reg
                    </span>
                    <span>Create Account</span>
                  </>
                )}
              </button>
            </form>

            <footer className="flex flex-col items-center gap-4 pt-2">
              <div className="flex items-center gap-1.5 font-body-sm text-body-sm text-secondary">
                <span>Already have an account?</span>
                <Link
                  href="/signin"
                  className="text-primary font-semibold hover:underline"
                >
                  Sign In
                </Link>
              </div>
            </footer>
          </div>
        </div>
      </main>
    </div>
  );
}
