"""Seed official marketplace templates and default system flags.

Revision ID: 0002
Revises: 0001
"""

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PLACEHOLDER_EVM = "0x0000000000000000000000000000000000000001"

TEMPLATES = [
    {
        "slug": "whale-trade-forensics",
        "name": "Whale trade → token forensics",
        "description": "When a watched wallet makes a large ERC-20 transfer on Base, scan the token and alert me.",
        "module": "autopilot",
        "definition": {
            "name": "Whale trade → token forensics",
            "trigger": {"type": "wallet_large_transfer", "chain": "base", "wallet": PLACEHOLDER_EVM, "token": PLACEHOLDER_EVM, "min_amount": "10000"},
            "actions": [{"type": "forensic_scan"}, {"type": "alert", "severity": "warning"}],
            "limits": {"max_runs_per_day": 24},
        },
    },
    {
        "slug": "low-balance-guard",
        "name": "Low balance guard",
        "description": "Alert when a wallet's native balance falls below a threshold (gas safety).",
        "module": "sentinel",
        "definition": {
            "name": "Low balance guard",
            "trigger": {"type": "balance_below", "chain": "base", "wallet": PLACEHOLDER_EVM, "token": "native", "threshold": "0.002"},
            "actions": [{"type": "alert", "severity": "warning"}],
            "limits": {"max_runs_per_day": 6},
        },
    },
    {
        "slug": "proxy-upgrade-watch",
        "name": "Proxy upgrade watch",
        "description": "Alert when a contract's bytecode or EIP-1967 implementation changes.",
        "module": "forensic",
        "definition": {
            "name": "Proxy upgrade watch",
            "trigger": {"type": "contract_code_change", "chain": "base", "address": PLACEHOLDER_EVM},
            "actions": [{"type": "alert", "severity": "critical"}],
            "limits": {"max_runs_per_day": 24},
        },
    },
]


def upgrade() -> None:
    t = sa.table(
        "marketplace_templates",
        sa.column("id", UUID(as_uuid=True)), sa.column("slug", sa.String), sa.column("name", sa.String),
        sa.column("description", sa.Text), sa.column("module", sa.String), sa.column("version", sa.Integer),
        sa.column("definition", JSONB), sa.column("definition_hash", sa.String), sa.column("status", sa.String),
        sa.column("installs", sa.Integer), sa.column("official", sa.Boolean),
    )
    rows = []
    for tpl in TEMPLATES:
        h = hashlib.sha256(json.dumps(tpl["definition"], sort_keys=True).encode()).hexdigest()
        rows.append({**tpl, "id": uuid.uuid4(), "version": 1, "definition_hash": h, "status": "published", "installs": 0, "official": True})
    op.bulk_insert(t, rows)
    f = sa.table("system_flags", sa.column("key", sa.String), sa.column("enabled", sa.Boolean), sa.column("reason", sa.Text))
    op.bulk_insert(f, [
        {"key": "execution_enabled", "enabled": True, "reason": "default"},
        {"key": "mint_ui_enabled", "enabled": True, "reason": "default"},
        {"key": "automations_enabled", "enabled": True, "reason": "default"},
    ])


def downgrade() -> None:
    op.execute("DELETE FROM marketplace_templates WHERE official = true")
    op.execute("DELETE FROM system_flags")
