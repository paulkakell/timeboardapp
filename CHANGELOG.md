# Changelog


## 00.12.00

- Additive: Add `GET /api/tasks/summary` to return authenticated-user totals for Archived, Past Due, All Upcoming Due, Due in 0-8h, Due in 8-24h, and Due in >24h.
- Additive: Add regression tests covering summary bucket boundaries, archived counts, and API auth scoping for per-user totals.

Compatibility: Backward compatible (no DB schema changes).

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
