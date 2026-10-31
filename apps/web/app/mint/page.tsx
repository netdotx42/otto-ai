"use client";
import { useConnection, useWallet } from "@solana/wallet-adapter-react";
import { PublicKey } from "@solana/web3.js";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Minus, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { useAccount, usePublicClient, useReadContract, useSwitchChain, useWriteContract } from "wagmi";
import { ChainToggle } from "@/components/chain-toggle";
import { Page, PageHeader, Empty } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { SkeletonRows } from "@/components/ui/skeleton";
import { WalletBar } from "@/components/wallet-bar";
import { ERC20_ABI, PASS_ABI } from "@/lib/abi";
import { api } from "@/lib/api";
import { BASE, BASE_CHAIN, BASE_PASS_ADDRESS, MAX_BATCH, SOLANA_PASS_PROGRAM_ID, clampQty, explorerTx, formatUsdc, mintCost } from "@/lib/chains";
import { buildMintTx } from "@/lib/solana-mint";
import { useApp } from "@/lib/store";

type Phase = "idle" | "approving" | "minting" | "confirming";
type Result = { chain: "base" | "solana"; tx: string; ok: boolean; reason?: string };

export default function MintPage() {
  const chain = useApp((s) => s.chain);
  const [qty, setQty] = useState(1);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<Result | null>(null);
  const cost = useMemo(() => mintCost(qty), [qty]);
  const qc = useQueryClient();
  const session = useApp((s) => s.session);

  const supply = useQuery({ queryKey: ["supply"], queryFn: () => api<any>("/mint/supply"), refetchInterval: 20_000 });
  const history = useQuery({ queryKey: ["my-mints"], queryFn: () => api<{ items: any[] }>("/me/mints"), enabled: !!session });

  async function afterSuccess() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["supply"] }),
      api("/me/entitlements?refresh=true").catch(() => null),
      qc.invalidateQueries({ queryKey: ["entitlements"] }),
      qc.invalidateQueries({ queryKey: ["my-mints"] }),
    ]);
  }

  return (
    <Page>
      <PageHeader eyebrow="Utility pass" title="Mint AGENT//98 access" description="Exactly 1.98 USDC per pass. No aggregate supply cap; batch size is limited per transaction for safety only. Passes on either chain unlock the same platform access." />
      <div className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Checkout</CardTitle>
            <ChainToggle />
          </CardHeader>
          <CardBody className="space-y-6">
            <div>
              <div className="label mb-2">Quantity</div>
              <div className="flex items-center gap-3">
                <Button variant="outline" size="icon" onClick={() => setQty((q) => clampQty(q - 1))} aria-label="Decrease quantity" disabled={qty <= 1}><Minus /></Button>
                <input
                  aria-label="Quantity"
                  inputMode="numeric"
                  className="focus-ring mono h-10 w-20 rounded-lg border border-line-strong bg-white/[0.02] text-center text-lg"
                  value={qty}
                  onChange={(e) => setQty(clampQty(Number(e.target.value.replace(/\D/g, "")) || 1))}
                />
                <Button variant="outline" size="icon" onClick={() => setQty((q) => clampQty(q + 1))} aria-label="Increase quantity" disabled={qty >= MAX_BATCH}><Plus /></Button>
                <span className="text-xs text-dim">max {MAX_BATCH} per transaction · mint again for more</span>
              </div>
            </div>
            <div className="flex items-end justify-between rounded-lg border border-line p-4">
              <div>
                <div className="label">Total</div>
                <div className="mono mt-1 text-3xl" data-testid="mint-total">{formatUsdc(cost)} <span className="text-base text-muted">USDC</span></div>
              </div>
              <div className="text-right text-xs text-dim">{qty} × 1.98 USDC<br />paid to treasury on-chain</div>
            </div>
            {chain === "base" ? (
              <BaseMint qty={qty} cost={cost} phase={phase} setPhase={setPhase} onResult={async (r) => { setResult(r); if (r.ok) await afterSuccess(); }} />
            ) : (
              <SolanaMint qty={qty} phase={phase} setPhase={setPhase} onResult={async (r) => { setResult(r); if (r.ok) await afterSuccess(); }} />
            )}
            {result && (
              <div className={`rounded-lg border p-4 text-sm ${result.ok ? "border-ok/30 bg-ok/5" : "border-bad/30 bg-bad/5"}`} role="status">
                <div className={result.ok ? "text-ok" : "text-bad"}>{result.ok ? "Mint confirmed on-chain." : `Mint failed: ${result.reason ?? "transaction reverted"}`}</div>
                <a className="mono mt-1 inline-flex items-center gap-1 text-xs text-[#8fb0ff] hover:underline" href={explorerTx(result.chain, result.tx)} target="_blank" rel="noreferrer">
                  {result.tx.slice(0, 18)}… <ExternalLink className="size-3" />
                </a>
              </div>
            )}
          </CardBody>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Live supply (verified)</CardTitle><Badge>no cap</Badge></CardHeader>
            <CardBody>
              {supply.isLoading ? <SkeletonRows rows={2} /> : supply.isError ? <p className="text-sm text-muted">Supply unavailable: API offline.</p> : (
                <div className="space-y-3">
                  {supply.data.chains.map((c: any) => (
                    <div key={c.chain} className="flex items-start justify-between gap-4 text-sm">
                      <span className="capitalize text-muted">{c.chain}</span>
                      <span className="text-right">
                        <span className="mono">{c.available ? Number(c.total_minted).toLocaleString() : "—"}</span>
                        <span className="block text-[11px] text-dim">{c.available ? c.source : c.reason}</span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader><CardTitle>Your mint history</CardTitle></CardHeader>
            <CardBody>
              {!session ? <Empty title="Verify a wallet to see your history"><div className="mt-3 flex justify-center"><WalletBar /></div></Empty> : history.isLoading ? <SkeletonRows /> : !history.data?.items.length ? <Empty title="No indexed mints yet">Indexed from verified PassMinted events; new mints appear within a minute.</Empty> : (
                <ul className="divide-y divide-[var(--color-line)]">
                  {history.data.items.map((m) => (
                    <li key={m.tx_id + m.chain} className="flex items-center justify-between py-2 text-sm">
                      <span><Badge tone={m.chain === "base" ? "base" : "sol"}>{m.chain}</Badge> <span className="mono ml-2">{m.quantity} pass{m.quantity > 1 ? "es" : ""}</span></span>
                      <a href={m.explorer_url} target="_blank" rel="noreferrer" className="mono text-xs text-muted hover:text-fg">{m.paid_usdc} USDC ↗</a>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
          <p className="text-xs leading-relaxed text-dim">Passes grant platform access and a transparent monthly usage-credit allowance (500 credits per pass, up to 20 passes counted). They are utility access, not an investment, and confer no rights to revenue. See <a className="underline" href="/legal/risk">risk disclosures</a>.</p>
        </div>
      </div>
    </Page>
  );
}

function BaseMint({ qty, cost, phase, setPhase, onResult }: { qty: number; cost: bigint; phase: Phase; setPhase: (p: Phase) => void; onResult: (r: Result) => void }) {
  const { address, isConnected, chainId } = useAccount();
  const client = usePublicClient({ chainId: BASE_CHAIN.id });
  const { writeContractAsync } = useWriteContract();
  const { switchChainAsync } = useSwitchChain();
  const usdc = BASE.usdc as `0x${string}`;
  const pass = BASE_PASS_ADDRESS as `0x${string}`;
  const enabled = !!address && !!pass;
  const bal = useReadContract({ address: usdc, abi: ERC20_ABI, functionName: "balanceOf", args: address ? [address] : undefined, chainId: BASE_CHAIN.id, query: { enabled } });
  const allow = useReadContract({ address: usdc, abi: ERC20_ABI, functionName: "allowance", args: address && pass ? [address, pass] : undefined, chainId: BASE_CHAIN.id, query: { enabled } });

  if (!pass) return <Empty title="Base pass contract not configured">Set NEXT_PUBLIC_BASE_PASS_ADDRESS after deploying to {BASE.name}. See the testnet guide.</Empty>;
  if (!isConnected) return <div className="flex justify-center"><WalletBar /></div>;

  const balance = bal.data as bigint | undefined;
  const allowance = (allow.data as bigint | undefined) ?? 0n;
  const insufficient = balance !== undefined && balance < cost;
  const needsApproval = allowance < cost;

  async function ensureChain() {
    if (chainId !== BASE_CHAIN.id) await switchChainAsync({ chainId: BASE_CHAIN.id });
  }

  async function approve() {
    try {
      await ensureChain();
      setPhase("approving");
      // Exact-amount approval only — never unlimited.
      await client!.simulateContract({ address: usdc, abi: ERC20_ABI, functionName: "approve", args: [pass, cost], account: address });
      const h = await writeContractAsync({ address: usdc, abi: ERC20_ABI, functionName: "approve", args: [pass, cost], chainId: BASE_CHAIN.id });
      setPhase("confirming");
      const rc = await client!.waitForTransactionReceipt({ hash: h });
      if (rc.status !== "success") throw new Error("Approval reverted");
      await allow.refetch();
      toast.success(`Approved exactly ${formatUsdc(cost)} USDC`);
    } catch (e: any) {
      toast.error("Approval not completed", { description: e?.shortMessage ?? e?.message });
    } finally {
      setPhase("idle");
    }
  }

  async function mint() {
    let hash: `0x${string}` | undefined;
    try {
      await ensureChain();
      setPhase("minting");
      await client!.simulateContract({ address: pass, abi: PASS_ABI, functionName: "mint", args: [BigInt(qty)], account: address });
      hash = await writeContractAsync({ address: pass, abi: PASS_ABI, functionName: "mint", args: [BigInt(qty)], chainId: BASE_CHAIN.id });
      setPhase("confirming");
      const rc = await client!.waitForTransactionReceipt({ hash, confirmations: 2 });
      onResult({ chain: "base", tx: hash, ok: rc.status === "success" });
      await Promise.all([bal.refetch(), allow.refetch()]);
    } catch (e: any) {
      const msg = e?.shortMessage ?? e?.message ?? String(e);
      if (hash) onResult({ chain: "base", tx: hash, ok: false, reason: msg });
      else toast.error(/reject|denied/i.test(msg) ? "Signature rejected — nothing was sent" : "Mint not sent", { description: msg });
    } finally {
      setPhase("idle");
    }
  }

  return (
    <div className="space-y-3">
      <div className="mono flex justify-between text-xs text-muted">
        <span>USDC balance</span><span>{balance === undefined ? "…" : formatUsdc(balance)}</span>
      </div>
      <div className="mono flex justify-between text-xs text-muted">
        <span>Current allowance</span><span>{formatUsdc(allowance)}</span>
      </div>
      {insufficient && <p className="text-xs text-bad">Insufficient USDC on {BASE.name}.</p>}
      {needsApproval ? (
        <Button className="w-full" size="lg" variant="base" onClick={approve} loading={phase !== "idle"} disabled={insufficient} data-testid="approve-usdc">
          Approve exactly {formatUsdc(cost)} USDC
        </Button>
      ) : (
        <Button className="w-full" size="lg" variant="base" onClick={mint} loading={phase !== "idle"} disabled={insufficient} data-testid="mint-base">
          {phase === "confirming" ? "Waiting for confirmations…" : `Mint ${qty} pass${qty > 1 ? "es" : ""}`}
        </Button>
      )}
      <p className="text-[11px] text-dim">Two steps: an exact USDC approval, then the mint. Each is simulated before your wallet opens.</p>
    </div>
  );
}

function SolanaMint({ qty, phase, setPhase, onResult }: { qty: number; phase: Phase; setPhase: (p: Phase) => void; onResult: (r: Result) => void }) {
  const { connection } = useConnection();
  const wallet = useWallet();
  const programId = useMemo(() => new PublicKey(SOLANA_PASS_PROGRAM_ID), []);
  if (!wallet.connected || !wallet.publicKey) return <div className="flex justify-center"><WalletBar /></div>;

  async function mint() {
    let sig: string | undefined;
    try {
      setPhase("minting");
      const { tx, blockhash, lastValidBlockHeight } = await buildMintTx(connection, programId, wallet.publicKey!, qty);
      const sim = await connection.simulateTransaction(tx, { sigVerify: false, commitment: "confirmed" });
      if (sim.value.err) throw new Error(`Simulation failed: ${JSON.stringify(sim.value.err)} ${sim.value.logs?.slice(-2).join(" ") ?? ""}`);
      sig = await wallet.sendTransaction(tx, connection);
      setPhase("confirming");
      const conf = await connection.confirmTransaction({ signature: sig, blockhash, lastValidBlockHeight }, "confirmed");
      onResult({ chain: "solana", tx: sig, ok: conf.value.err == null, reason: conf.value.err ? JSON.stringify(conf.value.err) : undefined });
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      if (sig) onResult({ chain: "solana", tx: sig, ok: false, reason: msg });
      else toast.error(/reject/i.test(msg) ? "Signature rejected — nothing was sent" : "Mint not sent", { description: msg });
    } finally {
      setPhase("idle");
    }
  }

  return (
    <div className="space-y-3">
      <Button className="w-full" size="lg" variant="sol" onClick={mint} loading={phase !== "idle"} data-testid="mint-solana">
        {phase === "confirming" ? "Waiting for confirmation…" : `Mint ${qty} pass${qty > 1 ? "es" : ""}`}
      </Button>
      <p className="text-[11px] text-dim">One atomic instruction: USDC transfer to the treasury and pass issuance succeed together or not at all. Simulated before your wallet opens.</p>
    </div>
  );
}
