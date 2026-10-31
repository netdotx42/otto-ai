"use client";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CornerDownLeft, Plus, Square } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useAccount } from "wagmi";
import { useWallet } from "@solana/wallet-adapter-react";
import { ChainToggle } from "@/components/chain-toggle";
import { ExecutionPanel } from "@/components/execution-panel";
import { CreditsMeter, Gate } from "@/components/gate";
import { Page } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { detectAddresses } from "@/lib/addresses";
import { api, post, streamSSE } from "@/lib/api";
import type { Execution } from "@/lib/execution";
import { useApp } from "@/lib/store";
import { cn, shortAddr } from "@/lib/utils";

type Tool = { module: string; node?: string; detail?: string };
type Msg = { id: string; role: "user" | "assistant"; content: string; tools?: Tool[]; evidence?: any[]; execution?: Execution; error?: string; streaming?: boolean };

const EXAMPLES = [
  "Prepare a 5 USDC swap into WETH",
  "Analyze this contract 0x4200000000000000000000000000000000000006 before I approve anything",
  "Show my portfolio balances",
  "send 0.01 SOL to <address>",
];

export default function TerminalPage() {
  return (
    <Page className="max-w-6xl">
      <Gate>
        <Terminal />
      </Gate>
    </Page>
  );
}

