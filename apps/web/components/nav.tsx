"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { ChainToggle } from "./chain-toggle";
import { WalletBar } from "./wallet-bar";

export const NAV = [
  { href: "/terminal", label: "Terminal" },
  { href: "/mint", label: "Mint" },
  { href: "/scanner", label: "Scanner" },
  { href: "/wallet", label: "Wallet Intel" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/simulator", label: "Simulator" },
  { href: "/automations", label: "Automations" },
  { href: "/marketplace", label: "Marketplace" },
  { href: "/activity", label: "Activity" },
];

export function Logo() {
  return (
    <Link href="/" className="focus-ring mono flex items-center gap-2 rounded text-[13px] font-semibold tracking-tight text-fg" aria-label="AGENT//98 home">
      <span className="grid size-6 place-items-center rounded-md border border-line-strong bg-white/[0.03] text-[10px]">98</span>
      AGENT<span className="text-dim">//</span>98
    </Link>
  );
}

export function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-40 border-b hairline bg-ink/80 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Logo />
        <nav className="hidden flex-1 items-center gap-1 lg:flex" aria-label="Primary">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={cn("focus-ring rounded-md px-2.5 py-1.5 text-[13px] transition", path?.startsWith(n.href) ? "text-fg" : "text-muted hover:text-fg")} aria-current={path?.startsWith(n.href) ? "page" : undefined}>
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <ChainToggle className="hidden sm:inline-flex" />
          <WalletBar compact />
          <button className="focus-ring rounded-md p-2 text-muted lg:hidden" onClick={() => setOpen(!open)} aria-label="Menu" aria-expanded={open}>
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      {open && (
        <nav className="border-t hairline px-4 pb-4 lg:hidden" aria-label="Mobile">
          <ChainToggle className="my-3 sm:hidden" />
          <div className="grid grid-cols-2 gap-1">
            {[...NAV, { href: "/settings", label: "Settings" }, { href: "/docs", label: "Docs" }].map((n) => (
              <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className="focus-ring rounded-md px-3 py-2 text-sm text-muted hover:bg-white/[0.04] hover:text-fg">
                {n.label}
              </Link>
            ))}
          </div>
        </nav>
      )}
    </header>
  );
}

export function Footer() {
  return (
    <footer className="mt-24 border-t hairline">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-10 text-sm text-muted sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="space-y-2">
          <Logo />
          <p className="max-w-sm text-[13px] text-dim">The AI researches. The system validates. The user signs. The chain executes.</p>
        </div>
        <div className="grid grid-cols-2 gap-x-10 gap-y-2 text-[13px]">
          <Link href="/docs" className="hover:text-fg">Documentation</Link>
          <Link href="/settings" className="hover:text-fg">Settings</Link>
          <Link href="/legal/terms" className="hover:text-fg">Terms</Link>
          <Link href="/legal/privacy" className="hover:text-fg">Privacy</Link>
          <Link href="/legal/risk" className="hover:text-fg">Risk disclosures</Link>
        </div>
      </div>
    </footer>
  );
}
