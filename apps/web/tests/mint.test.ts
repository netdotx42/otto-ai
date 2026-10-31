// @vitest-environment node
import { PublicKey, SystemProgram } from "@solana/web3.js";
import { TOKEN_PROGRAM_ID, getAssociatedTokenAddressSync } from "@solana/spl-token";
import { describe, expect, it } from "vitest";
import { clampQty, formatUsdc, mintCost } from "@/lib/chains";
import { MINT_PASS_ACCOUNTS, decodeConfig, encodeMintPass, mintPassInstruction, pdas } from "@/lib/solana-mint";

describe("mint checkout math", () => {
  it("is exactly 1.98 USDC per pass", () => {
    expect(mintCost(1)).toBe(1_980_000n);
    expect(mintCost(7)).toBe(13_860_000n);
    expect(formatUsdc(mintCost(50))).toBe("99.00");
  });
  it("rejects invalid quantities", () => {
    expect(() => mintCost(0)).toThrow();
    expect(() => mintCost(51)).toThrow();
    expect(() => mintCost(1.5)).toThrow();
  });
  it("clamps the quantity selector", () => {
    expect(clampQty(-3)).toBe(1);
    expect(clampQty(999)).toBe(50);
    expect(clampQty(NaN)).toBe(1);
    expect(clampQty(12.9)).toBe(12);
  });
});

describe("solana mint_pass instruction", () => {
  const programId = new PublicKey("ARd3pfR6GHqpLYr7sacHgjNwwagkzN15qmoq2GnHyw2Z");
  const { config, passMint } = pdas(programId);
  const usdc = new PublicKey("4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU");
  const treasury = PublicKey.unique();
  const buyer = PublicKey.unique();

  it("encodes the IDL discriminator + u64 LE quantity", () => {
    const d = encodeMintPass(3);
    expect(Array.from(d.slice(0, 8))).toEqual([142, 56, 26, 197, 227, 241, 84, 174]);
    expect(new DataView(d.buffer).getBigUint64(8, true)).toBe(3n);
    expect(() => encodeMintPass(51)).toThrow();
  });

  it("uses the IDL account order with PDAs and ATAs", () => {
    const ix = mintPassInstruction(programId, buyer, { usdcMint: usdc, treasuryUsdc: treasury, passMint, totalMinted: 0n, paused: false }, 2);
    expect(MINT_PASS_ACCOUNTS[0]).toBe("buyer");
    const keys = Object.fromEntries(MINT_PASS_ACCOUNTS.map((n, i) => [n, ix.keys[i]]));
    expect(keys.buyer.isSigner).toBe(true);
    expect(keys.config.pubkey.equals(config)).toBe(true);
    expect(keys.buyer_usdc.pubkey.equals(getAssociatedTokenAddressSync(usdc, buyer))).toBe(true);
    expect(keys.treasury_usdc.pubkey.equals(treasury)).toBe(true);
    expect(keys.usdc_token_program.pubkey.equals(TOKEN_PROGRAM_ID)).toBe(true);
    expect(keys.system_program.pubkey.equals(SystemProgram.programId)).toBe(true);
  });

  it("refuses to build against a substituted pass mint", () => {
    expect(() => mintPassInstruction(programId, buyer, { usdcMint: usdc, treasuryUsdc: treasury, passMint: PublicKey.unique(), totalMinted: 0n, paused: false }, 1)).toThrow(/mismatch/);
  });

  it("decodes the on-chain Config account", () => {
    const raw = new Uint8Array(8 + 32 * 5 + 11);
    raw.set(usdc.toBytes(), 8 + 64);
    raw.set(treasury.toBytes(), 8 + 96);
    raw.set(passMint.toBytes(), 8 + 128);
    new DataView(raw.buffer).setBigUint64(8 + 160, 303n, true);
    raw[8 + 168] = 1;
    const cfg = decodeConfig(raw);
    expect(cfg.usdcMint.equals(usdc)).toBe(true);
    expect(cfg.treasuryUsdc.equals(treasury)).toBe(true);
    expect(cfg.totalMinted).toBe(303n);
    expect(cfg.paused).toBe(true);
  });
});
