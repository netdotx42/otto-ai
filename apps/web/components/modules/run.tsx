"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { post } from "@/lib/api";

export type ModuleOut = { module: string; summary: string; data: any; evidence: { source: string; method: string; ref?: string }[]; warnings: string[]; limitations: string[]; credits?: { charged: number; remaining: number } };

export function useModule(name: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => post<ModuleOut>(`/modules/${name}/run`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["entitlements"] }),
    onError: (e: any) => toast.error(`${name.toUpperCase()} failed`, { description: e.message }),
  });
}

export function Evidence({ out }: { out: ModuleOut }) {
  return (
    <div className="space-y-2 border-t hairline pt-4">
      {out.warnings?.map((w, i) => <p key={i} className="text-xs text-warn">{w}</p>)}
      <div className="label">Evidence</div>
      <ol className="space-y-1">
        {out.evidence.map((e, i) => <li key={i} className="mono text-[11px] text-dim">[{i + 1}] {e.source} · {e.method}{e.ref ? ` · ${e.ref}` : ""}</li>)}
      </ol>
      {out.limitations?.length > 0 && <p className="text-[11px] text-dim">Limitations: {out.limitations.join(" ")}</p>}
      {out.credits && <p className="mono text-[11px] text-dim">{out.credits.charged} credits · {out.credits.remaining} remaining</p>}
    </div>
  );
}
