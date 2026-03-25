# TimeboardApp

A lightweight, dockerized task board that supports recurrence intervals shorter than a day.

Current version: **00.12.00**

Website:
- https://timeboardapp.com

Repository:
- https://github.com/paulkakell/timeboardapp

## Key features

- Recurrence options:
  - **Post-Completion Interval**: schedule next due time as `completion_time + interval` (e.g., every `8h` after completion).
  - **Multi-Slot Daily Scheduling**: schedule next due time at the next time slot in a daily list (e.g., `08:00, 15:00, 23:00`).
  - **Fixed Clock Scheduling**: schedule next due time on a fixed interval anchored to the previous due date (e.g., `1d` anchored to `10:00` every day), regardless of completion time.

- Mobile-friendly and desktop-friendly web UI (auto-detects mobile devices; footer link to switch to desktop).
- Light/Dark/System themes.
- Task Type filtering and sorting.
- Calendar view with color-coded due-state filtering (per-user, persisted).
- Archived view for completed/deleted tasks (restore archived tasks back to active).
- Admin user management:
  - create/delete users
  - promote/demote users between Admin and User
  - dashboard "Views" menu (My Tasks, All Tasks, per-user views)
  - export/import database JSON
- Email features (when SMTP is configured in the admin UI):
  - hourly overdue reminders
  - password reset via email ("Reset password" link)
  - login using username or email address
- Per-user notification services (each service entry generates a routing tag; tasks with that tag send notifications on create/update/past due/complete/archive):
  - Browser notifications (SSE)
  - Email
  - Windows Push Notification Services (WNS)
  - Gotify
  - ntfy
  - Discord (webhook)
  - Generic webhook
  - Generic API
  - Non-browser deliveries are dispatched asynchronously; delivery status/errors are recorded on `notification_events` and returned by `/api/notifications/events`.
- Application logging to `/data/logs` (daily files) with configurable log level + retention via the admin UI.
- SQLite database.
- Full OpenAPI-documented API (Swagger UI at `/docs`).
- Configurable via `settings.yml` on a Docker volume.
- Archived task purge job (default 15 days, per-user override).
- Application + database versioning (stored in `app_meta`). On startup, older/unversioned databases are automatically upgraded to the current schema.

## Quick start (Docker Compose)

```bash
docker compose up --build
```

Optionally, copy `.env.example` to `.env` and adjust defaults (host port, data directory, network name).

Open the UI at:

- http://localhost:8888

If demo mode is enabled (`demo.enabled: true`), the login page shows demo credentials and a reset warning.

Note: if you set `PORT`, Docker Compose maps the UI to `http://localhost:${PORT}` instead of `:8888`.


On first run, TimeboardApp creates an `admin` account and prints the password in the container logs.

```bash
docker compose logs -f timeboardapp
```

On first run (fresh database file), TimeboardApp also seeds a small set of demo tasks/tags under the initial admin account.
You can remove all seeded/user data via **Admin → Database → Purge All**.

## Resetting a forgotten admin password

If email is enabled and the admin account has an email address on file, use the **Reset password** link on the login page.

If email is not enabled (or the account has no email address), you can reset the admin password from the server/host with direct access to the SQLite database.

Docker Compose:

```bash
# Prints a new random password to stdout
docker compose exec timeboardapp python -m app.cli reset-admin

# Or set a specific password (won't print unless you add --print)
docker compose exec timeboardapp python -m app.cli reset-admin --password "NewStrongPasswordHere" --print
```

Bare metal (same machine as the app):

```bash
export TIMEBOARDAPP_SETTINGS=/path/to/settings.yml
python -m app.cli reset-admin
```

After resetting, sign in as `admin` with the new password and change it in **Profile → Password**.

## Configuration

TimeboardApp loads settings from:

- `TIMEBOARDAPP_SETTINGS` (default: `/data/settings.yml`)

On first run, if the settings file does not exist, TimeboardApp copies `settings.sample.yml` into place.

Common settings:

