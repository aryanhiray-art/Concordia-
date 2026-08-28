import type { Metadata } from "next";
import { TermsDoc } from "@/components/legal";

export const metadata: Metadata = {
  title: "Club Participation Agreement — Concordia",
  description: "Concordia's member agreement and risk disclosure: your account, your custody, one shared basket rebalanced by vote, no fees.",
  // Without this both legal pages inherit the root layout's canonical ("/")
  // and tell Google they are duplicates of the homepage.
  alternates: { canonical: "/terms" },
};

export default function TermsPage() {
  return (
    <div className="mx-auto w-[min(820px,100%)]">
      <TermsDoc />
    </div>
  );
}
