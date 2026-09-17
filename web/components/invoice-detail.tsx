import { ExtractionPanel } from "@/components/extraction-panel";
import { PdfPreview } from "@/components/pdf-preview";
import type { Invoice } from "@/lib/types";

const EMPTY_FIELDS = {};
const EMPTY_RECEIVED_FIELDS = new Set<string>();

/**
 * The read-only "Detail" screen (docs/FRONTEND_PLAN.md's Phase 9D, screen
 * 4): a completed invoice, its line items, and links out to its source PDF
 * and its trace. Reuses ExtractionPanel's `invoice` mode for the fields
 * grid - the same fully-settled rendering the live view already falls back
 * to once an authoritative Invoice exists (extraction-panel.tsx), so this
 * view can't drift from that one's field list or formatting.
 */
export function InvoiceDetail({
  threadId,
  invoice,
  file,
  pdfUrl,
  traceUrl,
}: {
  threadId: string;
  invoice: Invoice;
  file: File | undefined;
  pdfUrl?: string | null;
  traceUrl?: string | null;
}) {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <PdfPreview file={file} pdfUrl={pdfUrl} />
        <ExtractionPanel fields={EMPTY_FIELDS} receivedFields={EMPTY_RECEIVED_FIELDS} invoice={invoice} />
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-border pt-4 text-sm text-text-muted">
        <span>
          Thread <span className="font-mono text-text">{threadId}</span>
        </span>
        {pdfUrl && (
          <a href={pdfUrl} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">
            Open source PDF ↗
          </a>
        )}
        {traceUrl && (
          <a href={traceUrl} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">
            View trace ↗
          </a>
        )}
      </div>
    </div>
  );
}
