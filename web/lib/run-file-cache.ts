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

interface CachedFile {
  file: File;
  claimed: boolean;
}

const cache = new Map<string, CachedFile>();

export function setRunFile(threadId: string, file: File): void {
  cache.set(threadId, { file, claimed: false });
}

/** Non-consuming read, for anything that just wants to display the file
 * (PdfPreview) regardless of whether the upload has started. */
export function getRunFile(threadId: string): File | undefined {
  return cache.get(threadId)?.file;
}

/**
 * Returns the cached File the first time this is called for a thread_id,
 * and undefined every time after - even across an unmount/remount of the
 * component that calls it, since the guard lives here in the module-scope
 * cache rather than a per-instance ref. Without this, a component instance
 * unmounting and remounting for the same thread_id while the first
 * createRun() call is still in flight (the user navigates away and back)
 * would find the same never-evicted File and fire a second concurrent
 * upload for a run that's already processing.
 */
export function claimRunFile(threadId: string): File | undefined {
  const entry = cache.get(threadId);
  if (!entry || entry.claimed) return undefined;
  entry.claimed = true;
  return entry.file;
}

export function clearRunFile(threadId: string): void {
  cache.delete(threadId);
}
