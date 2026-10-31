"use client";
import "@rainbow-me/rainbowkit/styles.css";
import "@solana/wallet-adapter-react-ui/styles.css";
import { RainbowKitProvider, darkTheme } from "@rainbow-me/rainbowkit";
import { ConnectionProvider, WalletProvider } from "@solana/wallet-adapter-react";
import { WalletModalProvider } from "@solana/wallet-adapter-react-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Toaster } from "sonner";
import { WagmiProvider } from "wagmi";
import { SOLANA_RPC } from "@/lib/chains";
import { wagmiConfig } from "@/lib/wagmi";
import { SessionSync } from "./session-sync";

export function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } } }));
  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={qc}>
        <RainbowKitProvider theme={darkTheme({ accentColor: "#2864F5", borderRadius: "medium", overlayBlur: "small" })} modalSize="compact">
          {/* Wallet Standard auto-detects Phantom, Solflare, Backpack, etc. No adapters bundled. */}
          <ConnectionProvider endpoint={SOLANA_RPC}>
            <WalletProvider wallets={[]} autoConnect>
              <WalletModalProvider>
                <SessionSync />
                {children}
                <Toaster theme="dark" position="bottom-right" toastOptions={{ style: { background: "#0c0f16", border: "1px solid rgba(255,255,255,0.08)", color: "#e8ebf2" } }} />
              </WalletModalProvider>
            </WalletProvider>
          </ConnectionProvider>
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}
