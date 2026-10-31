"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { Gate } from "@/components/gate";
import { Empty, Field, Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SkeletonRows } from "@/components/ui/skeleton";
import { api, post } from "@/lib/api";

type Tpl = { id: string; slug: string; name: string; description: string; module: string; version: number; hash: string; installs: number; official: boolean; definition: any };

export default function MarketplacePage() {
  const q = useQuery({ queryKey: ["templates"], queryFn: () => api<{ items: Tpl[] }>("/marketplace/templates") });
  const [sel, setSel] = useState<Tpl | null>(null);
  return (
    <Page>
      <PageHeader eyebrow="MARKET" title="Agent marketplace" description="Versioned workflow templates restricted to approved tools. Installs are pinned to the exact definition hash you review. Community templates cannot prepare transactions." />
      {q.isLoading ? <SkeletonRows rows={4} /> : q.isError ? <Empty title="Marketplace unavailable">{(q.error as Error).message}</Empty> : !q.data?.items.length ? <Empty title="No templates published" /> : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {q.data.items.map((t) => (
            <Card key={t.id} className="flex flex-col">
              <CardBody className="flex flex-1 flex-col gap-3">
                <div className="flex items-center gap-2"><Badge>{t.module}</Badge>{t.official && <Badge tone="base">official</Badge>}<span className="mono ml-auto text-[11px] text-dim">v{t.version}</span></div>
                <div className="font-medium">{t.name}</div>
                <p className="flex-1 text-sm text-muted">{t.description}</p>
                <div className="mono truncate text-[10px] text-dim" title={t.hash}>sha256 {t.hash.slice(0, 16)}…</div>
                <div className="flex items-center justify-between"><span className="text-xs text-dim">{t.installs} installs</span><Button size="sm" variant="outline" onClick={() => setSel(t)}>Review & install</Button></div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
      <Dialog open={!!sel} onOpenChange={(o) => !o && setSel(null)}>
        {sel && <DialogContent title={sel.name} description="Review the exact definition. Bind it to your own addresses."><Gate><Install tpl={sel} onDone={() => setSel(null)} /></Gate></DialogContent>}
      </Dialog>
    </Page>
  );
}

function Install({ tpl, onDone }: { tpl: Tpl; onDone: () => void }) {
  const qc = useQueryClient();
  const trig = tpl.definition.trigger;
  const [wallet, setWallet] = useState("");
  const [token, setToken] = useState("");
  const m = useMutation({
    mutationFn: () => {
      const overrides: Record<string, string> = {};
      if ("wallet" in trig) overrides.wallet = wallet;
      if ("address" in trig) overrides.address = wallet;
      if ("token" in trig && token) overrides.token = token;
      return post(`/marketplace/templates/${tpl.id}/install`, { expected_hash: tpl.hash, overrides });
    },
    onSuccess: () => { toast.success("Installed as a workflow"); qc.invalidateQueries({ queryKey: ["templates"] }); onDone(); },
    onError: (e: any) => toast.error("Install failed", { description: e.message }),
  });
  return (
    <div className="space-y-4">
      <pre className="mono max-h-52 overflow-auto rounded border border-line p-3 text-[11px] text-muted">{JSON.stringify(tpl.definition, null, 2)}</pre>
      <Field label={"address" in trig ? "Contract to watch" : "Wallet to watch"}><Input className="mono" value={wallet} onChange={(e) => setWallet(e.target.value)} /></Field>
      {"token" in trig && trig.token !== "native" && <Field label="Token"><Input className="mono" value={token} onChange={(e) => setToken(e.target.value)} /></Field>}
      <div className="flex justify-end"><Button onClick={() => m.mutate()} loading={m.isPending} disabled={!wallet}>Install pinned v{tpl.version}</Button></div>
    </div>
  );
}
