# RBAC matrix

Roles come from the Keycloak realm role list in the access token (`realm_access.roles`).
Unknown roles grant nothing. A caller with no known role can call nothing.
Roles are additive (union). Authorization is **deny by default**: a tool not listed for a role is forbidden.

| Tool | viewer | developer | admin |
|---|---|---|---|
| `bulk_create_issues_from_csv` | ❌ | ❌ | ✅ |
| `github_create_issue` | ❌ | ✅ | ✅ |
| `github_get_pull_request` | ✅ | ✅ | ✅ |
| `github_list_issues` | ✅ | ✅ | ✅ |
| `jira_create_issue` | ❌ | ✅ | ✅ |
| `jira_search_issues` | ✅ | ✅ | ✅ |
| `jira_transition_issue` | ❌ | ✅ | ✅ |
| `slack_list_channels` | ✅ | ✅ | ✅ |
| `slack_post_message` | ❌ | ✅ | ✅ |

## Independent of role: scope allowlists

Even `admin` can only touch resources on the server's allowlists
(`MCPHUB_ALLOWED_REPOS`, `MCPHUB_ALLOWED_JIRA_PROJECTS`, `MCPHUB_ALLOWED_SLACK_CHANNELS`).
Empty list = nothing allowed. Violations return `OUT_OF_SCOPE` and are audited as `denied`.

## Test that keeps this file honest

`tests/test_rbac.py::test_docs_matrix_matches_code` fails if the table above differs from
`mcp_hub.rbac.ROLE_TOOLS`. Regenerate with:

```bash
python -c "from mcp_hub.rbac import render_matrix; print(render_matrix())"
```
