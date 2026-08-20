"use client";

// Morning digest on demand — the same builder the 08:00 ET timer mails
// (adminview.send_digest), for the "did the timer fire? just send it again"
// moments. printed=true means email is disabled server-side and the body went
// to the server log — say so instead of pretending mail moved.

import { useState } from "react";
import { api } from "@/lib/api";
import { Button, Card } from "@/components/ui";
import { SectionLabel } from "./util";

type Note = { tone: "ok" | "warn" | "err"; text: string };

export function DigestCard() {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);

  async function send() {
    setBusy(true); setNote(null);
    try {
      const r = await api.adminSendDigest();
      if (r.printed) {
        setNote({ tone: "warn", text: "email is disabled — digest printed to the server log instead" });
      } else if (r.failed.length > 0) {
        setNote({
          tone: "warn",
          text: `sent to ${r.sent.join(", ") || "nobody"} — FAILED for ${r.failed.join(", ")}`,
        });
      } else {
        setNote({ tone: "ok", text: `sent to ${r.sent.join(", ") || "nobody (no admin recipients)"}` });
      }
    } catch (e: any) {
      setNote({ tone: "err", text: e?.message || "send failed" });
    } finally { setBusy(false); }
  }

  return (
    <Card>
      <SectionLabel>Morning digest</SectionLabel>
      <p className="text-[12.5px] leading-relaxed text-muted">
        Mails the owner + cofounder the same exception digest the 08:00 ET timer
        sends: will-place verdict, member exceptions, cycle preflight, last run.
      </p>
      <div className="mt-3.5 flex flex-wrap items-center gap-3">
        <Button variant="glass" busy={busy} onClick={send} className="!px-3.5 !py-2 !text-[12.5px]">
          Send digest now
        </Button>
        {note && (
          <span className={`text-[12.5px] ${
            note.tone === "ok" ? "text-green" : note.tone === "warn" ? "text-amber" : "text-red"
          }`}>
            {note.tone === "err" ? "⚠ " : ""}{note.text}
          </span>
        )}
      </div>
    </Card>
  );
}
