"use client";

import { KeyManager, ResearchVoterCard, ConnectProgram } from "@/components/ApiBot";

export default function ProgramPage() {
  return (
    <div className="space-y-7">
      <header className="enter">
        <h1 className="font-display text-[34px] font-bold leading-none tracking-tight sm:text-[40px]">API bot</h1>
        <p className="mt-2.5 max-w-2xl text-[13.5px] text-muted">
          Hand your vote to a program. Mint an API key, then let the research-voter read your Robinhood watchlists and
          cast a conviction-weighted S&amp;P 500 vote on your behalf — same voting power, same ballot, no money moved.
        </p>
      </header>

      <div className="grid items-start gap-7 lg:grid-cols-[1fr_1fr]">
        <KeyManager />
        <ResearchVoterCard />
      </div>

      <ConnectProgram />
    </div>
  );
}
