"use client";

import { CheckCheck, Download, FileText, Inbox, Loader2, MailSearch, Play, Plug, XCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { formatMs } from "@/components/pipeline-steps";
import { Card, Chip, EmptyState, ErrorBox, pillButton, primaryButton, SkeletonBars } from "@/components/primitives";
import { ApiError, downloadInboxItem, inboxFileUrl, listInbox, markInboxItem, reserveRun } from "@/lib/api";
import { setRunFile } from "@/lib/run-file-cache";
import type { InboxItem, InboxSource } from "@/lib/types";

const SOURCES: { id: InboxSource; label: string; detail: string }[] = [
  { id: "gmail", label: "Gmail", detail: "gmail_imap.py · IMAP + App Password" },
  { id: "filesystem", label: "Local folder", detail: "filesystem_invoices.py · data/inbox/" },
];

interface LogEntry {
  key: number;
  at: string;
  tool: string;
  args: string;
  ms?: number;
  ok: boolean;
  note: string;
}

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : "Couldn't reach the backend.");

/**
 * The MCP demo surface: the backend (app/inbox.py) spawns the chosen MCP
 * server and calls its tools; this page exposes each of the three tools
 * as a button and logs every call with the server-measured duration.
 * "Process" hands a downloaded PDF to the normal upload flow, so it gets
 * the same live run page as a manual upload.
 */
