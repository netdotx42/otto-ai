"""Immutable transaction digests. Approvals are bound to these values.

EVM:    keccak256(canonical JSON of {chainId, from, to, data, value})
Solana: sha256(serialized message bytes) — exactly what the wallet signs.
The frontend recomputes the same digest (apps/web/lib/digest.ts) before requesting a signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from eth_utils import keccak


def evm_canonical(tx: dict[str, Any]) -> str:
    canon = {
        "chainId": int(tx["chainId"]),
        "data": str(tx["data"]).lower(),
        "from": str(tx["from"]).lower(),
        "to": str(tx["to"]).lower(),
        "value": str(int(tx["value"])),
    }
    return json.dumps(canon, sort_keys=True, separators=(",", ":"))


def evm_digest(tx: dict[str, Any]) -> str:
    return "0x" + keccak(text=evm_canonical(tx)).hex()


def solana_digest(message_b64: str) -> str:
    return "0x" + hashlib.sha256(base64.b64decode(message_b64)).hexdigest()
