# Native MCP testing

This unreleased branch adds a native MCP server for ChatGPT to the existing FastAPI application. It uses the official Python MCP SDK 2.2.0, Streamable HTTP at `/mcp`, and Timeboard accounts for individual OAuth authorization. No OpenAI API key is needed. The published application version remains 00.13.02 until maintainers choose a release.

## Run an isolated instance

Use synthetic tasks and users with a separate database. The standalone Compose file uses its own image tag, loopback port 18888, and data subdirectory. Docker and an HTTPS reverse proxy are prerequisites.

```sh
git clone --branch feature/native-mcp-testing https://github.com/paulkakell/timeboardapp.git timeboardapp-mcp
cd timeboardapp-mcp
export MCP_TEST_BASE_URL=https://timeboard-test.example.com
# Match these IDs to the private data directory's owner.
export PUID=1000 PGID=1000
sudo install -d -m 700 -o "$PUID" -g "$PGID" data/mcp-testing
docker compose -p timeboard-mcp-test -f docker-compose.mcp-test.yml config -q
docker compose -p timeboard-mcp-test -f docker-compose.mcp-test.yml up -d --build
docker compose -p timeboard-mcp-test -f docker-compose.mcp-test.yml exec timeboardapp cat /data/initial-admin-password.txt
```

Configure the test hostname to proxy to `127.0.0.1:18888`, preserve the request Host, and terminate publicly trusted TLS. The origin must have no path prefix. Sign in on the HTTPS origin, change the generated administrator password, and remove the initial credential file. Create disposable manager, subordinate, and unrelated accounts. Leave live notification services unconfigured during initial testing.

This file is standalone: do not combine it with production Compose or point `MCP_TEST_DATA` at production data. Optional variables are `MCP_TEST_PORT`, `MCP_TEST_DATA`, and `MCP_TEST_REDIRECT_URIS` (comma-separated exact callback URLs). `docker compose ... down` stops this test project without deleting its bind-mounted data.

For a non-container checkout, follow CONTRIBUTING.md's isolated environment setup and set `TIMEBOARDAPP_BASE_URL` plus `TIMEBOARDAPP_MCP_ENABLED=true` before launching. You can instead enable `mcp.enabled` in your private settings YAML. The regular Compose file reads settings YAML; the dedicated test Compose file passes the MCP environment overrides explicitly. Public demo mode and MCP cannot be enabled together.

## Connect ChatGPT

1. Open ChatGPT's app creation settings with developer mode enabled where available for your account/workspace.
2. Supply `https://timeboard-test.example.com/mcp` as the remote MCP URL and choose OAuth. The server publishes resource and authorization-server metadata and supports dynamic client registration. No manually copied Timeboard bearer token is needed.
3. The default callback allowlist contains `https://chatgpt.com/connector_platform_oauth_redirect`. If your ChatGPT setup displays a different callback, copy that exact URI into `MCP_TEST_REDIRECT_URIS` or `mcp.allowed_redirect_uris` and restart before registering the client. Never configure a wildcard. Changing the allowlist can invalidate existing clients.
4. Complete the Timeboard sign-in and review the consent page. It names the account, requested permissions, and callback. Approval issues a short-lived authorization code with S256 PKCE; cancellation returns `access_denied`.
5. Add the app to a conversation and ask it to identify your account, list your tasks, and create one disposable task. Review write confirmations and returned results. Disconnect it through **Profile → Connected applications** and confirm further calls fail.

Account availability and workspace policy may restrict developer-mode apps. Successful local tests do not prove live ChatGPT acceptance, public TLS correctness, or directory approval. This branch has no embedded ChatGPT UI widget or app-directory submission.

