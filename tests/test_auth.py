from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_hub.auth import JWKSCache, JWTVerifier, KeycloakTokenVerifier, claims_from_access_token
from mcp_hub.errors import ErrorCode, ToolError

from .conftest import AUDIENCE, ISSUER, JWKS_URL, KID

Mint = Callable[..., str]


def make_verifier(clock: Callable[[], float] | None = None) -> JWTVerifier:
    kw: dict[str, Any] = {"clock": clock} if clock else {}
    return JWTVerifier(JWKSCache(JWKS_URL, **kw), ISSUER, AUDIENCE)


@pytest.fixture
def jwks_route(jwks: dict[str, Any]) -> respx.Route:
    with respx.mock(assert_all_called=False) as m:
        yield m.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks))


async def expect_unauth(verifier: JWTVerifier, token: str) -> str:
    with pytest.raises(ToolError) as e:
        await verifier.verify(token)
    assert e.value.code is ErrorCode.UNAUTHENTICATED
    return e.value.message


async def test_valid_token_yields_claims(jwks_route: respx.Route, mint: Mint) -> None:
    c = await make_verifier().verify(mint(["developer", "offline_access"], sub="u9"))
    assert c.subject == "u9" and c.username == "name-u9"
    assert c.roles == {"developer"}  # unknown Keycloak default roles are dropped


async def test_expired_token_rejected(jwks_route: respx.Route, mint: Mint) -> None:
    assert "expired" in await expect_unauth(make_verifier(), mint(["viewer"], exp_in=-60))


@pytest.mark.parametrize(
    "kwargs", [{"aud": "someone-else"}, {"iss": "http://evil.test/realms/x"}], ids=["aud", "iss"]
)
async def test_wrong_audience_or_issuer_rejected(
    jwks_route: respx.Route, mint: Mint, kwargs: dict[str, str]
) -> None:
    await expect_unauth(make_verifier(), mint(["admin"], **kwargs))


async def test_token_signed_by_other_key_rejected(jwks_route: respx.Route, mint: Mint) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    await expect_unauth(make_verifier(), mint(["admin"], key=attacker))


async def test_alg_none_rejected(jwks_route: respx.Route) -> None:
    token = jwt.encode(
        {"sub": "x", "aud": AUDIENCE, "iss": ISSUER, "exp": 9999999999},
        None,
        algorithm="none",
        headers={"kid": KID},
    )
    assert "header" in await expect_unauth(make_verifier(), token)


async def test_hs256_key_confusion_rejected(jwks_route: respx.Route) -> None:
    token = jwt.encode(
        {"sub": "x", "aud": AUDIENCE, "iss": ISSUER, "exp": 9999999999},
        "secret" * 8,
        algorithm="HS256",
        headers={"kid": KID},
    )
    await expect_unauth(make_verifier(), token)


async def test_missing_kid_and_garbage_rejected(jwks_route: respx.Route, mint: Mint) -> None:
    await expect_unauth(make_verifier(), mint(["admin"], kid=None))
    await expect_unauth(make_verifier(), "not.a.jwt")


async def test_tampered_signature_rejected(jwks_route: respx.Route, mint: Mint) -> None:
    token = mint(["admin"])
    await expect_unauth(make_verifier(), token[:-4] + "AAAA")


async def test_jwks_cached_across_calls(jwks_route: respx.Route, mint: Mint) -> None:
    v = make_verifier()
    for _ in range(3):
        await v.verify(mint(["viewer"]))
    assert jwks_route.call_count == 1


async def test_unknown_kid_refetches_once_then_rate_limited(
    jwks_route: respx.Route, mint: Mint
) -> None:
    now = [0.0]
    v = make_verifier(clock=lambda: now[0])
    await v.verify(mint(["viewer"]))  # warm
    for _ in range(5):
        await expect_unauth(v, mint(["viewer"], kid="rotated-away"))
    assert jwks_route.call_count == 1  # within min_refresh: no hammering
    now[0] += 31
    await expect_unauth(v, mint(["viewer"], kid="rotated-away"))
    assert jwks_route.call_count == 2


async def test_stale_keys_served_when_refresh_fails(jwks: dict[str, Any], mint: Mint) -> None:
    now = [0.0]
    with respx.mock() as m:
        route = m.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks))
        v = make_verifier(clock=lambda: now[0])
        await v.verify(mint(["viewer"]))
        route.mock(return_value=httpx.Response(500))
        now[0] += 10_000  # TTL expired, refresh will fail
        c = await v.verify(mint(["viewer"]))
        assert c.roles == {"viewer"}


async def test_idp_down_with_cold_cache_is_unauthenticated(mint: Mint) -> None:
    with respx.mock() as m:
        m.get(JWKS_URL).mock(side_effect=httpx.ConnectError("down"))
        assert "identity provider" in await expect_unauth(make_verifier(), mint(["viewer"]))


async def test_fastmcp_adapter_roundtrip(jwks_route: respx.Route, mint: Mint) -> None:
    adapter = KeycloakTokenVerifier(make_verifier())
    assert await adapter.verify_token("garbage") is None
    at = await adapter.verify_token(mint(["admin"], sub="u5"))
    assert at is not None and at.resource == AUDIENCE and at.subject == "u5"
    c = claims_from_access_token(at)
    assert c.roles == {"admin"} and c.subject == "u5"
