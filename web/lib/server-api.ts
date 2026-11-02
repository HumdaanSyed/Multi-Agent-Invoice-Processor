import "server-only";
import { parseResponse } from "./api";
import { apiInternalUrl } from "./config";
import { createClient } from "./supabase/server";
import type { ExportStatusResponse, HealthResponse, RunListResponse } from "./types";

// The home page awaits these on every render, so a hung backend must not hang the page.
const SERVER_FETCH_TIMEOUT_MS = 5000;

/** Server-component fetch straight to FastAPI with the signed-in user's
 * access token. proxy.ts has already verified (and refreshed) the session
 * before any page renders; FastAPI verifies the token again. */
async function serverRequest<T>(path: string): Promise<T> {
  const supabase = await createClient();
  const token = (await supabase.auth.getSession()).data.session?.access_token;
  const response = await fetch(`${apiInternalUrl()}${path}`, {
    headers: token ? { authorization: `Bearer ${token}` } : {},
    signal: AbortSignal.timeout(SERVER_FETCH_TIMEOUT_MS),
    cache: "no-store",
  });
  return parseResponse<T>(response);
}

export const getHealth = () => serverRequest<HealthResponse>("/health");
export const listRuns = (limit = 20) => serverRequest<RunListResponse>(`/invoices?limit=${limit}`);
export const getExportStatus = () => serverRequest<ExportStatusResponse>("/export/status");
