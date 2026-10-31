from __future__ import annotations

import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_nonce() -> str:
    # EIP-4361 requires >= 8 alphanumeric chars.
    return secrets.token_hex(16)
