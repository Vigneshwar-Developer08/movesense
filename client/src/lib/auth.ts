import type { User } from "@supabase/supabase-js";
import { supabase } from "./supabase";

/** Safe subset of the user record returned by Supabase Auth. */
export interface AuthUser {
  id: string;
  full_name: string;
  email: string;
  created_at: string | null;
  last_login_at: string | null;
}

export const SESSION_COOKIE = "movesense_session";

/**
 * Manage a fast session cookie for Next.js proxy route gating.
 */
export function setSessionCookie(active: boolean, remember: boolean = true) {
  if (typeof document === "undefined") return;
  if (active) {
    const maxAge = remember ? 60 * 60 * 24 * 30 : 60 * 60 * 24; // 30 days vs 1 day
    document.cookie = `${SESSION_COOKIE}=1; path=/; max-age=${maxAge}; SameSite=Lax`;
  } else {
    document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
  }
}

/** Converts a Supabase Auth User object into the application's AuthUser shape. */
export function mapSupabaseUser(user: User | null): AuthUser | null {
  if (!user) return null;
  const fullName =
    (user.user_metadata?.full_name as string) ||
    (user.user_metadata?.display_name as string) ||
    (user.user_metadata?.name as string) ||
    (user.email ? user.email.split("@")[0] : "User");

  return {
    id: user.id,
    full_name: fullName,
    email: user.email || "",
    created_at: user.created_at || null,
    last_login_at: user.last_sign_in_at || null,
  };
}

/** Get the currently logged-in user from the active Supabase session. */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  try {
    const {
      data: { session },
      error,
    } = await supabase.auth.getSession();
    if (error || !session?.user) {
      setSessionCookie(false);
      return null;
    }
    setSessionCookie(true);
    return mapSupabaseUser(session.user);
  } catch (err) {
    console.error("fetchCurrentUser error:", err);
    return null;
  }
}

/** Sign in with email and password via Supabase Auth. */
export async function signIn(
  email: string,
  password: string,
  remember: boolean = true,
): Promise<AuthUser> {
  const { data, error } = await supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });

  if (error) {
    throw new Error(error.message);
  }

  if (!data.user) {
    throw new Error("Unable to sign in. Please verify your credentials.");
  }

  setSessionCookie(true, remember);
  return mapSupabaseUser(data.user)!;
}

/** Register a new user with Supabase Auth without email confirmation. */
export async function signUp(
  fullName: string,
  email: string,
  password: string,
): Promise<AuthUser> {
  const { data, error } = await supabase.auth.signUp({
    email: email.trim(),
    password,
    options: {
      data: {
        full_name: fullName.trim(),
        display_name: fullName.trim(),
      },
    },
  });

  if (error) {
    throw new Error(error.message);
  }

  // If session is already created
  if (data.session) {
    setSessionCookie(true, true);
    return mapSupabaseUser(data.user)!;
  }

  // Attempt direct sign-in to establish immediate session
  try {
    return await signIn(email, password, true);
  } catch {
    return mapSupabaseUser(data.user)!;
  }
}

/** Sign out from Supabase Auth and clear session cookies. */
export async function signOutRequest(): Promise<void> {
  try {
    await supabase.auth.signOut();
  } catch (err) {
    console.warn("Sign out error:", err);
  } finally {
    setSessionCookie(false);
  }
}

export function displayName(user: AuthUser | null): string {
  if (!user) return "Guest";
  return user.full_name || user.email;
}

export function initials(user: AuthUser | null): string {
  const source = displayName(user).trim();
  if (!source) return "?";
  return source
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}
