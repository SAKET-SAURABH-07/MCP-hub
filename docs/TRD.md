# Technical Requirements Document: mcp-hub

## 1. Goal
One MCP server that lets Claude (or any MCP client) use GitHub, Jira and Slack through nine tools,
with real login (Keycloak OIDC/JWT), role-based permissions, an audit trail, and safe defaults.

## 2. Scope
In: 9 tools, JWT auth, RBAC (viewer/developer/admin), scope allowlists, audit log with secret
redaction, per-user rate limiting, idempotent writes, bulk CSV import, Streamlit dashboard, CI.
Out: multi-tenant SaaS, OAuth dynamic client registration, UI for managing roles (use Keycloak),
production HA, write access to arbitrary repos/projects/channels.

## 3. Architecture
```
MCP client ──HTTP + Bearer JWT──▶ FastMCP (mcp_hub.server)
                                     │  every tool is a thin wrapper
                                     ▼
                                gateway.invoke()
   authenticate → rate limit → RBAC → validate → idempotency → handler → audit
                                     │
              ┌──────────────┬───────┴───────┬──────────────┐
              ▼              ▼               ▼              ▼
          GitHub REST    Jira Cloud v3    Slack Web API   SQLite (audit + idempotency)
```
All security logic lives in `gateway.py`, so stdio and HTTP behave identically.
The server also overrides `tools/list` so each role only *sees* the tools it can call.

## 4. Tools
| Tool | Type | Notes |
|---|---|---|
| github_list_issues | read | PRs filtered out of the issues API |
| github_create_issue | write | idempotent |
| github_get_pull_request | read | summary fields only |
| jira_search_issues | read | structured params, server builds quoted JQL; uses `/rest/api/3/search/jql` |
| jira_create_issue | write | description sent as Atlassian Document Format |
| jira_transition_issue | write | resolves transition name → id first |
| slack_list_channels | read | only allowlisted channels are returned |
| slack_post_message | write | channel ID must be allowlisted |
| bulk_create_issues_from_csv | write, admin | dry-run default; validate-all-then-write; per-row idempotency |

## 4.1 Why Jira search is not raw JQL
Raw JQL would let a caller query projects outside the allowlist (`project = SECRET`). The tool takes
`project`, `text`, `status` and builds escaped JQL itself.

## 5. Non-functional requirements
- Auth: RS256/ES256 only; verify signature, `exp`, `iss`, `aud`, `sub`. JWKS cached 10 min, refetched on unknown `kid` at most every 30 s; stale keys served if Keycloak is briefly down.
- Reliability: upstream calls have timeouts and retry (429/5xx, `Retry-After` honoured, exponential backoff).
- Errors: stable codes (`FORBIDDEN`, `OUT_OF_SCOPE`, `RATE_LIMITED`, `UPSTREAM_AUTH`, …); internals never leak.
- Audit: one row per call (including denied/failed): who, roles, tool, status, latency, redacted args.
- Rate limit: token bucket per user (default burst 20, 1/s).
- Quality gates: ruff, mypy --strict, pytest with 85% coverage gate, in CI.

## 6. Data
SQLite file (`MCPHUB_AUDIT_DB`): `audit_log` (append-only by convention) and `idempotency`
(key → pending/done + stored result, TTL 1 h).

## 7. Key decisions
| Decision | Why |
|---|---|
| Pin `mcp>=1.20,<2` | `mcp` 2.x changed the server layer; FastMCP API in 1.x is the target. |
| Gateway, not decorators | One testable pipeline instead of nine copies of the checks. |
| Roles from token, scopes from server config | Roles = who you are; scopes = what this deployment may touch. Both must pass. |
| Allowlists default to empty | A misconfigured deployment fails closed. |
| SQLite | Free tier, zero ops, enough for one server. Swap for Postgres if scaled out. |
| Idempotency key = hash(user, tool, args) unless caller supplies one | A retried call after a timeout never double-creates. |
| A supplied key is bound to an args fingerprint | Reusing one key for a different payload is refused, not silently replayed — a client must never be told a request succeeded when a different one did. |
| Bulk charges the rate limiter per row | The one tool that can make 50 upstream writes must not cost a single token. |
| Dashboard is a static page over an exported JSON snapshot | No extra service to host, publishable on GitHub Pages, and the same file renders live locally under `--watch`. |

## 8. Milestones (5 weeks, 8-10 h/week)
P0 docs + CI skeleton → P1 skeleton + GitHub tools → P2 Jira + Slack + idempotency →
P3 Keycloak auth + RBAC → P4 audit + redaction + rate limit → P5 bulk CSV + dashboard →
P6 README, demo GIF, eval set.

## 9. Definition of done
9 tools work on real accounts · JWT auth + 3 roles enforced · redaction proven by tests ·
CI green with coverage gate · TRD + threat model committed · dashboard live · Claude demo recorded.
