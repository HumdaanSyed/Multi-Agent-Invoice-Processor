import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { connection } from "next/server";
import Link from "next/link";
import "./globals.css";
import { ExportBar } from "@/components/export-bar";
import { ThemeToggle } from "@/components/theme-toggle";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/config";
import { LedgerStatusProvider } from "@/lib/ledger-status-context";
import { THEME_STORAGE_KEY } from "@/lib/theme";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: PRODUCT_NAME,
  description: PRODUCT_TAGLINE,
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Opts the whole tree into dynamic rendering so API_PUBLIC_URL is read at
  // request time (docs: environment-variables.md, "Runtime Environment
  // Variables") - one prebuilt image, different backend per deployment.
  await connection();
  const apiPublicUrl = process.env.API_PUBLIC_URL;

  return (
    <html
      lang="en"
      // The theme script below sets data-theme before hydration.
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <head>
        <script
          // Restore a saved theme choice before first paint (no flash of the
          // other theme). Storage may be blocked, hence the try/catch.
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`,
          }}
        />
        {apiPublicUrl && (
          <script
            // "<" escaped so a hostile env value can't close the tag.
            dangerouslySetInnerHTML={{
              __html: `window.__VERITY_API_BASE_URL__=${JSON.stringify(apiPublicUrl).replace(/</g, "\\u003c")};`,
            }}
          />
        )}
      </head>
      <body className="flex min-h-full flex-col bg-slate-50 font-sans text-slate-900 dark:bg-slate-950 dark:text-slate-100">
        <LedgerStatusProvider>
          {/* Wordmark-only bar with a thin accent stripe - deliberately no logo
              tile, so Verity reads differently from sibling demo apps. */}
          <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
            <div className="h-1 bg-gradient-to-r from-teal-500 to-cyan-600" aria-hidden />
            <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
              <Link href="/" className="flex min-w-0 items-baseline gap-3" data-testid="brand">
                <span className="text-2xl font-extrabold tracking-tight">
                  {PRODUCT_NAME.toLowerCase()}
                  <span className="text-teal-500">.</span>
                </span>
                <span className="hidden truncate border-l border-slate-200 pl-3 text-sm text-slate-500 sm:inline dark:border-slate-700">
                  {PRODUCT_TAGLINE}
                </span>
              </Link>
              <div className="flex items-center gap-4">
                <nav className="hidden items-center gap-4 text-sm font-medium text-slate-500 md:flex">
                  <Link href="/#upload" className="transition hover:text-teal-600 dark:hover:text-teal-400">
                    Upload
                  </Link>
                  <Link href="/#runs" className="transition hover:text-teal-600 dark:hover:text-teal-400">
                    Runs
                  </Link>
                </nav>
                <span className="hidden font-mono text-xs uppercase tracking-wider text-slate-400 lg:inline">claude · langgraph</span>
                <ThemeToggle />
              </div>
            </div>
          </header>
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
          <ExportBar />
        </LedgerStatusProvider>
      </body>
    </html>
  );
}
