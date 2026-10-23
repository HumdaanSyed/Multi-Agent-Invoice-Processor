"use client";

import { Calculator, Clock, ListOrdered, Wallet } from "lucide-react";
import { useState } from "react";
import { ExtractionPanel, LineItemsTable } from "@/components/extraction-panel";
import { formatMs } from "@/components/pipeline-steps";
import { Card, StatTiles } from "@/components/primitives";
import { ValidationPanel } from "@/components/validation-panel";
import type { CheckEvent, Invoice, ValidationResult } from "@/lib/types";

const EMPTY_FIELDS = {};
const EMPTY_RECEIVED_FIELDS = new Set<string>();

type Tab = "fields" | "items" | "checks";

/**
 * The read-only Detail screen for a completed invoice. Checks come from
 * RunResponse.validation.checks (present for every completed run, from any
 * endpoint), not the live view's SSE-only checks, so a cold revisit shows
 * them too.
 */
export function InvoiceDetail({
  threadId,
  invoice,
  validation,
  workMs,
}: {
  threadId: string;
  invoice: Invoice;
  validation?: ValidationResult | null;
  workMs: number | null;
}) {
  const [tab, setTab] = useState<Tab>("fields");
  const checks: CheckEvent[] = (validation?.checks ?? []).map((check) => ({ type: "check", thread_id: threadId, ...check }));
  const applicable = checks.filter((c) => !c.skipped);
  const passed = applicable.filter((c) => c.passed).length;

  const tabs: [Tab, string][] = [
    ["fields", "Fields"],
    ["items", `Line items (${invoice.line_items.length})`],
    ["checks", `Checks (${passed}/${applicable.length})`],
  ];

  return (
    <div className="space-y-6">
      <StatTiles
        items={[
          { icon: Wallet, label: "Total", value: `${invoice.total.toFixed(2)} ${invoice.currency}` },
          { icon: ListOrdered, label: "Line items", value: invoice.line_items.length },
          { icon: Calculator, label: "Checks passed", value: applicable.length ? `${passed}/${applicable.length}` : "—" },
          // Machine time only (review wait excluded), and only when this tab
          // saw the run's stream - never estimated.
          { icon: Clock, label: "Processing time", value: workMs != null ? formatMs(workMs) : "—" },
        ]}
      />

      <Card className="min-w-0">
        <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto [scrollbar-width:none] rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
          {tabs.map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={tab === id}
              onClick={() => setTab(id)}
              data-testid={`tab-${id}`}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition ${
                tab === id ? "bg-white shadow-sm dark:bg-slate-950" : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "fields" && <ExtractionPanel bare showLineItems={false} fields={EMPTY_FIELDS} receivedFields={EMPTY_RECEIVED_FIELDS} invoice={invoice} />}
        {tab === "items" && <LineItemsTable items={invoice.line_items} />}
        {tab === "checks" &&
          (checks.length ? (
            <ValidationPanel bare checks={checks} settling={false} />
          ) : (
            <p className="py-6 text-center text-sm text-slate-500">No check results were recorded for this run.</p>
          ))}
      </Card>
    </div>
  );
}
