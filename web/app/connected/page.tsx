"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Button, Card, Logo, Pill } from "@/components/ui";

function ConnectedInner() {
  const q = useSearchParams();
  const ok = q.get("status") === "ok";
  const agentic = q.get("agentic") === "1";
  const account = q.get("account");
  const reason = q.get("reason");

  return (
    <div className="mx-auto mt-[16vh] w-[min(440px,100%)] px-5">
      <Card className="text-center">
        <div className="flex justify-center"><Logo size={48} /></div>
        {ok ? (
          <>
            <div className="mt-4 flex justify-center">
              <Pill tone={agentic ? "ok" : "warn"}>{agentic ? "● connected · agentic" : "connected · not agentic"}</Pill>
            </div>
            <h1 className="mt-4 font-display text-[20px] font-bold">Robinhood connected</h1>
            {account && <p className="mt-1 text-[13px] text-muted">Account ···{account.slice(-4)}</p>}
            {!agentic && (
              <p className="mt-2.5 text-[13px] leading-relaxed text-amber">
                This account isn&apos;t agentic-enabled yet. Turn on Robinhood&apos;s “Agentic” account
                so Concord can place the basket for you.
              </p>
            )}
          </>
        ) : (
          <>
            <div className="mt-4 flex justify-center"><Pill tone="danger">connection failed</Pill></div>
            <h1 className="mt-4 font-display text-[20px] font-bold">Couldn&apos;t connect</h1>
            <p className="mt-1 text-[13px] text-muted">{reason || "Please try again."}</p>
          </>
        )}
        <Link href="/"><Button variant="go" className="mt-6">Back to Concord</Button></Link>
      </Card>
    </div>
  );
}

export default function ConnectedPage() {
  return (
    <Suspense fallback={<div className="grid min-h-screen place-items-center text-subtle"><Logo size={44} /></div>}>
      <ConnectedInner />
    </Suspense>
  );
}
