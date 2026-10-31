"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Gate, useEntitlements } from "@/components/gate";
import { Field, Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { WalletBar } from "@/components/wallet-bar";
import { api } from "@/lib/api";
import { useSignIn } from "@/lib/auth";
import { useApp } from "@/lib/store";

export default function SettingsPage() {
  return (
    <Page className="max-w-4xl">
      <PageHeader eyebrow="Account" title="Settings" description="Linked wallets, entitlements and execution limits. You can only tighten platform limits, never loosen them." />
      <Gate needPass={false}><Settings /></Gate>
    </Page>
  );
}

function Settings() {
  const session = useApp((s) => s.session);
  const qc = useQueryClient();
  const ent = useEntitlements();
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api<{ settings: any; platform_limits: any }>("/me/settings") });
  const [form, setForm] = useState({ max_tx_usd: "", daily_limit_usd: "", max_slippage_bps: "", allow_unlimited_approvals: false });
  const { signOut } = useSignIn();
  useEffect(() => {
    if (s.data) setForm({
      max_tx_usd: String(s.data.settings.max_tx_usd ?? s.data.platform_limits.max_tx_usd),
      daily_limit_usd: String(s.data.settings.daily_limit_usd ?? s.data.platform_limits.daily_limit_usd),
      max_slippage_bps: String(s.data.settings.max_slippage_bps ?? s.data.platform_limits.max_slippage_bps),
      allow_unlimited_approvals: !!s.data.settings.allow_unlimited_approvals,
    });
  }, [s.data]);
  const save = useMutation({
    mutationFn: () => api("/me/settings", { method: "PUT", body: JSON.stringify({ max_tx_usd: Number(form.max_tx_usd), daily_limit_usd: Number(form.daily_limit_usd), max_slippage_bps: Number(form.max_slippage_bps), allow_unlimited_approvals: form.allow_unlimited_approvals }) }),
    onSuccess: () => { toast.success("Limits saved"); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: (e: any) => toast.error("Save failed", { description: e.message }),
  });
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle>Linked wallets</CardTitle><WalletBar /></CardHeader>
        <CardBody>
          <ul className="space-y-2">
            {session?.wallets.map((w) => <li key={w.address} className="flex items-center gap-3 text-sm"><Badge tone={w.chain === "base" ? "base" : "sol"}>{w.chain}</Badge><span className="mono break-all">{w.address}</span></li>)}
          </ul>
          <p className="mt-3 text-xs text-dim">Connect another wallet with the chain toggle and press Verify to link it to this account.</p>
          <Button className="mt-4" size="sm" variant="ghost" onClick={signOut}>Sign out</Button>
        </CardBody>
      </Card>
      <Card>
        <CardHeader><CardTitle>Entitlements & usage</CardTitle><Button size="sm" variant="ghost" onClick={() => api("/me/entitlements?refresh=true").then(() => qc.invalidateQueries({ queryKey: ["entitlements"] }))}>Re-verify on-chain</Button></CardHeader>
        <CardBody className="grid gap-4 text-sm sm:grid-cols-3">
          <div><div className="label">Passes</div><div className="mono mt-1 text-xl">{ent.data?.passes ?? "—"}</div></div>
          <div><div className="label">Credits remaining</div><div className="mono mt-1 text-xl">{ent.data ? `${ent.data.credits_remaining}/${ent.data.credits_granted}` : "—"}</div></div>
          <div><div className="label">Period resets</div><div className="mono mt-1">{ent.data ? new Date(ent.data.period_resets_at).toLocaleDateString() : "—"}</div></div>
          <div className="sm:col-span-3">{ent.data?.by_wallet.map((b) => <div key={b.address} className="mono text-[11px] text-dim">{b.chain}:{b.address.slice(0, 10)}… → {b.passes ?? "unverified"} ({b.source})</div>)}</div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader><CardTitle>Execution limits</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Max per transaction (USD)"><Input type="number" value={form.max_tx_usd} onChange={(e) => setForm({ ...form, max_tx_usd: e.target.value })} /></Field>
            <Field label="Daily limit (USD)"><Input type="number" value={form.daily_limit_usd} onChange={(e) => setForm({ ...form, daily_limit_usd: e.target.value })} /></Field>
            <Field label="Max slippage (bps)"><Input type="number" value={form.max_slippage_bps} onChange={(e) => setForm({ ...form, max_slippage_bps: e.target.value })} /></Field>
          </div>
          <label className="flex items-center justify-between gap-4 rounded-lg border border-line p-3 text-sm">
            <span>Allow unlimited token approvals <span className="block text-xs text-dim">Off by default. Exact-amount approvals are strongly recommended.</span></span>
            <Switch checked={form.allow_unlimited_approvals} onCheckedChange={(v) => setForm({ ...form, allow_unlimited_approvals: v })} aria-label="Allow unlimited approvals" />
          </label>
          <div className="flex justify-end"><Button onClick={() => save.mutate()} loading={save.isPending}>Save limits</Button></div>
        </CardBody>
      </Card>
    </div>
  );
}
