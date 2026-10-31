import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badge = cva("mono inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] uppercase tracking-wider", {
  variants: {
    tone: {
      neutral: "border-line-strong text-muted",
      base: "border-base/40 bg-base/10 text-[#8fb0ff]",
      sol: "border-sol/40 bg-sol/10 text-[#c9a3ff]",
      ok: "border-ok/30 bg-ok/10 text-ok",
      warn: "border-warn/30 bg-warn/10 text-warn",
      bad: "border-bad/35 bg-bad/10 text-bad",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, ...p }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) {
  return <span className={cn(badge({ tone }), className)} {...p} />;
}
