"use client";
import { ConnectButton } from "@rainbow-me/rainbowkit";
import { useWallet } from "@solana/wallet-adapter-react";
import { useWalletModal } from "@solana/wallet-adapter-react-ui";
import { ShieldCheck } from "lucide-react";
import { useAccount } from "wagmi";
import { useSignIn } from "@/lib/auth";
import { useApp } from "@/lib/store";
import { shortAddr } from "@/lib/utils";
import { Button } from "./ui/button";

export function WalletBar({ compact = false }: { compact?: boolean }) {
  const chain = useApp((s) => s.chain);
  const session = useApp((s) => s.session);
  const { address, isConnected } = useAccount();
  const sol = useWallet();
  const { setVisible } = useWalletModal();
  const { signIn, pending } = useSignIn();

  const connectedAddr = chain === "base" ? (isConnected ? address?.toLowerCase() : undefined) : sol.publicKey?.toBase58();
  const verified = !!connectedAddr && !!session?.wallets.some((w) => w.chain === chain && w.address.toLowerCase() === connectedAddr.toLowerCase());

  return (
    <div className="flex items-center gap-2">
      {chain === "base" ? (
        <ConnectButton.Custom>
          {({ account, openConnectModal, openAccountModal, mounted }) => (
            <Button variant="outline" size="sm" onClick={account ? openAccountModal : openConnectModal} disabled={!mounted} aria-label={account ? "Base wallet" : "Connect Base wallet"}>
              <span className="size-1.5 rounded-full bg-base" />
              {account ? <span className="mono">{shortAddr(account.address)}</span> : compact ? "Connect" : "Connect Base"}
            </Button>
          )}
        </ConnectButton.Custom>
      ) : (
        <Button variant="outline" size="sm" onClick={() => (sol.connected ? sol.disconnect() : setVisible(true))} aria-label={sol.connected ? "Disconnect Solana wallet" : "Connect Solana wallet"}>
          <span className="size-1.5 rounded-full bg-sol-green" />
          {sol.connected ? <span className="mono">{shortAddr(sol.publicKey?.toBase58())}</span> : compact ? "Connect" : "Connect Solana"}
        </Button>
      )}
      {connectedAddr && !verified && (
        <Button size="sm" variant={chain === "base" ? "base" : "sol"} loading={pending === chain} onClick={() => signIn(chain)}>
          Verify
        </Button>
      )}
      {verified && (
        <span className="mono hidden items-center gap-1 text-[11px] uppercase tracking-wider text-ok sm:inline-flex" title="Wallet ownership verified by signature">
          <ShieldCheck className="size-3.5" /> verified
        </span>
      )}
    </div>
  );
}
