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

// iOS Safari tints its chrome (status bar + URL bar) from theme-color, so this
// must match the page's colour at y=0 or the chrome reads as a bar above the
// page. Two earlier misses: #081310 was technically teal but ~3x too dark, and
// matching a top-anchored WASH was chasing the wrong target — that wash was
// itself the artifact (a fixed band content scrolled through). Phones now use
// ONE uniform tinted base (#0a1c1e + soft glows, see globals.css), so this is
// simply that base with the glow at the top edge composited in.
export const viewport: Viewport = {
  themeColor: "#0D2C2E",
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
