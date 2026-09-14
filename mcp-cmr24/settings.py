"""Validated runtime configuration for the CMR24 MCP service."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

load_dotenv(Path(__file__).with_name(".env"))


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        item.strip() for item in os.getenv(name, default).split(",") if item.strip()
    )


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_url: str = "https://cmr24.by/api"
    auth_mode: Literal["service_account", "per_request"] = "per_request"
    cmr24_authkey: str | None = None
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
    allowed_origins: tuple[str, ...] = ()
    service_account_networks: tuple[str, ...] = ("127.0.0.0/8", "::1/128")
    trusted_proxy_header: str | None = None
    trusted_proxy_value: str | None = None
    trust_scope_header: bool = False
    default_scopes: tuple[str, ...] = ("cargo.read",)
    connect_timeout: float = Field(default=5.0, gt=0)
    read_timeout: float = Field(default=30.0, gt=0)
    write_timeout: float = Field(default=30.0, gt=0)
    pool_timeout: float = Field(default=5.0, gt=0)
    reference_cache_ttl: float = Field(default=3600.0, gt=0)
    reference_cache_size: int = Field(default=256, ge=1)
    matcher_threshold: float = Field(default=0.84, ge=0, le=1)
    matcher_margin: float = Field(default=0.08, ge=0, le=1)
    confirmation_ttl: int = Field(default=180, ge=10, le=900)
    confirmation_secret: str | None = None
    rate_limit_requests: int = Field(default=120, ge=1, le=100_000)
    rate_limit_window: int = Field(default=60, ge=1, le=3600)
    rate_limit_clients: int = Field(default=10_000, ge=100, le=1_000_000)

    @field_validator("base_url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("CMR24_BASE_URL must use http or https")
        parsed = urlsplit(value)
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("CMR24_BASE_URL must use https outside localhost")
        return value

    @field_validator("allowed_hosts", "allowed_origins")
    @classmethod
    def reject_wildcard_allowlists(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if "*" in values:
            raise ValueError("Wildcard Host and Origin allowlists are not permitted")
        return values

    @field_validator("default_scopes")
    @classmethod
    def validate_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        known = {"cargo.read", "cargo.write", "cargo.delete", "cargo.restore", "*"}
        unknown = set(values) - known
        if unknown:
            raise ValueError(f"Unknown scopes: {', '.join(sorted(unknown))}")
        return values

    @field_validator("service_account_networks")
    @classmethod
    def validate_networks(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            ipaddress.ip_network(value, strict=False)
        return values

    @model_validator(mode="after")
    def validate_auth(self) -> Settings:
        if self.auth_mode == "service_account" and not self.cmr24_authkey:
            raise ValueError("CMR24_AUTHKEY is required in service_account mode")
        if bool(self.trusted_proxy_header) != bool(self.trusted_proxy_value):
            raise ValueError(
                "Both MCP_TRUSTED_PROXY_HEADER and MCP_TRUSTED_PROXY_VALUE are required"
            )
        if self.trust_scope_header and not self.trusted_proxy_header:
            raise ValueError(
                "MCP_TRUST_SCOPE_HEADER requires an authenticated trusted proxy"
            )
        if (
            "cargo.delete" in self.default_scopes or "*" in self.default_scopes
        ) and not self.confirmation_secret:
            raise ValueError(
                "CONFIRMATION_SECRET is required when cargo.delete is enabled"
            )
        return self

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            base_url=os.getenv("CMR24_BASE_URL", "https://cmr24.by/api"),
            auth_mode=os.getenv("AUTH_MODE", "per_request"),
            cmr24_authkey=os.getenv("CMR24_AUTHKEY"),
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=os.getenv("MCP_PORT", "8000"),
            allowed_hosts=_csv("MCP_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]"),
            allowed_origins=_csv("MCP_ALLOWED_ORIGINS"),
            service_account_networks=_csv(
                "MCP_SERVICE_ACCOUNT_NETWORKS", "127.0.0.0/8,::1/128"
            ),
            trusted_proxy_header=os.getenv("MCP_TRUSTED_PROXY_HEADER"),
            trusted_proxy_value=os.getenv("MCP_TRUSTED_PROXY_VALUE"),
            trust_scope_header=os.getenv("MCP_TRUST_SCOPE_HEADER", "false").lower()
            == "true",
            default_scopes=_csv("MCP_DEFAULT_SCOPES", "cargo.read"),
            connect_timeout=os.getenv("CMR24_CONNECT_TIMEOUT", "5"),
            read_timeout=os.getenv("CMR24_READ_TIMEOUT", "30"),
            write_timeout=os.getenv("CMR24_WRITE_TIMEOUT", "30"),
            pool_timeout=os.getenv("CMR24_POOL_TIMEOUT", "5"),
            reference_cache_ttl=os.getenv("REFERENCE_CACHE_TTL", "3600"),
            reference_cache_size=os.getenv("REFERENCE_CACHE_SIZE", "256"),
            matcher_threshold=os.getenv("MATCHER_THRESHOLD", "0.84"),
            matcher_margin=os.getenv("MATCHER_MARGIN", "0.08"),
            confirmation_ttl=os.getenv("CONFIRMATION_TTL", "180"),
            confirmation_secret=os.getenv("CONFIRMATION_SECRET"),
            rate_limit_requests=os.getenv("MCP_RATE_LIMIT_REQUESTS", "120"),
            rate_limit_window=os.getenv("MCP_RATE_LIMIT_WINDOW", "60"),
            rate_limit_clients=os.getenv("MCP_RATE_LIMIT_CLIENTS", "10000"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
