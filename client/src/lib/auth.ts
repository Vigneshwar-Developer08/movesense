import { apiFetch } from "./api";

/** Safe subset of the local user record returned by the auth API. */
export interface AuthUser {
  id: string;
  full_name: string;
  email: string;
  created_at: string | null;
  last_login_at: string | null;
}

interface UserResponse {
  user: AuthUser;
}

function jsonBody(payload: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

/** Current user from the HttpOnly cookie, or null when not signed in. */
export async function fetchCurrentUser(): Promise<AuthUser | null> {
  try {
    const data = await apiFetch<UserResponse>("/api/auth/me", {
      cache: "no-store",
    });
    return data.user;
  } catch {
    return null;
  }
}

export async function signIn(
  email: string,
  password: string,
  remember: boolean,
): Promise<AuthUser> {
  const data = await apiFetch<UserResponse>(
    "/api/auth/signin",
    jsonBody({ email, password, remember }),
  );
  return data.user;
}

export async function signUp(
  fullName: string,
  email: string,
  password: string,
): Promise<AuthUser> {
  const data = await apiFetch<UserResponse>(
    "/api/auth/signup",
    jsonBody({ full_name: fullName, email, password }),
  );
  return data.user;
}

export async function signOutRequest(): Promise<void> {
  try {
    await apiFetch<{ signed_out: boolean }>("/api/auth/signout", {
      method: "POST",
    });
  } catch {
    // Local sign-out should always clear the client view, even if the API is down.
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
