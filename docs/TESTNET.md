# Testnet deployment (Base Sepolia + Solana devnet)

Use testnets end-to-end before any mainnet work. Nothing here requires mainnet funds.

## 0. Accounts & credentials you need

| Item | Where | Used by |
|---|---|---|
| Base Sepolia RPC URL | Alchemy / QuickNode / Coinbase Developer Platform | api `BASE_RPC_URL`, web `NEXT_PUBLIC_BASE_RPC_URL`, foundry `BASE_SEPOLIA_RPC_URL` |
| Basescan API key | basescan.org | contract verification |
| Solana devnet RPC URL | Helius / Triton / QuickNode (public endpoint works but is rate-limited) | api `SOLANA_RPC_URL`, web `NEXT_PUBLIC_SOLANA_RPC_URL` |
| Deployer key (Base) | `cast wallet import deployer --interactive` (encrypted keystore) | `scripts/deploy-base.sh` |
| Upgrade authority (Solana) | `solana-keygen new -o ~/.config/solana/agent98-authority.json` | `scripts/deploy-solana.sh` |
| Treasury addresses | ideally multisigs (Safe on Base, Squads on Solana) | constructor / initialize |
| Test ETH / SOL | Base Sepolia faucet, `solana airdrop 2 -ud` | gas |
| Test USDC | faucet.circle.com (Base Sepolia & Solana devnet) | minting |

## 1. Base Sepolia

```bash
export BASE_SEPOLIA_RPC_URL=... BASESCAN_API_KEY=...
export TREASURY_ADDRESS=0x... PASS_OWNER_ADDRESS=0x...   # owner = admin multisig recommended
NETWORK=base_sepolia ./scripts/deploy-base.sh
cat contracts/base/deployments/84532.json     # {"pass": "0x...", "block": N, ...}
```

The deploy script refuses non-canonical USDC on Base/Base Sepolia (address fixed by chain id), runs the full test
suite first, broadcasts and verifies on Basescan.

## 2. Solana devnet

```bash
solana config set -ud
solana airdrop 2
# Program keypair: target/deploy/agent98_pass-keypair.json. For a fresh id: solana-keygen new -o that path && anchor keys sync
TREASURY_OWNER=<treasury pubkey> UPGRADE_AUTHORITY=~/.config/solana/agent98-authority.json CLUSTER=devnet ./scripts/deploy-solana.sh
```

`--features devnet` pins the program to Circle's devnet USDC (`4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`) on-chain.
The initializer creates the treasury USDC ATA and calls `initialize` as the upgrade authority. If the program id
changed, copy `target/idl/agent98_pass.json` to `packages/shared/idl/` and update `passProgramId` in `chains.json`.

## 3. Configure and run

`apps/api/.env`:
```
NETWORK_MODE=testnet
BASE_RPC_URL=...            SOLANA_RPC_URL=...
BASE_PASS_ADDRESS=0x...     BASE_PASS_DEPLOY_BLOCK=<block from deployments/84532.json>
SOLANA_PASS_PROGRAM_ID=<program id>
```
`apps/web/.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_BASE_PASS_ADDRESS=0x...
NEXT_PUBLIC_SOLANA_PASS_PROGRAM_ID=<program id>
NEXT_PUBLIC_BASE_RPC_URL=...  NEXT_PUBLIC_SOLANA_RPC_URL=...
```

Start API, worker and web (README quick start).

## 4. Acceptance walkthrough

1. Connect a Base wallet → **Verify** (SIWE). Connect a Solana wallet → **Verify** (SIWS); both appear in Settings.
2. **Mint** on Base: approve exactly `n × 1.98` USDC → mint → wait for 2 confirmations → explorer link. Supply and
   *Your mint history* update (indexer runs every minute).
3. **Mint** on Solana: one atomic transaction → confirmation → explorer link (`?cluster=devnet`).
4. Settings → **Re-verify on-chain**: pass count equals the sum across both wallets; credits = min(passes, 20) × 500.
5. **Terminal** (Base): `Prepare a 1 USDC swap into WETH`. Expect step 1/2 *exact approval*, simulated, policy passed.
   Confirm → wallet → wait for receipt → step 2 swap preview appears → confirm → receipt → `confirmed`.
   Note: Uniswap liquidity on Base Sepolia is thin; a `no_route`, high price-impact block or simulation failure is the
   correct, honest outcome when the pool cannot fill.
6. **Terminal** (Solana devnet): `send 0.01 SOL to <address>` → preview → sign → `confirmed`. Swaps on devnet are
   rejected with an explanation (Jupiter is mainnet-only).
7. **Scanner**: scan the USDC contract and a random ERC-20; inspect findings and evidence.
8. **Automations**: create a large-transfer workflow on a wallet you control, send a qualifying transfer, observe the
   alert within ~1 minute; add `prepare_transaction` and confirm it lands in Activity as `awaiting approval`.
9. Admin (wallet in `ADMIN_WALLETS`): `PUT /v1/admin/flags/execution_enabled {"enabled": false, "reason": "drill"}` →
   new executions return 503 and approvals are refused.

## 5. Real-chain integration tests

`./scripts/integration-local.sh` runs the same code paths against a local Anvil node and a local Solana validator with
real deployments and real paid mints (no mocks).
