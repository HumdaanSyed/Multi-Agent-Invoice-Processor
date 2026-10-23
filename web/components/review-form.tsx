"use client";

import { Loader2, PencilLine, ShieldCheck, TriangleAlert, XCircle } from "lucide-react";
import { useState } from "react";
import { INVOICE_FIELD_ROWS, LineItemsTable } from "@/components/extraction-panel";
import { ErrorBox, primaryButton, tableHead } from "@/components/primitives";
import type { CheckEvent, CheckId, Invoice, InvoiceCorrections, LineItem } from "@/lib/types";

/**
 * Which invoice fields a failed check implicates - drives the amber
 * "this needs a look, and is editable" treatment (docs/FRONTEND_PLAN.md's
 * Phase 9C instruction 3). Mirrors invoice_agent/validate.py's six
 * check_ids (docs/api.md lists them as stable, safe to key UI off).
 * not_duplicate implicates vendor_name/invoice_number, not because
 * they're wrong exactly, but because docs/api.md says correcting one of
 * those is the only way to clear a false-positive duplicate flag - a
 * genuine duplicate can never clear it, by design.
 */
const CHECK_FIELDS: Record<CheckId, (keyof Invoice)[]> = {
  line_items_sum: ["line_items", "subtotal"],
  totals_match: ["subtotal", "tax", "total"],
  invoice_date_valid: ["invoice_date"],
  due_date_valid: ["due_date"],
  due_date_after_invoice_date: ["invoice_date", "due_date"],
  not_duplicate: ["vendor_name", "invoice_number"],
};

/**
 * Plain-language explanations (docs/FRONTEND_PLAN.md's Phase 9C
 * instruction 4: "not raw backend flag strings"). line_items_sum and
 * totals_match show the backend's own `detail` string rather than
 * recomputing the numbers client-side: invoice_agent/validate.py's detail
 * is built from `round()`-based Python arithmetic (the actual numbers
 * that decided the check), and JS's rounding can disagree with Python's at
 * an exact two-decimal tie - recomputing risked showing a different
 * number than the one that actually failed. The other checks don't
 * involve recomputing a number, so a hand-written explanation reads
 * better than validate.py's more technical/raw phrasing for those.
 */
function explainFailure(check: CheckEvent, invoice: Invoice): string {
  switch (check.check_id) {
    case "line_items_sum":
      return check.detail ?? "The line items don't add up to the subtotal.";
    case "totals_match":
      return check.detail ?? "Subtotal plus tax doesn't match the total.";
    case "invoice_date_valid":
      return `"${invoice.invoice_date}" doesn't look like a valid date.`;
    case "due_date_valid":
      return `"${invoice.due_date}" doesn't look like a valid date.`;
    case "due_date_after_invoice_date":
      return `The due date (${invoice.due_date}) is before the invoice date (${invoice.invoice_date}).`;
    case "not_duplicate":
      return "This looks like a duplicate of an invoice already on file - same vendor and invoice number. If that's wrong, correct whichever one is mistaken.";
    default:
      return check.detail ?? "This needs a second look.";
  }
}

function implicatedFields(failedChecks: CheckEvent[]): Set<keyof Invoice> {
  const fields = new Set<keyof Invoice>();
  for (const check of failedChecks) {
    for (const field of CHECK_FIELDS[check.check_id] ?? []) {
      fields.add(field);
    }
  }
  return fields;
}

interface LineItemFormState {
  description: string;
  quantity: string;
  unit_price: string;
  amount: string;
}

interface FormState {
  vendor_name: string;
  bill_to: string;
  invoice_number: string;
  invoice_date: string;
  due_date: string;
  subtotal: string;
  tax: string;
  total: string;
  currency: string;
  line_items: LineItemFormState[];
}

function toFormState(invoice: Invoice): FormState {
  return {
    vendor_name: invoice.vendor_name,
    bill_to: invoice.bill_to,
    invoice_number: invoice.invoice_number,
    invoice_date: invoice.invoice_date,
    due_date: invoice.due_date ?? "",
    subtotal: String(invoice.subtotal),
    tax: String(invoice.tax),
    total: String(invoice.total),
    currency: invoice.currency,
    line_items: invoice.line_items.map((item) => ({
      description: item.description,
      quantity: String(item.quantity),
      unit_price: String(item.unit_price),
      amount: String(item.amount),
    })),
  };
}

/** Empty string is "cleared, not yet retyped" - distinct from 0, which
 * Number("") would otherwise silently produce. Returns null for either an
 * empty or a non-numeric value, so the caller can tell "not a valid
 * number yet" apart from any real number including a genuine 0 (tax is
 * legitimately 0 for a tax-free invoice, per invoice_agent/schema.py). */
