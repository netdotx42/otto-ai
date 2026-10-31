"use client";
import { useWallet } from "@solana/wallet-adapter-react";
import { VersionedTransaction } from "@solana/web3.js";
import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import { toast } from "sonner";
import { useAccount, useSendTransaction, useSwitchChain } from "wagmi";
import { api, newIdempotencyKey, post } from "@/lib/api";
import { b64ToBytes, bytesToB64 } from "@/lib/digest";
import { confirmAndSign, FlowError, type Execution, stepOutcome } from "@/lib/execution";
import { PreviewCard } from "./preview-card";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

const STAGE_LABEL: Record<string, string> = {
  verifying: "Verifying digest…",
  approving: "Recording approval…",
  signing: "Waiting for wallet signature…",
  submitting: "Submitting…",
  confirming: "Awaiting on-chain confirmation…",
};

export function statusTone(s: string) {
  if (s === "confirmed") return "ok" as const;
  if (["failed", "blocked", "rejected", "expired"].includes(s)) return "bad" as const;
  if (["awaiting_approval", "submitted", "approved"].includes(s)) return "warn" as const;
  return "neutral" as const;
}

export function ExecutionPanel({ initial, onChange }: { initial: Execution; onChange?: (e: Execution) => void }) {
  const [ex, setEx] = useState<Execution>(initial);
  const [stage, setStage] = useState<string | null>(null);
  const qc = useQueryClient();
  const acct = useAccount();
  const { switchChainAsync } = useSwitchChain();
  const { sendTransactionAsync } = useSendTransaction();
  const sol = useWallet();
  const reduce = useReducedMotion();

  const update = (e: Execution) => {
    setEx(e);
    onChange?.(e);
  };
  const step = ex.steps.find((s) => s.index === ex.current_step) ?? ex.steps[ex.steps.length - 1];

  async function confirm() {
    if (!step) return;
    try {
      const final = await confirmAndSign(ex, step, {
        approve: (id, digest) => post(`/executions/steps/${id}/approve`, { digest, confirm: true }, newIdempotencyKey()),
        submit: (b) => post("/executions/submit", b, newIdempotencyKey()),
        poll: async (id) => {
          const e = await api<Execution>(`/executions/${id}`);
          setEx(e);
          return e;
        },
        account: () =>
          ex.chain === "base"
            ? { connected: acct.isConnected, address: acct.address, chainId: acct.chainId }
            : { connected: sol.connected, address: sol.publicKey?.toBase58() },
        ensureChain: async (id) => {
          await switchChainAsync({ chainId: id });
        },
        sendEvm: (tx) => sendTransactionAsync({ to: tx.to as `0x${string}`, data: tx.data as `0x${string}`, value: BigInt(tx.value), chainId: tx.chainId }),
        signSolana: async (b64) => {
          if (!sol.signTransaction) throw new Error("Wallet does not support signTransaction");
          const signed = await sol.signTransaction(VersionedTransaction.deserialize(b64ToBytes(b64)));
          return bytesToB64(signed.serialize());
        },
        onStage: (s) => setStage(s === "done" ? null : s),
      });
      update(final);
      qc.invalidateQueries({ queryKey: ["activity"] });
      if (final.status === "confirmed") toast.success("Confirmed on-chain", { description: "Receipt verified by the backend." });
      else if (final.status === "awaiting_approval") toast.message("Step confirmed. Next step is ready for review.");
      else if (final.status === "submitted") toast.message("Submitted. Still waiting for confirmation — not yet final.");
      else toast.error(`Execution ${final.status}`, { description: final.status_reason ?? final.error ?? undefined });
    } catch (e) {
      setStage(null);
      const msg = e instanceof FlowError ? e.message : String((e as Error)?.message ?? e);
      toast.error(e instanceof FlowError && e.code === "user_rejected" ? "Signature rejected" : "Not sent", { description: msg });
      api<Execution>(`/executions/${ex.id}`).then(update).catch(() => null);
    }
  }

  async function refresh() {
    try {
      update(await post<Execution>(`/executions/${ex.id}/refresh`, {}));
    } catch (e: any) {
      toast.error("Refresh failed", { description: e.message });
    }
  }

  if (ex.status === "needs_clarification" || (!step && ex.status !== "preparing")) {
    return (
      <div className="rounded-xl border border-warn/30 bg-warn/5 p-4 text-sm" data-testid="clarification">
        <div className="label mb-1 !text-warn">{ex.status.replace(/_/g, " ")}</div>
        {(ex.questions.length ? ex.questions : [ex.status_reason ?? ex.error ?? ""]).map((q, i) => (
          <p key={i} className="text-muted">{q}</p>
        ))}
      </div>
    );
  }
  if (!step) return null;
  const outcome = stepOutcome(step);
  const canConfirm = ex.status === "awaiting_approval" && step.status === "awaiting_approval" && step.preview?.policy?.allowed;
  return (
    <motion.div className="space-y-3" key={step.id} initial={reduce ? false : { opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.22, ease: "easeOut" }}>
      <PreviewCard step={step} chain={ex.chain} />
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(ex.status)} data-testid="exec-status">{ex.status.replace(/_/g, " ")}</Badge>
        {outcome === "pending" && <span className="text-xs text-muted">Submitted ≠ successful. Waiting for receipt.</span>}
        <div className="ml-auto flex gap-2">
          {["awaiting_approval", "blocked", "expired"].includes(ex.status) && (
            <Button variant="ghost" size="sm" onClick={refresh} aria-label="Re-quote and re-simulate"><RefreshCw /> Re-simulate</Button>
          )}
          {canConfirm && (
            <Button variant={ex.chain === "base" ? "base" : "sol"} size="sm" onClick={confirm} loading={!!stage} data-testid="confirm-sign">
              {stage ? STAGE_LABEL[stage] : "Confirm & open wallet"}
            </Button>
          )}
        </div>
      </div>
      {ex.status === "blocked" && <p className="text-xs text-bad">Blocked by the security policy: {ex.status_reason}</p>}
    </motion.div>
  );
}
