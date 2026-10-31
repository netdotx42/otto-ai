"""Runtime configuration. All secrets come from the environment; nothing is hard-coded."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    env: Literal["local", "test", "staging", "production"] = "local"
    # "testnet" => Base Sepolia + Solana devnet; "mainnet" => Base + Solana mainnet-beta.
    network_mode: Literal["testnet", "mainnet"] = "testnet"
    # Hard gate. Even with network_mode=mainnet, execution previews are blocked unless true.
    mainnet_execution_enabled: bool = False

    database_url: str = "postgresql+asyncpg://agent98:agent98@localhost:5432/agent98"
    redis_url: str = "redis://localhost:6379/0"

    app_domain: str = "localhost:3000"
    app_origin: str = "http://localhost:3000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    session_ttl_hours: int = 24 * 7
    session_cookie_name: str = "agent98_session"
    cookie_secure: bool = False
    nonce_ttl_seconds: int = 300

    # Chain RPCs (credentials required for real data; see .env.example).
    base_rpc_url: str = "https://sepolia.base.org"
    solana_rpc_url: str = "https://api.devnet.solana.com"

    # Deployed pass contracts. Empty => mint/entitlement features report "not deployed".
    base_pass_address: str = ""
    base_pass_deploy_block: int = 0
    solana_pass_program_id: str = "ARd3pfR6GHqpLYr7sacHgjNwwagkzN15qmoq2GnHyw2Z"

    # Optional third-party services.
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    jupiter_api_url: str = "https://lite-api.jup.ag"
    # With a key, requests go to the authenticated api.jup.ag tier (higher limits) unless
    # JUPITER_API_URL is set explicitly. The key is a secret: environment only, never committed.
    jupiter_api_key: str = ""

    def jupiter_base(self) -> str:
        if self.jupiter_api_key and self.jupiter_api_url == "https://lite-api.jup.ag":
            return "https://api.jup.ag"
        return self.jupiter_api_url

    def jupiter_headers(self) -> dict[str, str]:
        return {"x-api-key": self.jupiter_api_key} if self.jupiter_api_key else {}
    sentry_dsn: str = ""
    otel_exporter_otlp_endpoint: str = ""

    # Execution policy defaults (users may only tighten them).
    quote_ttl_seconds: int = 30
    simulation_ttl_seconds: int = 45
    approval_ttl_seconds: int = 90
    default_slippage_bps: int = 50
    max_slippage_bps: int = 500
    default_max_tx_usd: float = 250.0
    default_daily_limit_usd: float = 1000.0
    price_impact_warn_bps: int = 100
    price_impact_block_bps: int = 1500
    base_confirmations: int = 2

    # Rate limiting (requests per window).
    rate_limit_window_seconds: int = 60
    rate_limit_default: int = 120
    rate_limit_auth: int = 20
    rate_limit_ai: int = 30
    rate_limit_execution: int = 20

    admin_wallets: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    return Settings()
