/**
 * Client-side recomputation of the immutable transaction digest the backend binds approvals to.
 * Must match apps/api/app/execution/digest.py byte-for-byte.
 * The UI refuses to open a wallet prompt if the digest of the transaction it is about to sign
 * differs from the digest that was previewed and approved.
 */
import { keccak256, toBytes } from "viem";

export type UnsignedEvmTx = { chainId: number; from: string; to: string; data: string; value: string };

export function evmCanonical(tx: UnsignedEvmTx): string {
  // Keys in sorted order to match Python json.dumps(sort_keys=True, separators=(",", ":")).
  return JSON.stringify({
    chainId: Number(tx.chainId),
    data: String(tx.data).toLowerCase(),
    from: String(tx.from).toLowerCase(),
    to: String(tx.to).toLowerCase(),
    value: BigInt(tx.value).toString(),
  });
}

export function evmDigest(tx: UnsignedEvmTx): `0x${string}` {
  return keccak256(toBytes(evmCanonical(tx)));
}

export function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToB64(bytes: Uint8Array): string {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s);
}

export async function solanaDigest(messageB64: string): Promise<`0x${string}`> {
  const buf = await crypto.subtle.digest("SHA-256", b64ToBytes(messageB64) as BufferSource);
  return ("0x" + Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("")) as `0x${string}`;
}
