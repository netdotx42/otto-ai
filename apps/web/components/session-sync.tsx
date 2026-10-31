"use client";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/lib/store";

export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: () => api<{ user_id: string; wallets: { chain: "base" | "solana"; address: string }[] }>("/auth/session").catch(() => null),
    staleTime: 60_000,
  });
}

export function SessionSync() {
  const { data } = useSession();
  const setSession = useApp((s) => s.setSession);
  useEffect(() => setSession(data ?? null), [data, setSession]);
  return null;
}
