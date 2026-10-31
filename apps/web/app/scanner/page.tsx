"use client";
import { useState } from "react";
import { ChainToggle } from "@/components/chain-toggle";
import { Gate } from "@/components/gate";
import { Evidence, useModule } from "@/components/modules/run";
import { Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SkeletonRows } from "@/components/ui/skeleton";
import { isEvmAddress, isSolAddress } from "@/lib/addresses";
import { useApp } from "@/lib/store";

const SEV = { info: "neutral", low: "ok", medium: "warn", high: "bad", critical: "bad" } as const;

export default function ScannerPage() {
  const chain = useApp((s) => s.chain);
  const [target, setTarget] = useState("");
  const m = useModule("forensic");
  const valid = chain === "base" ? isEvmAddress(target) : isSolAddress(target);
  const rep = m.data?.data;
  return (
    <Page>
      <PageHeader eyebrow="FORENSIC" title="Token scanner" description="Risk analysis from raw chain state: bytecode selectors, proxy slots, ownership, mint/freeze authorities and Token-2022 extensions. Token metadata is treated as untrusted data." actions={<ChainToggle />} />
      <Gate>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (valid) m.mutate({ chain, target: target.trim() }); }}>
          <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder={chain === "base" ? "ERC-20 contract 0x…" : "SPL mint address"} className="mono" aria-label="Token address" aria-invalid={!!target && !valid} />
          <Button type="submit" disabled={!valid} loading={m.isPending}>Scan</Button>
        </form>
        {target && !valid && <p className="mt-2 text-xs text-bad">Not a valid {chain === "base" ? "EVM" : "Solana"} address.</p>}
        <div className="mt-6">
          {m.isPending && <SkeletonRows rows={5} />}
          {rep && (
            <Card>
              <CardHeader>
                <CardTitle>{rep.metadata?.symbol ?? "Token"} · <span className="mono normal-case text-muted">{rep.token}</span></CardTitle>
                <Badge tone={SEV[rep.risk_level as keyof typeof SEV] ?? "neutral"}>risk: {rep.risk_level}</Badge>
              </CardHeader>
              <CardBody className="space-y-4">
                <p className="text-sm">{m.data!.summary}</p>
                <ul className="divide-y divide-[var(--color-line)]">
                  {rep.findings.map((f: any) => (
                    <li key={f.id} className="flex gap-3 py-2.5 text-sm">
                      <Badge tone={SEV[f.severity as keyof typeof SEV]}>{f.severity}</Badge>
                      <div><div>{f.message}</div><div className="mono mt-0.5 text-[11px] text-dim">{f.evidence}</div></div>
                    </li>
                  ))}
                  {!rep.findings.length && <li className="py-2 text-sm text-muted">No findings from the checks performed.</li>}
                </ul>
                <div className="grid gap-2 text-xs sm:grid-cols-2">
                  {Object.entries(rep.metadata ?? {}).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-3 rounded border border-line px-2 py-1.5"><span className="text-muted">{k}</span><span className="mono truncate">{String(v ?? "—")}</span></div>
                  ))}
                </div>
                <Evidence out={m.data!} />
              </CardBody>
            </Card>
          )}
        </div>
      </Gate>
    </Page>
  );
}
