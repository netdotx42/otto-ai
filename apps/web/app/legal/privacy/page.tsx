import { Page, PageHeader } from "@/components/page";
import { Prose } from "@/components/prose";

export const metadata = { title: "Privacy" };

export default function Privacy() {
  return (
    <Page>
      <PageHeader eyebrow="Legal" title="Privacy" description="Template policy. Have counsel review before public launch." />
      <Prose>
        <p><strong>What we store:</strong> verified wallet addresses, session records (hashed tokens), usage-credit ledger, conversations you create in the terminal, workflow definitions, execution records and a tamper-evident audit log.</p>
        <p><strong>What we never collect:</strong> private keys, seed phrases, or wallet passwords. No page will ever ask for them.</p>
        <p><strong>Third parties:</strong> requests are sent to blockchain RPC providers, quote providers (Uniswap via RPC, Jupiter) and — only if enabled by the operator — an LLM provider for parsing and summarization. Error monitoring (Sentry) is configured without default PII.</p>
        <p><strong>Public data:</strong> blockchain transactions are public and permanent; we cannot delete on-chain records.</p>
        <p><strong>Your choices:</strong> you may revoke sessions, revoke workflows, and request deletion of off-chain account data.</p>
      </Prose>
    </Page>
  );
}
