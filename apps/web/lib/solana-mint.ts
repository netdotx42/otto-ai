/** Builds the Solana `mint_pass` instruction from the committed Anchor IDL (no hand-typed discriminators). */
import idl from "@agent98/shared/idl/agent98_pass.json";
import { Connection, PublicKey, SystemProgram, TransactionInstruction, TransactionMessage, VersionedTransaction } from "@solana/web3.js";
import { ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, getAssociatedTokenAddressSync } from "@solana/spl-token";
import { Buffer } from "buffer";
import { MAX_BATCH } from "./chains";

const ix = idl.instructions.find((i) => i.name === "mint_pass")!;
export const MINT_PASS_DISCRIMINATOR = Uint8Array.from(ix.discriminator);
export const MINT_PASS_ACCOUNTS = ix.accounts.map((a) => a.name);

export type PassConfig = { usdcMint: PublicKey; treasuryUsdc: PublicKey; passMint: PublicKey; totalMinted: bigint; paused: boolean };

export function pdas(programId: PublicKey) {
  const [config] = PublicKey.findProgramAddressSync([Buffer.from("config")], programId);
  const [passMint] = PublicKey.findProgramAddressSync([Buffer.from("pass_mint")], programId);
  return { config, passMint };
}

export function decodeConfig(data: Uint8Array): PassConfig {
  const at = (i: number) => new PublicKey(data.slice(8 + 32 * i, 8 + 32 * (i + 1)));
  const off = 8 + 32 * 5;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  return { usdcMint: at(2), treasuryUsdc: at(3), passMint: at(4), totalMinted: view.getBigUint64(off, true), paused: data[off + 8] === 1 };
}

export function encodeMintPass(quantity: number): Uint8Array {
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_BATCH) throw new Error(`Quantity must be 1–${MAX_BATCH}`);
  const out = new Uint8Array(16);
  out.set(MINT_PASS_DISCRIMINATOR, 0);
  new DataView(out.buffer).setBigUint64(8, BigInt(quantity), true);
  return out;
}

export function mintPassInstruction(programId: PublicKey, buyer: PublicKey, cfg: PassConfig, quantity: number): TransactionInstruction {
  const { config, passMint } = pdas(programId);
  if (!passMint.equals(cfg.passMint)) throw new Error("Pass mint mismatch: refusing to build");
  const keys: Record<string, { pubkey: PublicKey; isSigner: boolean; isWritable: boolean }> = {
    buyer: { pubkey: buyer, isSigner: true, isWritable: true },
    config: { pubkey: config, isSigner: false, isWritable: true },
    usdc_mint: { pubkey: cfg.usdcMint, isSigner: false, isWritable: false },
    buyer_usdc: { pubkey: getAssociatedTokenAddressSync(cfg.usdcMint, buyer), isSigner: false, isWritable: true },
    treasury_usdc: { pubkey: cfg.treasuryUsdc, isSigner: false, isWritable: true },
    pass_mint: { pubkey: passMint, isSigner: false, isWritable: true },
    buyer_pass: { pubkey: getAssociatedTokenAddressSync(passMint, buyer), isSigner: false, isWritable: true },
    usdc_token_program: { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    pass_token_program: { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    associated_token_program: { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    system_program: { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
  };
  return new TransactionInstruction({ programId, keys: MINT_PASS_ACCOUNTS.map((n) => keys[n]), data: Buffer.from(encodeMintPass(quantity)) });
}

export async function fetchPassConfig(conn: Connection, programId: PublicKey): Promise<PassConfig | null> {
  const { config } = pdas(programId);
  const acc = await conn.getAccountInfo(config, "confirmed");
  if (!acc || !acc.owner.equals(programId)) return null;
  return decodeConfig(acc.data);
}

export async function buildMintTx(conn: Connection, programId: PublicKey, buyer: PublicKey, quantity: number) {
  const cfg = await fetchPassConfig(conn, programId);
  if (!cfg) throw new Error("Solana pass program is not initialized on this cluster.");
  if (cfg.paused) throw new Error("Minting is paused by the operators (emergency switch).");
  const { blockhash, lastValidBlockHeight } = await conn.getLatestBlockhash("confirmed");
  const msg = new TransactionMessage({ payerKey: buyer, recentBlockhash: blockhash, instructions: [mintPassInstruction(programId, buyer, cfg, quantity)] }).compileToV0Message();
  return { tx: new VersionedTransaction(msg), blockhash, lastValidBlockHeight, cfg };
}
