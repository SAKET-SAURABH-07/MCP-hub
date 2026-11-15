from pathlib import Path

import pytest

from mcp_hub.rbac import ADMIN_TOOLS, ROLE_TOOLS, allowed_tools, is_allowed, render_matrix
from mcp_hub.tools import TOOLS


def test_every_registered_tool_is_in_the_matrix_and_vice_versa() -> None:
    assert set(TOOLS) == set(ADMIN_TOOLS)


def test_nine_tools() -> None:
    assert len(TOOLS) == 9


@pytest.mark.parametrize("tool", sorted(ROLE_TOOLS["viewer"]))
def test_viewer_gets_only_read_tools(tool: str) -> None:
    assert not TOOLS[tool].write
    assert is_allowed(["viewer"], tool)


def test_every_write_tool_needs_developer_or_admin() -> None:
    for name, spec in TOOLS.items():
        if spec.write:
            assert not is_allowed(["viewer"], name), name


def test_bulk_is_admin_only() -> None:
    assert is_allowed(["admin"], "bulk_create_issues_from_csv")
    assert not is_allowed(["developer"], "bulk_create_issues_from_csv")


def test_no_roles_and_unknown_roles_get_nothing() -> None:
    assert allowed_tools([]) == frozenset()
    assert allowed_tools(["superuser", "root"]) == frozenset()


def test_unknown_tool_denied() -> None:
    assert not is_allowed(["admin"], "delete_everything")


def test_roles_union() -> None:
    assert allowed_tools(["viewer", "developer"]) == ROLE_TOOLS["developer"]


def test_docs_matrix_matches_code() -> None:
    doc = (Path(__file__).parent.parent / "docs" / "RBAC.md").read_text()
    assert render_matrix() in doc, "docs/RBAC.md drifted from rbac.ROLE_TOOLS; regenerate it"
