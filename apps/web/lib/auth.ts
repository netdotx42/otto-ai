"use client";
import { useWallet } from "@solana/wallet-adapter-react";
import { useQueryClient } from "@tanstack/react-query";
import bs58 from "bs58";
import { useState } from "react";
import { toast } from "sonner";
import { useAccount, useSignMessage } from "wagmi";
import { api, post } from "./api";

/** SIWE / SIWS sign-in. The server constructs the message; the wallet signs it verbatim. */
export function useSignIn() {
  const qc = useQueryClient();
  const { address } = useAccount();
  const { signMessageAsync } = useSignMessage();
  const sol = useWallet();
  const [pending, setPending] = useState<"base" | "solana" | null>(null);

  async function signIn(chain: "base" | "solana") {
    setPending(chain);
    try {
      const addr = chain === "base" ? address : sol.publicKey?.toBase58();
      if (!addr) throw new Error(`Connect a ${chain === "base" ? "Base" : "Solana"} wallet first.`);
      const n = await post<{ nonce: string; message: string }>("/auth/nonce", { chain, address: addr });
      let signature: string;
      if (chain === "base") {
        signature = await signMessageAsync({ message: n.message });
      } else {
        if (!sol.signMessage) throw new Error("This Solana wallet does not support message signing.");
        signature = bs58.encode(await sol.signMessage(new TextEncoder().encode(n.message)));
      }
      await post("/auth/verify", { chain, address: addr, nonce: n.nonce, signature });
      await qc.invalidateQueries();
      toast.success("Wallet verified", { description: "Signature proves ownership only. No transaction was authorized." });
    } catch (e: any) {
      const msg = String(e?.shortMessage ?? e?.message ?? e);
      toast.error("Sign-in failed", { description: /reject|denied/i.test(msg) ? "Signature request was rejected." : msg });
    } finally {
      setPending(null);
    }
  }

  async function signOut() {
    await api("/auth/logout", { method: "POST" }).catch(() => null);
    await qc.invalidateQueries();
  }
  return { signIn, signOut, pending };
}
