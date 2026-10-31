"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";
import { Button } from "./ui/button";
import { SkeletonRows } from "./ui/skeleton";
import { WalletBar } from "./wallet-bar";

export type Entitlements = {
  passes: number; has_access: boolean; credits_granted: number; credits_used: number; credits_remaining: number;
  period: string; period_resets_at: string; errors: string[]; by_wallet: { chain: string; address: string; passes: number | null; source: string }[];
};

export function useEntitlements(enabled = true) {
  return useQuery({ queryKey: ["entitlements"], queryFn: () => api<Entitlements>("/me/entitlements"), enabled });
}

/** Renders children only for a verified session holding a pass; shows the honest reason otherwise. */
export function Gate({ children, needPass = true }: { children: ReactNode; needPass?: boolean }) {
  const session = useApp((s) => s.session);
  const ent = useEntitlements(!!session && needPass);
  if (!session) {
    return (
      <div className="glass rounded-xl p-8 text-center">
        <div className="text-sm font-medium">Connect and verify a wallet</div>
        <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">Sign-in uses a gasless signature that proves wallet ownership. It never authorizes a transaction.</p>
        <div className="mt-5 flex justify-center"><WalletBar /></div>
      </div>
    );
  }
  if (!needPass) return <>{children}</>;
  if (ent.isLoading) return <SkeletonRows rows={4} />;
  if (!ent.data?.has_access) {
    return (
      <div className="glass rounded-xl p-8 text-center">
        <div className="text-sm font-medium">A utility pass is required</div>
        <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">Passes are 1.98 USDC on Base or Solana and are verified from on-chain state.</p>
        {ent.data?.errors?.length ? <p className="mono mt-2 text-xs text-warn">{ent.data.errors.join(" · ")}</p> : null}
        <Button asChild className="mt-5"><Link href="/mint">Mint a pass</Link></Button>
      </div>
    );
  }
  return <>{children}</>;
}

export function CreditsMeter() {
  const session = useApp((s) => s.session);
  const { data } = useEntitlements(!!session);
  if (!data) return null;
  const pct = data.credits_granted ? Math.min(100, (data.credits_used / data.credits_granted) * 100) : 0;
  return (
    <div className="min-w-48" title={`Resets ${new Date(data.period_resets_at).toLocaleDateString()}`}>
      <div className="mono flex justify-between text-[11px] text-muted"><span>CREDITS</span><span>{data.credits_remaining}/{data.credits_granted}</span></div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full bg-fg/70" style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
