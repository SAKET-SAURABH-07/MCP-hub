"""The streamable-HTTP app must reject requests without a valid bearer token."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import respx
from starlette.testclient import TestClient

from mcp_hub.server import build_verifier, create_server
from mcp_hub.services import Services

from .conftest import JWKS_URL

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_http_requires_valid_bearer(
    svc: Services, jwks: dict[str, Any], mint: Callable[..., str]
) -> None:
    server = create_server(svc, verifier=build_verifier(svc.settings))
    app = server.streamable_http_app()
    with respx.mock(assert_all_called=False) as m:
        m.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks))
        with TestClient(app, base_url="http://localhost:8000") as c:
            assert c.post("/mcp", json=INIT, headers=HEADERS).status_code == 401
            bad = {**HEADERS, "Authorization": "Bearer not-a-jwt"}
            assert c.post("/mcp", json=INIT, headers=bad).status_code == 401
            expired = {**HEADERS, "Authorization": f"Bearer {mint(['admin'], exp_in=-10)}"}
            assert c.post("/mcp", json=INIT, headers=expired).status_code == 401
            good = {**HEADERS, "Authorization": f"Bearer {mint(['viewer'])}"}
            assert c.post("/mcp", json=INIT, headers=good).status_code == 200
