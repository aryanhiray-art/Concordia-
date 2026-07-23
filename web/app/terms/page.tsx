import type { Metadata } from "next";
import { TermsDoc } from "@/components/legal";

export const metadata: Metadata = {
  title: "Club Participation Agreement — Concordia",
  description: "Concordia's member agreement and risk disclosure: your account, your custody, one ballot a day, no fees.",
};

export default function TermsPage() {
  return (
    <div className="mx-auto w-[min(820px,100%)]">
      <TermsDoc />
    </div>
  );
}
