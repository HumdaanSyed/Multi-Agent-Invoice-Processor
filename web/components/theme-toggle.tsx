"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { THEME_STORAGE_KEY } from "@/lib/theme";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  const forced = document.documentElement.dataset.theme;
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Header button that flips between light and dark. With no saved choice the
 * page follows the OS (globals.css); the first click pins an explicit
 * data-theme on <html> and remembers it in localStorage, which app/layout.tsx
 * re-applies before first paint so a reload doesn't flash the other theme.
 * The label is only known in the browser, so nothing renders until mounted
 * (avoids a server/client hydration mismatch).
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    // The browser is the only place the real theme is known (localStorage /
    // OS preference), so it has to be read in an effect, not during render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTheme(currentTheme());
  }, []);

  if (theme === null) return <span className="size-8" aria-hidden />;

  const next: Theme = theme === "dark" ? "light" : "dark";

  function toggle() {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Private mode / blocked storage: the toggle still works for this visit.
    }
    setTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="inline-flex size-8 items-center justify-center rounded-lg border border-border text-text-muted transition-colors hover:bg-brand-soft hover:text-text"
    >
      {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}
