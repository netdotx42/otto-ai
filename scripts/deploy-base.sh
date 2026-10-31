#!/usr/bin/env bash
# Deploy + verify Agent98Pass on Base Sepolia (default) or Base mainnet.
#   NETWORK=base_sepolia|base TREASURY_ADDRESS=0x.. PASS_OWNER_ADDRESS=0x.. (multisig recommended)
#   BASESCAN_API_KEY=.. BASE_SEPOLIA_RPC_URL=.. ./scripts/deploy-base.sh
# Signing uses a Foundry keystore account (never a raw private key in env): `cast wallet import deployer --interactive`
set -euo pipefail
NETWORK=${NETWORK:-base_sepolia}
ACCOUNT=${DEPLOYER_ACCOUNT:-deployer}
cd "$(dirname "$0")/../contracts/base"
./install-deps.sh
forge build
forge test
if [ "$NETWORK" = "base" ] && [ "${I_HAVE_AN_AUDIT:-}" != "yes" ]; then
  echo "Refusing mainnet deploy without I_HAVE_AN_AUDIT=yes (see docs/MAINNET_CHECKLIST.md)"; exit 1
fi
forge script script/Deploy.s.sol --rpc-url "$NETWORK" --account "$ACCOUNT" --broadcast --verify -vvv
echo "Deployment record written to contracts/base/deployments/<chainId>.json"
echo "Set BASE_PASS_ADDRESS / BASE_PASS_DEPLOY_BLOCK (api) and NEXT_PUBLIC_BASE_PASS_ADDRESS (web)."
