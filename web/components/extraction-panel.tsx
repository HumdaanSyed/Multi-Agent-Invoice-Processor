import { Skeleton } from "@/components/ui/skeleton";
import type { Invoice, LineItem } from "@/lib/types";

const FIELD_ROWS: { key: keyof Invoice; label: string; mono?: boolean }[] = [
  { key: "vendor_name", label: "Vendor" },
  { key: "bill_to", label: "Bill to" },
  { key: "invoice_number", label: "Invoice #", mono: true },
  { key: "invoice_date", label: "Invoice date", mono: true },
  { key: "due_date", label: "Due date", mono: true },
  { key: "subtotal", label: "Subtotal", mono: true },
  { key: "tax", label: "Tax", mono: true },
  { key: "total", label: "Total", mono: true },
  { key: "currency", label: "Currency", mono: true },
];

function formatValue(key: keyof Invoice, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "number" && (key === "subtotal" || key === "tax" || key === "total")) {
    return value.toFixed(2);
  }
  return String(value);
}

function FieldRow({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <span className="text-sm text-text-muted">{label}</span>
      {value === null ? (
        <Skeleton className="h-4 w-28" />
      ) : (
        <span className={mono ? "font-mono text-sm text-text" : "text-sm text-text"}>{value}</span>
      )}
    </div>
  );
}

function LineItemsTable({ items }: { items: LineItem[] }) {
  if (items.length === 0) {
    return <p className="py-2 text-sm text-text-muted">No line items.</p>;
  }
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
              <td className="py-2 pr-2 text-text">{item.description}</td>
              <td className="py-2 pr-2 text-right font-mono text-text">{item.quantity}</td>
              <td className="py-2 pr-2 text-right font-mono text-text">{item.unit_price.toFixed(2)}</td>
              <td className="py-2 text-right font-mono text-text">{item.amount.toFixed(2)}</td>
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

  return (
    <div className="rounded-lg border border-border bg-surface p-6">
      <div className="divide-y divide-border">
        {FIELD_ROWS.map(({ key, label, mono }) => (
          <FieldRow
            key={key}
            label={label}
            mono={mono}
            value={has(key) ? formatValue(key, source[key]) : null}
          />
        ))}
      </div>
      <div className="mt-4 border-t border-border pt-4">
        <p className="mb-2 text-sm text-text-muted">Line items</p>
        {has("line_items") ? (
          <LineItemsTable items={(source.line_items as LineItem[]) ?? []} />
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
