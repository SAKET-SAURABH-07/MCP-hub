from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_hub.auth import Claims
from mcp_hub.config import Settings
from mcp_hub.services import Services

ISSUER = "http://kc.test/realms/mcp-hub"
AUDIENCE = "mcp-hub"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
KID = "test-key-1"

REPO = "acme/app"
PROJECT = "SBX"
CHANNEL = "C0123456789"


def _make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return _make_key()


@pytest.fixture(scope="session")
def jwks(rsa_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@pytest.fixture
def mint(rsa_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _mint(
        roles: list[str] | None = None,
        *,
        sub: str = "user-1",
        exp_in: int = 300,
        aud: str = AUDIENCE,
        iss: str = ISSUER,
        key: rsa.RSAPrivateKey | None = None,
        kid: str | None = KID,
        alg: str = "RS256",
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": sub,
            "iss": iss,
            "aud": aud,
            "iat": now,
            "exp": now + exp_in,
            "preferred_username": f"name-{sub}",
            "azp": "mcp-client",
            "realm_access": {"roles": roles or []},
        }
        payload.update(extra or {})
        headers = {"kid": kid} if kid else {}
        return jwt.encode(payload, key or rsa_key, algorithm=alg, headers=headers)

    return _mint


def claims_for(*roles: str, sub: str = "user-1") -> Claims:
    return Claims(subject=sub, username=f"name-{sub}", roles=frozenset(roles), expires_at=0)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        audit_db=":memory:",  # type: ignore[arg-type]
        issuer=ISSUER,
        jwks_url=JWKS_URL,
        audience=AUDIENCE,
        github_token="ghp_" + "a" * 36,
        jira_base_url="https://jira.test",
        jira_email="me@example.com",
        jira_api_token="ATATT" + "b" * 30,
        slack_bot_token="xoxb-" + "1" * 12 + "-secret",
        allowed_repos=[REPO],
        allowed_jira_projects=[PROJECT],
        allowed_slack_channels=[CHANNEL],
        http_retries=0,
        rate_capacity=100,
        rate_refill_per_sec=100,
    )


@pytest.fixture
async def svc(settings: Settings) -> AsyncIterator[Services]:
    s = Services.from_settings(settings)
    yield s
    await s.aclose()
