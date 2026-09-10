"use client";

// /club — the club's own room (owner spec, 2026-09-09): the voting-power
// dial opens the page as its hero (click it for your statistics), and the
// floor runs beneath — the power-weighted feed. Composition only; the dial
// and feed own their data.

import { ClubPowerDial } from "@/components/ClubPowerDial";
import { ClubFeed } from "@/components/ClubFeed";

export default function ClubPage() {
  return (
    <div className="stagger space-y-8">
      <section className="pt-2 text-center">
        <div className="label">your signal</div>
        <h1 className="mt-1 font-display text-[26px] font-bold tracking-tight text-ink sm:text-[30px]">
          The Club
        </h1>
        <div className="mt-4">
          <ClubPowerDial />
        </div>
      </section>

      <ClubFeed />
    </div>
  );
}
