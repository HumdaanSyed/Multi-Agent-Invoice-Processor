import { Skeleton } from "@/components/ui/skeleton";
import type { Invoice, LineItem } from "@/lib/types";

/**
 * The 9 header-field rows every invoice display renders, in one place so
 * this panel and web/components/review-form.tsx's editable form can't
 * drift apart (a past review found them maintained as two separately
 * hand-typed lists with matching labels). `mono` is this panel's own
 * concern (numbers/dates/identifiers get the mono font); `type` is
 * review-form's (which native `<input>` type to render) - each file uses
 * only the property it needs.
 */
export const INVOICE_FIELD_ROWS: {
  key: keyof Invoice;
  label: string;
  mono?: boolean;
  type: "text" | "date" | "number";
}[] = [
  { key: "vendor_name", label: "Vendor", type: "text" },
  { key: "bill_to", label: "Bill to", type: "text" },
  { key: "invoice_number", label: "Invoice #", mono: true, type: "text" },
  { key: "invoice_date", label: "Invoice date", mono: true, type: "date" },
  { key: "due_date", label: "Due date", mono: true, type: "date" },
  { key: "subtotal", label: "Subtotal", mono: true, type: "number" },
  { key: "tax", label: "Tax", mono: true, type: "number" },
  { key: "total", label: "Total", mono: true, type: "number" },
  { key: "currency", label: "Currency", mono: true, type: "text" },
];

export function formatValue(key: keyof Invoice, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number" && (key === "subtotal" || key === "tax" || key === "total")) {
    return value.toFixed(2);
  }
  return String(value);
}

function FieldRow({
  label,
  value,
  mono,
  provisional,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
  provisional?: boolean;
}) {
  // Streamed values are display-only until validation confirms them
  // (docs/api.md's "honest limitation") - provisional keeps that
  // visible rather than letting a still-unvalidated figure look as
  // settled as a confirmed one (docs/FRONTEND_PLAN.md's Phase 9B
  // pitfall).
  const valueClassName = [mono ? "font-mono" : "", "text-sm", provisional ? "text-text-muted italic" : "text-text"]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <span className="text-sm text-text-muted">{label}</span>
      {value === null ? <Skeleton className="h-4 w-28" /> : <span className={valueClassName}>{value}</span>}
    </div>
  );
}

export function LineItemsTable({ items, provisional }: { items: LineItem[]; provisional?: boolean }) {
  if (items.length === 0) {
    return <p className="py-2 text-sm text-text-muted">No line items.</p>;
  }
  const cellClassName = provisional ? "text-text-muted italic" : "text-text";
  return (
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
          {items.map((item, i) => (
            <tr key={i} className="border-b border-border last:border-0">
              <td className={`py-2 pr-2 ${cellClassName}`}>{item.description}</td>
              <td className={`py-2 pr-2 text-right font-mono ${cellClassName}`}>{item.quantity}</td>
              <td className={`py-2 pr-2 text-right font-mono ${cellClassName}`}>{item.unit_price.toFixed(2)}</td>
              <td className={`py-2 text-right font-mono ${cellClassName}`}>{item.amount.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every field starts as a skeleton and fills in as its `field` SSE event
 * arrives. Once the run has an authoritative Invoice (needs_review or
 * completed), pass it as `invoice` and every field renders from it instead
 * - streamed values are display-only (docs/api.md's "honest limitation")
 * and must never be presented as more final than they are.
 */
export function ExtractionPanel({
  fields,
  receivedFields,
  invoice,
}: {
  fields: Partial<Invoice>;
  receivedFields: Set<string>;
  invoice?: Invoice | null;
}) {
  const source = invoice ?? fields;
  const has = (key: string) => invoice != null || receivedFields.has(key);
  const provisional = invoice == null;

  return (
    <div className="rounded-lg border border-border bg-surface p-6">
      <div className="divide-y divide-border">
        {INVOICE_FIELD_ROWS.map(({ key, label, mono }) => (
          <FieldRow
            key={key}
            label={label}
            mono={mono}
            provisional={provisional}
            value={has(key) ? formatValue(key, source[key]) : null}
          />
        ))}
      </div>
      <div className="mt-4 border-t border-border pt-4">
        <p className="mb-2 text-sm text-text-muted">Line items</p>
        {has("line_items") ? (
          <LineItemsTable items={(source.line_items as LineItem[]) ?? []} provisional={provisional} />
        ) : (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-full" />
          </div>
        )}
      </div>
    </div>
  );
}
