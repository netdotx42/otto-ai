import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { ChainKey } from "./chains";

type Session = { user_id: string; wallets: { chain: ChainKey; address: string }[] } | null;

type State = {
  chain: ChainKey;
  setChain: (c: ChainKey) => void;
  session: Session;
  setSession: (s: Session) => void;
};

export const useApp = create<State>()(
  persist(
    (set) => ({
      chain: "base",
      setChain: (chain) => set({ chain }),
      session: null,
      setSession: (session) => set({ session }),
    }),
    { name: "agent98", partialize: (s) => ({ chain: s.chain }) },
  ),
);
