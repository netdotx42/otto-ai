# Mainnet launch checklist

Mainnet execution is disabled in code unless `NETWORK_MODE=mainnet` **and** `MAINNET_EXECUTION_ENABLED=true`.
The deploy scripts refuse mainnet unless `I_HAVE_AN_AUDIT=yes`. Do not flip either until every item below is done.

## Contracts
- [ ] Independent audit of `contracts/base/src/Agent98Pass.sol` — findings resolved, report published.
- [ ] Independent audit of `contracts/solana/programs/agent98_pass` — findings resolved, report published.
- [ ] Fresh Solana program keypair generated offline; `declare_id!`, `Anchor.toml`, `chains.json`, IDL updated.
- [ ] Program built with `--features mainnet` (pins `EPjFWdd5AufqSSqeM2qJ1zzybapC8G4wEGGkZwyTDt1v`); verifiable build
      (`anchor build --verifiable`) and on-chain verification (OtterSec / solana-verify).
- [ ] Base contract verified on Basescan; constructor args reviewed (canonical USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`).
- [ ] Owner / admin / upgrade authority are multisigs (Safe, Squads) with documented signers; treasury is a multisig.
- [ ] Decide Solana upgrade policy (keep upgradeable behind multisig + timelock, or make immutable after a soak period).
- [ ] Pause / `set_paused` drill performed on testnet and documented.
- [ ] Pass metadata URI hosted (`PASS_METADATA_URI`) and Solana token metadata (Metaplex or Token-2022 metadata) set.

## Allowlist & integrations
- [ ] Every address in `packages/shared/chains.json` (USDC, WETH, tokens, SwapRouter02, QuoterV2, factory, Solana
      program ids) independently verified against official sources by two people.
- [ ] Production RPC providers with SLAs for Base and Solana; rate limits sized; failover configured.
- [ ] Jupiter: consider the authenticated API tier for production volume.

## Backend / security
- [ ] `COOKIE_SECURE=true`, `CORS_ORIGINS` and `APP_DOMAIN/APP_ORIGIN` set to production domains (SIWE domain binding).
- [ ] Secrets in a secret manager; no `.env` files on hosts. Database encrypted at rest, backups + PITR tested.
- [ ] Rate limits and policy defaults reviewed (`DEFAULT_MAX_TX_USD`, `DEFAULT_DAILY_LIMIT_USD`, `MAX_SLIPPAGE_BPS`).
- [ ] `ADMIN_WALLETS` set; kill-switch runbook tested (`PUT /v1/admin/flags/execution_enabled`).
- [ ] Penetration test of API + web (auth, CSRF, IDOR, SSE, idempotency, approval replay, substitution).
- [ ] LLM provider data-processing terms reviewed; prompt-injection test corpus run (if `OPENAI_API_KEY` used).
- [ ] Audit-chain verification (`/v1/admin/audit/verify`) scheduled and alerting.

## Observability & operations
- [ ] Sentry (web + api) and OTLP tracing wired to dashboards; alerts on 5xx rate, RPC errors, indexer lag,
      worker failures, `execution.substitution_detected` events.
- [ ] Worker concurrency and job timeouts load-tested; indexer catch-up from deploy block verified.
- [ ] Incident response + on-call rotation; status page.

## Legal & product
- [ ] Counsel review of Terms, Privacy and Risk pages (templates in `apps/web/app/legal/*`), pass marketing
      (utility, no investment expectation), sanctions/geofencing requirements, tax handling of USDC receipts.
- [ ] Fair-use credit policy published and matches `chains.json` (`creditsPerPassPerPeriod`, `maxPassesCountedForCredits`).

## Go-live sequence
1. Deploy contracts → verify → initialize → small real mint by the team on each chain.
2. Deploy API/worker with `NETWORK_MODE=mainnet`, `MAINNET_EXECUTION_ENABLED=false` (read-only + mint).
3. Soak period with monitoring; then enable execution for an allowlisted cohort; then general availability.
