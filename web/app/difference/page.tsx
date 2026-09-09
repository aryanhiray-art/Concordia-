import { pageMetadata } from "@/components/marketing/seo";
import { DifferencePage } from "@/components/marketing/pages/DifferencePage";

// Thin server wrapper — see app/how-it-works/page.tsx for the pattern.
export const metadata = pageMetadata(
  "/difference",
  "The difference",
  "Investment clubs voted, then pooled the money. Copy-trading kept your account but handed the decision to one person. Concordia keeps the vote, your account — and the score.",
);

export default function Page() {
  return <DifferencePage />;
}
