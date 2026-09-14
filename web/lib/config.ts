/**
 * The product name lives in exactly one place (docs/FRONTEND_PLAN.md) so it
 * can be changed with a one-line edit. Import this everywhere instead of
 * writing "Verity" as a string literal.
 */
export const PRODUCT_NAME = "Verity";

export const PRODUCT_TAGLINE = "Invoices in, structured data out.";

/**
 * Base URL of the FastAPI backend (see docs/api.md). Falls back to the
 * local dev server so `npm run dev` works against `uvicorn app.main:app
 * --reload` with no .env.local required.
 */
export const API_BASE_URL: string =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://127.0.0.1:8000";
