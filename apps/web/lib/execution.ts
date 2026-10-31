/**
 * Client half of the Level 3 approval boundary.
 *
 *   preview → (user clicks Confirm) → verify digest locally → POST approve(digest)
 *   → verify returned tx digest again → wallet signs (user) → POST submit → poll receipt
 *
 * The wallet prompt is never opened if any digest check fails. Success is only reported when
 * the backend reports a confirmed receipt — never on submission.
 */
import { evmDigest, solanaDigest, type UnsignedEvmTx } from "./digest";

export type Preview = Record<string, any> & { digest: string; action?: string; policy?: { allowed: boolean; checks?: { id: string; status: string; message: string }[] } };
export type Step = {
  id: string;
  index: number;
  kind: "approve" | "swap" | "transfer";
  status: string;
  digest: string | null;
  preview: Preview;
  unsigned_tx: any;
  submissions: { tx_id: string; status: string; explorer_url: string; receipt: null | { success: boolean } }[];
};
export type Execution = {
  id: string;
  status: string;
  status_reason: string | null;
  chain: "base" | "solana";
  wallet: string;
  steps: Step[];
  questions: string[];
  error: string | null;
  current_step: number;
};
export type Approval = { approval_id: string; digest: string; expires_at: string; chain: "base" | "solana"; unsigned_tx: any };

export class FlowError extends Error {
  constructor(public code: "digest_mismatch" | "policy_blocked" | "wallet_disconnected" | "wrong_account" | "wrong_chain" | "user_rejected" | "approval_failed" | "submit_failed" | "timeout", message: string) {
    super(message);
  }
}

export type FlowDeps = {
  approve: (stepId: string, digest: string) => Promise<Approval>;
  submit: (body: { approval_id: string; tx_hash?: string; signed_tx?: string }) => Promise<Execution>;
  poll: (executionId: string) => Promise<Execution>;
  account: () => { connected: boolean; address?: string; chainId?: number };
  ensureChain?: (chainId: number) => Promise<void>;
  sendEvm?: (tx: UnsignedEvmTx) => Promise<`0x${string}`>;
  signSolana?: (unsignedTxB64: string) => Promise<string>;
  onStage?: (stage: "verifying" | "approving" | "signing" | "submitting" | "confirming" | "done", info?: string) => void;
  sleep?: (ms: number) => Promise<void>;
  maxPolls?: number;
};

export const TERMINAL = new Set(["confirmed", "failed", "rejected", "cancelled", "expired", "blocked"]);

export async function stepDigest(chain: "base" | "solana", tx: any): Promise<string> {
  return chain === "base" ? evmDigest(tx as UnsignedEvmTx) : solanaDigest(tx.message_b64);
}

function isUserRejection(e: unknown): boolean {
  const msg = String((e as any)?.shortMessage ?? (e as any)?.message ?? e).toLowerCase();
  const name = String((e as any)?.name ?? "");
  return name === "UserRejectedRequestError" || name === "WalletSignTransactionError" && msg.includes("reject") || msg.includes("user rejected") || msg.includes("user denied") || (e as any)?.code === 4001;
}

function isDisconnect(e: unknown): boolean {
  const msg = String((e as any)?.message ?? e).toLowerCase();
  const name = String((e as any)?.name ?? "");
  return name === "WalletNotConnectedError" || name === "ConnectorNotConnectedError" || msg.includes("not connected") || msg.includes("disconnected");
}

export async function confirmAndSign(execution: Execution, step: Step, deps: FlowDeps): Promise<Execution> {
  const chain = execution.chain;
  const sleep = deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  deps.onStage?.("verifying");
  if (!step.preview?.policy?.allowed) throw new FlowError("policy_blocked", "The security policy blocked this transaction.");
  const local = await stepDigest(chain, step.unsigned_tx);
  if (local !== step.digest || local !== step.preview.digest) {
    throw new FlowError("digest_mismatch", "The transaction differs from what was previewed. Nothing was signed. Refresh the preview.");
  }
  const acct = deps.account();
  if (!acct.connected || !acct.address) throw new FlowError("wallet_disconnected", "Wallet disconnected. Reconnect the wallet that requested this transaction.");
  const expectedFrom = chain === "base" ? step.unsigned_tx.from : step.unsigned_tx.fee_payer;
  if (acct.address.toLowerCase() !== String(expectedFrom).toLowerCase()) {
    throw new FlowError("wrong_account", `Connected wallet ${acct.address} is not the wallet this transaction was built for.`);
  }

  deps.onStage?.("approving");
  let approval: Approval;
  try {
    approval = await deps.approve(step.id, local);
  } catch (e: any) {
    throw new FlowError("approval_failed", e?.message ?? "Approval was refused by the server.");
  }
  const again = await stepDigest(chain, approval.unsigned_tx);
  if (again !== local || approval.digest !== local) {
    throw new FlowError("digest_mismatch", "Server returned a different transaction than the one you approved. Aborted before signing.");
  }

  deps.onStage?.("signing");
  let submitBody: { approval_id: string; tx_hash?: string; signed_tx?: string };
  try {
    if (chain === "base") {
      const tx = approval.unsigned_tx as UnsignedEvmTx;
      if (deps.ensureChain && acct.chainId !== tx.chainId) await deps.ensureChain(tx.chainId);
      if (deps.account().chainId !== undefined && deps.account().chainId !== tx.chainId) {
        throw new FlowError("wrong_chain", `Switch your wallet to chain ${tx.chainId}.`);
      }
      const hash = await deps.sendEvm!(tx);
      submitBody = { approval_id: approval.approval_id, tx_hash: hash };
    } else {
      const signed = await deps.signSolana!(approval.unsigned_tx.transaction);
      submitBody = { approval_id: approval.approval_id, signed_tx: signed };
    }
  } catch (e) {
    if (e instanceof FlowError) throw e;
    if (isUserRejection(e)) throw new FlowError("user_rejected", "Signature request rejected in wallet. Nothing was sent.");
    if (isDisconnect(e) || !deps.account().connected) throw new FlowError("wallet_disconnected", "Wallet disconnected during signing. Nothing was sent; the approval will expire.");
    throw new FlowError("submit_failed", String((e as any)?.shortMessage ?? (e as any)?.message ?? e));
  }

  deps.onStage?.("submitting");
  let ex: Execution;
  try {
    ex = await deps.submit(submitBody);
  } catch (e: any) {
    throw new FlowError("submit_failed", e?.message ?? "Submission failed.");
  }
  deps.onStage?.("confirming", submitBody.tx_hash);
  const max = deps.maxPolls ?? 90;
  for (let i = 0; i < max && !TERMINAL.has(ex.status) && !(ex.status === "awaiting_approval" && ex.current_step > step.index); i++) {
    await sleep(2000);
    ex = await deps.poll(ex.id);
  }
  deps.onStage?.("done", ex.status);
  return ex;
}

export function stepOutcome(step: Step): "confirmed" | "failed" | "pending" | "none" {
  const sub = step.submissions?.[0];
  if (!sub) return "none";
  if (sub.status === "confirmed" && sub.receipt?.success) return "confirmed";
  if (["failed", "mismatch", "dropped"].includes(sub.status)) return "failed";
  return "pending";
}
