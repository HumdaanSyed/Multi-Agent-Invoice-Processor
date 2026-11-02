import type { NextRequest } from "next/server";

/** Public origin for OAuth redirects. SITE_URL behind a reverse proxy (where
 * request.url is the container's own address); the request origin locally. */
export function siteUrl(request: NextRequest): string {
  return (process.env.SITE_URL || request.nextUrl.origin).replace(/\/+$/, "");
}