Official references: [build an MCP server](https://developers.openai.com/apps-sdk/build/mcp-server), [authentication](https://developers.openai.com/apps-sdk/build/auth), and [connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt).

## Tools and access

| Scope | Tools | Boundary |
| --- | --- | --- |
| `tasks:read` | `get_profile`, `list_tasks`, `get_task`, `task_summary` | Connected account's tasks, including for administrators |
| `tasks:write` | `create_task`, `update_task`, `complete_task`, `archive_task`, `restore_task` | Connected account's tasks; existing recurrence/subtask/notification rules |
| `users:read` | `list_assignable_users` | Self and eligible subordinates; administrators may list all users; IDs/usernames only |
| `tasks:assign` | `assign_task` | Transfer an owned active standalone task to an eligible user |

Tools have typed input/output schemas, read/write annotations, and OAuth scope metadata. The transport accepts a valid MCP token; each tool independently enforces its scope, so read-only tools work over HTTP POST. The consent screen discloses all requested scopes. Omitted authorization scopes follow the registered client's scopes; clients can request just `tasks:read` for read-only testing.

Listing returns at most ten tasks with pagination and a response-size budget. Dates must include an offset. Task descriptions and URLs are untrusted data. `content_truncated` means an assistant must not overwrite complete stored content with a truncated read result. No arbitrary URL fetch, admin/database access, account creation, export, or permanent deletion tool is exposed.

Creation and assignment require a `request_key` (8-128 characters, letters/digits plus `. _ : -`). Reuse the exact key and payload after a timeout. The audit row and task mutation commit together. A creation retry returns the current owned task; a purged task retains a tombstone and is never recreated. Assignment retries return the original assignment receipt after checking current assignment permission, without moving the task again. Use a new key for a new intentional action.

Assignment does not grant read access to the recipient's other tasks. After transfer, the former owner cannot read that task through MCP. Tasks in a parent/child tree cannot be transferred. Existing followers are removed to prevent subsequent data exposure; the recipient gets one in-app assignment notification. Creation, updates, completion, archive and restore retain existing configured notification behavior. Completing a recurring task twice cannot spawn two recurrences; a retry after successful completion returns a conflict, so read current state instead.

Example `tools/call` arguments, sent by an authenticated MCP client:

```json
{
  "name": "create_task",
  "arguments": {
    "request_key": "test-task-20261001-001",
    "payload": {
      "name": "Verify ChatGPT connection",
      "task_type": "Testing",
      "due_date": "2026-10-01T14:00:00-06:00"
    }
  }
}
```

## Authentication and storage

Discovery is at `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-authorization-server`. Protocol endpoints are `/authorize`, `/register`, `/token`, and `/revoke`; browser consent is `/mcp/consent`. Bearer tokens are bound to the exact configured `/mcp` resource and never accepted as browser sessions or existing API credentials. Both authorization and token requests must supply that resource. PKCE is S256, authorization codes are single-use for two minutes, and pending consent expires after ten minutes.

Access tokens default to 15 minutes. Refresh tokens rotate with an absolute connection lifetime of 30 days. Reusing an authorization code or refresh token revokes its issued connection. Password changes, account deletion, JWT signing-key rotation, client removal, and profile disconnection invalidate relevant access. Callback/issuer changes require reconnecting. Public, `client_secret_post`, and `client_secret_basic` OAuth clients are supported.

Enabling MCP creates `mcp_clients`, `mcp_grants`, `mcp_connections`, `mcp_credentials`, and `mcp_audit` through the existing startup `create_all` operation. Existing tables are unchanged. Codes and access/refresh credentials are stored as SHA-256 hashes; confidential client secrets are derived with a separate HMAC context from the existing JWT signing key. Audit records hold actor/client/tool IDs, retry keys, payload hashes, task IDs, and timestamps, not task text or bearer tokens. Use opaque retry keys without private content.

JSON backups intentionally omit OAuth state and MCP audit/retry history. A fresh JSON restore requires reconnecting and does not preserve retry protection. A consistent full SQLite/settings backup does preserve it. Treat such a backup as sensitive and do not copy live authorization state into a publicly accessible test instance. Audit records are retained as retry tombstones; used refresh credentials are retained to detect reuse. Plan storage retention before wider deployment rather than deleting active retry records.

The feature defaults off. Bounds in `settings.sample.yml` cover token lifetime and client count. Browser consent/disconnection retain CSRF protection; only exact machine protocol paths bypass cookie CSRF. MCP requests are capped at 1 MiB, OAuth writes at 16 KiB, and integration traffic has a per-process/IP limit of 120 requests/minute. Authentication/registration/consent also use the existing 20 attempts/five-minute limiter. Shared proxy/client IPs share these buckets; production-wide throttling needs a trusted proxy policy and shared rate limiting.

## Validation and acceptance

```sh
python -m pytest -q
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
python -m pip check
python -m pip_audit --format=json
python -m bandit -r app -ll
python -m playwright install --with-deps chromium
python scripts/browser_smoke.py
python scripts/mcp_browser_smoke.py
python scripts/benchmark_mcp.py
```

`tests/test_mcp.py` drives real HTTP OAuth and MCP handlers, including PKCE/resource binding, discovery, read scopes, ownership, refresh/code replay, all client authentication methods, CSRF, revocation, restart persistence, assignment, recurrence, and concurrent creation retries. The lifecycle test starts the actual application on a legacy-shaped database with MCP disabled, enabled, and disabled again. Browser smoke scripts use disposable local instances and synthetic data; the MCP script uses a temporary TLS certificate and intercepts the callback locally, with no live ChatGPT traffic.

The benchmark compares 30 warm MCP and GPT Actions list requests over the same 100 synthetic tasks and ten-item pages. It reuses the disposable test fixture and prints aggregate timing only. These in-process figures describe framework/authentication overhead, not public network latency or production capacity.

Before production use, test public HTTPS and actual ChatGPT login/refresh/disconnection, a second user's isolation, read-only permission rejection, assignment to an eligible subordinate, cancellation, and retry after an interrupted response. Real notification providers, load/multiple workers, container startup, and a matching full backup/restore need operator/CI verification. Keep the PR in draft while required checks are incomplete.

## Disable or roll back

Stop the disposable test project with `docker compose -p timeboard-mcp-test -f docker-compose.mcp-test.yml down`. To disable MCP on another branch deployment, set `TIMEBOARDAPP_MCP_ENABLED=false` (or `mcp.enabled: false` without an environment override) and restart. All MCP/OAuth/connection routes disappear; existing APIs and the website remain available. Disabling does not revoke stored grants, so revoke connections before disabling if re-enablement should require new consent.

The five extra tables can remain when rolling source back to 00.13.02; the older application ignores them. Back up the full database/settings first, keep a matching source/image snapshot, and verify login and representative tasks after rollback. Rolling back source does not undo task changes made during testing. Do not run older pre-Argon2 releases against a migrated database; their separate password-storage rollback constraints still apply.

Dependency locks were regenerated with pip-tools 7.6.1 in an isolated Python 3.12 tooling environment using `pip-compile --generate-hashes --strip-extras --output-file=requirements.txt requirements.in`, followed by the corresponding command for `requirements-dev.in`. Installation uses both locks with `--require-hashes`. Python 3.13 and container checks remain part of repository CI.
