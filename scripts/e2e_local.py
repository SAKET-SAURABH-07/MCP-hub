# ruff: noqa
# mypy: ignore-errors
"""Local end-to-end check (no Docker/Keycloak needed): run  python scripts/e2e_local.py
Real processes + real sockets: fake Keycloak JWKS, real mcp-hub server, real MCP HTTP client."""

import asyncio, json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
jwk.update(kid="k1", use="sig", alg="RS256")
ISS = "http://127.0.0.1:18080/realms/mcp-hub"


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"keys": [jwk]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


threading.Thread(target=HTTPServer(("127.0.0.1", 18080), H).serve_forever, daemon=True).start()


def tok(roles):
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "u-" + roles[0],
            "iss": ISS,
            "aud": "mcp-hub",
            "iat": now,
            "exp": now + 300,
            "preferred_username": roles[0],
            "realm_access": {"roles": roles},
        },
        key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )


env = {
    **os.environ,
    "MCPHUB_ISSUER": ISS,
    "MCPHUB_JWKS_URL": ISS + "/protocol/openid-connect/certs",
    "MCPHUB_AUDIT_DB": "/tmp/e2e_audit.db",
    "MCPHUB_ALLOWED_REPOS": '["acme/app"]',
    "MCPHUB_PUBLIC_URL": "http://127.0.0.1:18000",
}
os.path.exists("/tmp/e2e_audit.db") and os.remove("/tmp/e2e_audit.db")
p = subprocess.Popen(
    ["mcp-hub", "--transport", "http", "--port", "18000"],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
)
time.sleep(3)


async def session(token, fn):
    async with streamablehttp_client(
        "http://127.0.0.1:18000/mcp", headers={"Authorization": f"Bearer {token}"} if token else {}
    ) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


async def main():
    for role in ("viewer", "developer", "admin"):
        names = await session(tok([role]), lambda s: s.list_tools())
        print(f"{role:9s} sees {len(names.tools)} tools")
    res = await session(
        tok(["viewer"]),
        lambda s: s.call_tool("github_create_issue", {"repo": "acme/app", "title": "x"}),
    )
    print("viewer create_issue ->", "ERROR" if res.isError else "ok", "|", res.content[0].text)
    res = await session(
        tok(["admin"]), lambda s: s.call_tool("github_list_issues", {"repo": "evil/other"})
    )
    print("admin out-of-scope  ->", res.content[0].text)
    try:
        await session(None, lambda s: s.list_tools())
        print("NO TOKEN: connected (BAD)")
    except BaseException as e:
        print("no token            -> rejected:", type(e).__name__)
    try:
        await session("garbage", lambda s: s.list_tools())
        print("BAD TOKEN: connected (BAD)")
    except BaseException as e:
        print("garbage token       -> rejected:", type(e).__name__)


try:
    asyncio.run(main())
finally:
    p.terminate()
import sqlite3

for r in sqlite3.connect("/tmp/e2e_audit.db").execute(
    "select username,tool,status,error_code from audit_log"
):
    print("audit:", r)
