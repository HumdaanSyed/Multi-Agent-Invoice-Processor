import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";
import { COOKIE_FLAGS, isAllowed, supabaseKey, supabaseUrl } from "@/lib/supabase/env";

/**
 * Runs before every page and /api request: refreshes the Supabase session
 * cookies (rotating an expired access token with the refresh token) and
 * sends anyone not signed in, or not on the allowlist, to /login.
 *
 * This is the convenience layer, not the security boundary: the /api
 * route handler re-verifies the session, and FastAPI independently
 * verifies the JWT and authorizes every request (app/auth.py).
 */
export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });
  const supabase = createServerClient(supabaseUrl(), supabaseKey(), {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll: (toSet) => {
        toSet.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        toSet.forEach(({ name, value, options }) => response.cookies.set(name, value, { ...options, ...COOKIE_FLAGS }));
      },
    },
  });

  // Must run right after creating the client: it is what refreshes the session.
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  const { pathname } = request.nextUrl;

  if (userId && isAllowed(userId)) return response;

  const isApi = pathname.startsWith("/api/");
  if (isApi) {
    return NextResponse.json(
      userId
        ? { error: "forbidden", message: "This account doesn't have access." }
        : { error: "not_authenticated", message: "Sign in to continue." },
      { status: userId ? 403 : 401 },
    );
  }
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = userId ? "?error=denied" : "";
  const redirect = NextResponse.redirect(url);
  // Keep any refreshed/cleared session cookies on the redirect too.
  response.cookies.getAll().forEach((c) => redirect.cookies.set(c));
  return redirect;
}

export const config = {
  // Everything except the login flow, the container healthcheck and static assets.
  matcher: ["/((?!login|auth/|healthz|_next/static|_next/image|favicon.ico).*)"],
};
