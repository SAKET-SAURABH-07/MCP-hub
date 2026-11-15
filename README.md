# mcp-hub

One **MCP server** that gives Claude (or any MCP client) nine GitHub, Jira and Slack tools, behind
**Keycloak login (OIDC/JWT)**, **role-based permissions** (viewer / developer / admin), **scope allowlists**,
an **audit log with secret redaction**, rate limiting and idempotent writes.

```
MCP client ──Bearer JWT──▶ FastMCP ──▶ gateway: authn → rate limit → RBAC → validate → idempotency → handler → audit
                                                        │
                                         GitHub · Jira Cloud · Slack · SQLite
```

| Tool | viewer | developer | admin |
|---|:-:|:-:|:-:|
| github_list_issues, github_get_pull_request, jira_search_issues, slack_list_channels | ✅ | ✅ | ✅ |
| github_create_issue, jira_create_issue, jira_transition_issue, slack_post_message | ❌ | ✅ | ✅ |
| bulk_create_issues_from_csv (dry-run by default) | ❌ | ❌ | ✅ |

Each role also only **sees** its own tools in `tools/list`. Full matrix: [docs/RBAC.md](docs/RBAC.md).

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,dashboard]"
cp .env.example .env      # fill in tokens + allowlists (sandbox repo/project/channel ONLY)

docker compose up -d      # Keycloak on :8080 with realm "mcp-hub" and users alice/bob/carol
mcp-hub --transport http --port 8000
```

Demo users (dev only, defined in `keycloak/realm-mcp-hub.json`): `alice` = viewer, `bob` = developer, `carol` = admin.

### Connect Claude
```bash
# Claude Code. Tokens last 15 min; re-run to refresh.
claude mcp add --transport http mcp-hub http://localhost:8000/mcp \
  --header "Authorization: Bearer $(python scripts/get_token.py bob)"
```
Ask Claude: "List open issues in your-user/your-sandbox-repo", then switch to alice's token and ask it to
create an issue: it will be refused, and the attempt appears in the audit log.

### No Docker? Prove the auth path locally
`python scripts/e2e_local.py` starts a fake JWKS endpoint + the real server and checks role-filtered tool lists,
denials, and rejection of missing/forged tokens.

## Configuration
All via `MCPHUB_*` env vars (see `.env.example`). **Allowlists default to empty = nothing is allowed.**

## Security design (details in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md))
- RS256/ES256 only; `exp`, `iss`, `aud`, `sub` required; JWKS cached, refetch rate-limited.
- Deny-by-default RBAC, **and** server-side scope allowlists that apply even to admins.
- Strict identifier patterns (no `../`, no raw JQL). Jira search is structured and the server builds escaped JQL.
- Audit rows pass through a redactor (key names + token shapes: GitHub, Slack, Atlassian, JWT, Bearer/Basic, AWS).
- Unexpected exceptions never leak to the client (`INTERNAL`); details go only to the audit log.
- A prompt-injected model can do at most what the user's role + allowlists allow. Give models a viewer token by default.

## All-in-one web frontend

`web/index.html` is a single page for everyone: a plain-English **Start here** tour with a try-it
simulator (pick a person, pick a request, see allowed or blocked), the technical pipeline and role
matrix, live audit charts, and a **Connect** section where you sign in with Keycloak and copy a
ready-made `claude mcp add` command. Edit the four constants at the top of `web/portal.js`
(`ISSUER`, `CLIENT_ID`, `MCP_URL`, `SANDBOX`) if your setup differs. The simulator and charts work
on GitHub Pages; sign-in needs Keycloak running (`docker compose up -d`) and the page opened from
`localhost`.

## Dashboard & metrics

The dashboard is a static page (`web/`) over a JSON snapshot exported from the audit log, so it
hosts on GitHub Pages with no server and no database behind it. Chart.js and the fonts are
vendored — the page makes no third-party requests and works offline.

```bash
# 1. Something to show. Synthetic rows, clearly labelled as such in the UI.
python scripts/seed_demo.py --db data/demo_audit.db
python scripts/export_metrics.py --db data/demo_audit.db --source demo

# 2. Look at it.
python -m http.server 8777 --directory web     # → http://localhost:8777

# 3. Real numbers, once you have real usage.
python scripts/export_metrics.py --db data/audit.db --source live
python scripts/metrics.py                       # same numbers, printed for your resume
```

The page also explains the system: an animated request pipeline, a request simulator, the role
matrix and the idempotency key states. The matrix and simulator read `rbac` and `tools` from the
exported snapshot (taken from `rbac.py` and the tool registry), so they cannot drift from the server.

Add `--watch 5` to `export_metrics.py` for a live local view; the page re-reads the file.
`dashboard/app.py` (Streamlit) is still there for ad-hoc local poking.

### Publishing it
`.github/workflows/pages.yml` deploys `web/` on every push to `main` that touches it. Enable
**Settings → Pages → Source: GitHub Actions** once, then:

```bash
python scripts/export_metrics.py --db data/audit.db --source live
git add web/data/metrics.json && git commit -m "chore: refresh dashboard snapshot" && git push
```

The committed snapshot is what the world sees. Arguments are redacted at write time, but
**read the file before you commit it** — once you run against real accounts it contains real
usernames, repo names and Jira keys. Audit `.db` files are gitignored and never published.

## Quality gates
```bash
ruff check . && ruff format --check . && mypy && pytest     # pytest enforces 85% coverage
python evals/run_evals.py                                    # offline security eval set
python scripts/smoke_live.py --yes --repo ... --project ... --channel ...   # REAL accounts
```
CI (`.github/workflows/ci.yml`) runs the same.

## Known limits
- Upstream credentials are shared service tokens, not per-user. Use least-privilege tokens.
- The audit DB is a local SQLite file (tamper-resistance is an accepted risk, T14).
- Not yet verified here against your real Keycloak/GitHub/Jira/Slack accounts: run `smoke_live.py`.
- Jira search uses `/rest/api/3/search/jql` (the old `/search` endpoint was removed by Atlassian).

## Layout
`src/mcp_hub/` (gateway, auth, rbac, audit, clients, tools) · `tests/` · `evals/` · `scripts/` ·
`web/` (static dashboard published to Pages) · `dashboard/` (Streamlit, local only) ·
`docs/` (TRD, threat model, RBAC, resume notes) · `keycloak/` · `docker-compose.yml`
