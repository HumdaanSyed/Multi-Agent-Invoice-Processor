import { Chip, tableHead } from "@/components/primitives";
import type { Invoice, LineItem } from "@/lib/types";

/**
 * The 9 header-field rows every invoice display renders, in one place so this
 * panel and components/review-form.tsx's editable form can't drift apart.
 * `mono` marks numbers/dates/identifiers; `type` is the review form's input type.
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
  if (
    typeof value === "number" &&
    (key === "subtotal" || key === "tax" || key === "total")
  ) {
    return value.toFixed(2);
  }
  return String(value);
}

/** The signature "working" cue: a caret blinking in whichever field Claude is
 * filling in next. It only moves when a real field event arrives. */
function ReadingCaret() {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-teal-600 dark:text-teal-400"
      data-testid="reading-caret"
    >
      <span
        className="h-4 w-0.5 animate-[caret_1s_steps(1)_infinite] rounded bg-teal-500"
        aria-hidden
      />
      reading…
    </span>
  );
}

export function LineItemsTable({
  items,
  provisional,
}: {
  items: LineItem[];
  provisional?: boolean;
}) {
  if (items.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-slate-500">
        No line items on this invoice.
      </p>
    );
  }
  const cell = provisional ? "italic text-slate-500" : "";
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="line-items">
        <thead className={tableHead}>
          <tr>
            <th className="pb-2 pr-3 font-medium">Description</th>
            <th className="pb-2 pr-3 text-right font-medium">Qty</th>
            <th className="pb-2 pr-3 text-right font-medium">Unit price</th>
            <th className="pb-2 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 tabular-nums dark:divide-slate-800">
          {items.map((item, i) => (
            <tr
              key={i}
              className="animate-[fade-in_0.3s_ease-out_both]"
              style={{ animationDelay: `${i * 40}ms` }}
            >
              <td className={`py-2 pr-3 ${cell}`}>{item.description}</td>
              <td className={`py-2 pr-3 text-right ${cell}`}>
                {item.quantity}
              </td>
              <td className={`py-2 pr-3 text-right ${cell}`}>
                {item.unit_price.toFixed(2)}
              </td>
              <td className={`py-2 text-right ${cell}`}>
                {item.amount.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Every field starts empty and fills in as its `field` SSE event arrives.
 * Streamed values are display-only (docs/api.md's "honest limitation") and
 * render muted and italic until the run has an authoritative Invoice, passed
 * here as `invoice`, which then replaces them.
 */
export function ExtractionPanel({
  fields,
  receivedFields,
  invoice,
  reading = false,
  bare = false,
  showLineItems = true,
}: {
  fields: Partial<Invoice>;
  receivedFields: Set<string>;
  invoice?: Invoice | null;
  reading?: boolean;
  bare?: boolean;
  showLineItems?: boolean;
}) {
  const source = invoice ?? fields;
  const has = (key: string) => invoice != null || receivedFields.has(key);
  const provisional = invoice == null;
  const nextKey = reading
    ? INVOICE_FIELD_ROWS.find(({ key }) => !has(key))?.key
    : undefined;
  const total = INVOICE_FIELD_ROWS.length + 1;
  const received = invoice ? total : receivedFields.size;

  const body = (
    <>
      <table className="w-full text-sm" data-testid="fields-table">
        <thead className={tableHead}>
          <tr>
            <th className="w-32 pb-2 pr-3 font-medium">Field</th>
            <th className="pb-2 font-medium">Value</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
          {INVOICE_FIELD_ROWS.map(({ key, label, mono }) => (
            <tr key={key} data-testid={`field-${key}`} data-filled={has(key)}>
              <td className="py-2 pr-3 align-middle font-medium">{label}</td>
              <td className="min-w-0 py-2 break-words">
                {has(key) ? (
                  <span
                    key="value"
                    className={`inline-block animate-[fade-in_0.3s_ease-out_both] ${mono ? "font-mono tabular-nums" : ""} ${provisional ? "italic text-slate-500" : ""}`}
                  >
                    {formatValue(key, source[key])}
                  </span>
                ) : key === nextKey ? (
                  <ReadingCaret />
                ) : (
                  <span
                    className="block h-4 w-32 animate-pulse rounded bg-slate-100 dark:bg-slate-800"
                    aria-label="not read yet"
                  />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {showLineItems && (
        <div className="mt-5">
          <h3 className="mb-2 text-sm font-semibold">Line items</h3>
          {has("line_items") ? (
            <LineItemsTable
              items={(source.line_items as LineItem[]) ?? []}
              provisional={provisional}
            />
          ) : reading && nextKey === undefined ? (
            <ReadingCaret />
          ) : (
            <div className="space-y-2">
              <div className="h-4 w-full animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
              <div className="h-4 w-4/5 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
            </div>
          )}
        </div>
      )}
    </>
  );

  if (bare) return body;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Extracted fields</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs tabular-nums text-slate-500">
            {received}/{total} read
          </span>
          {provisional ? (
            <Chip tone="active">Live preview · not yet checked</Chip>
          ) : (
            <Chip tone="success">Final extraction</Chip>
          )}
        </div>
      </div>
      {body}
    </section>
  );
}
