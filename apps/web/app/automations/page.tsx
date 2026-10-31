"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Gate } from "@/components/gate";
import { Empty, Field, Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { SkeletonRows } from "@/components/ui/skeleton";
import { api, newIdempotencyKey, post } from "@/lib/api";
import { useApp } from "@/lib/store";

type TriggerType = "wallet_large_transfer" | "balance_below" | "contract_code_change";

export default function AutomationsPage() {
  return (
    <Page>
      <PageHeader eyebrow="AUTOPILOT · BUILDER" title="Automations" description="No-code monitoring workflows. Versioned, rate-limited, auditable and revocable. Workflows may prepare a transaction, but it always pauses for your explicit approval and signature — there is no autonomous fund movement." />
      <Gate><Automations /></Gate>
    </Page>
  );
}

function Automations() {
  const qc = useQueryClient();
  const chainSel = useApp((s) => s.chain);
  const session = useApp((s) => s.session);
  const list = useQuery({ queryKey: ["workflows"], queryFn: () => api<{ items: any[] }>("/workflows") });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api<{ items: any[] }>("/alerts"), refetchInterval: 30_000 });
  const myWallet = session?.wallets.find((w) => w.chain === chainSel)?.address ?? "";

  const [name, setName] = useState("Whale trade → analyze token");
  const [type, setType] = useState<TriggerType>("wallet_large_transfer");
  const [wallet, setWallet] = useState("");
  const [token, setToken] = useState("");
  const [amount, setAmount] = useState("10000");
  const [scan, setScan] = useState(true);
  const [prepare, setPrepare] = useState("");
  const [maxRuns, setMaxRuns] = useState(24);

  const definition = () => {
    const trigger =
      type === "contract_code_change" ? { type, chain: "base", address: wallet }
      : type === "balance_below" ? { type, chain: chainSel, wallet, token: token || "native", threshold: amount }
      : { type, chain: chainSel, wallet, token: token || null, min_amount: amount };
    const actions: any[] = [];
    if (scan) actions.push({ type: "forensic_scan" });
    if (prepare.trim()) actions.push({ type: "prepare_transaction", request: prepare, wallet: myWallet });
    actions.push({ type: "alert", severity: "warning" });
    return { name, trigger, actions, limits: { max_runs_per_day: maxRuns } };
  };

  const validate = useMutation({ mutationFn: () => post<{ valid: boolean; errors: string[] }>("/workflows/validate", { definition: definition() }) });
  const create = useMutation({
    mutationFn: () => post("/workflows", { definition: definition() }, newIdempotencyKey()),
    onSuccess: () => { toast.success("Workflow armed"); qc.invalidateQueries({ queryKey: ["workflows"] }); },
    onError: (e: any) => toast.error("Could not save workflow", { description: e.message }),
  });
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => post(`/workflows/${id}/status`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });

  return (
    <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
      <Card>
        <CardHeader><CardTitle>Builder</CardTitle><Badge>allowlisted tools only</Badge></CardHeader>
        <CardBody className="space-y-4">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="When">
            <select value={type} onChange={(e) => setType(e.target.value as TriggerType)} className="focus-ring h-10 w-full rounded-lg border border-line-strong bg-ink-2 px-3 text-sm">
              <option value="wallet_large_transfer">This wallet makes a large transfer</option>
              <option value="balance_below">A balance falls below a threshold</option>
              <option value="contract_code_change">A contract's code / proxy implementation changes (Base)</option>
            </select>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={type === "contract_code_change" ? "Contract" : "Wallet"}><Input className="mono" value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="address" /></Field>
            {type !== "contract_code_change" && <Field label="Token" hint={type === "wallet_large_transfer" && chainSel === "base" ? "Required on Base (ERC-20)" : "Optional"}><Input className="mono" value={token} onChange={(e) => setToken(e.target.value)} placeholder={type === "balance_below" ? "native" : "token address"} /></Field>}
            {type !== "contract_code_change" && <Field label={type === "balance_below" ? "Threshold" : "Minimum amount"}><Input className="mono" value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>}
            <Field label="Max runs / day"><Input type="number" min={1} max={288} value={maxRuns} onChange={(e) => setMaxRuns(Number(e.target.value))} /></Field>
          </div>
          <div className="space-y-2 rounded-lg border border-line p-3">
            <div className="label">Then</div>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={scan} onChange={(e) => setScan(e.target.checked)} /> Analyze the token (FORENSIC)</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked disabled /> Alert me</label>
            <Field label="Optionally prepare a transaction (pauses for approval)" hint={myWallet ? `Prepared for your verified wallet ${myWallet.slice(0, 8)}…` : "Verify a wallet to enable"}><Input className="mono" value={prepare} onChange={(e) => setPrepare(e.target.value)} placeholder="e.g. swap 5 USDC into WETH" disabled={!myWallet} /></Field>
          </div>
          {validate.data && !validate.data.valid && <ul className="space-y-1 text-xs text-bad">{validate.data.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          {validate.data?.valid && <p className="text-xs text-ok">Definition is valid.</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => validate.mutate()} loading={validate.isPending}>Validate</Button>
            <Button onClick={() => create.mutate()} loading={create.isPending}>Save & arm</Button>
          </div>
        </CardBody>
      </Card>
      <div className="space-y-6">
        <Card>
          <CardHeader><CardTitle>Your workflows</CardTitle></CardHeader>
          <CardBody>
            {list.isLoading ? <SkeletonRows /> : !list.data?.items.length ? <Empty title="No workflows yet" /> : (
              <ul className="divide-y divide-[var(--color-line)]">
                {list.data.items.map((w) => (
                  <li key={w.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div><div className="text-sm">{w.name}</div><div className="mono text-[11px] text-dim">v{w.version} · {w.runs_today} runs today</div></div>
                    <div className="flex items-center gap-2">
                      <Badge tone={w.status === "active" ? "ok" : w.status === "revoked" ? "bad" : "warn"}>{w.status}</Badge>
                      {w.status !== "revoked" && <Button size="sm" variant="ghost" onClick={() => setStatus.mutate({ id: w.id, status: w.status === "active" ? "paused" : "active" })}>{w.status === "active" ? "Pause" : "Resume"}</Button>}
                      {w.status !== "revoked" && <Button size="sm" variant="ghost" className="text-bad" onClick={() => setStatus.mutate({ id: w.id, status: "revoked" })}>Revoke</Button>}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Alerts</CardTitle></CardHeader>
          <CardBody>
            {alerts.isLoading ? <SkeletonRows /> : !alerts.data?.items.length ? <Empty title="No alerts">Alerts appear when a workflow trigger fires.</Empty> : (
              <ul className="space-y-2">
                {alerts.data.items.map((a) => (
                  <li key={a.id} className="rounded-lg border border-line p-3 text-sm">
                    <div className="flex items-center justify-between gap-2"><span>{a.title}</span><Badge tone={a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "neutral"}>{a.severity}</Badge></div>
                    <div className="mono mt-1 text-[11px] text-dim">{new Date(a.created_at).toLocaleString()}</div>
                    {a.body?.execution && <a href="/activity" className="mt-1 inline-block text-xs text-[#8fb0ff] underline">Review prepared transaction →</a>}
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
