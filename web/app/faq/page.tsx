import { pageMetadata } from "@/components/marketing/seo";
import { FaqPage } from "@/components/marketing/pages/FaqPage";

// Thin server wrapper — see app/how-it-works/page.tsx for the pattern.
export const metadata = pageMetadata(
  "/faq",
  "Questions",
  "Straight answers on how the club decides, how voting power is earned, what Concordia can touch, what it costs, and what happens when you disagree with a basket.",
);

export default function Page() {
  return <FaqPage />;
}
