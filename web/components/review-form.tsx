"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { INVOICE_FIELD_ROWS, LineItemsTable } from "@/components/extraction-panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
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
  onSubmit,
  submitting,
}: {
  invoice: Invoice;
  failedChecks: CheckEvent[];
  onSubmit: (corrections: InvoiceCorrections) => void;
  submitting: boolean;
}) {
  const [form, setForm] = useState<FormState>(() => toFormState(invoice));
  const [editAll, setEditAll] = useState(false);
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
  // items editable without implying a check actually flagged them - only
  // implication earns the amber treatment (matches the scalar fields loop
  // below, which keeps the same two booleans separate).
  const lineItemsImplicated = implicated.has("line_items");
  const lineItemsEditable = lineItemsImplicated || editAll;
  const lineItemInputClassName = lineItemsImplicated ? "border-flag bg-flag/5 focus-visible:ring-flag/50" : "";

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-lg border border-error/30 bg-surface p-6">
        <p className="font-medium text-error">Needs review</p>
        <ul className="mt-2 flex flex-col gap-1 text-sm text-error">
          {failedChecks.map((check) => (
            <li key={check.check_id} className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>{explainFailure(check, invoice)}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-lg border border-border bg-surface p-6">
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm text-text-muted">Fields</p>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            Edit all fields
            <Switch checked={editAll} onCheckedChange={setEditAll} disabled={disabled} />
          </label>
        </div>

        <div className="divide-y divide-border">
          {INVOICE_FIELD_ROWS.map(({ key, label, type }) => {
            const editable = implicated.has(key) || editAll;
            const amber = implicated.has(key);
            return (
              <div key={key} className="flex items-center justify-between gap-4 py-2">
                <span className="text-sm text-text-muted">{label}</span>
                {editable ? (
                  <Input
                    type={type === "number" ? "number" : type === "date" ? "date" : "text"}
                    step={type === "number" ? "0.01" : undefined}
                    value={form[key as keyof FormState] as string}
                    onChange={(e) => setField(key as keyof FormState, e.target.value)}
                    disabled={disabled}
                    className={`max-w-48 text-right font-mono ${
                      amber ? "border-flag bg-flag/5 focus-visible:ring-flag/50" : ""
                    }`}
                  />
                ) : (
                  <span className="font-mono text-sm text-text">{form[key as keyof FormState] as string}</span>
                )}
              </div>
            );
          })}
        </div>

        <div className="mt-4 border-t border-border pt-4">
          <p className="mb-2 text-sm text-text-muted">Line items</p>
          {lineItemsEditable ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-text-muted">
                    <th className="py-2 pr-2 font-normal">Description</th>
                    <th className="py-2 pr-2 text-right font-normal">Qty</th>
                    <th className="py-2 pr-2 text-right font-normal">Unit price</th>
                    <th className="py-2 text-right font-normal">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {form.line_items.map((item, i) => (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="py-1 pr-2">
                        <Input
                          value={item.description}
                          onChange={(e) => setLineItem(i, { description: e.target.value })}
                          disabled={disabled}
                          className={lineItemInputClassName}
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <Input
                          type="number"
                          step="1"
                          value={item.quantity}
                          onChange={(e) => setLineItem(i, { quantity: e.target.value })}
                          disabled={disabled}
                          className={`text-right font-mono ${lineItemInputClassName}`}
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <Input
                          type="number"
                          step="0.01"
                          value={item.unit_price}
                          onChange={(e) => setLineItem(i, { unit_price: e.target.value })}
                          disabled={disabled}
                          className={`text-right font-mono ${lineItemInputClassName}`}
                        />
                      </td>
                      <td className="py-1">
                        <Input
                          type="number"
                          step="0.01"
                          value={item.amount}
                          onChange={(e) => setLineItem(i, { amount: e.target.value })}
                          disabled={disabled}
                          className={`text-right font-mono ${lineItemInputClassName}`}
                        />
                      </td>
                    </tr>
                  ))}
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
      </div>

      {validationError && <p className="text-sm text-error">{validationError}</p>}

      <Button onClick={handleSubmit} disabled={disabled} className="self-start">
        {disabled ? "Saving…" : "Approve and Save"}
      </Button>
    </div>
  );
}
