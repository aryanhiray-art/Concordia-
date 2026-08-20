"use client";

// Who can get in, and with what. The club's invite code lives HERE — the owner
// asked for it to be readable in the console, not buried in the .env — and the
// card also states the rule the sign-in page embodies: the ops console itself
// has NO code (operator accounts sign in with email + password; roles are
// granted, never self-served). Demo and beta are separate instances with their
// own codes on their own consoles, so this card only ever shows its own.

import { useState } from "react";
import type { AdminOverview } from "@/lib/api";
import { Card } from "@/components/ui";
import { SectionLabel } from "./util";

export function AccessCard({ overview, err }: { overview: AdminOverview | null; err: string | null }) {
  const [copied, setCopied] = useState<null | "ok" | "fail">(null);
  const code = overview?.invite_code || null;

  async function copy() {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied("ok");
    } catch {
      // clipboard API needs a secure context + permission; fall back to the
      // execCommand path before telling the operator to do it by hand
      try {
        const ta = document.createElement("textarea");
        ta.value = code;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        ta.remove();
        setCopied(ok ? "ok" : "fail");
      } catch {
        setCopied("fail");
      }
    }
    setTimeout(() => setCopied(null), 1600);
  }

  return (
    <Card>
      <SectionLabel>Access</SectionLabel>
      <div className="flex flex-wrap items-center gap-2.5">
        <span className="text-[12.5px] text-muted">Member invite code</span>
        {code ? (
          <button onClick={copy} title="Copy invite code"
            className="rounded-[9px] border border-teal/30 bg-teal/[0.07] px-2.5 py-1 font-mono text-[13px] font-semibold tracking-wide text-ink transition hover:border-teal/50">
            {code}
          </button>
        ) : (
          <span className="text-[12.5px] text-subtle">{err ? "unavailable — overview failed" : "…"}</span>
        )}
        {copied && (
          <span className={`text-[12px] ${copied === "ok" ? "text-green" : "text-amber"}`}>
            {copied === "ok" ? "copied" : "copy failed — select it manually"}
          </span>
        )}
      </div>
      <p className="mt-2.5 text-[12.5px] leading-relaxed text-muted">
        New members create their account at voteconcordia.com with this code. This
        console has no code of its own: operators sign in with email + password,
        and admin rights are granted per account. The demo and beta channels are
        separate instances — each shows its own invite code on its own console.
      </p>
    </Card>
  );
}
