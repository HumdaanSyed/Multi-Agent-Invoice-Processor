/**
 * The product name lives in exactly one place (docs/FRONTEND_PLAN.md) so it
 * can be changed with a one-line edit. Import this everywhere instead of
 * writing "Verity" as a string literal.
 */
export const PRODUCT_NAME = "Verity";

export const PRODUCT_TAGLINE = "Invoices in, structured data out.";

declare global {
  interface Window {
    /** Injected by app/layout.tsx from the server's API_PUBLIC_URL env var,
     * so a single prebuilt image can point at a different backend per
     * deployment - NEXT_PUBLIC_* is inlined at `next build` time and can't. */
    __VERITY_API_BASE_URL__?: string;
  }
}

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

function trimTrailingSlashes(url: string): string {
  return url.replace(/\/+$/, "");
}

/**
 * Base URL of the FastAPI backend (see docs/api.md) as the *browser* must
 * reach it - the client-side fetches, the SSE stream, and the CSV download
 * link all use this. Resolved at request time, not build time: on the
 * server from `API_PUBLIC_URL`, in the browser from the value app/layout.tsx
 * injected from that same variable. `NEXT_PUBLIC_API_BASE_URL` (inlined at
 * build) and the local dev default remain as fallbacks, so `npm run dev`
 * against `uvicorn app.main:app --reload` still needs no configuration.
 */
export const API_BASE_URL: string = trimTrailingSlashes(
  (typeof window === "undefined" ? process.env.API_PUBLIC_URL : window.__VERITY_API_BASE_URL__) ||
    process.env.NEXT_PUBLIC_API_BASE_URL ||
    DEFAULT_API_BASE_URL,
);

/**
 * Base URL for fetches made from the Next.js *server* (the home page's
 * health check and recent-runs list). Inside Docker Compose the browser
 * reaches the backend at http://localhost:8000 but this server reaches it
 * at http://backend:8000, so it can differ from API_BASE_URL - set
 * `API_INTERNAL_URL` for that; otherwise it's the same URL.
 */
export const SERVER_API_BASE_URL: string = trimTrailingSlashes(process.env.API_INTERNAL_URL || API_BASE_URL);
