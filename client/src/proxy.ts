import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Fast session cookie set on client sign-in/sign-up.
 */
const SESSION_COOKIE = "movesense_session";

const AUTH_ROUTES = ["/signin", "/signup"];

function isAuthRoute(pathname: string): boolean {
  return AUTH_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );
}

/**
 * Next.js route gate proxy for authentication.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  const hasMovesenseCookie = request.cookies.has(SESSION_COOKIE);
  const hasSupabaseCookie = Array.from(request.cookies.getAll()).some(
    (c) => c.name.startsWith("sb-") && c.name.endsWith("-auth-token"),
  );
  const signedIn = hasMovesenseCookie || hasSupabaseCookie;

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
