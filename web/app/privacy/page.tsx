import type { Metadata } from "next";
import { PrivacyDoc } from "@/components/legal";

export const metadata: Metadata = {
  title: "Privacy — Concordia",
  description: "What Concordia stores (and never does) with member data: credentials, encrypted broker tokens, votes, trade records.",
  // Without this both legal pages inherit the root layout's canonical ("/")
  // and tell Google they are duplicates of the homepage.
  alternates: { canonical: "/privacy" },
};

export default function PrivacyPage() {
  return (
    <div className="mx-auto w-[min(820px,100%)]">
      <PrivacyDoc />
    </div>
  );
}
