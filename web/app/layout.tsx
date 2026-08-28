import "./globals.css";
// Scoped entirely under `.mkt` — safe to load globally, and it must be global
// so the marketing surface works without a route group splitting the shell.
import "./marketing.css";
import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono, Outfit } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { AppShell } from "@/components/AppShell";
import { StockPanelProvider } from "@/components/StockPanel";
import { siteMetadata } from "@/components/marketing/seo";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const outfit = Outfit({ subsets: ["latin"], weight: ["500", "600", "700", "800"], variable: "--font-outfit", display: "swap" });
// Mono is the marketing surface's microtype (eyebrows, stat captions, the
// ticker). Both reference sites lean on a mono at ~11px/0.16em to tie a page
// together; the member app never needed one, so it is loaded here for the first
// time. Two weights only — this is label text, never body copy.
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-mono", display: "swap" });

// The whole site's <head> comes from components/marketing/seo.ts, so the share
// card, the JSON-LD and the <meta> tags cannot drift apart — they are built
// from the same TAGLINE constant. Individual routes (/terms, /privacy) override
// their own title/description/canonical on top of this.
export const metadata: Metadata = siteMetadata;

// theme-color is kept for Safari 15-25 / Chrome Android, which still honour
// it. Safari 26 IGNORES this tag and samples the BODY background-color instead
// (see the phone block in globals.css) — both paths are pointed at the same
// colour so the chrome matches the page on every engine.
export const viewport: Viewport = {
  themeColor: "#091416",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${outfit.variable} ${mono.variable}`}>
      <body className="min-h-screen font-sans text-ink antialiased">
        <div className="bgfx" aria-hidden />
        <div className="aurora" aria-hidden />
        <AuthProvider>
          {/* ONE stock panel for the whole app — pages call openStock(sym) */}
          <StockPanelProvider>
            <AppShell>{children}</AppShell>
          </StockPanelProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
