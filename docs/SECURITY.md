# Security model

## Trust boundaries

1. **The model is untrusted.** It may only produce a candidate `TransactionIntent` (structured output). Every value it
   returns must appear literally in the user's text (`llm_output_to_outcome`), then the same code validator runs as for
   the deterministic grammar. The model never sees or produces calldata, never selects destinations and has no tool that
   signs, broadcasts or approves.
2. **On-chain strings are untrusted data.** Token names/symbols/URIs are NFKC-normalized, stripped of control/bidi
   characters, truncated, checked for instruction-like payloads (FORENSIC finding `metadata_injection_*`) and fenced as
   `<untrusted_data>` if ever placed in an LLM prompt.
3. **The browser is untrusted for entitlements.** Pass balances are read by the server from chain state.
4. **The backend is not trusted with funds.** It holds no keys and cannot sign. The user's wallet is the only signer.

## Approval binding

- Digest: EVM `keccak256(canonical{chainId,from,to,data,value})`; Solana `sha256(message bytes)`. Implemented identically
  in `apps/api/app/execution/digest.py` and `apps/web/lib/digest.ts` (cross-checked by test vectors).
- The client recomputes the digest of the exact transaction it will hand to the wallet and refuses to open the wallet on
  mismatch — both before approval and after the server returns the approved transaction.
- `ApprovalRecord` is bound to the digest and the specific simulation, expires (≤ 90 s and ≤ quote expiry), and is
  consumed with an atomic compare-and-set (`used_at IS NULL`) → approval replay is impossible.
- Any rebuild (refresh, new quote) changes the digest and invalidates outstanding approvals.
- After signing: Base — the server fetches the broadcast transaction by hash and recomputes its digest from
  on-chain fields; mismatch ⇒ `failed` + `execution.substitution_detected` audit event. Solana — the server checks the
  signed message bytes equal the approved digest and verifies the fee-payer's ed25519 signature before broadcasting.
- Success is only recorded from a receipt (`status=0x1` with ≥ `BASE_CONFIRMATIONS`) or a `confirmed/finalized`
  signature status with `err = null`. Expired blockhashes are marked `dropped`/failed.

## Policy engine (`app/execution/policy.py`)

Deterministic and pure. Any `fail` blocks the approve endpoint (HTTP 403/409). Checks:

| Category | Checks |
|---|---|
| Platform | `kill_switch` (DB flag), `chain_supported`, `mainnet_gate`, `wallet_ownership` |
| Hygiene | `address_format`, `chain_id`, `from_matches` / fee payer |
| Destination | Base: router/token allowlist; Solana: `program_allowlist`, `forbidden_instructions` (Approve, SetAuthority, Assign, …), exactly one Jupiter route |
| Intent match | decoded calldata/instructions vs intent (tokens, amount, recipient = user, min-out), `native_value`, swap `deadline` |
| Approvals | `approval_exact` (unlimited blocked unless the user opts in), spender must be the allowlisted router |
| Economics | `amount_positive`, `balance_sufficient`, `spend_limit_tx`, `spend_limit_daily` (fail-closed when USD value is unverifiable), `slippage_limit`, `price_impact` (warn ≥1 %, block ≥15 %), `quote_fresh`, `min_out_consistent`, `quote_amount_matches`, `tx_expiry` |
| Simulation | `simulation_ok`, `simulation_digest` (tx modified after simulation), `simulation_fresh`, `simulated_input`, `simulated_output` |
| Token risk | non-allowlisted tokens must be scanned; `critical` findings block |

## Platform controls

- Sessions: random 256-bit token, only its SHA-256 stored, httpOnly SameSite=Lax cookie (Secure in prod), revocable.
- CSRF: SameSite + origin guard on mutating requests + strict CORS.
- Rate limits: Redis fixed window per user/IP per route group (auth, ai, execution, default) with an in-process fallback.
- Idempotency-Key on mutating endpoints; conflicting reuse → 409.
- Hash-chained audit log; `GET /v1/admin/audit/verify` detects tampering.
- Emergency switches: `system_flags.execution_enabled`, `automations_enabled`, `mint_ui_enabled`; on-chain `pause()` /
  `set_paused` for minting (does not affect existing passes).
- Automations cannot sign; `prepare_transaction` ends at `awaiting_approval` and must target a verified wallet.

## Contracts

Base `Agent98Pass`: only paid mint paths; exact price constant; SafeERC20; payment goes straight to treasury; CEI +
`nonReentrant`; batch bound 1–50; Ownable2Step; renounce disabled; permit path is front-run safe.
Solana `agent98_pass`: initialize restricted to the upgrade authority; canonical USDC pinned by build feature; USDC
6-decimal check; `transfer_checked`; PDA mint authority, no freeze authority; `has_one` constraints for USDC mint,
treasury and pass mint; token-account owner checks; checked math; two-step admin rotation.

## Known limitations (tracked in STATUS.md)

- Smart-contract wallets: EIP-1271 sign-in is supported for deployed wallets; ERC-6492 (counterfactual) is not.
- Jupiter route internals are not decoded instruction-by-instruction; safety relies on program allowlist, forbidden
  instruction checks, fee-payer check and simulated balance deltas, with on-chain min-out enforced by Jupiter.
- FORENSIC is heuristic (static selector scan); it does not simulate sells (honeypot detection).
