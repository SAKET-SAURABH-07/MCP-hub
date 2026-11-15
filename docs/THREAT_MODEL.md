# Threat model (STRIDE-lite)

Assets: upstream credentials (GitHub/Jira/Slack), the ability to write to those systems, the audit log.
Trust boundaries: MCP client ↔ server (bearer JWT), server ↔ upstream APIs, server ↔ SQLite file.
Actors: legitimate users (3 roles), a malicious or confused user, a prompt-injected LLM acting *as* a user, an external attacker.

| # | Threat | Control | Test |
|---|---|---|---|
| T1 | Forged / unsigned token (`alg=none`, HS256 confusion, other key) | Algorithm allowlist RS256/ES256; signature checked against Keycloak JWKS | `test_auth.py` alg_none, hs256, other_key |
| T2 | Expired or other-audience / other-issuer token replay | `exp`, `aud`, `iss` required and checked | `test_auth.py` expired, aud/iss |
| T3 | Privilege escalation by calling a tool above your role | RBAC in gateway, deny by default, tools hidden from `tools/list` | `test_gateway.py` forbidden, `test_server.py` listing |
| T4 | Probing schemas of tools you can't use | RBAC check runs before validation | `test_forbidden_precedes_validation` |
| T5 | Acting on resources outside the intended sandbox (any repo, project, channel) | Server-side allowlists, empty = deny; Slack list filtered | `test_out_of_scope_even_for_admin` |
| T6 | Path traversal / injection via identifiers | Strict regex on repo, project, issue key, channel; `extra=forbid` | `test_invalid_arguments`, bulk `../bad` |
| T7 | JQL injection | No raw JQL; values quoted and escaped | `test_jql_is_quoted_and_escaped` |
| T8 | Secrets leaking into audit log (tokens in args, headers, error text) | `redact()` by key name and value shape on args, detail, username | `test_audit_redaction_proven_end_to_end`, `test_redact.py` |
| T9 | Internal details leaking to clients | Unexpected exceptions become `INTERNAL: internal error`; detail only in audit | `test_internal_errors_do_not_leak_details` |
| T10 | Abuse / runaway agent loops | Per-user token bucket; bulk row & byte caps; a bulk run is charged one token **per row**, so one call cannot outrun the bucket | `test_rate_limit`, `test_bulk_charges_the_rate_limiter_per_row` |
| T11 | Duplicate writes from retries | Idempotency store (per user) | `test_idempotent_*` |
| T15 | Caller recycles one `idempotency_key` for a different payload and is handed the earlier result as if their new request succeeded | Args are fingerprinted against the stored key; a mismatch is refused with `IDEMPOTENCY_KEY_REUSED` instead of replaying | `test_reused_idempotency_key_with_different_args_is_rejected` |
| T12 | Mass-write by mistake | Bulk is admin-only, dry-run default, all-or-nothing validation | `test_dry_run_is_default…`, `test_invalid_row_blocks…` |
| T13 | Hammering Keycloak with random `kid`s | JWKS refetch rate-limited; stale keys served on outage | `test_unknown_kid_refetches_once…` |
| T14 | Tampering with the audit log | Not fully mitigated: SQLite file is writable by the server user. Mitigation path: ship rows to append-only external storage. | n/a (accepted risk) |

## Prompt injection
An LLM using this server runs with the *user's* role, so a prompt-injected model can do at most what that user may do, only on allowlisted resources, rate-limited and audited. Give the model a **viewer** token unless writes are needed.

## Accepted / out of scope
- Upstream tokens are shared service credentials (not per-user). Use least-privilege tokens scoped to the sandbox.
- TLS termination is expected in front of the server in any non-local deployment.
- No token revocation check beyond `exp` (keep access-token lifetimes short in Keycloak).
