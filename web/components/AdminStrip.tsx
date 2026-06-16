"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, Pill } from "./ui";

export function AdminStrip({ onAction }: { onAction: () => void }) {
  const [ov, setOv] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() { try { setOv(await api.adminOverview()); } catch {} }
  useEffect(() => { refresh(); }, []);

  async function act(fn: () => Promise<any>) {
    setBusy(true);
    try { await fn(); await refresh(); onAction(); } finally { setBusy(false); }
  }
  if (!ov) return null;
  const live = ov.execution_mode === "live";

  return (
    <Card className="!border-teal/25">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Pill tone="teal">OWNER</Pill>
          <Pill tone={live ? "danger" : "neutral"}>{ov.execution_mode}</Pill>
          {ov.armed ? <Pill tone="danger"><span className="pulse">● ARMED</span></Pill>
                    : <Pill tone="ok">safe · disarmed</Pill>}
          {ov.halt && <Pill tone="warn">halted</Pill>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => act(() => api.runPhase("close"))} busy={busy}>Close vote</Button>
          <Button onClick={() => act(() => api.runPhase("execute", true))} busy={busy}>Dry-run</Button>
          {ov.armed
            ? <Button variant="ok" onClick={() => act(() => api.arm(false))} busy={busy}>Disarm</Button>
            : <Button variant="danger" onClick={() => act(() => api.arm(true))} busy={busy}>Arm</Button>}
          <Button variant="danger" onClick={() => act(() => api.kill())} busy={busy}>Kill</Button>
        </div>
      </div>
      <p className="mt-2.5 text-[11.5px] text-subtle">
        Real orders need <b className="text-muted">live</b> mode <i>and</i> ARMED ·
        {" "}{ov.members?.length ?? 0} members · top {ov.top_n} · executes {ov.schedule?.execute} ET
      </p>
    </Card>
  );
}
