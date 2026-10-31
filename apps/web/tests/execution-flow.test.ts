import { describe, expect, it, vi } from "vitest";
import { evmDigest, type UnsignedEvmTx } from "@/lib/digest";
import { confirmAndSign, FlowError, type Execution, type FlowDeps, type Step } from "@/lib/execution";

const W = "0xabababababababababababababababababababab";
const tx: UnsignedEvmTx = { chainId: 84532, from: W, to: "0x94cc0aac535ccdb3c01d6787d6413c739ae12bc4", data: "0x5ae401dc", value: "0" };
const digest = evmDigest(tx);

function mk(over: Partial<Step> = {}): { ex: Execution; step: Step } {
  const step: Step = { id: "s1", index: 0, kind: "swap", status: "awaiting_approval", digest, unsigned_tx: tx, submissions: [], preview: { digest, policy: { allowed: true } }, ...over };
  return { ex: { id: "e1", status: "awaiting_approval", status_reason: null, chain: "base", wallet: W, steps: [step], questions: [], error: null, current_step: 0 }, step };
}

function deps(over: Partial<FlowDeps> = {}): FlowDeps & { calls: string[] } {
  const calls: string[] = [];
  const confirmed = { ...mk().ex, status: "confirmed" };
  return {
    calls,
    approve: vi.fn(async () => { calls.push("approve"); return { approval_id: "a1", digest, expires_at: "", chain: "base" as const, unsigned_tx: tx }; }),
    submit: vi.fn(async () => { calls.push("submit"); return { ...mk().ex, status: "submitted" }; }),
    poll: vi.fn(async () => { calls.push("poll"); return confirmed; }),
    account: () => ({ connected: true, address: W, chainId: 84532 }),
    sendEvm: vi.fn(async () => { calls.push("sign"); return ("0x" + "11".repeat(32)) as `0x${string}`; }),
    sleep: async () => {},
    ...over,
  } as any;
}

describe("confirmAndSign — the client-side approval boundary", () => {
  it("happy path: verify → approve → sign → submit → poll until receipt-confirmed", async () => {
    const d = deps();
    const { ex, step } = mk();
    const out = await confirmAndSign(ex, step, d);
    expect(d.calls).toEqual(["approve", "sign", "submit", "poll"]);
    expect(out.status).toBe("confirmed");
    expect(d.approve).toHaveBeenCalledWith("s1", digest);
  });

  it("never opens the wallet if the previewed tx was modified (transaction substitution)", async () => {
    const d = deps();
    const { ex, step } = mk({ unsigned_tx: { ...tx, value: "1" } });
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "digest_mismatch" });
    expect(d.calls).toEqual([]);
  });

  it("aborts before signing if the server returns a different tx than approved", async () => {
    const d = deps({ approve: vi.fn(async () => ({ approval_id: "a1", digest, expires_at: "", chain: "base" as const, unsigned_tx: { ...tx, to: "0x" + "66".repeat(20) } })) });
    const { ex, step } = mk();
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "digest_mismatch" });
    expect(d.sendEvm).not.toHaveBeenCalled();
  });

  it("refuses policy-blocked steps", async () => {
    const d = deps();
    const { ex, step } = mk({ preview: { digest, policy: { allowed: false } } });
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "policy_blocked" });
    expect(d.calls).toEqual([]);
  });

  it("requires the connected wallet to be the tx sender", async () => {
    const d = deps({ account: () => ({ connected: true, address: "0x" + "99".repeat(20), chainId: 84532 }) });
    const { ex, step } = mk();
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "wrong_account" });
  });

  it("reports user rejection without submitting", async () => {
    const err = Object.assign(new Error("User rejected the request."), { name: "UserRejectedRequestError" });
    const d = deps({ sendEvm: vi.fn(async () => { throw err; }) });
    const { ex, step } = mk();
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "user_rejected" });
    expect(d.submit).not.toHaveBeenCalled();
  });

  it("handles wallet disconnect during signing", async () => {
    let connected = true;
    const d = deps({
      account: () => ({ connected, address: W, chainId: 84532 }),
      sendEvm: vi.fn(async () => { connected = false; throw new Error("Connector not connected."); }),
    });
    const { ex, step } = mk();
    await expect(confirmAndSign(ex, step, d)).rejects.toMatchObject({ code: "wallet_disconnected" });
    expect(d.submit).not.toHaveBeenCalled();
  });

  it("switches chain before signing when the wallet is on the wrong network", async () => {
    let chainId = 8453;
    const ensureChain = vi.fn(async (id: number) => { chainId = id; });
    const d = deps({ account: () => ({ connected: true, address: W, chainId }), ensureChain });
    const { ex, step } = mk();
    await confirmAndSign(ex, step, d);
    expect(ensureChain).toHaveBeenCalledWith(84532);
  });

  it("does not report success while only submitted", async () => {
    const d = deps({ poll: vi.fn(async () => ({ ...mk().ex, status: "submitted" })), maxPolls: 3 });
    const { ex, step } = mk();
    const out = await confirmAndSign(ex, step, d);
    expect(out.status).toBe("submitted");
  });

  it("surfaces on-chain failure accurately", async () => {
    const d = deps({ poll: vi.fn(async () => ({ ...mk().ex, status: "failed", status_reason: "Transaction reverted on-chain." })) });
    const { ex, step } = mk();
    const out = await confirmAndSign(ex, step, d);
    expect(out.status).toBe("failed");
  });

  it("solana: signs the exact message and submits signed bytes", async () => {
    const msgB64 = btoa("hello-message");
    const { solanaDigest } = await import("@/lib/digest");
    const sd = await solanaDigest(msgB64);
    const stx = { transaction: "AAAA", message_b64: msgB64, fee_payer: "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", recent_blockhash: "x", last_valid_block_height: 1 };
    const step: Step = { id: "s1", index: 0, kind: "transfer", status: "awaiting_approval", digest: sd, unsigned_tx: stx, submissions: [], preview: { digest: sd, policy: { allowed: true } } };
    const ex: Execution = { ...mk().ex, chain: "solana", steps: [step] };
    const signSolana = vi.fn(async () => "SIGNED");
    const submit = vi.fn(async () => ({ ...ex, status: "confirmed" }));
    await confirmAndSign(ex, step, {
      approve: async () => ({ approval_id: "a1", digest: sd, expires_at: "", chain: "solana", unsigned_tx: stx }),
      submit, poll: async () => ({ ...ex, status: "confirmed" }),
      account: () => ({ connected: true, address: stx.fee_payer }), signSolana, sleep: async () => {},
    });
    expect(signSolana).toHaveBeenCalledWith("AAAA");
    expect(submit).toHaveBeenCalledWith({ approval_id: "a1", signed_tx: "SIGNED" });
  });

  it("FlowError is typed", () => {
    expect(new FlowError("timeout", "x").code).toBe("timeout");
  });
});
