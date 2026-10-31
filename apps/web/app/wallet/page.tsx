"use client";
import { useState } from "react";
import { ChainToggle } from "@/components/chain-toggle";
import { Gate } from "@/components/gate";
import { Evidence, useModule } from "@/components/modules/run";
import { Page, PageHeader, Field } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApp } from "@/lib/store";
import { shortAddr } from "@/lib/utils";

export default function WalletIntelPage() {
  const chain = useApp((s) => s.chain);
  const [token, setToken] = useState("");
  const [wallet, setWallet] = useState("");
  const [min, setMin] = useState("10000");
  const whale = useModule("whale");
  const alpha = useModule("alpha");
  const sentinel = useModule("sentinel");
  return (
    <Page>
      <PageHeader eyebrow="WHALE · ALPHA · SENTINEL" title="Wallet intelligence" description="Large transfers, counterparty clusters, statistical anomalies and live monitoring — computed from logs and parsed transactions with cited block ranges." actions={<ChainToggle />} />
      <Gate>
        <Tabs defaultValue="whale">
          <TabsList><TabsTrigger value="whale">Whale flows</TabsTrigger><TabsTrigger value="alpha">Anomalies</TabsTrigger><TabsTrigger value="sentinel">Monitor</TabsTrigger></TabsList>
          <TabsContent value="whale" className="mt-6">
            <Card><CardBody className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
                {chain === "base" ? <Field label="ERC-20 token"><Input className="mono" value={token} onChange={(e) => setToken(e.target.value)} placeholder="0x…" /></Field> : <Field label="Solana wallet"><Input className="mono" value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="wallet address" /></Field>}
                <Field label="Min amount (token units)"><Input className="mono" value={min} onChange={(e) => setMin(e.target.value)} /></Field>
                <Button loading={whale.isPending} onClick={() => whale.mutate(chain === "base" ? { chain, token, min_amount: min } : { chain, wallet, min_amount: min })}>Analyze</Button>
              </div>
              {whale.data && (
                <div className="space-y-4">
                  <p className="text-sm">{whale.data.summary}</p>
                  {whale.data.data.clusters && (
                    <table className="w-full text-left text-xs">
                      <thead className="label"><tr><th className="py-1.5">Counterparty</th><th>Inflow</th><th>Outflow</th><th>Transfers</th></tr></thead>
                      <tbody>{whale.data.data.clusters.map((c: any) => <tr key={c.address} className="mono border-t hairline"><td className="py-1.5">{shortAddr(c.address, 6)}</td><td>{c.inflow}</td><td>{c.outflow}</td><td>{c.count}</td></tr>)}</tbody>
                    </table>
                  )}
                  {whale.data.data.large_changes && <ul className="mono space-y-1 text-xs">{whale.data.data.large_changes.map((c: any) => <li key={c.signature + c.mint}>{shortAddr(c.signature, 8)} · {c.delta} · {shortAddr(c.mint)}</li>)}</ul>}
                  <Evidence out={whale.data} />
                </div>
              )}
            </CardBody></Card>
          </TabsContent>
          <TabsContent value="alpha" className="mt-6">
            <Card><CardBody className="space-y-4">
              {chain !== "base" ? <p className="text-sm text-muted">ALPHA anomaly windows currently run on Base Transfer logs.</p> : (
                <div className="flex gap-2"><Input className="mono" value={token} onChange={(e) => setToken(e.target.value)} placeholder="ERC-20 token 0x…" /><Button loading={alpha.isPending} onClick={() => alpha.mutate({ chain: "base", token })}>Run</Button></div>
              )}
              {alpha.data && (
                <div className="space-y-4">
                  <p className="text-sm">{alpha.data.summary}</p>
                  <div className="flex h-24 items-end gap-1" aria-label="Transfer count per window">
                    {alpha.data.data.counts.map((c: number, i: number, arr: number[]) => (
                      <div key={i} className="flex-1 rounded-sm bg-base/60" style={{ height: `${(c / Math.max(1, ...arr)) * 100}%` }} title={`${c} transfers`} />
                    ))}
                  </div>
                  <p className="mono text-[11px] text-dim">z(count) {fmtZ(alpha.data.data.z_scores.count)} · z(volume) {fmtZ(alpha.data.data.z_scores.volume)} · z(senders) {fmtZ(alpha.data.data.z_scores.senders)}</p>
                  <Evidence out={alpha.data} />
                </div>
              )}
            </CardBody></Card>
          </TabsContent>
          <TabsContent value="sentinel" className="mt-6">
            <Card><CardHeader><CardTitle>Arm a monitor</CardTitle></CardHeader><CardBody className="space-y-4">
              <div className="flex gap-2"><Input className="mono" value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="Wallet or contract address" />
                <Button variant="outline" loading={sentinel.isPending} onClick={() => sentinel.mutate({ chain, target: wallet, kind: "wallet" })}>Snapshot</Button>
                <Button loading={sentinel.isPending} onClick={() => sentinel.mutate({ chain, target: wallet, kind: chain === "base" ? "contract" : "wallet", create_rule: true })}>Monitor</Button>
              </div>
              {sentinel.data && <><p className="text-sm">{sentinel.data.summary}</p><pre className="mono overflow-auto rounded border border-line p-3 text-[11px] text-muted">{JSON.stringify(sentinel.data.data, null, 2)}</pre><Evidence out={sentinel.data} /></>}
              <p className="text-xs text-dim">For richer triggers with actions, build a workflow in Automations.</p>
            </CardBody></Card>
          </TabsContent>
        </Tabs>
      </Gate>
    </Page>
  );
}

function fmtZ(z: number | null) {
  return z == null ? "n/a" : z.toFixed(2);
}
