"""Keycloak JWT verification with a JWKS cache.

Checks: signature (RS256/ES256 only), exp, iss, aud, and that a `sub` exists.
Roles come from Keycloak's `realm_access.roles`; only known roles are kept.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from mcp.server.auth.provider import AccessToken

from .errors import ErrorCode, ToolError
from .rbac import KNOWN_ROLES

ALLOWED_ALGS = ["RS256", "ES256"]


@dataclass(frozen=True)
class Claims:
    subject: str
    username: str
    roles: frozenset[str]
    expires_at: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


class JWKSCache:
    """Caches the realm's signing keys. Refetches on TTL expiry or an unknown `kid`,
    but never more often than `min_refresh` seconds, so garbage tokens cannot make us
    hammer Keycloak. If a refresh fails we keep serving the stale keys."""

    def __init__(
        self,
        url: str,
        ttl: float = 600.0,
        min_refresh: float = 30.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.ttl = ttl
        self.min_refresh = min_refresh
        self._client = client
        self._clock = clock
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _fetch(self) -> None:
        owns = self._client is None
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            resp = await client.get(self.url)
            resp.raise_for_status()
            keys = {k["kid"]: k for k in resp.json().get("keys", []) if "kid" in k}
        finally:
            if owns:
                await client.aclose()
        self._keys = keys

    async def get_key(self, kid: str) -> Any:
        async with self._lock:
            now = self._clock()
            stale = self._fetched_at is None or now - self._fetched_at > self.ttl
            unknown = kid not in self._keys
            can_refresh = self._fetched_at is None or now - self._fetched_at >= self.min_refresh
            if (stale or unknown) and can_refresh:
                try:
                    await self._fetch()
                except (httpx.HTTPError, ValueError, KeyError):
                    if not self._keys:
                        raise ToolError(
                            ErrorCode.UNAUTHENTICATED, "cannot reach identity provider"
                        ) from None
                finally:
                    self._fetched_at = now
            jwk = self._keys.get(kid)
        if jwk is None:
            raise ToolError(ErrorCode.UNAUTHENTICATED, "unknown signing key")
        return jwt.PyJWK(jwk).key


class JWTVerifier:
    def __init__(self, jwks: JWKSCache, issuer: str, audience: str) -> None:
        self.jwks = jwks
        self.issuer = issuer
        self.audience = audience

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise ToolError(ErrorCode.UNAUTHENTICATED, "malformed token") from None
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGS or not kid:  # blocks alg=none and HS256 key confusion
            raise ToolError(ErrorCode.UNAUTHENTICATED, "unsupported token header")
        key = await self.jwks.get_key(kid)
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=ALLOWED_ALGS,
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise ToolError(ErrorCode.UNAUTHENTICATED, "token expired") from None
        except jwt.PyJWTError:
            raise ToolError(ErrorCode.UNAUTHENTICATED, "invalid token") from None
        roles = frozenset(
            r for r in payload.get("realm_access", {}).get("roles", []) if r in KNOWN_ROLES
        )
        return Claims(
            subject=str(payload["sub"]),
            username=str(payload.get("preferred_username", payload["sub"])),
            roles=roles,
            expires_at=int(payload["exp"]),
            raw=payload,
        )


class KeycloakTokenVerifier:
    """Adapter so FastMCP's bearer middleware can use JWTVerifier (TokenVerifier protocol)."""

    def __init__(self, verifier: JWTVerifier) -> None:
        self.verifier = verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            c = await self.verifier.verify(token)
        except ToolError:
            return None
        return AccessToken(
            token=token,
            client_id=str(c.raw.get("azp", "unknown")),
            scopes=sorted(c.roles),
            expires_at=c.expires_at,
            resource=self.verifier.audience,
            subject=c.subject,
            claims={"username": c.username, "roles": sorted(c.roles)},
        )


def claims_from_access_token(at: AccessToken) -> Claims:
    data = at.claims or {}
    return Claims(
        subject=at.subject or "unknown",
        username=str(data.get("username", at.subject or "unknown")),
        roles=frozenset(r for r in data.get("roles", []) if r in KNOWN_ROLES),
        expires_at=at.expires_at or 0,
    )
