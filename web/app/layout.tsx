import "./globals.css";
import type { Metadata, Viewport } from "next";
import { Inter, Outfit } from "next/font/google";
import { AuthProvider } from "@/lib/auth";
import { AppShell } from "@/components/AppShell";
import { StockPanelProvider } from "@/components/StockPanel";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const outfit = Outfit({ subsets: ["latin"], weight: ["500", "600", "700", "800"], variable: "--font-outfit", display: "swap" });

export const metadata: Metadata = {
  title: "Concordia — taking on Wall Street, one vote at a time",
  description: "An invite-only investing club: members vote, the top picks become one shared basket, and every trade executes in your own brokerage account.",
};

// iOS Safari tints its chrome (status bar + URL bar) from theme-color. This
// value must MATCH the page's composited colour at y=0, or the chrome reads as
// a black bar above a tinted page — which is exactly what happened: the first
// attempt used #081310 (8,19,16), technically teal but ~3x darker than the
// real top and visually indistinguishable from black.
//
// Measured composite at the viewport top: --bg #06080C, + the .bgfx radial
// glows, + the .bgfx::before wash at its 35vh stop (rgba(45,212,191,.10))
// = #0E3134 mid-screen, warming to ~#114343 under the left aurora orb.
// Matching the mid value makes chrome and page read as one surface.
export const viewport: Viewport = {
  themeColor: "#0E3134",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${outfit.variable}`}>
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
