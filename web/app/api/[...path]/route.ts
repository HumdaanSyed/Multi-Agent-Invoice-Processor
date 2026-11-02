import type { NextRequest } from "next/server";
import { isAllowed } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { apiInternalUrl } from "@/lib/config";

/**
 * Same-origin proxy to FastAPI. The browser only ever talks to this app;
 * the backend sits on a private network with no published port.
 *
 * Every request: verify the session (getClaims checks the JWT signature),
 * then forward the user's Supabase access token as a Bearer header. Only
 * that token is forwarded - never a user-id header - and FastAPI verifies
 * it again on its own (app/auth.py), so this hop is not trusted blindly.
 * It is also what lets EventSource (which can't set headers) authenticate:
 * the browser sends its HttpOnly cookie, this handler adds the header.
 */

// Method + path allowlist: only the endpoints the UI uses. /health/ready
// (deep readiness) and /docs are deliberately not reachable from outside.
const ROUTES: [string, RegExp][] = [
  ["GET", /^health$/],
  ["GET", /^invoices$/],
  ["POST", /^invoices$/],
  ["POST", /^invoices\/reserve$/],
  ["GET", /^invoices\/[\w-]{1,64}$/],
  ["GET", /^invoices\/[\w-]{1,64}\/stream$/],
  ["POST", /^invoices\/[\w-]{1,64}\/resume$/],
  ["GET", /^export\.csv$/],
  ["GET", /^export\/status$/],
  ["GET", /^inbox$/],
  ["POST", /^inbox\/(download|mark)$/],
  ["GET", /^inbox\/files\/[^/]{1,255}$/],
];

const FORWARD_REQUEST_HEADERS = ["content-type", "accept"];
const FORWARD_RESPONSE_HEADERS = ["content-type", "content-disposition", "cache-control", "retry-after"];

function error(status: number, code: string, message: string) {
  return Response.json({ error: code, message }, { status });
}

async function handle(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const segments = (await ctx.params).path;
  const path = segments.map(encodeURIComponent).join("/");
  // "." / ".." would be normalized away by fetch, reaching a different path than the one allowlisted.
  if (segments.some((s) => s === "." || s === "..") || !ROUTES.some(([method, re]) => method === request.method && re.test(path))) {
    return error(404, "not_found", "Not found.");
  }

  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) return error(401, "not_authenticated", "Sign in to continue.");
  if (!isAllowed(userId)) return error(403, "forbidden", "This account doesn't have access.");
  // getClaims just verified this session's token; getSession hands us the raw value to forward.
  const token = (await supabase.auth.getSession()).data.session?.access_token;
  if (!token) return error(401, "not_authenticated", "Sign in to continue.");

  const headers = new Headers({ authorization: `Bearer ${token}` });
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${apiInternalUrl()}/${path}${request.nextUrl.search}`, {
      method: request.method,
      headers,
      body: request.method === "POST" ? request.body : undefined,
      // Streams the upload through instead of buffering it here.
      duplex: "half",
      // A closed tab or a closed EventSource cancels the backend request too.
      signal: request.signal,
      cache: "no-store",
    } as RequestInit);
  } catch {
    return error(502, "upstream_unavailable", "The backend isn't reachable right now.");
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  // Tell nginx not to buffer the SSE stream (or anything else) on the way out.
  responseHeaders.set("x-accel-buffering", "no");
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = handle;
export const POST = handle;
