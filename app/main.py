from __future__ import annotations

from .clock import utc_now

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import joinedload
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit
from .browser_security import BrowserSecurityMiddleware
from .paths import data_directory
from .private_files import write_private_text

from .config import get_settings
from .crud import create_user, purge_archived_tasks
from .db_admin import backup_database_json, get_auto_backup_settings, purge_backup_files
from .db import Base, SessionLocal, engine
from .demo_data import seed_demo_data
from .demo_dunder_mifflin import reset_to_dunder_mifflin_demo
from .emailer import build_overdue_reminder_email, email_enabled, send_email
from .logging_setup import apply_log_level, purge_old_logs, setup_logging
from .meta_settings import (
    get_email_settings,
    get_logging_settings,
    seed_email_settings_from_legacy_yaml,
    seed_logging_settings_from_legacy_yaml,
)
from .migrations import ensure_db_schema
from .models import Task, TaskStatus, User
from .notifications import EVENT_PAST_DUE, notify_task_event, shutdown_notification_dispatcher
from .routers import chatgpt, api_admin, api_auth, api_notifications, api_tags, api_tasks, api_users, ui
from .utils.time_utils import format_dt_display, to_local
from .version import APP_VERSION


settings = get_settings()

# File + stdout logging (initially seeded from settings.yml; can be overridden
# from the database after startup).
setup_logging(level=settings.logging.level)
logger = logging.getLogger("timeboardapp")


@asynccontextmanager
async def lifespan(application: FastAPI):
    on_startup()
    try:
        if mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield
    finally:
        on_shutdown()


app = FastAPI(title=settings.app.name, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(BrowserSecurityMiddleware, base_url=settings.app.base_url, mcp_enabled=settings.mcp.enabled)
secure_cookies = settings.security.secure_cookies
if secure_cookies is None:
    secure_cookies = settings.app.base_url.startswith("https://")
app.add_middleware(SessionMiddleware, secret_key=settings.security.session_secret,
                   https_only=secure_cookies, same_site="lax", max_age=86400)
if settings.app.base_url:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.app.base_url).hostname, "localhost", "127.0.0.1", "[::1]"])


SECURITY_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


@app.middleware("http")
async def add_browser_security_headers(request: Request, call_next):
    """Attach baseline browser security headers to every response."""

    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    policy = SECURITY_CONTENT_SECURITY_POLICY
    if settings.mcp.enabled and request.url.path == "/mcp/consent":
        # Browsers enforce form-action on the OAuth POST's redirect too.
        # Allow only configured callback origins, on this consent page only.
        from pydantic import AnyHttpUrl
        origins = set()
        for uri in settings.mcp.allowed_redirect_uris:
            callback = urlsplit(str(AnyHttpUrl(uri)))
            origins.add(f"{callback.scheme}://{callback.netloc}")
        policy = policy.replace("form-action 'self'", "form-action 'self' " + " ".join(sorted(origins)))
    # FastAPI's optional interactive documentation still loads its own viewer.
    if request.url.path in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
        policy = policy.replace("script-src 'self'", "script-src 'self' https://cdn.jsdelivr.net").replace("style-src 'self'", "style-src 'self' https://cdn.jsdelivr.net")
    response.headers.setdefault("Content-Security-Policy", policy)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if not request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")

    # The ASGI server may set scheme using forwarding headers only from trusted proxies.
    scheme = str(request.url.scheme or "").lower()
    if scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