- `app.timezone`: used for displaying and interpreting date/time inputs.
- `app.base_url`: public URL prefix when behind a reverse proxy or served from a subpath (can also be set via `TIMEBOARDAPP_BASE_URL`).
- `security.session_secret`: used to sign UI session cookies.
- `security.jwt_secret`: used to sign API JWT tokens.
- `database.path`: SQLite DB file path (default `/data/timeboardapp.db`).
- `purge.default_days`: default purge window for archived tasks.
- `purge.interval_minutes`: how often the purge job runs.
- `demo.enabled`: when true, TimeboardApp runs as a self-resetting demo instance.
- `demo.reset_interval_minutes`: how often the demo dataset is wiped + rebuilt.
- `demo.disable_external_apis`: blocks outbound notifications/webhooks/email in demo mode.
- `email.*`: legacy seed values (copied into the database on first run if no DB settings exist). Runtime configuration is managed in the admin UI (SMTP or SendGrid).

Docker note (SMTP): if TimeboardApp is running in a container, setting the SMTP host to `localhost` / `127.0.0.1` will try to connect to the container itself.
Use a hostname/IP reachable from inside the container (for example: an SMTP container service name on the same docker-compose network, or `host.docker.internal`
when using Docker Desktop).

## API usage

Swagger UI:

- `/docs`

Get a token:

```bash
curl -X POST http://localhost:8888/api/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=YOUR_PASSWORD"
```

List tasks:

```bash
curl "http://localhost:8888/api/tasks?sort=due_date" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Filtering and sorting:

- `tag`: filter by a tag name
- `task_type`: filter by task type
- `status`: `active` or `archived` (completed + deleted)
- `sort`: `due_date`, `task_type`, `name`, `archived_at`

Restore an archived task:

```bash
curl -X POST http://localhost:8888/api/tasks/123/restore \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Admin: update a user (email/role):

```bash
curl -X PATCH http://localhost:8888/api/users/2 \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_admin": true, "email": "user@example.com"}'
```


Create a notification service (returns a generated routing tag):

```bash
curl -X POST http://localhost:8888/api/notifications/services \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service_type": "ntfy",
    "name": "Phone",
    "enabled": true,
    "config": { "server_url": "https://ntfy.sh", "topic": "my-topic" }
  }'
```

List notification services:

```bash
curl http://localhost:8888/api/notifications/services \
  -H "Authorization: Bearer YOUR_TOKEN"
```

List notification events:

```bash
curl http://localhost:8888/api/notifications/events?limit=50 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Admin: update email settings (admin only):

```bash
curl -X PUT http://localhost:8888/api/admin/email \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "provider": "smtp",
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_username": "user@example.com",
    "smtp_password": "YOUR_PASSWORD",
    "smtp_from": "TimeboardApp <timeboardapp@example.com>",
    "use_tls": true
  }'

```

Admin: update SendGrid settings (admin only):

```bash
curl -X PUT http://localhost:8888/api/admin/email \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "provider": "sendgrid",
    "sendgrid_api_key": "YOUR_SENDGRID_API_KEY",
    "smtp_from": "TimeboardApp <timeboardapp@example.com>"
  }'
```

## Task fields

Required:

- Task Name
- Task Type
- Recurrence type (None/Post-Completion/Multi-Slot Daily/Fixed Clock)

Optional:

- Due Date (if omitted, creation time is used as due date)
- Description
- Tags
- URL

## Notes

- Deleting a task archives it as `deleted` with a timestamp and does not spawn a recurrence.
- Completing a task archives it as `completed` with a timestamp, and spawns a new active task if recurrence is configured.
- Admin users default to viewing only their own tasks; use the Views menu for All Tasks or a specific user.
- Dashboard filters (tag/type/sort/page size/view) are sticky within a session until you click Reset.
- Deleting a user permanently deletes all associated tasks.

## Development checks

Unit tests:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Security scan (Bandit):

```bash
bandit -r app
```


### Tasks API summary endpoint

Authenticated API clients can call `GET /api/tasks/summary` to retrieve per-user totals derived from the bearer token. The response includes these counters: `archived`, `past_due`, `all_upcoming_due`, `due_in_0_8h`, `due_in_8_24h`, and `due_in_over_24h`.

