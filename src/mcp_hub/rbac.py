"""Role-based access control. Deny by default: a tool not listed for a role is forbidden.

docs/RBAC.md is generated from / checked against ROLE_TOOLS by tests/test_rbac.py.
"""

from __future__ import annotations

from collections.abc import Iterable

VIEWER_TOOLS = frozenset(
    {"github_list_issues", "github_get_pull_request", "jira_search_issues", "slack_list_channels"}
)
DEVELOPER_TOOLS = VIEWER_TOOLS | frozenset(
    {"github_create_issue", "jira_create_issue", "jira_transition_issue", "slack_post_message"}
)
ADMIN_TOOLS = DEVELOPER_TOOLS | frozenset({"bulk_create_issues_from_csv"})

ROLE_TOOLS: dict[str, frozenset[str]] = {
    "viewer": VIEWER_TOOLS,
    "developer": DEVELOPER_TOOLS,
    "admin": ADMIN_TOOLS,
}
KNOWN_ROLES = frozenset(ROLE_TOOLS)


def allowed_tools(roles: Iterable[str]) -> frozenset[str]:
    out: frozenset[str] = frozenset()
    for role in roles:
        out |= ROLE_TOOLS.get(role, frozenset())  # unknown roles grant nothing
    return out


def is_allowed(roles: Iterable[str], tool: str) -> bool:
    return tool in allowed_tools(roles)


def render_matrix() -> str:
    """Markdown table of tool x role; docs/RBAC.md must contain this exact text."""
    roles = list(ROLE_TOOLS)
    tools = sorted(ADMIN_TOOLS)
    lines = ["| Tool | " + " | ".join(roles) + " |", "|---|" + "---|" * len(roles)]
    for t in tools:
        cells = ["✅" if t in ROLE_TOOLS[r] else "❌" for r in roles]
        lines.append(f"| `{t}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)
