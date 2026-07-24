"use client";

// /welcome — the guided onboarding wizard. All the mechanics (step derivation,
// stepper, escape hatches) live in components/wizard/.

import { Wizard } from "@/components/wizard/Wizard";

export default function WelcomePage() {
  return <Wizard />;
}
