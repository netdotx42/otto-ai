import type { ReactNode } from "react";

export function Prose({ children }: { children: ReactNode }) {
  return (
    <div className="max-w-3xl space-y-4 text-[14px] leading-relaxed text-muted [&_code]:mono [&_code]:rounded [&_code]:bg-white/[0.06] [&_code]:px-1 [&_code]:text-[12px] [&_code]:text-fg [&_h2]:mt-10 [&_h2]:text-lg [&_h2]:font-semibold [&_h2]:text-fg [&_h3]:mt-6 [&_h3]:font-medium [&_h3]:text-fg [&_li]:ml-5 [&_li]:list-disc [&_strong]:text-fg">
      {children}
    </div>
  );
}