# Static files
static_path = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Routers
app.include_router(api_auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(api_users.router, prefix="/api/users", tags=["users"])
app.include_router(api_tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(api_tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(api_notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(api_admin.router, prefix="/api/admin", tags=["admin"])

app.include_router(chatgpt.router)
app.include_router(chatgpt.tokens_router)
app.include_router(ui.router)


@app.get("/openapi-chatgpt.json", include_in_schema=False)
def chatgpt_openapi():
    from .chatgpt_schema import build_chatgpt_schema
    if not settings.app.base_url.startswith("https://"):
        raise HTTPException(503, "Configure an HTTPS TIMEBOARDAPP_BASE_URL before importing GPT Actions")
    return build_chatgpt_schema(app, settings.app.base_url)



scheduler: BackgroundScheduler | None = None


def _configure_email_jobs(app: FastAPI, sched: BackgroundScheduler) -> None:
    """(Re)configure email reminder jobs.

    Email settings are stored in the database (app_meta) so they can be changed
    from the UI without editing settings.yml.
    """

    dbj = SessionLocal()
    try:
        cfg = get_email_settings(dbj)
    finally:
        dbj.close()

    # Remove existing reminders job.
    try:
        sched.remove_job("overdue_reminders")
    except Exception:
        pass

    # Store current config snapshot for UI rendering/debug.
    app.state.email_config = {
        "enabled": bool(cfg.enabled),
        "provider": str(getattr(cfg, "provider", "smtp") or "smtp"),
        "smtp_host": str(cfg.smtp_host or ""),
        "smtp_port": int(cfg.smtp_port),
        "smtp_username": str(cfg.smtp_username or ""),
        "smtp_from": str(cfg.smtp_from or ""),
        "use_tls": bool(cfg.use_tls),
        "sendgrid_api_key_set": bool(getattr(cfg, "sendgrid_api_key", "") or ""),
        "reminder_interval_minutes": int(cfg.reminder_interval_minutes),
        "reset_token_minutes": int(cfg.reset_token_minutes),
    }

    provider = str(getattr(cfg, "provider", "smtp") or "smtp").strip().lower() or "smtp"
    is_configured = False
    if provider == "sendgrid":
        is_configured = bool(str(getattr(cfg, "sendgrid_api_key", "") or "").strip())
    else:
        is_configured = bool(str(cfg.smtp_host or "").strip())

    if (not cfg.enabled) or (not is_configured) or int(cfg.reminder_interval_minutes) <= 0:
        return

    def _reminder_job() -> None:
        dbx = SessionLocal()
        try:
            if not email_enabled(dbx):
                return

            now = utc_now().replace(tzinfo=None)

            # Find overdue active tasks for users with email addresses.
            q = (
                dbx.query(Task)
                .join(User, User.id == Task.user_id)
                .filter(Task.status == TaskStatus.active)
                .filter(Task.due_date_utc < now)
                .filter(User.email.is_not(None))
            )

            tasks = q.all()
            if not tasks:
                return

            base_url = _public_base_url()
            dashboard_url = f"{base_url}/dashboard" if base_url else ""

            grouped: dict[int, list[Task]] = {}
            for t in tasks:
                grouped.setdefault(int(t.user_id), []).append(t)

            for user_id, items in grouped.items():
                user = dbx.query(User).filter(User.id == user_id).first()
                if not user or not user.email:
                    continue

                task_rows = []
                for t in sorted(items, key=lambda x: x.due_date_utc):
                    task_rows.append(
                        {
                            "name": t.name,
                            "task_type": t.task_type,
                            "due": format_dt_display(t.due_date_utc),
                        }
                    )

                subject, body = build_overdue_reminder_email(
                    username=user.username,
                    tasks=task_rows,
                    dashboard_url=dashboard_url or "(dashboard URL not configured)",
                )

                try:
                    send_email(to_address=user.email, subject=subject, body_text=body, db=dbx)
                except Exception:
                    logger.exception("Failed to send overdue reminder to %s", user.email)

        except Exception:
            logger.exception("Error while sending overdue reminders")
        finally:
            dbx.close()

    sched.add_job(
        _reminder_job,
        "interval",
        minutes=int(cfg.reminder_interval_minutes),
        id="overdue_reminders",
        replace_existing=True,
    )


def _configure_logging_jobs(app: FastAPI, sched: BackgroundScheduler) -> None:
    """(Re)configure log retention and apply current log level."""

    dbj = SessionLocal()
    try:
        cfg = get_logging_settings(dbj)
    finally:
        dbj.close()

    # Apply log level immediately.
    try:
        apply_log_level(cfg.level)
    except Exception:
        logger.exception("Failed to apply log level")

    # Remove existing retention job.
    try:
        sched.remove_job("log_retention")
    except Exception:
        pass

    app.state.logging_config = {"level": cfg.level, "retention_days": int(cfg.retention_days)}

    # Best-effort purge at startup.
    try:
        purged = purge_old_logs(retention_days=int(cfg.retention_days))
        if purged:
            logger.info("Purged %s old log files", purged)
    except Exception:
        logger.exception("Failed to purge old log files")

    if int(cfg.retention_days) <= 0:
        return

    def _log_retention_job() -> None:
        dbx = SessionLocal()
        try:
            cfg2 = get_logging_settings(dbx)
        finally:
            dbx.close()

        try:
            purged2 = purge_old_logs(retention_days=int(cfg2.retention_days))
            if purged2:
                logger.info("Purged %s old log files", purged2)
        except Exception:
            logger.exception("Error while purging old log files")

    sched.add_job(
        _log_retention_job,
        "cron",
        hour=0,
        minute=15,
        id="log_retention",
        replace_existing=True,
        timezone=settings.app.timezone,
    )


def _configure_past_due_notification_job(app: FastAPI, sched: BackgroundScheduler) -> None:
    """Configure periodic scanning for tasks that have crossed into 'past due'."""

    # Remove existing job first.
    try:
        sched.remove_job("past_due_notifications")
    except Exception:
        pass

    def _past_due_job() -> None:
        dbx = SessionLocal()
        try:
            now = utc_now().replace(tzinfo=None)
            # Scan active tasks that are overdue.
            tasks = (
                dbx.query(Task)
                .options(joinedload(Task.tags))
                .filter(Task.status == TaskStatus.active)
                .filter(Task.due_date_utc < now)
                .all()
            )
            if not tasks:
                return

            for t in tasks:
                try:
                    due = getattr(t, "due_date_utc", None)
                    due_key = due.isoformat() if due else ""
                    event_key = f"past_due:{int(t.id)}:{due_key}"
                    notify_task_event(dbx, task=t, event_type=EVENT_PAST_DUE, event_key=event_key)
                except Exception:
                    logger.exception("Failed to process past-due notification for task %s", getattr(t, "id", "?"))

        except Exception:
            logger.exception("Error while scanning for past-due notifications")
        finally:
            dbx.close()

    # Run frequently; per-task de-duping prevents repeated notifications.
    sched.add_job(
        _past_due_job,
        "interval",
        minutes=5,
        id="past_due_notifications",
        replace_existing=True,
    )


def _configure_auto_backup_jobs(app: FastAPI, sched: BackgroundScheduler) -> None:
    """(Re)configure automated backup + retention jobs.

    Settings are stored in the database (app_meta) so they can be changed from
    the UI without editing settings.yml.
    """

    # Read config from DB.
    dbj = SessionLocal()
    try:
        cfg = get_auto_backup_settings(dbj)
    finally:
        dbj.close()

    freq = str(cfg.get("frequency") or "daily").strip().lower()
    retention_days = int(cfg.get("retention_days") or 0)

    # Remove existing jobs first.
    for job_id in ("auto_backup", "backup_retention"):
        try:
            sched.remove_job(job_id)
        except Exception:
            pass

    def _auto_backup_job() -> None:
        dbx = SessionLocal()
        try:
            # Label backups by frequency for easier inspection.
            label = freq.upper() if freq and freq != "disabled" else "BACKUP"
            path = backup_database_json(dbx, prefix=label)
            logger.info("Auto backup written: %s", path)
        except Exception:
            logger.exception("Error while creating automated backup")
        finally:
            dbx.close()

        # Opportunistic retention purge right after backup creation.
        if retention_days and retention_days > 0:
            try:
                deleted = purge_backup_files(retention_days=retention_days)
                if deleted:
                    logger.info("Purged %s old backup file(s)", deleted)
            except Exception:
                logger.exception("Error while purging old backups")

    def _backup_retention_job() -> None:
        # Read retention_days at runtime so changes apply without restart.
        dbx = SessionLocal()
        try:
            cfgx = get_auto_backup_settings(dbx)
            rd = int(cfgx.get("retention_days") or 0)
        except Exception:
            rd = 0
        finally:
            dbx.close()

        if rd <= 0:
            return

        try:
            deleted = purge_backup_files(retention_days=rd)
            if deleted:
                logger.info("Purged %s old backup file(s)", deleted)
        except Exception:
            logger.exception("Error while purging old backups")

    # Schedule backup job (cron-based) if enabled.
    if freq == "disabled":
        logger.info("Auto backups disabled")
    else:
        tz = settings.app.timezone
        if freq == "hourly":
            sched.add_job(_auto_backup_job, "cron", minute=0, timezone=tz, id="auto_backup", replace_existing=True)
        elif freq == "6h":
            sched.add_job(
                _auto_backup_job,
                "cron",
                hour="*/6",
                minute=0,
                timezone=tz,
                id="auto_backup",
                replace_existing=True,
            )
        elif freq == "12h":
            sched.add_job(
                _auto_backup_job,
                "cron",
                hour="*/12",
                minute=0,
                timezone=tz,
                id="auto_backup",
                replace_existing=True,
            )
        elif freq == "weekly":
            sched.add_job(
                _auto_backup_job,
                "cron",
                day_of_week="mon",
                hour=0,
                minute=0,
                timezone=tz,
                id="auto_backup",
                replace_existing=True,
            )
        else:
            # Default: daily at midnight in app timezone.
            sched.add_job(
                _auto_backup_job,
                "cron",
                hour=0,
                minute=0,
                timezone=tz,
                id="auto_backup",
                replace_existing=True,
            )

        logger.info("Auto backup schedule configured: %s", freq)

    # Schedule retention purge job daily at 00:30 in app timezone.
    # (It will no-op when retention_days == 0.)
    sched.add_job(
        _backup_retention_job,
        "cron",
        hour=0,
        minute=30,
        timezone=settings.app.timezone,
        id="backup_retention",
        replace_existing=True,
    )

    # Expose current config on app.state for UI rendering/debugging.
    app.state.auto_backup_config = {"frequency": freq, "retention_days": retention_days}


def _public_base_url() -> str:
    # Prefer configured base_url for email links.
    base = (settings.app.base_url or "").strip().rstrip("/")
    return base


def on_startup() -> None:
    global scheduler

    # Detect a fresh install before SQLAlchemy creates the SQLite DB file.
    is_fresh_db_file = False
    try:
        if str(getattr(engine, "dialect", None).name or "").lower() == "sqlite":
            db_file = getattr(getattr(engine, "url", None), "database", None)
            if db_file and str(db_file) != ":memory:":
                is_fresh_db_file = not Path(str(db_file)).exists()
    except Exception:
        is_fresh_db_file = False

    # Ensure DB tables exist.
    Base.metadata.create_all(bind=engine)

    # Lightweight schema migrations (adds new nullable columns/tables).
    report = ensure_db_schema(engine)
    app.state.db_migration_report = report
    if report.applied_steps:
        logger.warning("Database schema upgraded to %s (%s)", report.current_db_version, ", ".join(report.applied_steps))

    # Seed DB-backed settings from legacy settings.yml if missing.
    db_seed = SessionLocal()
    try:
        seed_email_settings_from_legacy_yaml(db_seed, getattr(settings, "email", None))
        seed_logging_settings_from_legacy_yaml(db_seed, getattr(settings, "logging", None))
    except Exception:
        logger.exception("Failed to seed DB-backed settings")
    finally:
        db_seed.close()

    # Ensure first-run admin.
    db = SessionLocal()
    try:
        if getattr(settings, "demo", None) and bool(getattr(settings.demo, "enabled", False)):
            # Demo mode: rebuild the DB into a known-safe demo dataset.
            try:
                result = reset_to_dunder_mifflin_demo(db)
                logger.warning("Demo mode enabled: database reset + reseeded (%s)", result)
            except Exception:
                logger.exception("Failed to build demo dataset")
        else:
            if db.query(User).count() == 0:
                admin_password = secrets.token_urlsafe(12)
                credential_path = data_directory() / "initial-admin-password.txt"
                write_private_text(credential_path, admin_password + "\n")
                create_user(db, username="admin", password=admin_password, is_admin=True)
                logger.warning("============================================================")
                logger.warning("TimeboardApp initial admin account created")
                logger.warning("Username: admin")
                logger.warning("Read the initial password from %s (owner-only); remove it after changing the password.", credential_path)
                logger.warning("Please log in and change this password.")
                logger.warning("============================================================")

            # Seed demo tasks/tags for true first-run installs.
            if is_fresh_db_file:
                try:
                    admin_user = db.query(User).filter(User.username == "admin").first() or db.query(User).first()
                    if admin_user:
                        result = seed_demo_data(db, owner=admin_user)
                        if result.get("seeded"):
                            logger.info("Seeded demo data (tasks_created=%s)", result.get("tasks_created"))
                except Exception:
                    logger.exception("Failed to seed demo data")
    finally:
        db.close()

    # Start background scheduler.
    scheduler = BackgroundScheduler(timezone="UTC")

    def _purge_job() -> None:
        dbj = SessionLocal()
        try:
            deleted = purge_archived_tasks(dbj)
            if deleted:
                logger.info("Purged %s archived tasks", deleted)
        except Exception:
            logger.exception("Error while purging archived tasks")
        finally:
            dbj.close()

    scheduler.add_job(
        _purge_job,
        "interval",
        minutes=int(settings.purge.interval_minutes),
        id="purge",
    )

    # Demo reset job.
    if getattr(settings, "demo", None) and bool(getattr(settings.demo, "enabled", False)):
        def _demo_reset_job() -> None:
            dbj = SessionLocal()
            try:
                res = reset_to_dunder_mifflin_demo(dbj)
                logger.warning("Demo database reset (%s)", res)
            except Exception:
                logger.exception("Error while resetting demo database")
            finally:
                dbj.close()

        try:
            minutes = int(getattr(settings.demo, "reset_interval_minutes", 0) or 0)
        except Exception:
            minutes = 0
        if minutes > 0:
            scheduler.add_job(
                _demo_reset_job,
                "interval",
                minutes=minutes,
                id="demo_reset",
                replace_existing=True,
            )

    # Email reminder jobs (configurable via Admin → Email).
    try:
        _configure_email_jobs(app, scheduler)
    except Exception:
        logger.exception("Failed to configure email jobs")

    # Notification scanning for past-due tasks.
    try:
        _configure_past_due_notification_job(app, scheduler)
    except Exception:
        logger.exception("Failed to configure past-due notification job")

    # Logging level + retention (configurable via Admin → Logs).
    try:
        _configure_logging_jobs(app, scheduler)
    except Exception:
        logger.exception("Failed to configure logging jobs")

    # Automated backups (configurable via Admin → Database).
    # Defaults preserve legacy behavior (daily at midnight; no retention purge).
    try:
        _configure_auto_backup_jobs(app, scheduler)
    except Exception:
        logger.exception("Failed to configure automated backup jobs")

    # Make scheduler + configure hook accessible from request handlers.
    app.state.scheduler = scheduler
    app.state.configure_auto_backup_jobs = lambda: _configure_auto_backup_jobs(app, scheduler)
    app.state.configure_email_jobs = lambda: _configure_email_jobs(app, scheduler)
    app.state.configure_logging_jobs = lambda: _configure_logging_jobs(app, scheduler)
    scheduler.start()


def on_shutdown() -> None:
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None

    # Best-effort stop of background notification workers.
    shutdown_notification_dispatcher()


@app.get("/", include_in_schema=False)
def root(request: Request):
    return RedirectResponse(url="/dashboard")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok", "version": APP_VERSION}


# The root mount must remain last so existing website/API routes keep priority.
mcp_server = None
if settings.mcp.enabled:
    from .mcp.server import build_mcp
    mcp_server, mcp_http, mcp_browser, mcp_auth_routes, _ = build_mcp(settings, SessionLocal)
    app.include_router(mcp_browser)
    app.router.routes.extend(mcp_auth_routes)
    app.mount("/", mcp_http)
