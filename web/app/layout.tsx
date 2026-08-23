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

// iOS Safari paints its browser chrome from theme-color / the page's top
// colors. Flat #06080C there against the teal-washed page top read as a black
// bar on phones (owner report) — this is the same dark with the aurora's
// whisper of teal mixed in, so chrome and page meet seamlessly.
export const viewport: Viewport = {
  themeColor: "#081310",
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
