"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * Lets the persistent export bar (components/export-bar.tsx, rendered once
 * in the root layout) show "this active run's" ledger status - pending
 * while processing, then written/failed once the ledger SSE event arrives
 * (docs/FRONTEND_PLAN.md's Phase 9D) - even though that state actually
 * lives inside hooks/use-run.ts, several component layers below the
 * layout that renders the bar. The run page writes into this context as
 * its own ledgerStatus changes; nothing else needs to.
 */
export type RunLedgerStatus = "pending" | "written" | "failed" | null;

interface LedgerStatusContextValue {
  runLedgerStatus: RunLedgerStatus;
  setRunLedgerStatus: (status: RunLedgerStatus) => void;
}

const LedgerStatusContext = createContext<LedgerStatusContextValue | null>(null);

export function LedgerStatusProvider({ children }: { children: ReactNode }) {
  const [runLedgerStatus, setRunLedgerStatus] = useState<RunLedgerStatus>(null);
  const value = useMemo(() => ({ runLedgerStatus, setRunLedgerStatus }), [runLedgerStatus]);
  return <LedgerStatusContext.Provider value={value}>{children}</LedgerStatusContext.Provider>;
}

export function useLedgerStatusContext(): LedgerStatusContextValue {
  const ctx = useContext(LedgerStatusContext);
  if (!ctx) {
    throw new Error("useLedgerStatusContext must be used within a LedgerStatusProvider");
  }
  return ctx;
}
