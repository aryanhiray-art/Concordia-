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

// theme-color is kept for Safari 15-25 / Chrome Android, which still honour
// it. Safari 26 IGNORES this tag and samples the BODY background-color instead
// (see the phone block in globals.css) — both paths are pointed at the same
// colour so the chrome matches the page on every engine.
export const viewport: Viewport = {
  themeColor: "#0E2E30",
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
