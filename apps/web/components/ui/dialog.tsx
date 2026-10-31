"use client";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({ className, children, title, description, ...p }: D.DialogContentProps & { title: string; description?: string }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
      <D.Content className={cn("fixed left-1/2 top-1/2 z-50 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-line-strong bg-ink-2 p-6 shadow-2xl", className)} {...p}>
        <D.Title className="text-base font-semibold">{title}</D.Title>
        {description && <D.Description className="mt-1 text-sm text-muted">{description}</D.Description>}
        <div className="mt-4">{children}</div>
        <D.Close className="focus-ring absolute right-4 top-4 rounded-md p-1 text-muted hover:text-fg" aria-label="Close">
          <X className="size-4" />
        </D.Close>
      </D.Content>
    </D.Portal>
  );
}
