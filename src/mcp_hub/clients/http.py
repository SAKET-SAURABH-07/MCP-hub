"""Shared HTTP behaviour for upstream APIs: timeouts, retry with backoff on 429/5xx,
and mapping of upstream failures to stable ToolError codes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..errors import ErrorCode, ToolError

RETRY_STATUSES = {429, 500, 502, 503, 504}


class UpstreamClient:
    def __init__(
        self,
        base_url: str,
        headers: dict[str, str],
        *,
        timeout: float = 15.0,
        retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
        auth: httpx.Auth | tuple[str, str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout, transport=transport, auth=auth
        )
        self._retries = retries
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        last: httpx.Response | None = None
        for attempt in range(self._retries + 1):
            try:
                resp = await self._client.request(method, path, params=params, json=json)
            except httpx.TransportError as exc:
                if attempt == self._retries:
                    raise ToolError(ErrorCode.UPSTREAM_ERROR, "upstream unreachable") from exc
                await self._sleep(0.5 * 2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self._retries:
                retry_after = resp.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else 0.5 * 2**attempt
                await self._sleep(min(delay, 30.0))
                last = resp
                continue
            last = resp
            break
        assert last is not None
        return self._handle(last)

    @staticmethod
    def _handle(resp: httpx.Response) -> Any:
        s = resp.status_code
        if s in (401, 403) and not _is_rate_limit(resp):
            raise ToolError(ErrorCode.UPSTREAM_AUTH, f"upstream rejected credentials ({s})")
        if s == 404:
            raise ToolError(ErrorCode.UPSTREAM_NOT_FOUND, "upstream resource not found")
        if s == 429 or _is_rate_limit(resp):
            raise ToolError(ErrorCode.UPSTREAM_RATE_LIMITED, "upstream rate limit exceeded")
        if s >= 400:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, f"upstream error ({s})")
        if s == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ToolError(ErrorCode.UPSTREAM_ERROR, "upstream returned non-JSON") from exc


def _is_rate_limit(resp: httpx.Response) -> bool:
    return resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0"
