/**
 * The product name lives in exactly one place (docs/FRONTEND_PLAN.md) so it
 * can be changed with a one-line edit. Import this everywhere instead of
 * writing "Verity" as a string literal.
 */
export const PRODUCT_NAME = "Verity";

export const PRODUCT_TAGLINE = "Invoices in, structured data out.";

/**
 * Where the browser reaches the backend: always this app's own /api proxy
 * (app/api/[...path]/route.ts), which adds the user's token. The backend
 * itself is never exposed to the browser, so there is no public API URL
 * to configure and no CORS.
 */
export const BROWSER_API_BASE = "/api";

/**
 * Where the Next.js *server* reaches FastAPI (the /api proxy and the home
 * page's server-side fetches). Inside Docker Compose that's
 * http://backend:8000; locally the uvicorn default. Read at request time,
 * so one prebuilt image serves every deployment.
 */
export function apiInternalUrl(): string {
  return (process.env.API_INTERNAL_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
}
