"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
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
 * Plain-language explanations computed from the invoice's own values,
 * rather than the backend's raw check.detail string (docs/FRONTEND_PLAN.md's
 * Phase 9C instruction 4: "not raw backend flag strings").
 */
function explainFailure(checkId: CheckId, invoice: Invoice): string {
  switch (checkId) {
    case "line_items_sum": {
      const sum = invoice.line_items.reduce((total, item) => total + item.amount, 0);
      return `The line items add up to ${sum.toFixed(2)}, but the subtotal says ${invoice.subtotal.toFixed(2)}.`;
    }
    case "totals_match":
      return `Subtotal plus tax is ${(invoice.subtotal + invoice.tax).toFixed(2)}, but the total says ${invoice.total.toFixed(2)}.`;
    case "invoice_date_valid":
      return `"${invoice.invoice_date}" doesn't look like a valid date.`;
    case "due_date_valid":
      return `"${invoice.due_date}" doesn't look like a valid date.`;
    case "due_date_after_invoice_date":
      return `The due date (${invoice.due_date}) is before the invoice date (${invoice.invoice_date}).`;
    case "not_duplicate":
      return "This looks like a duplicate of an invoice already on file - same vendor and invoice number. If that's wrong, correct whichever one is mistaken.";
    default:
      return "This needs a second look.";
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

const FIELD_ROWS: { key: keyof Invoice; label: string; type: "text" | "date" | "number" }[] = [
  { key: "vendor_name", label: "Vendor", type: "text" },
  { key: "bill_to", label: "Bill to", type: "text" },
  { key: "invoice_number", label: "Invoice #", type: "text" },
  { key: "invoice_date", label: "Invoice date", type: "date" },
  { key: "due_date", label: "Due date", type: "date" },
  { key: "subtotal", label: "Subtotal", type: "number" },
  { key: "tax", label: "Tax", type: "number" },
  { key: "total", label: "Total", type: "number" },
  { key: "currency", label: "Currency", type: "text" },
];

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
  line_items: LineItem[];
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
    line_items: invoice.line_items,
  };
}

function toCorrections(form: FormState): InvoiceCorrections {
  return {
    vendor_name: form.vendor_name,
    bill_to: form.bill_to,
    invoice_number: form.invoice_number,
    invoice_date: form.invoice_date,
    due_date: form.due_date === "" ? null : form.due_date,
    subtotal: Number(form.subtotal),
    tax: Number(form.tax),
    total: Number(form.total),
    currency: form.currency,
    line_items: form.line_items,
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
  const implicated = implicatedFields(failedChecks);

  const disabled = submitting || isSubmitting;

  function setField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function setLineItem(index: number, patch: Partial<LineItem>) {
    setForm((prev) => ({
      ...prev,
      line_items: prev.line_items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    }));
  }

  function handleSubmit() {
    if (disabled) return;
    setIsSubmitting(true);
    onSubmit(toCorrections(form));
  }

  const lineItemsEditable = implicated.has("line_items") || editAll;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-lg border border-error/30 bg-surface p-6">
        <p className="font-medium text-error">Needs review</p>
        <ul className="mt-2 flex flex-col gap-1 text-sm text-error">
          {failedChecks.map((check) => (
            <li key={check.check_id} className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>{explainFailure(check.check_id, invoice)}</span>
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
          {FIELD_ROWS.map(({ key, label, type }) => {
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
                {form.line_items.map((item, i) =>
                  lineItemsEditable ? (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="py-1 pr-2">
                        <Input
                          value={item.description}
                          onChange={(e) => setLineItem(i, { description: e.target.value })}
                          disabled={disabled}
                          className="border-flag bg-flag/5 focus-visible:ring-flag/50"
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <Input
                          type="number"
                          step="1"
                          value={item.quantity}
                          onChange={(e) => setLineItem(i, { quantity: Number(e.target.value) })}
                          disabled={disabled}
                          className="text-right font-mono border-flag bg-flag/5 focus-visible:ring-flag/50"
                        />
                      </td>
                      <td className="py-1 pr-2">
                        <Input
                          type="number"
                          step="0.01"
                          value={item.unit_price}
                          onChange={(e) => setLineItem(i, { unit_price: Number(e.target.value) })}
                          disabled={disabled}
                          className="text-right font-mono border-flag bg-flag/5 focus-visible:ring-flag/50"
                        />
                      </td>
                      <td className="py-1">
                        <Input
                          type="number"
                          step="0.01"
                          value={item.amount}
                          onChange={(e) => setLineItem(i, { amount: Number(e.target.value) })}
                          disabled={disabled}
                          className="text-right font-mono border-flag bg-flag/5 focus-visible:ring-flag/50"
                        />
                      </td>
                    </tr>
                  ) : (
                    <tr key={i} className="border-b border-border last:border-0">
                      <td className="py-2 pr-2 text-text">{item.description}</td>
                      <td className="py-2 pr-2 text-right font-mono text-text">{item.quantity}</td>
                      <td className="py-2 pr-2 text-right font-mono text-text">{item.unit_price.toFixed(2)}</td>
                      <td className="py-2 text-right font-mono text-text">{item.amount.toFixed(2)}</td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <Button onClick={handleSubmit} disabled={disabled} className="self-start">
        {disabled ? "Saving…" : "Approve and Save"}
      </Button>
    </div>
  );
}
