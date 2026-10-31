import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function shortAddr(a?: string | null, n = 4) {
  if (!a) return "";
  return a.length <= n * 2 + 3 ? a : `${a.slice(0, n + (a.startsWith("0x") ? 2 : 0))}…${a.slice(-n)}`;
}
