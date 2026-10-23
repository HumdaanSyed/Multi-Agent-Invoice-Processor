import { CheckCircle2, CircleDashed, Loader2, MinusCircle, TriangleAlert, XCircle } from "lucide-react";
import { Chip, type Tone } from "@/components/primitives";
import type { RunStatus } from "@/lib/types";

export const STATUS_META: Record<RunStatus, { label: string; tone: Tone; icon: typeof CheckCircle2 }> = {
  processing: { label: "Processing", tone: "active", icon: Loader2 },
  needs_review: { label: "Needs review", tone: "warning", icon: TriangleAlert },
  completed: { label: "Completed", tone: "success", icon: CheckCircle2 },
  skipped: { label: "Skipped", tone: "inactive", icon: MinusCircle },
  failed: { label: "Failed", tone: "error", icon: XCircle },
};

export function RunStatusChip({ status }: { status: RunStatus | undefined }) {
  if (!status) {
    return (
      <Chip tone="inactive" data-testid="run-status" data-status="loading">
        <CircleDashed className="size-3" aria-hidden /> Loading
      </Chip>
    );
  }
  const { label, tone, icon: Icon } = STATUS_META[status];
  return (
    <Chip tone={tone} data-testid="run-status" data-status={status}>
      <Icon className={`size-3 ${status === "processing" ? "animate-spin" : ""}`} aria-hidden />
      {label}
    </Chip>
  );
}
