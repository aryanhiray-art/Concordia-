import { pageMetadata } from "@/components/marketing/seo";
import { HowItWorksPage } from "@/components/marketing/pages/HowItWorksPage";

// Thin server wrapper: the <head> lives here (server component), the page
// lives in components/marketing/pages/ (client — the marketing primitives use
// hooks). AppShell renders these children bare for anyone on the apex host;
// see the isMktPage branch there.
export const metadata = pageMetadata(
  "/how-it-works",
  "How it works",
  "Connect your own Robinhood account, vote one ballot per cycle across the S&P 500, and watch the tally settle into one basket. The more often you're right, the more your ballot moves it.",
);

export default function Page() {
  return <HowItWorksPage />;
}
