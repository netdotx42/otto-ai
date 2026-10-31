import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { LiveSupply } from "./supply-ticker";

const MODULES = [
  ["SENTINEL", "Wallet, liquidity and contract monitoring"],
  ["FORENSIC", "Token and contract risk from raw chain state"],
  ["WHALE", "Large transfers and counterparty clusters"],
  ["ALPHA", "Statistical on-chain anomaly detection"],
  ["SIMULATOR", "Build and simulate before anything is signed"],
  ["PORTFOLIO", "Unified Base + Solana holdings"],
  ["AUTOPILOT", "Monitoring workflows that pause for approval"],
  ["BUILDER", "No-code agent workflow builder"],
  ["INTEL", "Natural-language research with citations"],
  ["MARKET", "Versioned, hash-pinned agent templates"],
];

const LIFECYCLE = ["Intent", "Research", "Build", "Simulate", "Explain", "Sign", "Verify"];

export default function Home() {
  return (
    <main id="main">
      <section className="mx-auto max-w-7xl px-4 pb-20 pt-20 sm:px-6 sm:pt-28">
        <div className="mono mb-6 inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-[11px] uppercase tracking-[0.14em] text-muted">
          <span className="size-1.5 rounded-full bg-base" /> Base <span className="text-dim">×</span> <span className="size-1.5 rounded-full bg-sol-green" /> Solana
        </div>
        <h1 className="max-w-4xl text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl">
          The chain never sleeps.
          <br />
          <span className="text-muted">Neither do your agents.</span>
        </h1>
        <p className="mt-6 max-w-xl text-[15px] leading-relaxed text-muted">
          AGENT//98 is a sovereign intelligence and assisted-execution layer for Base and Solana. Agents research and prepare.
          A deterministic policy engine validates. You review a simulated preview and sign in your own wallet.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Button asChild size="lg"><Link href="/mint">Mint pass · 1.98 USDC <ArrowRight /></Link></Button>
          <Button asChild size="lg" variant="outline"><Link href="/terminal">Open terminal</Link></Button>
        </div>
        <LiveSupply />
      </section>

      <section className="border-y hairline bg-white/[0.01]">
        <div className="mx-auto grid max-w-7xl gap-px px-4 sm:px-6 md:grid-cols-[1fr_2fr]">
          <div className="py-12 pr-8">
            <div className="label mb-3">Level 3 · Assisted execution</div>
            <h2 className="text-xl font-semibold tracking-tight">The AI researches. The system validates. The user signs. The chain executes.</h2>
            <p className="mt-3 text-sm text-muted">Every approval is bound to an immutable transaction digest. Change one byte and the approval is void.</p>
          </div>
          <ol className="grid grid-cols-2 gap-px py-12 sm:grid-cols-4 lg:grid-cols-7">
            {LIFECYCLE.map((s, i) => (
              <li key={s} className="border-l hairline px-4 py-2">
                <div className="mono text-[11px] text-dim">0{i + 1}</div>
                <div className="mt-1 text-sm font-medium">{s}</div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
        <div className="mb-8 flex items-end justify-between">
          <div>
            <div className="label mb-2">Modules</div>
            <h2 className="text-2xl font-semibold tracking-tight">Ten specialized agents. Typed tools. Traceable evidence.</h2>
          </div>
        </div>
        <div className="grid gap-px overflow-hidden rounded-xl border hairline bg-[var(--color-line)] sm:grid-cols-2 lg:grid-cols-5">
          {MODULES.map(([n, d]) => (
            <div key={n} className="bg-ink p-5 transition hover:bg-ink-2">
              <div className="mono text-[13px] font-semibold">{n}</div>
              <p className="mt-2 text-[13px] leading-relaxed text-muted">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-6 px-4 sm:px-6 md:grid-cols-3">
        {[
          ["Unlimited, honestly", "No supply cap on passes. Usage credits are transparent and capped per period under a fair-use policy — passes are access, not unlimited inference."],
          ["Non-custodial by design", "No private keys, no seed phrases, no server signing. Agents can prepare transactions; only your wallet can authorize them."],
          ["No invented data", "Every number cites its RPC method and block or slot. Missing prices are shown as unavailable, never estimated."],
        ].map(([t, d]) => (
          <div key={t} className="glass rounded-xl p-6">
            <div className="text-sm font-semibold">{t}</div>
            <p className="mt-2 text-[13px] leading-relaxed text-muted">{d}</p>
          </div>
        ))}
      </section>
    </main>
  );
}
