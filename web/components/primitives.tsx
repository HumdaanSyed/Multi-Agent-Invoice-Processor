import type { ReactNode } from "react";

/** Shared building blocks for the slate + teal design (see web/README.md). */

export function Card({
  className = "",
  children,
  ...rest
}: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLElement>) {
  return (
    <section
      className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}
      {...rest}
    >
      {children}
    </section>
  );
}

export const primaryButton =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-teal-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-teal-600/25 transition hover:bg-teal-500 disabled:cursor-not-allowed disabled:opacity-60";

export const secondaryButton =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium transition hover:bg-slate-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800";

export const pillButton =
  "inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1 text-sm font-medium transition hover:border-teal-400 hover:text-teal-600 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:text-teal-300";

export type Tone = "success" | "warning" | "error" | "inactive" | "active";

const CHIP: Record<Tone, string> = {
  success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  warning: "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  error: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  inactive: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  active: "bg-teal-100 text-teal-700 dark:bg-teal-950 dark:text-teal-300",
};

export function Chip({ tone, children, ...rest }: { tone: Tone; children: ReactNode } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${CHIP[tone]}`}
      {...rest}
    >
      {children}
    </span>
  );
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950/50 dark:text-rose-200"
    >
      {children}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, children }: { icon: React.ComponentType<{ className?: string }>; title: string; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-sm text-center">
      <Icon className="mx-auto size-12 text-slate-300 dark:text-slate-700" aria-hidden />
      <h2 className="mt-3 font-semibold">{title}</h2>
      <p className="mt-1 text-sm text-slate-500">{children}</p>
    </div>
  );
}

export function SkeletonBars({ label, widths = [80, 60, 90, 70, 50] }: { label?: string; widths?: number[] }) {
  return (
    <div className="w-full max-w-md space-y-3">
      {widths.map((w, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-slate-200 dark:bg-slate-800" style={{ width: `${w}%` }} />
      ))}
      {label && <p className="pt-2 text-center text-sm text-slate-500">{label}</p>}
    </div>
  );
}

/** Horizontal score bar, coloured by threshold. `value` is 0-1. */
export function ScoreBar({ value, label }: { value: number; label: string }) {
  const color = value >= 1 ? "bg-emerald-500" : value >= 0.67 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2" aria-label={label}>
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="w-9 text-xs tabular-nums text-slate-500">{Math.round(value * 100)}%</span>
    </div>
  );
}

export function StatTiles({ items }: { items: { icon: React.ComponentType<{ className?: string }>; label: string; value: ReactNode }[] }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="stats">
      {items.map(({ icon: Icon, label, value }) => (
        <div key={label} className="rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Icon className="size-3.5" aria-hidden />
            {label}
          </div>
          <div className="mt-1 truncate font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

export const tableHead = "text-left text-xs uppercase tracking-wide text-slate-500";
