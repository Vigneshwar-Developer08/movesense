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
  setSessionCookie,
  mapSupabaseUser,
  type AuthUser,
} from "@/lib/auth";
import { supabase } from "@/lib/supabase";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  /** Re-read the session (call after sign-in/sign-up). */
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

  // Listen to Supabase auth events in real-time
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

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      if (cancelled || !mountedRef.current) return;
      if (session?.user) {
        setUser(mapSupabaseUser(session.user));
        setSessionCookie(true);
      } else {
        setUser(null);
        setSessionCookie(false);
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
      subscription.unsubscribe();
    };
  }, []);

  // Route protection guard
  useEffect(() => {
    if (loading) return;

    if (!user && !isPublicRoute(pathname)) {
      const next =
        pathname && pathname !== "/"
          ? `?next=${encodeURIComponent(pathname)}`
          : "";
      router.replace(`/signin${next}`);
    } else if (user && isPublicRoute(pathname)) {
      router.replace("/dashboard");
    }
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
