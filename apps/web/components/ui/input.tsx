import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => (
  <input ref={ref} className={cn("focus-ring h-10 w-full rounded-lg border border-line-strong bg-white/[0.02] px-3 text-sm text-fg placeholder:text-dim", className)} {...p} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => (
  <textarea ref={ref} className={cn("focus-ring w-full rounded-lg border border-line-strong bg-white/[0.02] px-3 py-2.5 text-sm text-fg placeholder:text-dim", className)} {...p} />
));
Textarea.displayName = "Textarea";
