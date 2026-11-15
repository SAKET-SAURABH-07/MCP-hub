"""Secret redaction applied to everything that is written to the audit log."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|cookie|credential)", re.I
)

_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub classic tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),  # GitHub fine-grained PAT
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),  # Slack tokens
    re.compile(r"\bATATT[A-Za-z0-9_\-=]{20,}\b"),  # Atlassian API tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWTs
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"(?i)((?:token|secret|password|api[_-]?key)\s*[=:]\s*)([^\s,;\"']{4,})"),
]


def redact_text(text: str) -> str:
    for pat in _VALUE_PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pat.sub(REDACTED, text)
    return text


def redact(obj: Any) -> Any:
    """Return a deep copy of obj with secrets removed (by key name and by value shape)."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEYS.search(k) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list | tuple):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj
