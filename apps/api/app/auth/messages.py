"""Sign-In-With-Ethereum (EIP-4361) and Sign-In-With-Solana message construction + verification.

The server constructs the exact message and stores it with the nonce. The client signs it
unmodified. Verification recovers/validates the signer and compares against the stored record,
so a client can never choose its own nonce, domain or expiry.
"""

from __future__ import annotations

from datetime import datetime

import base58
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import is_address, to_checksum_address
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

STATEMENT = (
    "Sign in to AGENT//98. This signature proves wallet ownership only. It does not authorize "
    "any transaction, approval or transfer, and costs no gas."
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_siwe(domain: str, uri: str, address: str, chain_id: int, nonce: str, issued: datetime, exp: datetime) -> str:
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{to_checksum_address(address)}\n\n"
        f"{STATEMENT}\n\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_iso(issued)}\n"
        f"Expiration Time: {_iso(exp)}"
    )


def build_siws(domain: str, uri: str, address: str, cluster: str, nonce: str, issued: datetime, exp: datetime) -> str:
    return (
        f"{domain} wants you to sign in with your Solana account:\n"
        f"{address}\n\n"
        f"{STATEMENT}\n\n"
        f"URI: {uri}\n"
        f"Version: 1\n"
        f"Chain ID: {cluster}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_iso(issued)}\n"
        f"Expiration Time: {_iso(exp)}"
    )


def recover_evm_signer(message: str, signature: str) -> str | None:
    try:
        return Account.recover_message(encode_defunct(text=message), signature=signature).lower()
    except Exception:  # noqa: BLE001 - any malformed signature is simply invalid
        return None


EIP1271_MAGIC = "0x1626ba7e"


def verify_solana_signature(message: str, address: str, signature_b58: str) -> bool:
    try:
        pub = base58.b58decode(address)
        sig = base58.b58decode(signature_b58)
        if len(pub) != 32 or len(sig) != 64:
            return False
        VerifyKey(pub).verify(message.encode(), sig)
        return True
    except (BadSignatureError, ValueError):
        return False


def is_valid_solana_address(address: str) -> bool:
    try:
        return len(base58.b58decode(address)) == 32
    except ValueError:
        return False


def normalize_address(chain: str, address: str) -> str:
    if chain == "base":
        if not is_address(address):
            raise ValueError("invalid EVM address")
        return address.lower()
    if not is_valid_solana_address(address):
        raise ValueError("invalid Solana address")
    return address
