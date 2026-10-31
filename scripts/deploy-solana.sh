#!/usr/bin/env bash
# Build with the cluster feature (pins canonical USDC on-chain), deploy, and initialize.
#   CLUSTER=devnet|mainnet TREASURY_OWNER=<pubkey> UPGRADE_AUTHORITY=~/.config/solana/id.json ./scripts/deploy-solana.sh
set -euo pipefail
CLUSTER=${CLUSTER:-devnet}
AUTH=${UPGRADE_AUTHORITY:-$HOME/.config/solana/id.json}
cd "$(dirname "$0")/../contracts/solana"
if [ "$CLUSTER" = "mainnet" ] && [ "${I_HAVE_AN_AUDIT:-}" != "yes" ]; then
  echo "Refusing mainnet deploy without I_HAVE_AN_AUDIT=yes (see docs/MAINNET_CHECKLIST.md)"; exit 1
fi
URL=$([ "$CLUSTER" = "mainnet" ] && echo "https://api.mainnet-beta.solana.com" || echo "https://api.devnet.solana.com")
anchor build -- --features "$CLUSTER"
anchor deploy --provider.cluster "$URL" --provider.wallet "$AUTH"
ANCHOR_PROVIDER_URL="$URL" ANCHOR_WALLET="$AUTH" TREASURY_OWNER="$TREASURY_OWNER" pnpm exec ts-node --transpile-only scripts/initialize.ts
echo "Program: $(solana-keygen pubkey target/deploy/agent98_pass-keypair.json)"
echo "Copy target/idl/agent98_pass.json to packages/shared/idl/ if the interface changed."
