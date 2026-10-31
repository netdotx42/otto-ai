"use client";
import { useEffect } from "react";
import { Gate } from "@/components/gate";
import { Evidence, useModule } from "@/components/modules/run";
import { Page, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { shortAddr } from "@/lib/utils";

export default function PortfolioPage() {
  return (
    <Page>
      <PageHeader eyebrow="PORTFOLIO" title="Unified portfolio" description="Balances across your verified Base and Solana wallets. USD values come only from trusted, cited sources; anything without one is marked unavailable and excluded from the total." />
      <Gate><Portfolio /></Gate>
    </Page>
  );
}

function Portfolio() {
  const m = useModule("portfolio");
  useEffect(() => { m.mutate({}); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const d = m.data?.data;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Holdings</CardTitle>
        <div className="flex items-center gap-3">
          {d && <span className="mono text-sm">${d.total_usd_priced.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>}
          <Button size="sm" variant="outline" onClick={() => m.mutate({})} loading={m.isPending}>Refresh</Button>
        </div>
      </CardHeader>
      <CardBody>
        {m.isPending && !d ? <SkeletonRows rows={5} /> : d ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left text-sm">
                <thead className="label"><tr><th className="pb-2">Asset</th><th>Chain</th><th>Wallet</th><th className="text-right">Amount</th><th className="text-right">USD</th></tr></thead>
                <tbody>
                  {d.holdings.map((h: any, i: number) => (
                    <tr key={i} className="border-t hairline">
                      <td className="py-2.5 font-medium">{h.symbol}</td>
                      <td><Badge tone={h.chain === "base" ? "base" : "sol"}>{h.chain}</Badge></td>
                      <td className="mono text-xs text-muted">{shortAddr(h.wallet)}</td>
                      <td className="mono text-right">{Number(h.amount).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
                      <td className="mono text-right" title={h.price_source ?? "no trusted price"}>{h.usd == null ? <span className="text-dim">unavailable</span> : `$${h.usd.toLocaleString(undefined, { maximumFractionDigits: 2 })}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-4"><Evidence out={m.data!} /></div>
          </>
        ) : null}
      </CardBody>
    </Card>
  );
}
