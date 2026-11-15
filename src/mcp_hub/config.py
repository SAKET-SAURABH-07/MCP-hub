"""Settings, read from MCPHUB_* environment variables (or a .env file)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MCPHUB_", env_file=".env", extra="ignore")

    # Auth (Keycloak)
    issuer: str = "http://localhost:8080/realms/mcp-hub"
    jwks_url: str = "http://localhost:8080/realms/mcp-hub/protocol/openid-connect/certs"
    audience: str = "mcp-hub"
    public_url: str = "http://localhost:8000"
    stdio_token: str = ""
    jwks_ttl_seconds: float = 600.0
    jwks_min_refresh_seconds: float = 30.0

    # Upstreams
    github_api: str = "https://api.github.com"
    github_token: str = ""
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    slack_api: str = "https://slack.com/api"
    slack_bot_token: str = ""
    http_timeout: float = 15.0
    http_retries: int = 3

    # Scope allowlists: deny by default
    allowed_repos: list[str] = Field(default_factory=list)
    allowed_jira_projects: list[str] = Field(default_factory=list)
    allowed_slack_channels: list[str] = Field(default_factory=list)

    # Storage & limits
    audit_db: Path = Path("data/audit.db")
    rate_capacity: float = 20.0
    rate_refill_per_sec: float = 1.0
    idempotency_ttl_seconds: float = 3600.0
    bulk_max_rows: int = 50
    bulk_max_bytes: int = 65536
