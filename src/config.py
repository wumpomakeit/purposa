"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OKX API credentials ───────────────────────────────────────────────
    okx_api_key: str = Field(default="", alias="OKX_API_KEY")
    okx_secret_key: str = Field(default="", alias="OKX_SECRET_KEY")
    okx_passphrase: str = Field(default="", alias="OKX_PASSPHRASE")

    # ── LLM providers ────────────────────────────────────────────────────
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    nvidia_api_key: str = Field(default="", alias="NVIDIA_API_KEY")
    # Which provider to prefer: "nvidia" | "openai" | "anthropic"
    llm_provider: str = Field(default="nvidia", alias="LLM_PROVIDER")
    nvidia_base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", alias="NVIDIA_BASE_URL"
    )
    # Which models to use in the analysis ensemble
    primary_model: str = Field(
        default="meta/llama-3.1-70b-instruct", alias="PRIMARY_MODEL"
    )
    secondary_model: str = Field(
        default="nvidia/llama-3.3-nemotron-super-49b-v1", alias="SECONDARY_MODEL"
    )
    judge_model: str = Field(
        default="nvidia/llama-3.3-nemotron-super-49b-v1", alias="JUDGE_MODEL"
    )

    # ── Service ───────────────────────────────────────────────────────────
    purposa_host: str = Field(default="0.0.0.0", alias="PURPOSA_HOST")
    purposa_port: int = Field(default=8000, alias="PURPOSA_PORT")
    purposa_env: str = Field(default="development", alias="PURPOSA_ENV")

    # ── x402 payment settings ────────────────────────────────────────────
    seller_address: str = Field(default="", alias="SELLER_ADDRESS")
    analysis_price_usdt: int = Field(default=100, alias="ANALYSIS_PRICE_USDT")
    payment_network: str = Field(default="eip155:1952", alias="PAYMENT_NETWORK")
    payment_token_address: str = Field(
        default="0x9e29b3aada05bf2d2c827af80bd28dc0b9b4fb0c",
        alias="PAYMENT_TOKEN_ADDRESS",
    )
    payment_timeout_seconds: int = Field(default=120, alias="PAYMENT_TIMEOUT_SECONDS")

    # ── Snapshot ──────────────────────────────────────────────────────────
    snapshot_hub_url: str = Field(
        default="https://hub.snapshot.org", alias="SNAPSHOT_HUB_URL"
    )
    snapshot_graphql_url: str = Field(
        default="https://hub.snapshot.org/graphql", alias="SNAPSHOT_GRAPHQL_URL"
    )

    # ── Trace / audit ─────────────────────────────────────────────────────
    trace_backend: str = Field(default="local", alias="TRACE_BACKEND")
    trace_dir: Path = Field(default=Path("./traces"), alias="TRACE_DIR")

    # ── onchainos CLI ─────────────────────────────────────────────────────
    onchainos_bin: str = Field(
        default=str(Path.home() / ".local/bin/onchainos"), alias="ONCHAINOS_BIN"
    )

    @field_validator("purposa_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "production", "test"}
        if v not in allowed:
            raise ValueError(f"purposa_env must be one of {allowed}")
        return v

    @property
    def is_production(self) -> bool:
        return self.purposa_env == "production"

    @property
    def has_okx_credentials(self) -> bool:
        return bool(self.okx_api_key and self.okx_secret_key and self.okx_passphrase)

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.openai_api_key or self.anthropic_api_key or self.nvidia_api_key)

    def write_onchainos_env(self) -> None:
        """Write OKX credentials to ~/.onchainos/.env for the CLI."""
        if not self.has_okx_credentials:
            return
        onchainos_dir = Path.home() / ".onchainos"
        onchainos_dir.mkdir(exist_ok=True)
        env_path = onchainos_dir / ".env"
        env_path.write_text(
            f"OKX_API_KEY={self.okx_api_key}\n"
            f"OKX_SECRET_KEY={self.okx_secret_key}\n"
            f"OKX_PASSPHRASE={self.okx_passphrase}\n"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