function parseRequiredNumber(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/**
 * Validates every editable numeric field and builds the corrections
 * payload, or returns an error message instead of silently letting a
 * cleared/invalid field become a 0 that reaches persistence (nothing in
 * app/models.py's InvoiceCorrections or invoice_agent/validate.py's
 * arithmetic-only checks would catch that on its own).
 */
function buildCorrections(form: FormState): { corrections: InvoiceCorrections } | { error: string } {
  const subtotal = parseRequiredNumber(form.subtotal);
  if (subtotal === null) return { error: "Subtotal needs a number." };
  const tax = parseRequiredNumber(form.tax);
  if (tax === null) return { error: "Tax needs a number." };
  const total = parseRequiredNumber(form.total);
  if (total === null) return { error: "Total needs a number." };

  const line_items: LineItem[] = [];
  for (const [i, item] of form.line_items.entries()) {
    const quantity = parseRequiredNumber(item.quantity);
    const unit_price = parseRequiredNumber(item.unit_price);
    const amount = parseRequiredNumber(item.amount);
    if (quantity === null || unit_price === null || amount === null) {
      return { error: `Line item ${i + 1} (${item.description || "untitled"}) needs valid numbers.` };
    }
    line_items.push({ description: item.description, quantity, unit_price, amount });
  }

  return {
    corrections: {
      vendor_name: form.vendor_name,
      bill_to: form.bill_to,
      invoice_number: form.invoice_number,
      invoice_date: form.invoice_date,
      due_date: form.due_date === "" ? null : form.due_date,
      subtotal,
      tax,
      total,
      currency: form.currency,
      line_items,
    },
  };
}

/**
 * Reached automatically whenever a run is needs_review. Failed checks stay
 * visible in red with their explanation (carried over from the live view,
 * per instruction 2); the fields those checks implicate are editable and
 * amber-highlighted; every other field is editable behind "Edit all
 * fields" instead. Approve and Save is the one primary action - it's
 * disabled the instant it's clicked (isSubmitting), not just while
 * `resuming` (the parent's state update from that dispatch lands a render
 * later - see docs/FRONTEND_PLAN.md's Phase 9C instruction 7), so a
 * double-click can't fire two resumes.
 */
export function ReviewForm({
  invoice,
  failedChecks,
  flags = [],
  onSubmit,
  submitting,
}: {
  invoice: Invoice;
  failedChecks: CheckEvent[];
  /** The run's own flag strings. Used only when the per-check results never
   * arrived (the in-memory event stream doesn't survive a backend restart),
   * so the reviewer still sees what's wrong. */
  flags?: string[];
  onSubmit: (corrections: InvoiceCorrections) => void;
  submitting: boolean;
}) {
  const [form, setForm] = useState<FormState>(() => toFormState(invoice));
  // Without per-check results there's no way to know which fields a failure
  // points at, so open every field rather than leaving the wrong ones locked.
  const [editAll, setEditAll] = useState(failedChecks.length === 0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [validationError, setValidationError] = useState<string | null>(null);
  const implicated = implicatedFields(failedChecks);

  const disabled = submitting || isSubmitting;

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setLineItem(index: number, patch: Partial<LineItemFormState>) {
    setForm((prev) => ({
      ...prev,
      line_items: prev.line_items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }));
  }

  function handleSubmit() {
    if (disabled) return;
    const result = buildCorrections(form);
    if ("error" in result) {
      setValidationError(result.error);
      return;
    }
    setValidationError(null);
    setIsSubmitting(true);
    onSubmit(result.corrections);
  }

  // Editable and amber are independent: "Edit all fields" alone makes line
  // items editable without implying a check flagged them - only implication
  // earns the amber treatment (same as the scalar fields loop below).
  const lineItemsImplicated = implicated.has("line_items");
  const lineItemsEditable = lineItemsImplicated || editAll;

  return (
    <div className="space-y-6" data-testid="review-form">
      <section className="rounded-2xl border border-amber-200 bg-amber-50 p-5 dark:border-amber-900 dark:bg-amber-950/40">
        <div className="flex items-center gap-2 font-semibold text-amber-800 dark:text-amber-200">
          <TriangleAlert className="size-5" aria-hidden /> Needs your review
        </div>
        <ul className="mt-3 space-y-2">
          {failedChecks.map((check, i) => (
            <li
              key={check.check_id}
              className="flex animate-[fade-in_0.3s_ease-out_both] items-start gap-2 text-sm text-slate-700 dark:text-slate-200"
              style={{ animationDelay: `${i * 60}ms` }}
              data-testid={`failure-${check.check_id}`}
            >
              <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" aria-hidden />
              <span>{explainFailure(check, invoice)}</span>
            </li>
          ))}
          {failedChecks.length === 0 &&
            flags.map((flag, i) => (
              <li
                key={flag}
                className="flex animate-[fade-in_0.3s_ease-out_both] items-start gap-2 text-sm text-slate-700 dark:text-slate-200"
                style={{ animationDelay: `${i * 60}ms` }}
                data-testid="failure-flag"
              >
                <XCircle className="mt-0.5 size-4 shrink-0 text-rose-500" aria-hidden />
                <span>{flag}</span>
              </li>
            ))}
        </ul>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">Correct and approve</h2>
            <p className="text-xs text-slate-500">
              {failedChecks.length
                ? "Highlighted fields are the ones a failed check points at."
                : "This run's individual check results weren't recorded, so every field is open."}
            </p>
          </div>
          <button
            type="button"
            aria-pressed={editAll}
            onClick={() => setEditAll((v) => !v)}
            disabled={disabled}
            data-testid="edit-all"
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium transition disabled:opacity-50 ${
              editAll
                ? "border-teal-500 bg-teal-50 text-teal-700 dark:bg-teal-950 dark:text-teal-300"
                : "border-slate-200 bg-white hover:border-teal-400 hover:text-teal-600 dark:border-slate-700 dark:bg-slate-900"
            }`}
          >
            <PencilLine className="size-3.5" aria-hidden /> Edit all fields
          </button>
        </div>

        <table className="w-full text-sm">
          <thead className={tableHead}>
            <tr>
              <th className="w-32 pb-2 pr-3 font-medium">Field</th>
              <th className="pb-2 font-medium">Value</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {INVOICE_FIELD_ROWS.map(({ key, label, type, mono }) => {
              const amber = implicated.has(key);
              const editable = amber || editAll;
              const value = form[key as keyof FormState] as string;
              return (
                <tr key={key} data-testid={`review-field-${key}`} data-flagged={amber}>
                  <td className="py-1.5 pr-3 font-medium">
                    {label}
                    {amber && <span className="ml-1.5 inline-block size-1.5 rounded-full bg-amber-500 align-middle" aria-label="flagged" />}
                  </td>
                  <td className="py-1.5">
                    {editable ? (
                      <input
                        aria-label={label}
                        type={type === "number" ? "number" : type === "date" ? "date" : "text"}
                        step={type === "number" ? "0.01" : undefined}
                        value={value}
                        onChange={(e) => setField(key as keyof FormState, e.target.value)}
                        disabled={disabled}
                        className={`${cellInput} ${mono ? "font-mono tabular-nums" : ""} ${amber ? flaggedInput : ""}`}
                      />
                    ) : (
                      <span className={`block px-2 py-1 ${mono ? "font-mono tabular-nums" : ""}`}>{value || "—"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div className="mt-5">
          <h3 className="mb-2 text-sm font-semibold">Line items</h3>
          {lineItemsEditable ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-sm">
                <thead className={tableHead}>
                  <tr>
                    <th className="pb-2 pr-2 font-medium">Description</th>
                    <th className="w-20 pb-2 pr-2 text-right font-medium">Qty</th>
                    <th className="w-28 pb-2 pr-2 text-right font-medium">Unit price</th>
                    <th className="w-28 pb-2 text-right font-medium">Amount</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {form.line_items.map((item, i) => {
                    const extra = lineItemsImplicated ? flaggedInput : "";
                    return (
                      <tr key={i}>
                        <td className="py-1 pr-2">
                          <input
                            aria-label={`Line ${i + 1} description`}
                            value={item.description}
                            onChange={(e) => setLineItem(i, { description: e.target.value })}
                            disabled={disabled}
                            className={`${cellInput} ${extra}`}
                          />
                        </td>
                        {(["quantity", "unit_price", "amount"] as const).map((field) => (
                          <td key={field} className="py-1 pr-2 last:pr-0">
                            <input
                              aria-label={`Line ${i + 1} ${field.replace("_", " ")}`}
                              type="number"
                              step={field === "quantity" ? "1" : "0.01"}
                              value={item[field]}
                              onChange={(e) => setLineItem(i, { [field]: e.target.value })}
                              disabled={disabled}
                              className={`${cellInput} text-right font-mono tabular-nums ${extra}`}
                            />
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <LineItemsTable
              items={form.line_items.map((item) => ({
                description: item.description,
                quantity: Number(item.quantity),
                unit_price: Number(item.unit_price),
                amount: Number(item.amount),
              }))}
            />
          )}
        </div>
      </section>

      {validationError && <ErrorBox>{validationError}</ErrorBox>}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={handleSubmit} disabled={disabled} className={primaryButton} data-testid="approve-button">
          {disabled ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <ShieldCheck className="size-4" aria-hidden />}
          {disabled ? "Saving…" : "Approve and save"}
        </button>
        <span className="text-xs text-slate-500">Your corrections are checked again before anything is saved.</span>
      </div>
    </div>
  );
}

const cellInput =
  "w-full min-w-0 rounded-md border border-transparent bg-transparent px-2 py-1 transition hover:border-slate-200 focus:border-teal-500 focus:outline-none disabled:opacity-60 dark:hover:border-slate-700";
const flaggedInput =
  "border-amber-300 bg-amber-50/60 hover:border-amber-400 focus:border-amber-500 dark:border-amber-800 dark:bg-amber-950/30";
