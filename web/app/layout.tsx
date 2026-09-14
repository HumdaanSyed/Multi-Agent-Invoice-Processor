import type { Metadata } from "next";
import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { PRODUCT_NAME, PRODUCT_TAGLINE } from "@/lib/config";

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

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${heading.variable} ${ui.variable} ${data.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-bg text-text">
        <header className="border-b border-border">
          <div className="mx-auto flex max-w-[1100px] items-center px-6 py-5">
            <Link href="/" className="font-serif text-2xl tracking-tight text-text">
              {PRODUCT_NAME}
            </Link>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1100px] flex-1 px-6 py-12">{children}</main>
      </body>
    </html>
  );
}
