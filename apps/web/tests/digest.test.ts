import { describe, expect, it } from "vitest";
import { bytesToB64, evmCanonical, evmDigest, solanaDigest } from "@/lib/digest";

// Vectors produced by apps/api/app/execution/digest.py — the two implementations must agree.
describe("transaction digests match the backend byte-for-byte", () => {
  const tx = { chainId: 84532, from: "0xAbCd000000000000000000000000000000000001", to: "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4", data: "0x5AE401DC00ff", value: "1000" };

  it("evm canonical form is sorted, lowercased, decimal value", () => {
    expect(evmCanonical(tx)).toBe('{"chainId":84532,"data":"0x5ae401dc00ff","from":"0xabcd000000000000000000000000000000000001","to":"0x94cc0aac535ccdb3c01d6787d6413c739ae12bc4","value":"1000"}');
  });
  it("evm digest", () => {
    expect(evmDigest(tx)).toBe("0xca9f3f657b30dab01b5379024cca4ab7f2c281e237d7568db82a5555660af40d");
  });
  it("any byte change changes the digest", () => {
    expect(evmDigest({ ...tx, value: "1001" })).not.toBe(evmDigest(tx));
    expect(evmDigest({ ...tx, data: "0x5ae401dc00fe" })).not.toBe(evmDigest(tx));
  });
  it("solana digest = sha256(message bytes)", async () => {
    const msg = bytesToB64(Uint8Array.from({ length: 40 }, (_, i) => i));
    expect(await solanaDigest(msg)).toBe("0x5faa4eec3611556812c2d74b437c8c49add3f910f10063d801441f7d75cd5e3b");
  });
});
