from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Toolsets group tools so the model only sees what it needs (fewer tools = less context).
# "core" (flow inspection and basic operations) is always enabled.
ALL_TOOLSETS: frozenset[str] = frozenset(
    {"core", "flow", "services", "parameters", "ports", "guides"}
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- NiFi connection -------------------------------------------------
    nifi_url: str = "https://localhost:8443"
    nifi_username: str = "admin"
    nifi_password: str = ""
    nifi_verify_ssl: bool = True
    nifi_ca_bundle: str | None = None
    # Sent as X-ProxyContextPath when NiFi sits behind a reverse proxy with a path prefix.
    nifi_proxy_context_path: str | None = None
    nifi_timeout: float = 30.0
    nifi_max_retries: int = 3
    nifi_retry_backoff: float = 0.5

    # --- MCP behaviour ---------------------------------------------------
    mcp_mode: Literal["readonly", "full"] = "readonly"
    mcp_toolsets: str = "all"
    mcp_dry_run: bool = False
    mcp_audit_log: bool = True
    mcp_audit_log_path: str = "./logs/audit.log"

    # --- Transport -------------------------------------------------------
    mcp_transport: Literal["stdio", "sse", "http", "streamable-http"] = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000

    @field_validator("mcp_toolsets")
    @classmethod
    def _validate_toolsets(cls, v: str) -> str:
        names = {n.strip().lower() for n in v.split(",") if n.strip()}
        unknown = names - ALL_TOOLSETS - {"all"}
        if unknown:
            valid = ", ".join(sorted(ALL_TOOLSETS))
            raise ValueError(f"Unknown MCP_TOOLSETS {sorted(unknown)}; valid: all, {valid}")
        return v

    @property
    def toolsets(self) -> frozenset[str]:
        names = {n.strip().lower() for n in self.mcp_toolsets.split(",") if n.strip()}
        if not names or "all" in names:
            return ALL_TOOLSETS
        return frozenset(names | {"core"})

    @property
    def api_base(self) -> str:
        base = self.nifi_url.rstrip("/")
        return base if base.endswith("/nifi-api") else f"{base}/nifi-api"

    @property
    def verify(self) -> bool | str:
        return self.nifi_ca_bundle or self.nifi_verify_ssl

    @property
    def can_write(self) -> bool:
        return self.mcp_mode == "full"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
