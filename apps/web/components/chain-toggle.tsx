"use client";
import { cn } from "@/lib/utils";
import { useApp } from "@/lib/store";

export function ChainToggle({ className }: { className?: string }) {
  const { chain, setChain } = useApp();
  return (
    <div role="radiogroup" aria-label="Chain" className={cn("inline-flex rounded-lg border border-line bg-white/[0.02] p-0.5", className)}>
      {(["base", "solana"] as const).map((c) => (
        <button
          key={c}
          role="radio"
          aria-checked={chain === c}
          onClick={() => setChain(c)}
          className={cn(
            "focus-ring mono flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] uppercase tracking-wider transition",
            chain === c ? "bg-white/[0.08] text-fg" : "text-muted hover:text-fg",
          )}
        >
          <span className={cn("size-1.5 rounded-full", c === "base" ? "bg-base" : "bg-linear-to-r from-sol to-sol-green")} />
          {c}
        </button>
      ))}
    </div>
  );
}
