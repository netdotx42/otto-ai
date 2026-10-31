"use client";
import { AlertTriangle, CheckCircle2, ExternalLink, ShieldAlert, XCircle } from "lucide-react";
import type { Step } from "@/lib/execution";
import { shortAddr } from "@/lib/utils";
import { Badge } from "./ui/badge";

const riskTone = { low: "ok", medium: "warn", high: "bad", blocked: "bad" } as const;

function Row({ k, v, mono = false }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 text-[13px]">
      <span className="text-muted">{k}</span>
      <span className={mono ? "mono break-all text-right text-fg" : "text-right text-fg"}>{v}</span>
    </div>
  );
}

export function PreviewCard({ step, chain }: { step: Step; chain: "base" | "solana" }) {
  const p = step.preview ?? {};
  const risk = (p.risk?.level ?? "blocked") as keyof typeof riskTone;
  const sim = p.simulation ?? {};
  const checks: { id: string; status: string; message: string }[] = p.policy?.checks ?? [];
  const sub = step.submissions?.[0];
  return (
    <div className="overflow-hidden rounded-xl border border-line-strong bg-ink-2" data-testid="preview-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b hairline px-4 py-3">
        <div className="flex items-center gap-2">
          <Badge tone={chain === "base" ? "base" : "sol"}>{p.network ?? chain}</Badge>
          <Badge>{`step ${p.step?.index ?? step.index + 1}/${p.step?.of ?? 1} · ${step.kind}`}</Badge>
        </div>
        <Badge tone={riskTone[risk] ?? "neutral"} aria-label={`Risk ${risk}`}>risk: {risk}</Badge>
      </div>
      <div className="space-y-4 p-4">
        <div className="mono text-[15px] font-medium leading-snug">{p.action}</div>
        <div className="grid gap-x-8 sm:grid-cols-2">
          <div>
            {(p.sends ?? []).map((s: any) => <Row key={s.token} k="You send" v={`${s.amount} ${s.symbol}`} />)}
            {p.max_input && <Row k="Maximum input" v={p.max_input} />}
            {(p.receives ?? []).map((r: any) => (
              <div key={r.token}>
                <Row k="Expected output" v={`${r.expected} ${r.symbol}`} />
                <Row k="Minimum output" v={`${r.minimum} ${r.symbol}`} />
              </div>
            ))}
            {p.type === "swap" && <Row k="Slippage" v={p.slippage} />}
            {p.quote?.price_impact && <Row k="Price impact" v={p.quote.price_impact} />}
            {p.usd_value != null && <Row k="Value (trusted source)" v={`$${Number(p.usd_value).toFixed(2)}`} />}
          </div>
          <div>
            {p.destination && <Row k="Destination" v={<span title={p.destination.address}>{p.destination.label} · {shortAddr(p.destination.address, 6)}</span>} />}
            {(p.approvals ?? []).map((a: any) => (
              <Row key={a.spender} k="Token approval" v={<span>{a.amount} {a.symbol} → {a.spender_label}{a.unlimited ? " (UNLIMITED)" : " (exact)"}</span>} />
            ))}
            {p.fees && <Row k="Est. network fee" v={p.fees.estimated_total ?? p.fees.base_fee} />}
            {p.quote && <Row k="Quote" v={`${p.quote.provider}`} />}
            {p.quote?.expires_at && <Row k="Quote expires" v={new Date(p.quote.expires_at * 1000).toLocaleTimeString()} />}
            <Row k="Simulation" v={
              <span className={sim.status === "success" ? "text-ok" : "text-bad"}>
                {sim.status}{sim.block_or_slot ? ` @ ${chain === "base" ? "block" : "slot"} ${sim.block_or_slot}` : ""}
              </span>
            } />
          </div>
        </div>

        {sim.error && (
          <div className="flex gap-2 rounded-lg border border-bad/30 bg-bad/5 p-3 text-[13px] text-bad"><XCircle className="mt-0.5 size-4 shrink-0" />{sim.error}</div>
        )}
        {(sim.balance_changes?.length ?? 0) > 0 && (
          <div>
            <div className="label mb-1.5">Simulated balance changes</div>
            <div className="flex flex-wrap gap-2">
              {sim.balance_changes.map((c: any, i: number) => (
                <span key={i} className={`mono rounded-md border border-line px-2 py-1 text-xs ${String(c.delta).startsWith("-") ? "text-bad" : "text-ok"}`}>{String(c.delta).startsWith("-") ? "" : "+"}{c.delta} {c.symbol}</span>
              ))}
            </div>
          </div>
        )}

        <details className="group rounded-lg border border-line">
          <summary className="focus-ring flex cursor-pointer items-center justify-between px-3 py-2 text-[13px]">
            <span className="flex items-center gap-2">{p.policy?.allowed ? <CheckCircle2 className="size-4 text-ok" /> : <ShieldAlert className="size-4 text-bad" />} Security policy · {checks.filter((c) => c.status === "pass").length}/{checks.length} passed</span>
            <span className="label">details</span>
          </summary>
          <ul className="space-y-1 border-t hairline px-3 py-2">
            {checks.map((c) => (
              <li key={c.id} className="flex gap-2 text-xs">
                <span className={c.status === "pass" ? "text-ok" : c.status === "warn" ? "text-warn" : "text-bad"}>{c.status === "pass" ? "✓" : c.status === "warn" ? "!" : "✕"}</span>
                <span className="mono text-dim">{c.id}</span>
                <span className="text-muted">{c.message}</span>
              </li>
            ))}
          </ul>
        </details>

        {(p.risk?.warnings ?? []).filter((w: string) => w).length > 0 && (
          <ul className="space-y-1">
            {p.risk.warnings.map((w: string, i: number) => (
              <li key={i} className="flex gap-2 text-xs text-warn"><AlertTriangle className="size-3.5 shrink-0" />{w}</li>
            ))}
          </ul>
        )}

        <details className="rounded-lg border border-line">
          <summary className="focus-ring cursor-pointer px-3 py-2 text-[13px]">Decoded transaction</summary>
          <pre className="mono max-h-64 overflow-auto border-t hairline p-3 text-[11px] text-muted">{JSON.stringify(p.decoded, null, 2)}</pre>
          <div className="mono break-all border-t hairline px-3 py-2 text-[11px] text-dim">digest {step.digest}</div>
        </details>

        {(sim.limitations ?? []).length > 0 && <p className="text-[11px] leading-relaxed text-dim">Limitations: {sim.limitations.join(" ")}</p>}

        {sub && (
          <a href={sub.explorer_url} target="_blank" rel="noreferrer" className="focus-ring mono inline-flex items-center gap-1.5 text-xs text-[#8fb0ff] hover:underline">
            {shortAddr(sub.tx_id, 8)} · {sub.status}{sub.receipt ? (sub.receipt.success ? " · receipt ok" : " · REVERTED") : ""} <ExternalLink className="size-3" />
          </a>
        )}
      </div>
    </div>
  );
}
