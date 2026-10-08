# TimeboardApp

A self-hosted task board with recurrence, nested subtasks, calendar and mobile views, tags, manager assignment, task following, notifications, and administrator tools. FastAPI, Jinja, SQLAlchemy/SQLite, and APScheduler power the application.

**Source version:** `00.14.01` (2026-10-08). This patch adds OAuth authorization-response issuer identification (RFC 9207) for ChatGPT connections. It preserves the experimental native MCP endpoint introduced in 00.14.00, its eleven scoped tools, and per-user consent. MCP is disabled by default. Existing private GPT Actions remain available. Upgrades from versions before 00.13.00 still require the documented authentication/deployment migration. Read [release notes](RELEASE_NOTES_00.14.01.md) and [versioning guidance](VERSIONING.md) before upgrading. A source release does not automatically upgrade existing application deployments or publish a container-registry image.

<!-- release-badges:start -->
[![Source version 00.14.01](docs/assets/badges/version.svg)](https://github.com/paulkakell/timeboardapp/releases/tag/v00.14.01)
[![CI on main](https://github.com/paulkakell/timeboardapp/actions/workflows/audit.yml/badge.svg?branch=main&event=push)](https://github.com/paulkakell/timeboardapp/actions/workflows/audit.yml?query=branch%3Amain)
[![Tested Python 3.12 and 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)](https://github.com/paulkakell/timeboardapp/blob/main/.github/workflows/audit.yml)
[![MIT license](https://img.shields.io/badge/license-MIT-blue)](https://github.com/paulkakell/timeboardapp/blob/main/LICENSE)
[![Private GPT Actions](https://img.shields.io/badge/ChatGPT-private%20GPT%20Actions-blue)](https://timeboardapp.com/docs/chatgpt.html)
[![Native MCP (opt-in)](https://img.shields.io/badge/MCP-opt--in-blue)](https://timeboardapp.com/docs/chatgpt.html#native-mcp)
<!-- release-badges:end -->

## Start locally

```sh
git clone --branch v00.14.01 https://github.com/paulkakell/timeboardapp.git
cd timeboardapp
cp .env.example .env
# Linux example: match PUID and PGID in .env.
sudo install -d -m 700 -o 1000 -g 1000 data
docker compose up -d --build
docker compose exec timeboardapp cat /data/initial-admin-password.txt
```

Visit `http://localhost:8888`, sign in as `admin`, and change the generated password. After confirming the new password works, remove the initial credential file. Application logs no longer disclose the password. Recovery is an explicit operator command: `docker compose exec timeboardapp python -m app.cli reset-admin`. This command intentionally prints its generated password to the operator's terminal.

Compose now has a stable `timeboardapp` service, defaults to loopback exposure, and needs no pre-created external networks. The container runs as the configured PUID/PGID, drops capabilities, and uses a read-only root filesystem. Pre-create the persistent directory with matching ownership and keep it private.

## Configuration and upgrades

`TIMEBOARDAPP_SETTINGS` defaults to `/data/settings.yml`. `TIMEBOARDAPP_BASE_URL` sets the public application origin, for example `https://tasks.example.com`; it is required for password-reset links, the private GPT schema, and native MCP. MCP requires publicly trusted HTTPS with no path prefix. Set application display time in `app.timezone`; container `TZ` alone does not change it. Email, logging, WNS, and backup settings are database-backed administrator settings, seeded from legacy YAML where applicable.

Before upgrading, back up the full SQLite database and settings consistently and retain the previous source/image. In your existing checkout, fetch tags and select the release with `git fetch origin --tags` and `git switch --detach v00.14.01`, then run `docker compose up -d --build --force-recreate`. To follow current source instead, use `git switch main` and `git pull --ff-only origin main` before rebuilding. Preserve local configuration and review local edits before changing revisions. Compose builds the image locally from the checkout; `docker compose pull` or a container restart alone does not install this source release. The [MCP setup guide](MCP_TESTING.md#enable-mcp-on-an-existing-docker-deployment) covers enabling it on an existing deployment.

Since 00.13.00, the source uses Argon2id for new passwords, with transparent migration from existing PBKDF2-SHA256 hashes on successful login. Upgrades from before 00.13.00 invalidate existing JWTs/browser sessions; sign in again. Password changes/resets invalidate issued credentials. A rollback to a pre-00.13.00 image also needs the matching pre-upgrade database/settings snapshot because the old password library cannot read migrated Argon2id hashes. Back up and test restoration before updating production.

## API and ChatGPT

The deployed application provides `/docs` and `/openapi.json`. Task listings are paginated (`limit` default 100, maximum 200; `offset`). Closing a parent with open subtasks returns a conflict instead of a server error. The profile endpoint `PATCH /api/users/me` precedes numeric user routes. Completion claims its database state before spawning a recurrence, preventing duplicate concurrent completion.

The separate private GPT Actions interface exposes eight owner-only task operations at `/api/chatgpt`. Create a revocable read-only token by default through `POST /api/integrations/tokens`, then use API-key/Bearer authentication in a **private** custom GPT. Import `/openapi-chatgpt.json` from the HTTPS application deployment. Tokens are shown once, hashed at rest, scoped, expiring, and unable to access administrator APIs. An administrator's integration token still accesses only that administrator's own tasks.

Version 00.14.01 includes an **experimental native MCP endpoint** at `/mcp`, with individual OAuth consent, eleven task/account/assignment tools, and profile controls to disconnect applications. It is disabled by default. Set `TIMEBOARDAPP_MCP_ENABLED=true` and `TIMEBOARDAPP_BASE_URL=https://tasks.example.com` in `.env` when using this release's Compose file, then recreate the container. A nonempty MCP environment flag overrides `mcp.enabled` in settings YAML; an empty or omitted flag preserves that setting. For custom Compose files, explicitly pass the variables under the service's `environment` block. Follow the [MCP setup and testing guide](MCP_TESTING.md), including its separate deployment for isolated tests. Existing private GPT Actions continue to use their existing interface.

OAuth discovery now advertises `authorization_response_iss_parameter_supported: true`, and approval, cancellation, and authorization-error callbacks carry one `iss` value matching the published issuer exactly. This supports the stable ChatGPT callback already in the default exact allowlist. Preserve callback-specific URLs for existing connections and any explicit `TIMEBOARDAPP_MCP_REDIRECT_URIS` override. See [issuer verification](MCP_ISSUER_TESTING.md) and [connection troubleshooting](MCP_TESTING.md#troubleshoot-a-chatgpt-connection).

A real ChatGPT/TLS acceptance test remains necessary; local protocol tests do not establish live account compatibility. This application does not serve OpenAI models. Never place access tokens in a GPT prompt, public schema, or source file.

## Feature and notification boundaries

Task boards, archive/restore, filtering, subtasks, cloning, manager assignment, follow/unfollow, and recurrence remain supported. Recurrence modes include post-completion intervals, daily time slots, and fixed clock/calendar schedules. Browser notifications use a connected page's SSE stream, not offline Web Push. Email supports SMTP and SendGrid; other adapters include Discord, webhook, generic API, Gotify, ntfy, and legacy UWP WNS. Actual provider delivery requires configured credentials and live testing.

Public notification endpoints require HTTPS and cannot redirect into private networks. DNS results are validated and the connection is pinned to a checked address. Exact trusted LAN hosts can be configured in `security.outbound_allowed_hosts`; loopback and metadata destinations remain blocked. WNS is a legacy UWP integration, not a modern Windows App SDK integration.

## Development and validation

```sh
python -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
# Configure a writable TIMEBOARDAPP_SETTINGS and local database.path.
python -m pytest -q
python -m pip_audit --format=json
python -m bandit -r app -ll
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
```

Direct requirements live in `.in` files; generated `.txt` files lock resolved versions and hashes. Do not upgrade `pydantic_core` independently of Pydantic. Keep APScheduler within 3.x until its major-version migration is implemented. Bootstrap 5.3.8 and FullCalendar 6.1.21 are npm-locked and vendored with licenses; the breaking FullCalendar 7 migration is intentionally deferred. To refresh assets, use `npm ci --ignore-scripts` and `python scripts/vendor_assets.py`.

The CI workflow validates Python 3.12/3.13, security regressions, the actual generated OpenAPI schema, source/website consistency, browser smoke flows, dependency audits, and container startup. The browser test runs against an isolated temporary instance; real provider, production restore, and live ChatGPT tests are separate acceptance gates. Consult the exact commit's logs, not a historical validation report, for results.

## Documentation and audit

The static website source is `docs/`. It is not the private application API. `scripts/sync_docs.py` generates route/package reference data and the sitemap; `scripts/release_metadata.py` synchronizes source version, release links, and badges; CI checks consistency and internal links. The ChatGPT guide documents native MCP and private GPT Actions. Website publication and application rollout are separate from a source release.

See [the audit and feature decision tree](AUDIT_REPORT.md), [security policy](SECURITY.md), and [website documentation](docs/README.md). Historical release and validation reports remain as historical records. Only verified unreferenced icon aliases were removed.

MIT licensed. Keep demo mode isolated: its public credentials and destructive resets are intentional, never appropriate for private production data.

### CI and release publication

Every main-branch push still runs the complete CI checks. A read-only release-intent job compares `APP_VERSION` across the complete push range. Unchanged versions skip publication, so documentation or maintenance commits do not attempt to recreate an existing release. Version increases request publication only after all checks pass. An explicit main-branch manual workflow run may retry publication; conflicting tags and incomplete releases still require review. See [VERSIONING.md](VERSIONING.md) for examples and recovery instructions.

## Community and contributing

Read the [Code of Conduct](CODE_OF_CONDUCT.md) and [Contributing Guidelines](CONTRIBUTING.md) before participating. The [issue chooser](https://github.com/paulkakell/timeboardapp/issues/new/choose) provides bug, feature, and documentation forms. Pull requests use the [review template](.github/pull_request_template.md). The [community guide](docs/docs/community.html) links these policies without duplicating them.

Report vulnerabilities through [SECURITY.md](SECURITY.md), and use the conduct policy's reporting routes for behavior concerns. Do not put credentials, private tasks, or incident details in public reports.
