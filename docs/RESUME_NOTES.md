# Resume notes: add ONLY after you have run it for real

You said: "add only once built, using real numbers from my own runs." So this file gives
you the recipe, not numbers. Do not copy numbers from `scripts/seed_demo.py` (synthetic).

## Get the real numbers
```bash
pytest                               # test count + coverage % are printed at the end
python evals/run_evals.py            # "eval: N/N passed" (offline security cases)
python scripts/smoke_live.py --yes --repo you/sandbox --project SBX --channel C0123456789
python scripts/metrics.py            # real call counts and p50/p95 latency from YOUR audit log
python scripts/export_metrics.py --db data/audit.db --source live   # refresh the published dashboard
```

## Bullet template (fill the blanks from the commands above)
- Built an MCP server (Python 3.12, FastMCP) exposing **9** GitHub/Jira/Slack tools to Claude,
  secured with Keycloak OIDC/JWT and **3-role RBAC** (deny-by-default, per-resource allowlists).
- Wrote an audit pipeline (SQLite) with secret redaction **proven by tests**; N tests, X% coverage,
  CI gate at 85% (ruff, mypy --strict, pytest).
- Hardened for agent use: idempotent writes (keys bound to an args fingerprint), per-user rate limiting
  charged per bulk row, dry-run-by-default CSV import; **N/N** offline security evals;
  live p95 latency of ___ ms over ___ real calls.
- Built the audit dashboard as a dependency-free static page (vanilla JS + Chart.js) over an exported
  JSON snapshot, deployed on GitHub Pages: ___ (your live URL).

## Be ready to explain in an interview
Why gateway over decorators · why Jira search is structured, not raw JQL · why scopes are separate
from roles · why `mcp<2` is pinned · what the accepted risks in `docs/THREAT_MODEL.md` are (T14).
