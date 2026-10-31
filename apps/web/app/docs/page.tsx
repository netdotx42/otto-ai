import { Page, PageHeader } from "@/components/page";
import { Prose } from "@/components/prose";

export const metadata = { title: "Documentation" };

export default function DocsPage() {
  return (
    <Page>
      <PageHeader eyebrow="Docs" title="How AGENT//98 works" />
      <Prose>
        <h2>Access</h2>
        <p>Mint a utility pass for exactly <strong>1.98 USDC</strong> on Base (ERC-1155) or Solana (Anchor program). There is no aggregate supply cap; a single transaction mints at most 50 passes for safety. The backend verifies passes from on-chain state (<code>balanceOf</code> on Base, token-account balances on Solana) — never from the browser.</p>
        <p>Each pass grants <strong>500 usage credits per calendar month</strong>, counting up to 20 passes (10,000 credits) under the fair-use policy. Holding more passes is allowed but does not add credits. Module costs: FORENSIC 3, WHALE 4, ALPHA 4, SENTINEL 2, PORTFOLIO 2, SIMULATOR 5, INTEL 3, AUTOPILOT run 2, BUILDER and MARKET free.</p>

        <h2>Sign-in</h2>
        <p>Sign-In-With-Ethereum (EIP-4361) and Sign-In-With-Solana. The server writes the exact message, including a single-use nonce and expiry. The signature proves ownership and authorizes nothing. You can link several wallets across both chains to one account.</p>

        <h2>Level 3 assisted execution</h2>
        <ol className="space-y-1">
          <li><strong>Intent</strong> — your request is parsed into a strict typed intent by a deterministic grammar (with an optional LLM structured parser whose every value must appear literally in your text). Ambiguity produces questions, never guesses.</li>
          <li><strong>Research</strong> — balances, token metadata, allowances and quotes are fetched fresh (Uniswap V3 QuoterV2 on Base, Jupiter on Solana mainnet), with provider attribution and quote expiry.</li>
          <li><strong>Build</strong> — an unsigned transaction is built only through allowlisted adapters (SwapRouter02, ERC-20, System/SPL programs, Jupiter v6). Raw model output is never turned into calldata.</li>
          <li><strong>Simulate</strong> — the exact candidate is simulated (<code>eth_call</code> + <code>eth_estimateGas</code>; <code>simulateTransaction</code>) and balance changes are extracted.</li>
          <li><strong>Policy</strong> — a deterministic engine runs 20+ checks: destination/program allowlists, decoded-calldata-vs-intent, exact approvals, spend limits, quote/simulation freshness, digest equality, slippage, price impact, token risk, emergency switch.</li>
          <li><strong>Explain</strong> — you see assets in/out, minimum output, approvals, fees, simulation result and every check.</li>
          <li><strong>Sign</strong> — on confirmation your browser recomputes the transaction digest, the server records a single-use approval bound to it, and only then does your wallet open.</li>
          <li><strong>Verify</strong> — the backend checks that what was broadcast matches what you approved and reports success only after a confirmed receipt.</li>
        </ol>

        <h2>Automations</h2>
        <p>Workflows combine a trigger (large wallet transfer, balance threshold, contract/proxy change) with allowlisted actions (alert, forensic scan, prepare transaction). Every save creates a new version with a content hash. Prepared transactions always stop at <code>awaiting_approval</code>.</p>

        <h2>API</h2>
        <p>The REST + SSE API is documented with OpenAPI at <code>/docs</code> on the API host. Mutating endpoints accept an <code>Idempotency-Key</code> header.</p>

        <h2>Networks</h2>
        <p>Testnet mode uses Base Sepolia and Solana devnet. Solana swaps require Jupiter, which only routes on mainnet; on devnet only transfers are available. Mainnet execution is disabled unless the operator explicitly enables it after audits.</p>
      </Prose>
    </Page>
  );
}
