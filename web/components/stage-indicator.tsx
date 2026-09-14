const STAGE_LABELS: Record<string, string> = {
  classifying: "Classifying",
  extracting: "Extracting",
  validating: "Validating",
  awaiting_review: "Awaiting review",
  review_applied: "Review applied",
  saving: "Saving",
};

export function StageIndicator({ stage }: { stage: { node: string; stage: string } | null }) {
  if (!stage) return null;
  const label = STAGE_LABELS[stage.stage] ?? stage.stage;

  return (
    <div className="flex items-center gap-2 text-sm text-text-muted">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" aria-hidden />
      {label}
    </div>
  );
}
