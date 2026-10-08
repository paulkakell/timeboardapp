# Changelog

## 00.14.01 - 2026-10-08

- Fix: Add RFC 9207 authorization-response issuer identification for native MCP OAuth (PR #38). Discovery advertises support only with matching `iss` values on approval, cancellation, and authorization-error callbacks.
- Fix: Preserve exact issuer serialization, callback validation, PKCE, resource binding, consent CSRF, and invalid-client/redirect rejection. No dependency or database-schema change relative to 00.14.00.
- Tests: Cover issuer handling with 43 focused cases; verify discovery and the real callback in browser/container smoke checks. Retain publication-boundary tests and retire the completed one-time preview job.
- Documentation: Update README, MCP setup/troubleshooting, in-app help, release/versioning guidance, and the public website. Synchronize source version, install references, Docker/npm metadata, and generated badges/reference data.
- Operations: Preserve the published preview and old release tags. Rebuild and recreate from 00.14.01 to deploy; merging source and publishing the website do not update existing applications. Live ChatGPT acceptance remains deployment-specific and unverified here.

## 00.14.00 - 2026-10-08

- Additive: Opt-in Streamable HTTP MCP at `/mcp`, eleven account/task/assignment tools, per-tool scopes, persistent OAuth authorization-code grants with S256 PKCE, explicit consent, rotating refresh tokens, and connection revocation.
- Additive: Profile connection management, bounded client registration, hashed opaque credentials, mutation audit records, and atomic retry keys for creation and assignment.
- Maintenance: Share task operations and assignment permissions with the existing application; retain private GPT Actions contracts and owner-only task access. Lock MCP SDK 2.2.0 and its dependencies.
- Validation: Add protocol, authorization, concurrency, migration/feature rollback, and browser coverage plus a disposable test deployment guide.
- Maintenance: Include FastAPI 0.142.2 and PyJWT 2.15.1, retaining the reviewed hash locks and dependency reference.
- Documentation: Update in-app help, README, MCP setup/testing, contributor/security guidance, website pages, release badges, and generated references for the released opt-in capability.
- Deployment: Expose `TIMEBOARDAPP_MCP_ENABLED` in regular Compose. Empty preserves settings YAML; explicit true/false overrides it. Advance application/container version to 00.14.00 and npm metadata to 0.14.0.

Compatibility: Additive release with no intended breaking API changes from 00.13.02. MCP is experimental and disabled by default. Enabling requires an HTTPS application origin without a path prefix and creates five additive SQLite tables. Existing APIs remain available. See MCP_TESTING.md for schema, backup, rollback, and live acceptance limitations. A source release does not publish a registry image or upgrade a running instance.

Refs: PR #34 (native MCP/OAuth), PR #35 (PyJWT), PR #36 (FastAPI). Previous published release: v00.13.02. Release validation and artifact provenance are recorded by the final release PR, CI, and RELEASE_MANIFEST.json.

## 00.13.02 - 2026-09-26

- Additive/Community: Add an original Code of Conduct with reporting, fair review, enforcement, and reconsideration procedures; add repository-specific Contributing Guidelines with isolated setup, validation, release, security, and rollback instructions.
- Additive/Templates: Add public bug, feature, and documentation issue forms, a chooser that directs security/conduct concerns away from public reports, and a pull request template covering the review/release checklist. No dependency on pre-created labels or automatic assignees.
- Fix/CI: Include the version-aware release publication correction from the unpublished 00.13.01 candidate. Existing published tags remain unchanged.
- Maintenance: Add community-file structure, privacy, link and example regression tests; expose the canonical policies from README and the website; synchronize version 00.13.02 (npm 0.13.2), badges, metadata, and release notes.

Compatibility: No new application, API, authentication, configuration, dependency, or database behavior changes. The GitHub issue chooser now disables blank issues in favor of the three structured forms. Templates take effect after merging into the default branch. The 00.13.00 migration precautions remain applicable to older installations.

Refs: PR #32; user request for Code of Conduct, Contributing Guidelines, Issue Templates, and Pull Request Template. Base candidate commit 1c2184c92449a13e5022001c4e11151f376269d9. Previous published release v00.13.00; 00.13.01 was not published. Validation results are tied to the final PR commit and its CI artifacts.

## 00.13.01 - 2026-09-26 (unpublished candidate, superseded by 00.13.02)

- Fix/CI: Separate read-only release-intent detection from publication. Ordinary pushes with an unchanged application version run CI without attempting to recreate an existing release.
- Fix/Release: Compare versions across the full push before/after range, not only the final commit. Preserve explicit manual retries, exact-commit checks, immutable existing tags, and incomplete-release safeguards.
- Fix/Validation: Parse previous version source as a literal, reject missing/rewritten history and version decreases, and test documentation-only pushes with real local Git history.
- Maintenance: Synchronize version 00.13.01 (npm 0.13.1), container metadata, documentation and generated badges. Dependencies are unchanged; remove the deleted audit branch from the CI push filter.

Compatibility: Backward-compatible patch. No new application, API, database, authentication, environment-variable or dependency changes. Prior 00.13.00 upgrade precautions remain applicable.

Refs: PR #30 release regression; failed CI run 36261828606, job 108459388374; affected main 81cbec352bfde90ed7861460736ffdb6b53e7c5e; preserved v00.13.00 at 676295c7dc0338b5841e5f7ee3e40c7d8ddf3861. The patch PR and exact-commit CI artifacts record validation results.

## 00.13.00 - 2026-09-26

- Additive: Introduce private, owner-only GPT Actions with scoped/revocable integration tokens and a generated OpenAPI contract.
- Fix/Security: Replace legacy authentication libraries, revoke credentials on password changes, remove login-triggered admin recovery/password logging, add CSRF/origin protection, restrict outbound requests, redact secrets, and protect private file writes.
- Fix: Repair configured logging paths, profile routing, parent/subtask API conflicts, recurrence completion claims, calendar assets, and Compose/non-root behavior.
- Additive/Maintenance: Lock Python and npm dependencies, vendor browser assets with licenses, refresh source-backed website documentation, remove seven obsolete icon aliases, and replace the Docker-only workflow with full validation.
- Additive/Release: Advance feature version from 00.12.03 to 00.13.00; synchronize application, npm, container, website and badge identities; add drift tests and gated exact-commit tag/release publication. npm uses the unpadded equivalent 0.13.0.
- Tests: Repeat the full Python 3.12/3.13, API/security/migration/concurrency, website, Chromium, and container suites; add release identity and publisher safety regressions.

Compatibility: BREAKING changes to existing session/JWT validity, legacy password rollback, CSRF-protected writes/POST logout, task pagination, outbound destination policy, and container/service defaults. New integration-token storage is additive. Back up and test restoration of database and settings with the matching old image before upgrading. Read RELEASE_NOTES_00.13.00.md and VERSIONING.md.

Refs: PR #30; original main 1a9d32a4e6770870b4d0a1094cb2c9177d040706; reviewed implementation 0b20c8f511888e25236920884bc75547af973d07. The v00.13.00 tag and release manifest identify the final merged commit.

## 00.12.03

- Fix/Security: Auto-repair legacy placeholder or short session/JWT secrets in existing `settings.yml` files and ignore weak secret environment overrides so Admin -> Validation no longer fails runtime secret strength on upgraded deployments.
- Fix/Security: Add application-level browser security headers (`X-Frame-Options`, `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy`, and HTTPS-only `Strict-Transport-Security`) instead of relying solely on reverse-proxy configuration.
- Fix: Make username lookup, username authentication, API token user resolution, and username duplicate checks case-insensitive while preserving stored username casing.
- Additive/Dependency: Add `pip-audit` to runtime requirements so Admin -> Validation can confirm CVE tooling is available in the container.
- Tests: Add regression coverage for placeholder secret repair, weak env-secret override handling, case-insensitive username authentication/duplicate denial, and emitted browser security headers.

Compatibility: Backward compatible (no DB schema changes). Existing active sessions and API tokens may be invalidated once legacy placeholder secrets are automatically rotated.

Refs: Issue Admin -> Validation 2 warnings / 2 failures, Commit N/A

## 00.12.02

- Fix: Update UI template rendering calls to use the request-first `TemplateResponse` signature so the dashboard and other server-rendered pages launch correctly on newer Starlette/FastAPI deployments.
- Tests: Add regression coverage that flags any UI template render call that omits `request` as the first argument.

Compatibility: Backward compatible (no DB schema changes).

Refs: Issue docker deploy Internal Server Error / TemplateResponse TypeError, Commit N/A


## 00.12.01

- Fix: Restore the TimeboardApp configuration loader module so `python -m app.run` can import `get_settings` during Docker startup.
- Fix/Security: Preserve first-run settings generation with randomized session/JWT secrets and keep the deployment entrypoint on the supported `TIMEBOARDAPP_SETTINGS` path.
- Tests: Add regression coverage for startup configuration import and first-run settings-file creation.
- Dependency/Tests: Add `httpx` to development requirements because FastAPI/Starlette TestClient requires it during regression tests.

Compatibility: Backward compatible (no DB schema changes).

Refs: Issue deployment ImportError get_settings, Commit N/A

## 00.12.00

- Additive: Add a per-user Profile setting for a frozen past-due tag shortcut bar. When enabled, the top bar dynamically lists tags assigned to active past-due tasks and opens a new dashboard tab filtered to the selected tag.
- Additive: Add Admin → Validation for in-app full feature validation and security-oriented runtime checks in a running Docker environment. The suite writes redacted, pasteable logs under the validation log directory and is also available through `python -m app.cli validate`.
- Fix/Security: First-run settings generation now replaces sample session/JWT secret placeholders with random secrets instead of copying placeholder values into a new runtime settings file.
- Additive/Tests: Add regression coverage for the past-due tag data source, per-user preference persistence, validation log redaction, and validation fixture cleanup.

Compatibility: Backward compatible (no DB schema changes; the new user setting uses existing `users.ui_prefs_json`).

Refs: Issue N/A, Commit N/A


## 00.11.00

- Additive/Branding: Rebrand product name to TimeboardApp across the codebase (UI, docs, config defaults, notification headers/user-agent).
- Additive: When demo mode is enabled (`demo.enabled: true`), the login page displays a demo warning and the demo admin username/password.
- Fix: Docker Compose now defaults to publishing the app on `http://localhost:8888` without requiring `PORT` to be set.
- Additive: Add a companion static website under `/web` (intended for deployment at `timeboardapp.com`) that documents features, architecture, and deployment.
- Fix/Docs: Add `timeboardapp.com` links in the README and UI footer.
- Fix/Legal: Change license to MIT.
- Maintenance: Update requirements to explicitly include direct dependencies.

Compatibility: Backward compatible (no DB schema changes).

Refs: Issue N/A, Commit N/A


## 00.10.00

- Additive: Demo mode in settings.yml (`demo.enabled`) to run TimeboardApp as a safe public demo.
- Additive: Robust seeded demo dataset themed as "Dunder Mifflin Paper Company, Inc".
  - Seeds users with manager/subordinate hierarchy, assigned tasks, task follows, nested subtasks, recurrence patterns, and in-app notifications.
- Additive: Automatic demo reset job (`demo.reset_interval_minutes`) that purges + rebuilds the demo dataset on a schedule.
- Fix/Security: Outbound notification integrations (email/webhooks/API/discord/gotify/ntfy/WNS) are blocked when `demo.disable_external_apis` is enabled.

Compatibility: Backward compatible (no DB schema changes).

Refs: Issue N/A, Commit N/A


## 00.09.00

- Additive: Global task search (navbar) that searches across task fields and tags.
- Additive: Task cloning, including full subtask trees.
- Additive: Nested subtasks (unlimited depth) via parent/child tasks.
  - Recurrent parent tasks rebuild their full child task tree on recurrence.
  - Safeguard: completing/deleting a parent task with open subtasks prompts to cascade-close or cancel.
- Additive: In-app notifications with navbar bell + unread badge.
  - Viewing notifications clears the "new" badge state.
  - Uncleared notifications persist indefinitely; cleared notifications are purged using the same retention policy as archived tasks.
- Additive: Hierarchical users (manager/subordinate).
  - Admin can set each user's manager.
  - Managers can assign tasks to subordinates.
  - Managers can follow subordinate tasks to receive in-app notifications on update/complete/delete.
  - Manager dashboard can optionally include tasks they assigned to subordinates.
- Additive: Admin user deletion supports optional reassignment of completed tasks.

Compatibility: Backward compatible (DB migration is additive: new nullable columns and new tables).

Refs: Issue N/A, Commit N/A


## 00.08.00

- Additive: Calendar view now includes checkbox filters for the color-coded time-left buckets and for Completed/Deleted tasks.
  - Completed and Deleted are hidden by default.
  - Calendar filter + view selection (Month/Week/Day/Year) are persisted per-user.
- Additive: Dashboard now auto-linkifies URLs found in task descriptions.
- Fix: Dashboard pagination is now preserved when completing, deleting, or updating tasks (no longer resets to page 1).

Compatibility: Backward compatible (DB migration is additive: new nullable `users.ui_prefs_json`).

Refs: Issue N/A, Commit N/A


## 00.07.01

- Fix: Clarify the login-page password reset link text (now labeled "Reset password").
- Fix/Docs: Document the supported admin password recovery command (`python -m app.cli reset-admin`) for deployments without email reset.

Compatibility: Backward compatible.

Refs: Issue N/A, Commit N/A


## 00.07.00

- Additive: First-run installs now seed a small set of demo tasks/tags for the initial admin account (only when the SQLite DB file did not exist before startup).
- Additive: Admin → Database now includes a "Purge All" action to permanently delete tasks, tags, and notification-related data (user accounts + admin settings are preserved). A pre-purge JSON backup is written to `/data/backups`.
- Fix: Gotify notifications now authenticate using the `X-Gotify-Key` header instead of `?token=...` query params (improves compatibility with reverse proxies/WAFs and avoids leaking tokens in URLs).

Compatibility: Backward compatible.

Refs: Issue N/A, Commit N/A


## 00.06.00

- Additive: Email can now be delivered via SendGrid API (v3) as an alternative to SMTP. Configurable in Admin → Email and via the Admin email settings API.
- Fix: Admin email settings API now supports partial updates consistently (mirrors the logging/WNS admin endpoints behavior).

Compatibility: Backward compatible.

Refs: Issue N/A, Commit N/A


## 00.05.01

- Fix: Email (SMTP) delivery failures now include host/port/timeout context (and a Docker/localhost hint) in logs and notification event delivery errors to make configuration and networking issues easier to diagnose.

Compatibility: Backward compatible.

Refs: Issue N/A, Commit N/A


## 00.05.00

- Additive: Asynchronous delivery for all non-browser notification services (email, gotify, ntfy, discord, webhook, generic_api, wns) so task create/update/complete no longer blocks on network calls.
- Additive: Notification delivery status and error fields are now persisted on `notification_events` and returned by the notifications events API to aid troubleshooting.
- Fix: Outbound notification HTTP failures now include safe URL context (query stripped) and response snippets, and async worker failures are logged with event/service/user context.

Compatibility: Backward compatible (DB migration is additive).

Refs: Issue N/A, Commit N/A


## 00.04.01

- Additive: Dashboard page size default is now 10 (options now include 10, 25, 50, 100, 200).

Compatibility: Backward compatible.

## 00.04.00

- Fix: Discord webhook notifications now use an embed so the task name is a clickable link to the task entry (when an absolute URL is available via `app.base_url` or a task's `url`).
- Additive: Profile → Notifications: clicking a generated `notify:…` routing tag now copies it to the clipboard.
- Additive: `TIMEBOARDAPP_BASE_URL` environment variable can override `app.base_url` (useful for generating absolute links in external notifications).

Compatibility: Backward compatible.

## 00.03.01

- Fix: Correct broken module imports in API routers that prevented the container from starting (Portainer deployments crashed with `ModuleNotFoundError: No module named 'app.database'`).
  - `app/routers/api_admin.py` now imports `get_db` from `app.db` and `list_log_files` from `app.logging_setup`.
  - `app/routers/api_notifications.py` now imports `get_db` from `app.db`.

Compatibility: Backward compatible.

## 00.03.02

- Fix: Database schema upgrade banner now behaves like a one-time notification (shown once after an actual upgrade, then cleared) instead of reappearing on every page load.
- Fix: Discord webhook notifications now send Discord-friendly Markdown (not HTML), disable @mention parsing by default, and accept common legacy config keys (e.g. `url`).
- Fix: Dashboard filters are now stateful across navigation within a session until explicitly reset.
- Additive: Notification payloads now include `due_date_display` (stable UTC string) for downstream webhook/API consumers.
- Fix/Security: Outbound notification URLs are now restricted to `http://` and `https://` schemes.

Compatibility: Backward compatible.
