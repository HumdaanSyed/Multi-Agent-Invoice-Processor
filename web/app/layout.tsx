import type { Metadata } from "next";
import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";
import { connection } from "next/server";
import Link from "next/link";
import "./globals.css";
import { ExportBar } from "@/components/export-bar";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/config";
import { LedgerStatusProvider } from "@/lib/ledger-status-context";

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
      className={`${heading.variable} ${ui.variable} ${data.variable} h-full antialiased`}
    >
      <head>
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
            <div className="mx-auto flex max-w-[1100px] items-center px-6 py-5">
              <Link href="/" className="font-serif text-2xl tracking-tight text-text">
                {PRODUCT_NAME}
              </Link>
            </div>
          </header>
          <main className="mx-auto w-full max-w-[1100px] flex-1 px-6 py-12">{children}</main>
          <ExportBar />
        </LedgerStatusProvider>
      </body>
    </html>
  );
}
