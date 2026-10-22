import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Cookie set by the local API (server/api.py) on sign-in/sign-up.
 * It is HttpOnly, but the Next server still receives it on every request.
 */
const SESSION_COOKIE = "movesense_session";

const AUTH_ROUTES = ["/signin", "/signup"];

function isAuthRoute(pathname: string): boolean {
  return AUTH_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

/**
 * Fast, cookie-presence gate. The API remains the source of truth: the client
 * AuthProvider validates the token against `/api/auth/me` and clears a stale
 * cookie, so this proxy only avoids rendering protected pages for visitors.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const signedIn = request.cookies.has(SESSION_COOKIE);

  if (isAuthRoute(pathname)) {
    if (signedIn) {
      return NextResponse.redirect(new URL("/dashboard", request.url));
    }
    return NextResponse.next();
  }

  if (!signedIn) {
    const signInUrl = new URL("/signin", request.url);
    if (pathname !== "/") {
      signInUrl.searchParams.set("next", `${pathname}${search}`);
    }
    return NextResponse.redirect(signInUrl);
  }

  return NextResponse.next();
}

export const config = {
  // Skip API routes, Next internals, and static files (anything with a dot).
  matcher: ["/((?!api|_next|favicon.ico|.*\\..*).*)"],
};