export default function InboxPage() {
  const router = useRouter();
  const [source, setSource] = useState<InboxSource>("gmail");
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [listing, setListing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [downloads, setDownloads] = useState<Record<string, string[]>>({});
  const [marked, setMarked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);

  const nextKey = useRef(0);
  const gmail = source === "gmail";
  const record = (entry: Omit<LogEntry, "at" | "key">) =>
    setLog((prev) => [{ key: nextKey.current++, at: new Date().toLocaleTimeString(), ...entry }, ...prev].slice(0, 30));

  async function checkInbox() {
    setListing(true);
    setError(null);
    try {
      const res = await listInbox(source);
      setItems(res.items);
      setDownloads({});
      setMarked({});
      record({ tool: res.tool, args: `limit=10`, ms: res.ms, ok: true, note: gmail ? `${res.items.length} unread with a PDF` : `${res.items.length} PDF in data/inbox/` });
    } catch (err) {
      setError(errorText(err));
      record({ tool: "list_pending_invoices", args: "limit=10", ok: false, note: errorText(err) });
    } finally {
      setListing(false);
    }
  }

  async function download(item: InboxItem) {
    setBusy(`download:${item.id}`);
    setError(null);
    try {
      const res = await downloadInboxItem(source, item.id);
      setDownloads((d) => ({ ...d, [item.id]: res.files }));
      record({ tool: res.tool, args: `id=${item.id}`, ms: res.ms, ok: true, note: `${res.files.length} PDF saved on the server` });
    } catch (err) {
      setError(errorText(err));
      record({ tool: "download_invoice_pdfs", args: `id=${item.id}`, ok: false, note: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  async function mark(item: InboxItem) {
    setBusy(`mark:${item.id}`);
    setError(null);
    try {
      const res = await markInboxItem(source, item.id);
      setMarked((m) => ({ ...m, [item.id]: res.marked }));
      record({ tool: res.tool, args: `id=${item.id}`, ms: res.ms, ok: res.marked, note: !res.marked ? "server returned false" : gmail ? "marked as read in Gmail" : "moved to data/inbox/processed/" });
    } catch (err) {
      setError(errorText(err));
      record({ tool: "mark_processed", args: `id=${item.id}`, ok: false, note: errorText(err) });
    } finally {
      setBusy(null);
    }
  }

  // Same reserve-then-navigate flow as components/upload-zone.tsx.
  async function processFile(name: string) {
    setBusy(`process:${name}`);
    setError(null);
    try {
      const response = await fetch(inboxFileUrl(name));
      if (!response.ok) throw new Error(`Couldn't fetch ${name} (${response.status}).`);
      const file = new File([await response.blob()], name, { type: "application/pdf" });
      const ticket = await reserveRun();
      setRunFile(ticket.thread_id, file);
      router.push(`/runs/${ticket.thread_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : errorText(err));
      setBusy(null);
    }
  }

  const active = SOURCES.find((s) => s.id === source)!;

  return (
    <div className="space-y-6">
      <Card className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 font-mono text-xs font-medium uppercase tracking-wider text-teal-700 dark:text-teal-400">
            <Plug className="size-3.5" aria-hidden /> MCP client · stdio
          </div>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">Inbox</h1>
          <p className="mt-1 max-w-xl text-sm text-slate-500">
            The backend starts an MCP server and calls its three tools: list pending invoices, download their PDFs, and
            mark them processed. Process a PDF to run it through the pipeline like an upload.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div role="tablist" className="flex gap-1 rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
            {SOURCES.map(({ id, label }) => (
              <button
                key={id}
                role="tab"
                aria-selected={source === id}
                disabled={listing}
                onClick={() => {
                  setSource(id);
                  setItems(null);
                }}
                data-testid={`source-${id}`}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                  source === id ? "bg-white shadow-sm dark:bg-slate-950" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <button type="button" onClick={checkInbox} disabled={listing} className={primaryButton} data-testid="check-inbox">
            {listing ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <MailSearch className="size-4" aria-hidden />}
            {listing ? "Checking…" : "Check inbox"}
          </button>
        </div>
      </Card>

      {error && <ErrorBox>{error}</ErrorBox>}

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card className="min-w-0" data-testid="inbox-items">
          <div className="mb-4 flex items-center justify-between gap-2">
            <h2 className="font-semibold">Pending invoices</h2>
            <span className="truncate font-mono text-xs text-slate-500">{active.detail}</span>
          </div>
          {listing && !items ? (
            <div className="grid min-h-60 place-items-center">
              <SkeletonBars label="Asking the MCP server…" widths={[90, 70, 80]} />
            </div>
          ) : items === null ? (
            <div className="grid min-h-60 place-items-center">
              <EmptyState icon={MailSearch} title="Not checked yet">
                Press &ldquo;Check inbox&rdquo; to call list_pending_invoices on the {active.label} server.
              </EmptyState>
            </div>
          ) : items.length === 0 ? (
            <div className="grid min-h-60 place-items-center">
              <EmptyState icon={Inbox} title="Nothing pending">
                No unread emails with a PDF attachment
                {source === "filesystem" ? " (no PDFs in data/inbox/)" : ""}.
              </EmptyState>
            </div>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {items.map((item, i) => {
                const files = downloads[item.id];
                const isMarked = marked[item.id];
                return (
                  <li
                    key={item.id}
                    className="animate-[fade-in_0.3s_ease-out_both] py-3"
                    style={{ animationDelay: `${i * 50}ms` }}
                    data-testid="inbox-item"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="break-words text-sm font-medium">{item.source_description}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <span className="font-mono text-xs text-slate-400">id {item.id}</span>
                          {item.attachment_filenames.map((name) => (
                            <Chip key={name} tone="inactive">
                              <FileText className="size-3" aria-hidden /> {name}
                            </Chip>
                          ))}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          className={pillButton}
                          disabled={busy !== null}
                          onClick={() => download(item)}
                          data-testid="download-button"
                        >
                          {busy === `download:${item.id}` ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Download className="size-3.5" aria-hidden />}
                          Download
                        </button>
                        <button
                          type="button"
                          className={pillButton}
                          disabled={busy !== null || isMarked}
                          onClick={() => mark(item)}
                          data-testid="mark-button"
                        >
                          {busy === `mark:${item.id}` ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <CheckCheck className="size-3.5" aria-hidden />}
                          {isMarked ? "Marked processed" : "Mark processed"}
                        </button>
                      </div>
                    </div>
                    {files && (
                      <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 p-2 dark:bg-slate-800/50">
                        {files.length === 0 && <span className="text-xs text-slate-500">No PDF could be downloaded.</span>}
                        {files.map((name) => (
                          <button
                            key={name}
                            type="button"
                            disabled={busy !== null}
                            onClick={() => processFile(name)}
                            className={primaryButton}
                            data-testid="process-button"
                          >
                            {busy === `process:${name}` ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Play className="size-4" aria-hidden />}
                            Process {name}
                          </button>
                        ))}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        <Card className="min-w-0" data-testid="mcp-log">
          <h2 className="mb-3 font-semibold">MCP activity</h2>
          {log.length === 0 ? (
            <p className="text-sm text-slate-500">Each tool call appears here with its arguments and how long it took.</p>
          ) : (
            <ol className="space-y-2">
              {log.map((entry) => (
                <li key={entry.key} className="animate-[fade-in_0.3s_ease-out_both] rounded-xl border border-slate-200 p-2.5 dark:border-slate-800">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex min-w-0 items-center gap-1.5 font-mono text-xs font-semibold">
                      {entry.ok ? <CheckCheck className="size-3.5 shrink-0 text-emerald-500" aria-hidden /> : <XCircle className="size-3.5 shrink-0 text-rose-500" aria-hidden />}
                      <span className="truncate">{entry.tool}</span>
                    </span>
                    {entry.ms != null && <span className="font-mono text-xs tabular-nums text-slate-500">{formatMs(entry.ms)}</span>}
                  </div>
                  <div className="mt-0.5 break-words text-xs text-slate-500">
                    <span className="font-mono">{entry.args}</span> · {entry.note}
                  </div>
                  <div className="text-[11px] text-slate-400">{entry.at}</div>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}
