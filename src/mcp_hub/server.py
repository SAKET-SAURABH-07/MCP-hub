"""FastMCP server. Each MCP tool is a thin wrapper over gateway.invoke(); all security
logic lives in the gateway so it is identical for stdio and HTTP."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import Tool as MCPTool
from pydantic import AnyHttpUrl

from .auth import (
    Claims,
    JWKSCache,
    JWTVerifier,
    KeycloakTokenVerifier,
    claims_from_access_token,
)
from .config import Settings
from .gateway import invoke
from .rbac import allowed_tools
from .services import Services

ClaimsGetter = Callable[[], Claims | None]


def make_claims_getter(static: Claims | None = None) -> ClaimsGetter:
    """HTTP: claims come from the verified bearer token. stdio/tests: a fixed identity."""

    def get() -> Claims | None:
        at = get_access_token()
        if at is not None:
            return claims_from_access_token(at)
        return static

    return get


class HubMCP(FastMCP):
    """FastMCP that only lists the tools the caller's roles may use."""

    def __init__(self, *args: Any, claims_getter: ClaimsGetter, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._claims_getter = claims_getter

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        claims = self._claims_getter()
        allowed = allowed_tools(claims.roles) if claims else frozenset()
        return [t for t in tools if t.name in allowed]


def create_server(
    svc: Services,
    *,
    static_claims: Claims | None = None,
    verifier: JWTVerifier | None = None,
) -> HubMCP:
    getter = make_claims_getter(static_claims)
    kwargs: dict[str, Any] = {}
    if verifier is not None:
        kwargs["token_verifier"] = KeycloakTokenVerifier(verifier)
        kwargs["auth"] = AuthSettings(
            issuer_url=AnyHttpUrl(svc.settings.issuer),
            resource_server_url=AnyHttpUrl(svc.settings.public_url),
            # JWTVerifier already enforces `aud`; Keycloak's aud is a client name, not this URL
            validate_token_resource=False,
        )

    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await svc.aclose()

    mcp = HubMCP("mcp-hub", claims_getter=getter, lifespan=lifespan, **kwargs)

    async def run(tool: str, args: dict[str, Any]) -> str:
        result = await invoke(svc, getter(), tool, args)
        return json.dumps(result, default=str)

    @mcp.tool(
        name="github_list_issues",
        description="List issues in an allowlisted GitHub repository (owner/name).",
    )
    async def github_list_issues(repo: str, state: str = "open", limit: int = 20) -> str:
        return await run("github_list_issues", {"repo": repo, "state": state, "limit": limit})

    @mcp.tool(
        name="github_create_issue",
        description="Create an issue in an allowlisted GitHub repository.",
    )
    async def github_create_issue(
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        return await run(
            "github_create_issue",
            {
                "repo": repo,
                "title": title,
                "body": body,
                "labels": labels or [],
                "idempotency_key": idempotency_key,
            },
        )

    @mcp.tool(name="github_get_pull_request", description="Get summary details of a pull request.")
    async def github_get_pull_request(repo: str, number: int) -> str:
        return await run("github_get_pull_request", {"repo": repo, "number": number})

    @mcp.tool(
        name="jira_search_issues",
        description="Search issues in an allowlisted Jira project by text and/or status.",
    )
    async def jira_search_issues(
        project: str, text: str = "", status: str = "", limit: int = 20
    ) -> str:
        return await run(
            "jira_search_issues",
            {"project": project, "text": text, "status": status, "limit": limit},
        )

    @mcp.tool(
        name="jira_create_issue", description="Create an issue in an allowlisted Jira project."
    )
    async def jira_create_issue(
        project: str,
        summary: str,
        description: str = "",
        issue_type: str = "Task",
        idempotency_key: str | None = None,
    ) -> str:
        return await run(
            "jira_create_issue",
            {
                "project": project,
                "summary": summary,
                "description": description,
                "issue_type": issue_type,
                "idempotency_key": idempotency_key,
            },
        )

    @mcp.tool(
        name="jira_transition_issue",
        description="Move a Jira issue to another workflow status, e.g. 'In Progress'.",
    )
    async def jira_transition_issue(
        issue_key: str, transition: str, idempotency_key: str | None = None
    ) -> str:
        return await run(
            "jira_transition_issue",
            {"issue_key": issue_key, "transition": transition, "idempotency_key": idempotency_key},
        )

    @mcp.tool(
        name="slack_list_channels",
        description="List the Slack channels this server is allowed to use.",
    )
    async def slack_list_channels(limit: int = 50) -> str:
        return await run("slack_list_channels", {"limit": limit})

    @mcp.tool(
        name="slack_post_message",
        description="Post a message to an allowlisted Slack channel (channel ID).",
    )
    async def slack_post_message(
        channel: str, text: str, idempotency_key: str | None = None
    ) -> str:
        return await run(
            "slack_post_message",
            {"channel": channel, "text": text, "idempotency_key": idempotency_key},
        )

    @mcp.tool(
        name="bulk_create_issues_from_csv",
        description="Admin only. Create many GitHub or Jira issues from CSV text. Dry run by default.",
    )
    async def bulk_create_issues_from_csv(
        target: str, destination: str, csv_text: str, dry_run: bool = True
    ) -> str:
        return await run(
            "bulk_create_issues_from_csv",
            {
                "target": target,
                "destination": destination,
                "csv_text": csv_text,
                "dry_run": dry_run,
            },
        )

    return mcp


def build_verifier(settings: Settings) -> JWTVerifier:
    jwks = JWKSCache(
        settings.jwks_url,
        ttl=settings.jwks_ttl_seconds,
        min_refresh=settings.jwks_min_refresh_seconds,
    )
    return JWTVerifier(jwks, settings.issuer, settings.audience)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mcp-hub")
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    ns = parser.parse_args()

    settings = Settings()
    svc = Services.from_settings(settings)
    verifier = build_verifier(settings)

    if ns.transport == "stdio":
        if not settings.stdio_token:
            raise SystemExit("stdio transport needs MCPHUB_STDIO_TOKEN (a Keycloak access token)")
        claims = asyncio.run(verifier.verify(settings.stdio_token))
        create_server(svc, static_claims=claims).run(transport="stdio")
    else:
        server = create_server(svc, verifier=verifier)
        server.settings.host, server.settings.port = ns.host, ns.port
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
