"use client";
import { useState } from "react";
import { useAccount } from "wagmi";
import { useWallet } from "@solana/wallet-adapter-react";
import { ChainToggle } from "@/components/chain-toggle";
import { ExecutionPanel } from "@/components/execution-panel";
import { Gate } from "@/components/gate";
import { Page, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { newIdempotencyKey, post } from "@/lib/api";
import type { Execution } from "@/lib/execution";
import { useApp } from "@/lib/store";
import { toast } from "sonner";

export default function SimulatorPage() {
  const chain = useApp((s) => s.chain);
  const { address } = useAccount();
  const sol = useWallet();
  const [text, setText] = useState(chain === "base" ? "swap 5 USDC into WETH with 0.5% slippage" : "send 0.01 SOL to ");
  const [ex, setEx] = useState<Execution | null>(null);
  const [busy, setBusy] = useState(false);
  const wallet = chain === "base" ? address : sol.publicKey?.toBase58();

  async function run() {
    if (!wallet) return toast.error("Connect a wallet for the selected chain.");
    setBusy(true);
    setEx(null);
    try {
      setEx(await post<Execution>("/executions", { request: text, chain, wallet }, newIdempotencyKey()));
    } catch (e: any) {
      toast.error("Simulation request failed", { description: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page className="max-w-4xl">
      <PageHeader eyebrow="SIMULATOR" title="Transaction simulator" description="Intent → research → build → simulate → policy → preview. Simulations run against current chain state; approval requires your explicit confirmation and your wallet's signature." actions={<ChainToggle />} />
      <Gate>
        <div className="space-y-3">
          <Textarea rows={3} className="mono" value={text} onChange={(e) => setText(e.target.value)} aria-label="Transaction request" />
          <div className="flex items-center justify-between">
            <span className="text-xs text-dim">5 credits per simulation · quotes valid ~30s</span>
            <Button onClick={run} loading={busy}>Simulate</Button>
          </div>
        </div>
        <div className="mt-6">{ex && <ExecutionPanel key={ex.id} initial={ex} onChange={setEx} />}</div>
      </Gate>
    </Page>
  );
}
