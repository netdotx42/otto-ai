# Status & blockers

Legend: **Done** = implemented and verified by automated tests in this repo · **Needs credentials** = code complete,
requires your keys/accounts to run against real networks · **External** = requires work outside the codebase.

## Milestones

| Milestone | State | Evidence |
|---|---|---|
| A — monorepo, DB, wallet auth, design system | Done | 26-table schema + migrations (up/down verified); SIWE/SIWS tests; 13 routes build |
| B — pass contracts, mint UI, entitlements | Done (local/testnet-ready) | 22 Foundry + 13 Anchor tests; real Anvil/validator integration tests; mint UI e2e |
| C — terminal, chain adapters, portfolio, scanning | Done | module tests; SSE terminal tests; portfolio pricing tests |
| D — intent, construction, simulation, policy | Done | 34 parser/policy/adversarial tests; Base + Solana lifecycle tests |
| E — approval, broadcast, confirmation, history | Done | approval replay, substitution, stale quote, revert, dropped-tx tests; client flow tests |
| F — monitoring, builder, marketplace | Done | workflow versioning/revocation, rule firing, hash-pinned installs |
| G — tests, hardening, deployment, docs | Done in repo; deployment needs credentials | CI workflow, Dockerfiles, deploy scripts, docs |

## Feature matrix

| Feature | State | Notes |
|---|---|---|
| Base ERC-1155 pass (1.98 USDC, no cap, permit, pause) | Done | Not yet deployed to Base Sepolia — run `scripts/deploy-base.sh` (needs RPC, Basescan key, funded deployer). |
| Solana Anchor pass (atomic pay+mint, PDA authority, spoof-resistant) | Done | Not yet deployed to devnet — run `scripts/deploy-solana.sh` (needs funded upgrade authority). |
| Mint UI (quantity, exact approval, simulation, confirmations, explorer, history) | Done | Shows "not configured" until contract env vars are set. |
| Live supply from verified state | Done | `totalMinted()` / config PDA; never estimated. |
| Unified entitlements + usage credits (fair-use cap) | Done | |
| SIWE / SIWS sessions, wallet linking | Done | EIP-1271 supported; ERC-6492 (undeployed smart wallets) not supported. |
| Level 3 lifecycle on Base (Uniswap V3 SwapRouter02, ERC-20/native transfers) | Done | Base Sepolia Uniswap liquidity is thin; expect honest `no_route`/blocked outcomes there. |
| Level 3 lifecycle on Solana (SOL/SPL transfers) | Done | |
| Solana swaps via Jupiter | Done in code; Needs mainnet | Jupiter only routes on mainnet; blocked on devnet with an explanation. Not exercised against live Jupiter in this sandbox. |
| Policy engine (20+ checks), digest-bound single-use approvals | Done | |
| LLM intent parsing / INTEL narrative | Needs credentials (optional) | `OPENAI_API_KEY`. Deterministic parser & summaries work without it. The LLM path is unit-tested on validated outputs; live OpenAI calls were not exercised here. |
| Ten modules | Done | ALPHA runs on Base logs; Solana anomaly windows not implemented. WHALE/ALPHA log spans capped at 2,000 blocks per call (RPC limits). |
| Background workers (indexing, rules, confirmations) | Done | arq; Base native-ETH large-transfer triggers not supported (no logs) — ERC-20 only. |
| Automations, marketplace | Done | |
| Activity, audit log (hash-chained) | Done | |
| Rate limits, idempotency, structured errors, OpenAPI, health | Done | |
| Sentry / OpenTelemetry | Needs credentials (optional) | Wired; activates when DSN / OTLP endpoint set. |
| Docker / Compose | Written; not executed here | No Docker daemon in the build sandbox. CI `docker` job builds both images. |
| CI (GitHub Actions) | Written | Has not run yet on GitHub; first push will execute it. Solana job installs Agave + Anchor (slow on cold cache). |
| Vercel / container deploy | Needs credentials | `VERCEL_*` secrets, container platform credentials. |
| Pass token metadata (Base URI host, Solana Metaplex/Token-2022 metadata) | External | Host JSON at `PASS_METADATA_URI`; add Solana metadata before mainnet. |
| Audits, legal review, pen test | External | See MAINNET_CHECKLIST.md. |

## Toolchain notes

- Solana platform-tools in Agave 2.1 ship Cargo 1.79; `Cargo.lock` was resolved MSRV-aware
  (`CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=fallback`, `blake3` pinned to 1.5.5). Keep the lockfile when upgrading.
- `anchor test` misdetected the RPC port as busy in the sandbox; `contracts/solana/scripts/test-local.sh` is the
  equivalent runner used by CI.
- The Solana program keypair (`target/deploy/agent98_pass-keypair.json`) is intentionally not committed. CI generates
  one and runs `anchor keys sync`. For shared devnet deployments, generate your own and update `chains.json`.
