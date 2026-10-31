"use client";
import * as S from "@radix-ui/react-switch";
import { cn } from "@/lib/utils";

export function Switch({ className, ...p }: S.SwitchProps) {
  return (
    <S.Root className={cn("focus-ring relative h-5 w-9 rounded-full border border-line-strong bg-white/[0.06] transition data-[state=checked]:bg-base", className)} {...p}>
      <S.Thumb className="block size-4 translate-x-0.5 rounded-full bg-fg transition data-[state=checked]:translate-x-[18px]" />
    </S.Root>
  );
}
