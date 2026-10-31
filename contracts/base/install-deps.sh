#!/usr/bin/env bash
# Vendored (non-submodule) dependency install, pinned versions.
set -euo pipefail
cd "$(dirname "$0")"
[ -d lib/forge-std ] || forge install --no-git foundry-rs/forge-std@v1.9.4
[ -d lib/openzeppelin-contracts ] || forge install --no-git OpenZeppelin/openzeppelin-contracts@v5.1.0
