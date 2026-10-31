#!/usr/bin/env bash
# Builds the program, starts a fresh local validator with the program deployed as UPGRADEABLE
# (required: `initialize` checks the upgrade authority), and runs the mocha integration suite.
# Equivalent to `anchor test`, but robust in sandboxes where anchor's port probe misfires.
set -euo pipefail
cd "$(dirname "$0")/.."
PROGRAM_ID=$(solana-keygen pubkey target/deploy/agent98_pass-keypair.json 2>/dev/null || true)
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
ANCHOR_PROVIDER_URL=http://127.0.0.1:8899 ANCHOR_WALLET="$WALLET" \
  pnpm exec ts-mocha -p ./tsconfig.json -t 1000000 'tests/**/*.ts'
