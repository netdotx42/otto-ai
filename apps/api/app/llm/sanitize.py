"""Treat all on-chain strings (token names, symbols, URIs, memos) as untrusted data.

They are never interpreted as instructions. Before display or inclusion in any LLM context they
are stripped of control/bidi characters, truncated, and (for LLM context) fenced as data.
"""

from __future__ import annotations

import re
import unicodedata

_BIDI = re.compile("[‪-‮⁦-⁩​-‏﻿]")
INJECTION_PATTERNS = re.compile(
    r"(ignore (all|any|previous|prior) (instructions|rules)|system prompt|you are now|"
    r"approve (all|unlimited)|send (all|your) (funds|tokens)|disregard|developer mode|"
    r"<\s*/?\s*(system|assistant|tool)\s*>|seed phrase|private key)",
    re.I,
)


def sanitize_untrusted(value: str, max_len: int = 64) -> str:
    v = unicodedata.normalize("NFKC", value)
    v = _BIDI.sub("", v)
    v = "".join(ch for ch in v if unicodedata.category(ch)[0] != "C")
    v = v.strip()
    return v[:max_len] + ("…" if len(v) > max_len else "")


def looks_like_injection(value: str) -> bool:
    return bool(INJECTION_PATTERNS.search(unicodedata.normalize("NFKC", value)))


def fence(label: str, value: str) -> str:
    """Wrap untrusted data for an LLM prompt."""
    clean = sanitize_untrusted(value, 512).replace("```", "'''")
    return f"<untrusted_data label=\"{label}\">{clean}</untrusted_data>"
