"use client";
import * as T from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

export const Tabs = T.Root;
export function TabsList({ className, ...p }: T.TabsListProps) {
  return <T.List className={cn("inline-flex rounded-lg border border-line bg-white/[0.02] p-1", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: T.TabsTriggerProps) {
  return <T.Trigger className={cn("focus-ring rounded-md px-3 py-1.5 text-xs text-muted transition data-[state=active]:bg-white/[0.08] data-[state=active]:text-fg", className)} {...p} />;
}
export const TabsContent = T.Content;
