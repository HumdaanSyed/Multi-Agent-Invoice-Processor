/**
 * Carries the just-selected File across the client-side navigation from
 * the upload zone to /runs/[threadId]. A thread_id is minted by
 * reserveRun() before the route change, but the File object itself can't
 * round-trip through a URL or sessionStorage - this is plain module-scope
 * memory, which survives an App Router client-side navigation (the module
 * isn't re-evaluated) but not a hard reload, a bookmark, or a link shared
 * with someone else. /runs/[threadId] treats a cache miss as normal - it
 * just means "revisiting a run, not the tab that started it" - and falls
 * back to fetching status from the backend with no PDF preview.
 */

const cache = new Map<string, File>();

export function setRunFile(threadId: string, file: File): void {
  cache.set(threadId, file);
}

export function getRunFile(threadId: string): File | undefined {
  return cache.get(threadId);
}

export function clearRunFile(threadId: string): void {
  cache.delete(threadId);
}
