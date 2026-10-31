#!/usr/bin/env bash
# Real-chain integration run (no mocks):
#   1. build the Anchor program and start a fresh local validator with it (upgradeable)
#   2. run the Anchor suite (initializes config, performs real USDC-paid mints)
#   3. run backend integration tests: Base against Anvil (deploy+mint via forge/cast),
#      Solana against the validator from step 1 (supply PDA + event indexing)
# Requires: solana CLI, anchor, foundry, PostgreSQL (agent98_test DB), uv, pnpm.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOL="$ROOT/contracts/solana"
cd "$SOL"
anchor build
PROGRAM_ID=$(solana-keygen pubkey target/deploy/agent98_pass-keypair.json)
WALLET=${ANCHOR_WALLET:-$HOME/.config/solana/id.json}
LEDGER=$(mktemp -d)
solana-test-validator --reset --quiet --ledger "$LEDGER" \
  --upgradeable-program "$PROGRAM_ID" target/deploy/agent98_pass.so "$WALLET" &
VPID=$!
trap 'kill $VPID 2>/dev/null || true; rm -rf "$LEDGER"' EXIT
for i in $(seq 1 60); do solana -u localhost cluster-version >/dev/null 2>&1 && break; sleep 1; done
sleep 3
ANCHOR_PROVIDER_URL=http://127.0.0.1:8899 ANCHOR_WALLET="$WALLET" pnpm exec ts-mocha -p ./tsconfig.json -t 1000000 'tests/**/*.ts'
cd "$ROOT/contracts/base" && ./install-deps.sh
cd "$ROOT/apps/api"
INTEGRATION=1 SOLANA_LOCAL_RPC=http://127.0.0.1:8899 SOLANA_PASS_PROGRAM_ID="$PROGRAM_ID" uv run pytest tests/integration -q
