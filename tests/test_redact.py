import pytest

from mcp_hub.redact import REDACTED, redact, redact_text

SECRETS = [
    "ghp_" + "A1b2C3d4E5" * 4,
    "github_pat_" + "A1b2C3d4E5" * 5,
    "xoxb-123456789012-abcdefghijkl",
    "ATATT3xFfGF0" + "abcdEFGH12" * 3,
    "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJlX2hlcmU",
    "Bearer abcdefghijklmnop12345",
    "Basic dXNlcjpwYXNzd29yZA==",
    "AKIAIOSFODNN7EXAMPLE",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_value_patterns_are_redacted(secret: str) -> None:
    out = redact_text(f"please use {secret} for this")
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "text", ["password=hunter2hunter2", "api_key: sk_live_1234567", "token = abcd1234"]
)
def test_key_value_secrets(text: str) -> None:
    out = redact_text(text)
    assert REDACTED in out
    assert "hunter2" not in out and "1234567" not in out and "abcd1234" not in out


def test_sensitive_keys_redacted_at_any_depth() -> None:
    obj = {"a": {"Authorization": "Bearer xyz", "n": [{"slack_token": "x"}, "ok"]}, "title": "hi"}
    out = redact(obj)
    assert out["a"]["Authorization"] == REDACTED
    assert out["a"]["n"][0]["slack_token"] == REDACTED
    assert out["title"] == "hi" and out["a"]["n"][1] == "ok"


def test_non_secret_text_untouched() -> None:
    assert redact_text("Fix login bug in auth module #42") == "Fix login bug in auth module #42"


def test_original_object_not_mutated() -> None:
    obj = {"password": "p"}
    redact(obj)
    assert obj == {"password": "p"}
