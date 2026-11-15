import pytest

from mcp_hub.audit import connect
from mcp_hub.errors import ErrorCode, ToolError
from mcp_hub.idempotency import IdempotencyStore, fingerprint_args, make_key
from mcp_hub.scope import ScopePolicy


def test_scope_is_deny_by_default() -> None:
    p = ScopePolicy()
    for fn, arg in [(p.check_repo, "a/b"), (p.check_project, "ABC"), (p.check_channel, "C1")]:
        with pytest.raises(ToolError) as e:
            fn(arg)
        assert e.value.code is ErrorCode.OUT_OF_SCOPE


def test_scope_allows_listed_case_insensitively() -> None:
    p = ScopePolicy(["Acme/App"], ["sbx"], ["C0123456789"])
    p.check_repo("acme/app")
    p.check_project("SBX")
    p.check_channel("C0123456789")


def test_make_key_depends_on_subject_tool_args_and_explicit_key() -> None:
    a = make_key("u1", "t", {"x": 1}, None)
    assert a == make_key("u1", "t", {"x": 1}, None)
    assert a != make_key("u2", "t", {"x": 1}, None)
    assert a != make_key("u1", "t", {"x": 2}, None)
    assert make_key("u1", "t", {"x": 1}, "k") == make_key("u1", "t", {"x": 99}, "k")


def test_state_machine_new_pending_done() -> None:
    store = IdempotencyStore(connect(":memory:"))
    assert store.begin("k") == ("new", None)
    assert store.begin("k") == ("pending", None)
    store.complete("k", {"n": 1})
    assert store.begin("k") == ("done", {"n": 1})


def test_abort_allows_retry_but_never_removes_done() -> None:
    store = IdempotencyStore(connect(":memory:"))
    store.begin("k")
    store.abort("k")
    assert store.begin("k")[0] == "new"
    store.complete("k", {})
    store.abort("k")
    assert store.begin("k")[0] == "done"


def test_entries_expire() -> None:
    now = [1000.0]
    store = IdempotencyStore(connect(":memory:"), ttl=10, clock=lambda: now[0])
    store.begin("k")
    store.complete("k", {"a": 1})
    now[0] += 11
    assert store.begin("k")[0] == "new"


def test_reused_key_with_different_payload_conflicts() -> None:
    store = IdempotencyStore(connect(":memory:"))
    assert store.begin("k", fingerprint_args({"title": "a"})) == ("new", None)
    store.complete("k", {"n": 1})
    assert store.begin("k", fingerprint_args({"title": "a"})) == ("done", {"n": 1})
    assert store.begin("k", fingerprint_args({"title": "b"})) == ("conflict", None)


def test_fingerprint_is_order_independent_but_value_sensitive() -> None:
    assert fingerprint_args({"a": 1, "b": 2}) == fingerprint_args({"b": 2, "a": 1})
    assert fingerprint_args({"a": 1}) != fingerprint_args({"a": 2})


def test_rows_without_a_fingerprint_still_replay() -> None:
    """Keys written before fingerprinting existed must not start conflicting."""
    store = IdempotencyStore(connect(":memory:"))
    store.begin("k")  # legacy row: fingerprint NULL
    store.complete("k", {"n": 1})
    assert store.begin("k", fingerprint_args({"anything": True})) == ("done", {"n": 1})


def test_legacy_table_is_migrated_in_place() -> None:
    """A DB created before the fingerprint column must gain it rather than crash."""
    conn = connect(":memory:")
    conn.execute(
        "CREATE TABLE idempotency (key TEXT PRIMARY KEY, state TEXT NOT NULL,"
        " result_json TEXT, created REAL NOT NULL)"
    )
    conn.commit()
    store = IdempotencyStore(conn)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(idempotency)")}
    assert "fingerprint" in columns
    assert store.begin("k", fingerprint_args({"a": 1})) == ("new", None)
