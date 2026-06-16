import "./globals.css";
import type { Metadata } from "next";
import { Inter, Outfit } from "next/font/google";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
const outfit = Outfit({ subsets: ["latin"], weight: ["600", "700"], variable: "--font-outfit", display: "swap" });

export const metadata: Metadata = {
  title: "Concord — daily vote",
  description: "Collective daily-vote trading club",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${outfit.variable}`}>
      <body className="min-h-screen font-sans text-ink antialiased">{children}</body>
    </html>
  );
}
