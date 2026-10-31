"""Common module interface: typed input, typed output, evidence, credit cost, tool allowlist."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class Evidence(BaseModel):
    source: str  # e.g. "base-rpc", "solana-rpc", "uniswap-v3-quoter", "jupiter"
    method: str  # RPC method / API endpoint
    ref: str | None = None  # block / slot / timestamp
    detail: str | None = None
    fetched_at: float = Field(default_factory=time.time)


class ModuleOutput(BaseModel):
    module: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


@dataclass
class ModuleContext:
    db: AsyncSession
    user: User
    clients: Any
    conversation_id: Any = None


class Module:
    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    credit_cost: ClassVar[int] = 1
    # The only tools this module may invoke. Enforced by `ToolGate`.
    tools: ClassVar[tuple[str, ...]] = ()
    Input: ClassVar[type[BaseModel]]
    read_only: ClassVar[bool] = True

    async def run(self, ctx: ModuleContext, inp: BaseModel) -> ModuleOutput:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "name": cls.name, "title": cls.title, "description": cls.description, "credit_cost": cls.credit_cost,
            "tools": list(cls.tools), "read_only": cls.read_only, "input_schema": cls.Input.model_json_schema(),
        }


class ToolNotAllowed(Exception):
    pass


class ToolGate:
    """Typed tool registry with per-module authorization. LLM-selected tool names are checked here."""

    def __init__(self, module: type[Module]) -> None:
        self.module = module

    def check(self, tool: str) -> None:
        if tool not in self.module.tools:
            raise ToolNotAllowed(f"{self.module.name} is not authorized to call tool '{tool}'")
