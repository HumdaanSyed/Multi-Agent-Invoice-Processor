export { cn } from "cn"

/** Shared by the export bar's "last written" text and the home screen's
 * recent-runs list (both format an ISO timestamp from the backend the same
 * way) - a single home for this instead of two independently-written
 * copies that could silently drift in locale/format. `fallback` covers a
 * missing/unparseable timestamp; defaults to the empty string since most
 * call sites just splice this into a sentence. */
export function formatDateTime(iso: string | null | undefined, fallback = ""): string {
  if (!iso) return fallback;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return fallback;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
