import registry from "@agent98/shared/chains.json";
import { base, baseSepolia } from "wagmi/chains";

export const NETWORK_MODE = (process.env.NEXT_PUBLIC_NETWORK_MODE ?? "testnet") as "testnet" | "mainnet";
export const BASE = registry.base[NETWORK_MODE];
export const SOLANA = registry.solana[NETWORK_MODE] as (typeof registry.solana)["testnet"];
export const PASS = registry.pass;
export const BASE_CHAIN = NETWORK_MODE === "mainnet" ? base : baseSepolia;
export const SOLANA_RPC = process.env.NEXT_PUBLIC_SOLANA_RPC_URL ?? (NETWORK_MODE === "mainnet" ? "https://api.mainnet-beta.solana.com" : "https://api.devnet.solana.com");
export const BASE_PASS_ADDRESS = (process.env.NEXT_PUBLIC_BASE_PASS_ADDRESS ?? "") as `0x${string}` | "";
export const SOLANA_PASS_PROGRAM_ID = process.env.NEXT_PUBLIC_SOLANA_PASS_PROGRAM_ID ?? registry.solana.passProgramId;

export const PRICE_BASE_UNITS = BigInt(PASS.priceUsdcBaseUnits);
export const MAX_BATCH = PASS.maxBatch;

export type ChainKey = "base" | "solana";

export function explorerTx(chain: ChainKey, tx: string) {
  if (chain === "base") return `${BASE.explorer}/tx/${tx}`;
  return `${SOLANA.explorer}/tx/${tx}${"explorerSuffix" in SOLANA ? (SOLANA as { explorerSuffix?: string }).explorerSuffix ?? "" : ""}`;
}

/** Exact total in USDC base units. Rejects quantities outside the per-transaction batch bound. */
export function mintCost(quantity: number): bigint {
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_BATCH) throw new Error(`Quantity must be 1–${MAX_BATCH}`);
  return PRICE_BASE_UNITS * BigInt(quantity);
}

export function formatUsdc(baseUnits: bigint): string {
  const whole = baseUnits / 1_000_000n;
  const frac = (baseUnits % 1_000_000n).toString().padStart(6, "0").slice(0, 2);
  return `${whole.toLocaleString("en-US")}.${frac}`;
}

export function clampQty(n: number): number {
  if (!Number.isFinite(n)) return 1;
  return Math.max(1, Math.min(MAX_BATCH, Math.floor(n)));
}
