import { pageMetadata } from "@/components/marketing/seo";
import { RecordPage } from "@/components/marketing/pages/RecordPage";

// Thin server wrapper — see app/how-it-works/page.tsx for the pattern.
export const metadata = pageMetadata(
  "/record",
  "The record",
  "Every basket the club has decided, published as a paper decisions index — equal-weighted, marked at daily closes, shown against the S&P 500. Shown, not promised.",
);

export default function Page() {
  return <RecordPage />;
}
