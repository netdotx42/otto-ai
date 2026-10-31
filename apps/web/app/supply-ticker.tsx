"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

type Supply = { price_usdc: string; total_verified: number; chains: { chain: string; available: boolean; total_minted?: number; source?: string; reason?: string }[] };

export function LiveSupply() {
  const q = useQuery({ queryKey: ["supply"], queryFn: () => api<Supply>("/mint/supply"), refetchInterval: 30_000 });
  return (
    <div className="mt-14 grid max-w-2xl grid-cols-3 gap-px overflow-hidden rounded-xl border hairline bg-[var(--color-line)]">
      {[
        ["Verified passes", q.data ? q.data.total_verified.toLocaleString() : null, "sum of on-chain state"],
        ["Base", q.data ? fmt(q.data.chains[0]) : null, q.data?.chains[0]?.available ? "totalMinted()" : q.data?.chains[0]?.reason],
        ["Solana", q.data ? fmt(q.data.chains[1]) : null, q.data?.chains[1]?.available ? "config PDA" : q.data?.chains[1]?.reason],
      ].map(([k, v, s]) => (
        <div key={k as string} className="bg-ink p-4">
          <div className="label">{k}</div>
          <div className="mono mt-1.5 text-lg">{q.isLoading ? <Skeleton className="h-6 w-16" /> : q.isError ? <span className="text-sm text-dim">API offline</span> : v}</div>
          <div className="mt-1 truncate text-[11px] text-dim" title={s as string}>{q.isError ? "" : s}</div>
        </div>
      ))}
    </div>
  );
}

function fmt(c?: Supply["chains"][number]) {
  if (!c) return "—";
  return c.available ? (c.total_minted ?? 0).toLocaleString() : "not deployed";
}
