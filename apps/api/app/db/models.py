"""SQLAlchemy 2.0 models for every persisted entity."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------------------------


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-user execution limits. Users can only tighten platform defaults.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    wallets: Mapped[list[Wallet]] = relationship(back_populates="user", lazy="selectin")


class Wallet(TimestampMixin, Base):
    """A wallet whose control has been proven by signature (SIWE / SIWS)."""

    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_wallet_chain_address"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chain: Mapped[str] = mapped_column(String(16))  # "base" | "solana"
    address: Mapped[str] = mapped_column(String(64))  # EVM: lowercase hex; Solana: base58
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship(back_populates="wallets")


class AuthNonce(TimestampMixin, Base):
    __tablename__ = "auth_nonces"
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    chain: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(TimestampMixin, Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256(token); raw token never stored
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ---------------------------------------------------------------------------------------------
# Mint + entitlements + credits
# ---------------------------------------------------------------------------------------------


class MintEvent(TimestampMixin, Base):
    """Verified on-chain PassMinted events (indexed from logs / program events)."""

    __tablename__ = "mint_events"
    __table_args__ = (UniqueConstraint("chain", "tx_id", "log_index", name="uq_mint_event"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chain: Mapped[str] = mapped_column(String(16), index=True)
    tx_id: Mapped[str] = mapped_column(String(100))
    log_index: Mapped[int] = mapped_column(Integer, default=0)
    buyer: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    paid_base_units: Mapped[int] = mapped_column(Numeric(78, 0))
    total_minted_after: Mapped[int] = mapped_column(Numeric(78, 0))
    block_or_slot: Mapped[int] = mapped_column(BigInteger)
    block_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IndexerCursor(Base):
    __tablename__ = "indexer_cursors"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PassEntitlement(Base):
    """Last verified pass balance for a wallet, read from chain state (not from the client)."""

    __tablename__ = "pass_entitlements"
    wallet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wallets.id", ondelete="CASCADE"), primary_key=True)
    passes: Mapped[int] = mapped_column(BigInteger, default=0)
    source: Mapped[str] = mapped_column(String(128))  # e.g. "base:eth_call balanceOf @ block 123"
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageLedger(TimestampMixin, Base):
    """Append-only usage-credit ledger. Balance = grants - debits for the current period."""

    __tablename__ = "usage_ledger"
    __table_args__ = (Index("ix_usage_user_period", "user_id", "period"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(16))  # e.g. "2026-10"
    delta: Mapped[int] = mapped_column(Integer)  # negative = debit
    reason: Mapped[str] = mapped_column(String(64))
    module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


# ---------------------------------------------------------------------------------------------
# Agent conversations
# ---------------------------------------------------------------------------------------------


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="New session")
    chain: Mapped[str] = mapped_column(String(16), default="base")


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # cards, evidence, execution ids


class ToolCall(TimestampMixin, Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    module: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    evidence: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16))  # ok | error | denied
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


class TokenScan(TimestampMixin, Base):
    __tablename__ = "token_scans"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    chain: Mapped[str] = mapped_column(String(16))
    token: Mapped[str] = mapped_column(String(64), index=True)
    risk_level: Mapped[str] = mapped_column(String(16))  # low | medium | high | critical | unknown
    findings: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------------------------------------
# Monitoring / automations / marketplace
# ---------------------------------------------------------------------------------------------


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | paused | revoked
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marketplace_templates.id", ondelete="SET NULL"), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runs_today: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowVersion(TimestampMixin, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version", name="uq_workflow_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_hash: Mapped[str] = mapped_column(String(64))


class MonitoringRule(TimestampMixin, Base):
    """Compiled trigger of a workflow, evaluated by background workers."""

    __tablename__ = "monitoring_rules"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))  # wallet_large_transfer | balance_below | contract_code_change
    chain: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cursor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Alert(TimestampMixin, Base):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("monitoring_rules.id", ondelete="SET NULL"), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketplaceTemplate(TimestampMixin, Base):
    __tablename__ = "marketplace_templates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="published")  # draft | published | delisted
    installs: Mapped[int] = mapped_column(Integer, default=0)
    official: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------------------------
# Level 3 execution
# ---------------------------------------------------------------------------------------------


class TransactionIntent(TimestampMixin, Base):
    """Root of an execution. Status machine mirrors the LangGraph lifecycle."""

    __tablename__ = "transaction_intents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflows.id", ondelete="SET NULL"), nullable=True)
    chain: Mapped[str] = mapped_column(String(16))
    wallet: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # swap | transfer
    raw_text: Mapped[str] = mapped_column(Text)
    intent: Mapped[dict[str, Any]] = mapped_column(JSONB)
    research: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    steps: Mapped[list[ExecutionStep]] = relationship(
        back_populates="intent", order_by="ExecutionStep.index", lazy="selectin"
    )


class ExecutionStep(TimestampMixin, Base):
    """One wallet transaction inside an execution (e.g. exact approve, then swap)."""

    __tablename__ = "execution_steps"
    __table_args__ = (UniqueConstraint("intent_id", "index", name="uq_step_index"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    intent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transaction_intents.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # approve | swap | transfer
    unsigned_tx: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decoded: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    digest: Mapped[str | None] = mapped_column(String(66), nullable=True)
    quote: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32))
    intent: Mapped[TransactionIntent] = relationship(back_populates="steps")


class SimulationRecord(TimestampMixin, Base):
    __tablename__ = "simulations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("execution_steps.id", ondelete="CASCADE"), index=True)
    digest: Mapped[str] = mapped_column(String(66))
    success: Mapped[bool] = mapped_column(Boolean)
    block_or_slot: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gas_or_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fee_estimate: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    balance_changes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    limitations: Mapped[list[Any]] = mapped_column(JSONB, default=list)


class ApprovalRecord(TimestampMixin, Base):
    """Explicit user confirmation bound to an immutable transaction digest. Single use."""

    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("execution_steps.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    digest: Mapped[str] = mapped_column(String(66))
    simulation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulations.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TransactionSubmission(TimestampMixin, Base):
    __tablename__ = "transaction_submissions"
    __table_args__ = (UniqueConstraint("chain", "tx_id", name="uq_submission_tx"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("execution_steps.id", ondelete="CASCADE"), index=True)
    approval_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.id"), unique=True)
    chain: Mapped[str] = mapped_column(String(16))
    tx_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16))  # submitted | confirmed | failed | mismatch | dropped
    explorer_url: Mapped[str] = mapped_column(String(256))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checks: Mapped[int] = mapped_column(Integer, default=0)


class ConfirmedReceipt(TimestampMixin, Base):
    __tablename__ = "confirmed_receipts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transaction_submissions.id", ondelete="CASCADE"), unique=True)
    success: Mapped[bool] = mapped_column(Boolean)
    block_or_slot: Mapped[int] = mapped_column(BigInteger)
    fee_paid: Mapped[str | None] = mapped_column(String(78), nullable=True)
    confirmation: Mapped[str] = mapped_column(String(32))  # e.g. "2 confirmations" / "finalized"
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


# ---------------------------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------------------------


class AuditEvent(Base):
    """Append-only, hash-chained audit log (each row commits to the previous row's hash)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class IdempotencyKey(TimestampMixin, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idem_user_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    key: Mapped[str] = mapped_column(String(128))
    route: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class SystemFlag(Base):
    """Runtime kill switches (e.g. execution_enabled, mint_ui_enabled)."""

    __tablename__ = "system_flags"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


__all__ = [n for n in dir() if n[0].isupper()]
_ = Decimal  # re-exported for type hints in services
