"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ExecutionPanel, statusTone } from "@/components/execution-panel";
import { Gate } from "@/components/gate";
import { Empty, Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { Execution } from "@/lib/execution";

const PAGE = 15;

export default function ActivityPage() {
  return (
    <Page>
      <PageHeader eyebrow="History" title="Activity & execution history" description="Every execution, its lifecycle status, receipts and the hash-chained audit trail. A transaction is only shown as confirmed after its receipt is verified." />
      <Gate needPass={false}><Activity /></Gate>
    </Page>
  );
}

function Activity() {
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["activity", offset], queryFn: () => api<{ items: Execution[]; total: number }>(`/activity?limit=${PAGE}&offset=${offset}`), refetchInterval: 15_000 });
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => api<{ items: any[] }>("/audit?limit=30") });
  return (
    <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
      <Card>
        <CardHeader><CardTitle>Executions</CardTitle><span className="mono text-xs text-dim">{q.data?.total ?? 0} total</span></CardHeader>
        <CardBody>
          {q.isLoading ? <SkeletonRows rows={6} /> : !q.data?.items.length ? <Empty title="No executions yet">Prepare one from the Terminal or Simulator.</Empty> : (
            <ul className="divide-y divide-[var(--color-line)]">
              {q.data.items.map((e) => {
                const step = e.steps[e.steps.length - 1];
                return (
                  <li key={e.id} className="py-3">
                    <button className="focus-ring flex w-full items-center justify-between gap-3 text-left" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                      <div className="min-w-0">
                        <div className="truncate text-sm">{step?.preview?.action ?? (e as any).raw_text}</div>
                        <div className="mono text-[11px] text-dim">{e.chain} · {new Date((e as any).created_at).toLocaleString()}</div>
                      </div>
                      <Badge tone={statusTone(e.status)}>{e.status.replace(/_/g, " ")}</Badge>
                    </button>
                    {open === e.id && <div className="mt-3"><ExecutionPanel initial={e} /></div>}
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-4 flex justify-between">
            <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
            <Button size="sm" variant="ghost" disabled={(q.data?.items.length ?? 0) < PAGE} onClick={() => setOffset(offset + PAGE)}>Next</Button>
          </div>
        </CardBody>
      </Card>
      <Card>
        <CardHeader><CardTitle>Audit trail</CardTitle><Badge>hash-chained</Badge></CardHeader>
        <CardBody>
          {audit.isLoading ? <SkeletonRows /> : (
            <ol className="space-y-2">
              {(audit.data?.items ?? []).map((a) => (
                <li key={a.id} className="text-xs"><span className="mono text-fg">{a.action}</span> <span className="text-dim">{new Date(a.at).toLocaleTimeString()}</span><div className="mono truncate text-[10px] text-dim">{a.hash}</div></li>
              ))}
            </ol>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