function Terminal() {
  const chain = useApp((s) => s.chain);
  const session = useApp((s) => s.session);
  const qc = useQueryClient();
  const { address } = useAccount();
  const sol = useWallet();
  const [cid, setCid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const convs = useQuery({ queryKey: ["conversations"], queryFn: () => api<{ items: { id: string; title: string; chain: string }[] }>("/terminal/conversations") });

  const wallet = chain === "base" ? address?.toLowerCase() : sol.publicKey?.toBase58();
  const walletVerified = !!wallet && !!session?.wallets.some((w) => w.chain === chain && w.address.toLowerCase() === wallet.toLowerCase());

  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [msgs]);

  async function open(id: string) {
    setCid(id);
    const r = await api<{ items: any[] }>(`/terminal/conversations/${id}/messages`);
    setMsgs(r.items.map((m) => ({ id: m.id, role: m.role, content: m.content, execution: m.payload?.execution, evidence: m.payload?.evidence })));
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    let id = cid;
    if (!id) {
      const c = await post<{ id: string }>("/terminal/conversations", { title: text.slice(0, 60), chain });
      id = c.id;
      setCid(id);
      qc.invalidateQueries({ queryKey: ["conversations"] });
    }
    const aid = crypto.randomUUID();
    setMsgs((m) => [...m, { id: crypto.randomUUID(), role: "user", content: text }, { id: aid, role: "assistant", content: "", tools: [], streaming: true }]);
    setInput("");
    setBusy(true);
    abort.current = new AbortController();
    const patch = (f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.id === aid ? f(m) : m)));
    try {
      for await (const ev of streamSSE(`/terminal/conversations/${id}/stream`, { prompt: text, chain, wallet: walletVerified ? wallet : undefined }, abort.current.signal)) {
        if (ev.event === "tool" || ev.event === "tool_start") patch((m) => ({ ...m, tools: [...(m.tools ?? []), ev.data] }));
        else if (ev.event === "execution") patch((m) => ({ ...m, execution: ev.data }));
        else if (ev.event === "evidence") patch((m) => ({ ...m, evidence: ev.data.evidence }));
        else if (ev.event === "token") patch((m) => ({ ...m, content: m.content + ev.data.text }));
        else if (ev.event === "error") patch((m) => ({ ...m, error: ev.data.message }));
      }
    } catch (e: any) {
      if (e?.name !== "AbortError") {
        patch((m) => ({ ...m, error: e?.message ?? "Stream failed" }));
        toast.error("Terminal error", { description: e?.message });
      }
    } finally {
      patch((m) => ({ ...m, streaming: false }));
      setBusy(false);
      qc.invalidateQueries({ queryKey: ["entitlements"] });
    }
  }

  const detected = detectAddresses(input);
  return (
    <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
      <aside className="hidden space-y-2 lg:block" aria-label="Saved conversations">
        <Button variant="outline" size="sm" className="w-full" onClick={() => { setCid(null); setMsgs([]); }}><Plus /> New session</Button>
        <div className="label px-1 pt-3">Sessions</div>
        <ul className="space-y-0.5">
          {(convs.data?.items ?? []).map((c) => (
            <li key={c.id}>
              <button onClick={() => open(c.id)} className={cn("focus-ring w-full truncate rounded-md px-2 py-1.5 text-left text-[13px]", c.id === cid ? "bg-white/[0.06] text-fg" : "text-muted hover:text-fg")}>{c.title}</button>
            </li>
          ))}
        </ul>
      </aside>
      <section className="glass flex min-h-[70vh] flex-col rounded-xl" aria-label="AI terminal">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b hairline px-4 py-3">
          <div className="mono flex items-center gap-2 text-xs text-muted">
            <span className={cn("size-2 rounded-full", busy ? "animate-pulse bg-warn" : "bg-ok")} /> agent98 · {chain}
            {wallet && <span className="text-dim">· {shortAddr(wallet)} {walletVerified ? "" : "(unverified)"}</span>}
          </div>
          <div className="flex items-center gap-4"><CreditsMeter /><ChainToggle /></div>
        </div>
        <div className="flex-1 space-y-6 overflow-y-auto p-4" aria-live="polite">
          {msgs.length === 0 && (
            <div className="mx-auto max-w-lg py-10 text-center">
              <div className="mono text-sm text-muted">Ask for research or prepare a transaction. Nothing is signed without your explicit confirmation.</div>
              <div className="mt-6 grid gap-2">
                {EXAMPLES.map((e) => (
                  <button key={e} onClick={() => setInput(e)} className="focus-ring mono rounded-lg border border-line px-3 py-2 text-left text-xs text-muted hover:border-line-strong hover:text-fg">{e}</button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m) => (
            <motion.div key={m.id} className={m.role === "user" ? "flex justify-end" : ""} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}>
              {m.role === "user" ? (
                <div className="mono max-w-[85%] rounded-lg bg-white/[0.05] px-3 py-2 text-[13px]">{m.content}</div>
              ) : (
                <div className="space-y-3">
                  {!!m.tools?.length && (
                    <div className="flex flex-wrap gap-1.5" aria-label="Tool activity">
                      {m.tools.map((t, i) => (
                        <span key={i} className="mono rounded border border-line px-1.5 py-0.5 text-[10px] uppercase text-dim" title={t.detail}>{t.module}{t.node ? ` · ${t.node}` : ""}</span>
                      ))}
                    </div>
                  )}
                  {m.content && <pre className="mono whitespace-pre-wrap text-[13px] leading-relaxed text-fg">{m.content}{m.streaming && <span className="animate-pulse">▍</span>}</pre>}
                  {!m.content && m.streaming && <div className="mono text-xs text-muted"><span className="animate-pulse">▍</span> working…</div>}
                  {m.error && <div className="text-xs text-bad">{m.error}</div>}
                  {m.execution && <ExecutionPanel initial={m.execution} />}
                  {!!m.evidence?.length && (
                    <details className="rounded-lg border border-line">
                      <summary className="focus-ring cursor-pointer px-3 py-2 text-xs text-muted">Evidence · {m.evidence.length} source{m.evidence.length > 1 ? "s" : ""}</summary>
                      <ol className="space-y-1 border-t hairline p-3">
                        {m.evidence.map((e, i) => (
                          <li key={i} className="mono text-[11px] text-dim">[{i + 1}] {e.source} · {e.method}{e.ref ? ` · ${e.ref}` : ""}</li>
                        ))}
                      </ol>
                    </details>
                  )}
                </div>
              )}
            </motion.div>
          ))}
          <div ref={endRef} />
        </div>
        <form className="border-t hairline p-3" onSubmit={(e) => { e.preventDefault(); send(input); }}>
          {(detected.evm.length > 0 || detected.sol.length > 0) && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {detected.evm.map((a) => <Badge key={a} tone="base">evm {shortAddr(a)}</Badge>)}
              {detected.sol.map((a) => <Badge key={a} tone="sol">sol {shortAddr(a)}</Badge>)}
            </div>
          )}
          <div className="flex items-end gap-2">
            <label htmlFor="prompt" className="sr-only">Prompt</label>
            <textarea
              id="prompt"
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder={`> ${chain === "base" ? "swap 5 USDC into 0x…" : "send 0.1 SOL to …"}`}
              className="focus-ring mono max-h-40 min-h-10 flex-1 resize-none rounded-lg border border-line-strong bg-white/[0.02] px-3 py-2.5 text-[13px] placeholder:text-dim"
            />
            {busy ? (
              <Button type="button" variant="outline" size="icon" onClick={() => abort.current?.abort()} aria-label="Stop"><Square /></Button>
            ) : (
              <Button type="submit" size="icon" aria-label="Send" disabled={!input.trim()}><CornerDownLeft /></Button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
