# Architecture

```
 Browser (Next.js)                         API (FastAPI)                               Chains
 ─────────────────                         ─────────────                               ──────
 wagmi/RainbowKit ─ SIWE ─┐                /v1/auth  ── nonce/verify (EIP-4361, SIWS)
 Wallet Adapter ── SIWS ──┤                /v1/me/entitlements ── eth_call / getTokenAccounts ──► Base / Solana
                          │                /v1/mint/supply|history ◄── arq: index_mints ◄──── logs / program events
 Mint UI ─ approve+mint ──┼──── wallet ───────────────────────────────────────────────────────► Agent98Pass / agent98_pass
 Terminal (SSE) ──────────┼──► /v1/terminal/.../stream ─┐
 Simulator / Activity ────┼──► /v1/executions ──────────┼─► LangGraph execution graph
                          │                             │     ├─ intent parser (grammar | LLM structured output)
 confirmAndSign():        │                             │     ├─ research (RPC, Uniswap QuoterV2, Jupiter)
   recompute digest ──────┼──► /steps/{id}/approve ─────┤     ├─ builders (SwapRouter02 / ERC-20 / System / SPL / Jupiter)
   wallet signs (user) ───┼──► /executions/submit ──────┤     ├─ simulators (eth_call+estimateGas / simulateTransaction)
   poll receipt ──────────┼──► /executions/{id} ────────┘     ├─ policy engine (pure, deterministic)
                          │                                   └─ verify (tx-hash content / signed-message check, receipts)
 Automations ─────────────┴──► /v1/workflows ──► monitoring_rules ◄── arq: evaluate_rules ──► alerts / prepared executions
```

## Services

| Component | Path | Notes |
|---|---|---|
| Web | `apps/web` | 13 user-facing routes + 404/error. All chain constants come from `packages/shared/chains.json`. |
| API | `apps/api/app/main.py` | `/v1` REST + SSE, OpenAPI at `/docs`. Structured errors `{error:{code,message,details}}`. |
| Workers | `apps/api/app/workers/tasks.py` | arq (Redis): `index_mints` (60s), `evaluate_rules` (60s), `track_confirmations` (15s). |
| DB | `apps/api/app/db/models.py`, `alembic/` | 26 tables. Migrations `0001` (schema) and `0002` (seed templates + flags). |

## Data model (PostgreSQL)

Identity: `users`, `wallets` (verified by signature), `auth_nonces` (single-use, server-built message), `sessions` (sha256 of token only).
Mint & access: `mint_events` (unique per chain/tx/log), `indexer_cursors`, `pass_entitlements` (cached on-chain reads, 5 min TTL), `usage_ledger` (append-only credits).
Agents: `conversations`, `messages`, `tool_calls` (input, output summary, evidence, credits, latency), `token_scans`.
Automation: `workflows`, `workflow_versions` (content hash), `monitoring_rules`, `alerts`, `marketplace_templates`.
Execution: `transaction_intents` (root + status machine), `execution_steps` (approve/swap/transfer; unsigned tx, decoded, digest, quote, policy, preview), `simulations`, `approvals` (digest-bound, single-use, expiring), `transaction_submissions`, `confirmed_receipts`.
Platform: `audit_events` (hash-chained), `idempotency_keys`, `system_flags` (kill switches).

## Execution graph (LangGraph)

`app/execution/graph.py` wires exactly:

```
USER_REQUEST → PARSE_INTENT → VALIDATE_INTENT → FETCH_CHAIN_DATA → BUILD_UNSIGNED_TRANSACTION
→ SIMULATE_TRANSACTION → RUN_POLICY_CHECKS → GENERATE_EXECUTION_PREVIEW → WAIT_FOR_USER_APPROVAL
→ WALLET_SIGN → BROADCAST → VERIFY_CONFIRMATION → RECORD_ACTIVITY
```

Two invocation phases share one graph:

- **prepare** enters at `PARSE_INTENT` and always ends at `WAIT_FOR_USER_APPROVAL`.
- **settle** enters at `WAIT_FOR_USER_APPROVAL`, which atomically consumes an `ApprovalRecord` (created only by the
  explicit `POST /executions/steps/{id}/approve {digest, confirm: true}`), then verifies the wallet's artifact.

Multi-step executions (exact ERC-20 approval, then swap) persist a fixed step plan. When step *n* is confirmed,
`service.advance` re-quotes, rebuilds and re-simulates step *n+1* and returns to `awaiting_approval`.

## Modules

All modules implement `app/modules/base.py:Module` — a pydantic `Input`, a `ModuleOutput` with `evidence[]`,
`warnings[]`, `limitations[]`, a credit cost and a tool allowlist enforced by `ToolGate`.

| Module | Core | Data sources |
|---|---|---|
| SENTINEL | snapshots + arms monitoring rules (wallet, contract code/proxy, liquidity probe) | eth_getBalance/getCode, Uniswap quoter, getBalance/getAccountInfo |
| FORENSIC | bytecode PUSH4 selector scan, EIP-1967 proxy slots, owner, ERC-20 metadata (sanitized, injection-checked); SPL mint/freeze authorities, Token-2022 extensions | eth_getCode/getStorageAt/eth_call, getAccountInfo |
| WHALE | large transfers + counterparty clustering | eth_getLogs Transfer; getSignaturesForAddress + getTransaction |
| ALPHA | z-scores of count/volume/unique senders per block window | eth_getLogs |
| SIMULATOR | the execution graph (prepare phase) | all of the above |
| PORTFOLIO | balances across verified wallets; executable-quote or Jupiter prices only | RPC, QuoterV2, Jupiter Price v3 |
| AUTOPILOT | lists workflows/rules; `run_actions` executes approved actions | DB |
| BUILDER | workflow DSL validation (discriminated pydantic union, allowlisted actions) | — |
| INTEL | routes NL questions to read-only modules (ToolGate-checked); optional LLM narrative over fenced tool output | modules |
| MARKET | versioned, hash-pinned templates; community templates cannot prepare transactions | DB |
