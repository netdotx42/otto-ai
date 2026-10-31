"""Strict, typed data model for the Level 3 execution lifecycle."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Chain(StrEnum):
    base = "base"
    solana = "solana"


class IntentKind(StrEnum):
    swap = "swap"
    transfer = "transfer"


class ExecStatus(StrEnum):
    needs_clarification = "needs_clarification"
    rejected = "rejected"
    preparing = "preparing"
    awaiting_approval = "awaiting_approval"
    blocked = "blocked"
    approved = "approved"
    submitted = "submitted"
    confirmed = "confirmed"
    failed = "failed"
    expired = "expired"
    cancelled = "cancelled"


class TokenRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str | None = None
    address: str | None = None  # EVM hex / Solana mint; "native" for ETH/SOL


class TransactionIntent(BaseModel):
    """Parsed user request. This is a REQUEST, never an authorization."""

    model_config = ConfigDict(extra="forbid")
    chain: Chain
    kind: IntentKind
    wallet: str
    input_token: TokenRef
    output_token: TokenRef | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("1e15"))
    amount_percent: Decimal | None = Field(default=None, gt=0, le=100)
    destination: str | None = None
    slippage_bps: int = Field(default=50, ge=1, le=5000)
    raw_text: str = Field(max_length=2000)
    parser: Literal["deterministic", "llm"] = "deterministic"


class ResolvedToken(BaseModel):
    symbol: str
    address: str  # "native" for ETH/SOL
    decimals: int
    allowlisted: bool
    native: bool = False
    stable: bool = False
    token_program: str | None = None  # Solana only


class Quote(BaseModel):
    provider: str
    route: list[str]
    amount_in: int
    amount_out: int
    min_amount_out: int
    price_impact_bps: int | None
    fetched_at: float
    expires_at: float
    raw: dict[str, Any] = Field(default_factory=dict)


class UnsignedEvmTx(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chainId: int
    from_: str = Field(alias="from")
    to: str
    data: str
    value: str  # decimal string (wei)

    def wire(self) -> dict[str, Any]:
        return {"chainId": self.chainId, "from": self.from_, "to": self.to, "data": self.data, "value": self.value}


class UnsignedSolanaTx(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction: str  # base64 VersionedTransaction with empty signature slots
    message_b64: str
    fee_payer: str
    recent_blockhash: str
    last_valid_block_height: int


class BuiltStep(BaseModel):
    kind: Literal["approve", "swap", "transfer"]
    tx: dict[str, Any]
    digest: str
    decoded: dict[str, Any]
    quote: Quote | None = None
    expires_at: float | None = None


class BalanceChange(BaseModel):
    token: str
    symbol: str
    delta: int
    decimals: int


class SimulationResult(BaseModel):
    digest: str
    success: bool
    block_or_slot: int | None
    gas_or_units: int | None
    fee_estimate: dict[str, Any] = Field(default_factory=dict)
    balance_changes: list[BalanceChange] = Field(default_factory=list)
    return_data: str | None = None
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    simulated_at: float


class CheckStatus(StrEnum):
    passed = "pass"
    warn = "warn"
    fail = "fail"


class PolicyCheck(BaseModel):
    id: str
    status: CheckStatus
    message: str


class PolicyResult(BaseModel):
    allowed: bool
    checks: list[PolicyCheck]

    @property
    def failures(self) -> list[PolicyCheck]:
        return [c for c in self.checks if c.status == CheckStatus.fail]
