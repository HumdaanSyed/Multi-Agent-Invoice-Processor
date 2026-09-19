import type { Metadata } from "next";
import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";
import { connection } from "next/server";
import Link from "next/link";
import "./globals.css";
import { ExportBar } from "@/components/export-bar";
import { ThemeToggle } from "@/components/theme-toggle";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/config";
import { LedgerStatusProvider } from "@/lib/ledger-status-context";
import { THEME_STORAGE_KEY } from "@/lib/theme";

// Verity's three type roles (docs/FRONTEND_PLAN.md): a serif for headings
// and the wordmark, a clean sans for UI/body, a mono for numbers, dates,
// invoice numbers, and thread IDs. Each carries a real fallback stack so
// the page still looks intentional before/without the web font loading.
const heading = Fraunces({
  variable: "--font-heading",
  subsets: ["latin"],
  fallback: ["Georgia", "Times New Roman", "serif"],
});

const ui = Inter({
  variable: "--font-ui",
  subsets: ["latin"],
  fallback: ["system-ui", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
});

const data = JetBrains_Mono({
  variable: "--font-data",
  subsets: ["latin"],
  fallback: ["SFMono-Regular", "Consolas", "Liberation Mono", "monospace"],
});

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
      className={`${heading.variable} ${ui.variable} ${data.variable} h-full antialiased`}
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
      <body className="min-h-full flex flex-col bg-bg text-text">
        <LedgerStatusProvider>
          <header className="border-b border-border">
            <div className="mx-auto flex max-w-[1100px] items-center justify-between px-6 py-5">
              <Link href="/" className="font-serif text-2xl tracking-tight text-text">
                {PRODUCT_NAME}
              </Link>
              <ThemeToggle />
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1100px] flex-1 px-6 py-12">{children}</main>
          <ExportBar />
        </LedgerStatusProvider>
      </body>
    </html>
  );
}
