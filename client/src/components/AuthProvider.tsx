"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  fetchCurrentUser,
  signOutRequest,
  type AuthUser,
} from "@/lib/auth";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  /** Re-read the cookie session (call after sign-in/sign-up). */
  refresh: () => Promise<AuthUser | null>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const PUBLIC_ROUTES = ["/signin", "/signup"];

function isPublicRoute(pathname: string): boolean {
  return PUBLIC_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(true);
  const pathnameRef = useRef(pathname);

  useEffect(() => {
    pathnameRef.current = pathname;
  }, [pathname]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    const next = await fetchCurrentUser();
    if (mountedRef.current) setUser(next);
    return next;
  }, []);

  // Read the cookie session once on mount. The Proxy already handles the
  // first navigation; this call also validates the token against the API and
  // keeps the header in sync after a refresh.
  useEffect(() => {
    let cancelled = false;
    fetchCurrentUser()
      .then((next) => {
        if (cancelled || !mountedRef.current) return;
        setUser(next);
      })
      .finally(() => {
        if (!cancelled && mountedRef.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Client-side guard as a backstop to Proxy: a stale cookie must not leave a
  // protected page rendering without a user.
  useEffect(() => {
    if (loading || user) return;
    if (isPublicRoute(pathname)) return;
    const next =
      pathname && pathname !== "/"
        ? `?next=${encodeURIComponent(pathname)}`
        : "";
    router.replace(`/signin${next}`);
  }, [loading, user, pathname, router]);

  const signOut = useCallback(async () => {
    await signOutRequest();
    if (mountedRef.current) setUser(null);
    router.replace("/signin");
    router.refresh();
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, refresh, signOut }),
    [user, loading, refresh, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
