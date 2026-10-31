# AGENT//98

**THE CHAIN NEVER SLEEPS. NEITHER DO YOUR AGENTS.**

AGENT//98 is a dual-chain (Base × Solana) AI intelligence and **Level 3 assisted-execution** platform:

> The AI researches. The system validates. The user signs. The chain executes.

Access is a **1.98 USDC utility pass** with **no aggregate supply cap**, minted on Base (ERC-1155) or Solana (Anchor program). Passes on either chain unlock the same platform access through a unified, server-verified entitlement system with transparent, fair-use-capped usage credits.

---

## Monorepo

```
apps/
  web/                Next.js 15 (App Router) · TypeScript · Tailwind v4 · shadcn-style UI · Motion
                      TanStack Query · Zustand · wagmi/viem/RainbowKit · Solana Wallet Adapter
  api/                FastAPI · SQLAlchemy 2 + Alembic (PostgreSQL) · Redis · arq workers
                      LangGraph execution graph · OpenAI (optional) · Sentry · OpenTelemetry
contracts/
  base/               Foundry · Solidity 0.8.28 · OpenZeppelin v5 — Agent98Pass (ERC-1155)
  solana/             Anchor 0.31 · Rust — agent98_pass program (SPL / Token-2022 interface)
packages/shared/      chains.json (single source of truth: chain ids, USDC, routers, program allowlist),
                      Base ABI, Solana IDL
scripts/              deploy-base.sh · deploy-solana.sh · integration-local.sh
docs/                 ARCHITECTURE · SECURITY · TESTNET · DEPLOYMENT · MAINNET_CHECKLIST · STATUS
```

## Quick start (local)

Prerequisites: Node 22 + pnpm 10, Python 3.12+ with [uv](https://docs.astral.sh/uv/), PostgreSQL 16, Redis 7.
Contracts additionally need Foundry, Rust, Agave (Solana CLI) 2.1.x and Anchor CLI 0.31.1.

```bash
# 1. infrastructure (or: docker compose up postgres redis)
createdb agent98 && createdb agent98_test

# 2. API
cd apps/api
cp .env.example .env                 # fill RPC URLs; OPENAI_API_KEY optional
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload # http://localhost:8000/docs (OpenAPI)
uv run arq app.workers.tasks.WorkerSettings   # background workers (separate shell)

# 3. Web
cd ../.. && pnpm install
cp apps/web/.env.example apps/web/.env.local
pnpm dev:web                         # http://localhost:3000
```

Full stack in containers: `docker compose up --build` (requires `apps/api/.env`).

## Tests

| Suite | Command | Count (last run) |
|---|---|---|
| Base contract (Foundry, incl. fuzz) | `cd contracts/base && ./install-deps.sh && forge test` | 22 |
| Solana program (Anchor, local validator) | `cd contracts/solana && pnpm install --ignore-workspace && pnpm test` | 13 |
| API unit/E2E (deterministic fake chains) | `cd apps/api && uv run pytest` | 101 |
| API real-chain integration (Anvil + validator) | `./scripts/integration-local.sh` | 3 (+13 Anchor) |
| Web unit/component (Vitest) | `pnpm --filter @agent98/web test` | 29 |
| Web E2E (Playwright, desktop + mobile) | `pnpm --filter @agent98/web e2e` | 36 |

CI (`.github/workflows/ci.yml`) runs all of the above plus lint, typecheck, production build, migration up/down and Docker builds.

## What is real, what needs credentials

See **[docs/STATUS.md](docs/STATUS.md)** for an explicit feature-by-feature matrix. In short:

- **Working end-to-end locally and verified by tests:** both pass contracts; SIWE/SIWS auth; on-chain entitlement verification; usage credits; mint UI; mint-event indexing; the full Level 3 lifecycle (intent → research → build → simulate → policy → preview → digest-bound approval → wallet signature → verify → receipt); the policy engine; all ten modules; workflows/monitoring; marketplace; terminal streaming.
- **Needs your credentials / deployments:** RPC endpoints, deployed pass contracts (testnet scripts provided), WalletConnect project id (optional), OpenAI key (optional — deterministic fallbacks are complete), Sentry/OTel (optional), hosting accounts.
- **Needs external work before mainnet:** independent audits of both programs, legal review of the template Terms/Privacy, verification of every allowlisted address in `packages/shared/chains.json`. See **[docs/MAINNET_CHECKLIST.md](docs/MAINNET_CHECKLIST.md)**.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — services, data model, execution graph
- [Security model](docs/SECURITY.md) — threat model and the policy engine
- [Testnet deployment](docs/TESTNET.md) — Base Sepolia + Solana devnet, end to end
- [Deployment](docs/DEPLOYMENT.md) — Vercel, containers, managed Postgres/Redis, observability
- [Mainnet launch checklist](docs/MAINNET_CHECKLIST.md)
- [Status & blockers](docs/STATUS.md)

## Non-negotiables

- No private keys or seed phrases are ever requested, stored or transmitted. The backend never signs.
- LLM output is never turned into calldata and can never bypass the policy engine.
- Approvals are single-use and bound to an immutable transaction digest; any change voids them.
- A transaction is never shown as successful because it was submitted — only after a verified receipt.
- No fabricated on-chain data, prices, outcomes or risk scores. Missing data is shown as missing.
- Passes are utility access, not an investment, and do not provide unlimited inference.
