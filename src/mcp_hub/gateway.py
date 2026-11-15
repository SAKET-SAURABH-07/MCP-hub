"""The single choke point every tool call passes through:

  authenticate -> rate limit -> authorize (RBAC) -> validate -> [idempotency] -> handler -> audit

Every call, including denied and failed ones, writes exactly one audit row.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import ValidationError

from .auth import Claims
from .errors import ErrorCode, ToolError
from .idempotency import fingerprint_args, make_key
from .rbac import is_allowed
from .services import Services
from .tools import TOOLS

_DENIED = {ErrorCode.FORBIDDEN, ErrorCode.UNAUTHENTICATED, ErrorCode.OUT_OF_SCOPE}


async def invoke(
    svc: Services, claims: Claims | None, tool: str, raw_args: dict[str, Any]
) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    status, code, detail = "ok", None, None
    try:
        return await _pipeline(svc, claims, tool, raw_args)
    except ToolError as exc:
        status = "denied" if exc.code in _DENIED else "error"
        code, detail = exc.code.value, exc.message
        raise
    except Exception as exc:  # never leak internals to the client
        status, code, detail = "error", ErrorCode.INTERNAL.value, f"{type(exc).__name__}: {exc}"
        raise ToolError(ErrorCode.INTERNAL, "internal error") from None
    finally:
        svc.audit.record(
            request_id=request_id,
            subject=claims.subject if claims else "anonymous",
            username=claims.username if claims else "anonymous",
            roles=sorted(claims.roles) if claims else [],
            tool=tool,
            status=status,
            error_code=code,
            latency_ms=(time.perf_counter() - started) * 1000,
            args=raw_args,
            detail=detail,
        )


async def _pipeline(
    svc: Services, claims: Claims | None, tool: str, raw_args: dict[str, Any]
) -> dict[str, Any]:
    if claims is None:
        raise ToolError(ErrorCode.UNAUTHENTICATED, "authentication required")
    spec = TOOLS.get(tool)
    if spec is None:
        raise ToolError(ErrorCode.UNKNOWN_TOOL, f"unknown tool {tool!r}")

    ok, retry_after = svc.limiter.try_acquire(claims.subject)
    if not ok:
        raise ToolError(ErrorCode.RATE_LIMITED, f"rate limit exceeded; retry in {retry_after:.0f}s")
    if not is_allowed(claims.roles, tool):
        raise ToolError(ErrorCode.FORBIDDEN, f"your role cannot use {tool}")
    try:
        args = spec.model.model_validate(raw_args)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ToolError(
            ErrorCode.INVALID_ARGUMENTS, f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
        ) from None

    if not spec.idempotent:
        return await spec.handler(svc, claims, args)

    dump = args.model_dump(exclude={"idempotency_key"})
    explicit = getattr(args, "idempotency_key", None)
    key = make_key(claims.subject, tool, dump, explicit)
    # Without an explicit key the args ARE the key, so a fingerprint adds nothing.
    outcome, prior = svc.idempotency.begin(key, fingerprint_args(dump) if explicit else None)
    if outcome == "conflict":
        raise ToolError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "idempotency_key was already used for a different request",
        )
    if outcome == "done" and prior is not None:
        return {**prior, "idempotent_replay": True}
    if outcome == "pending":
        raise ToolError(ErrorCode.DUPLICATE_IN_PROGRESS, "an identical request is in progress")
    try:
        result = await spec.handler(svc, claims, args)
    except BaseException:
        svc.idempotency.abort(key)
        raise
    svc.idempotency.complete(key, result)
    return result
