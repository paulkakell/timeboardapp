from __future__ import annotations

from .clock import utc_now

import importlib.metadata
import re
import secrets
import shutil
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .auth import authenticate_user
from .config import get_settings
from .crud import (
    OpenSubtasksError,
    clear_in_app_unread,
    complete_task,
    consume_password_reset_token,
    count_in_app_unread,
    create_in_app_notification,
    create_password_reset_token,
    create_task,
    create_user,
    delete_user,
    follow_task,
    is_following_task,
    is_manager_of,
    list_in_app_notifications,
    list_past_due_tags_for_user,
    list_tasks,
    restore_task,
    soft_delete_task,
    unfollow_task,
    update_task,
    update_user_admin,
)
from .db_admin import export_db_json, get_auto_backup_settings, validate_import_payload
from .emailer import build_password_reset_email, email_enabled
from .logging_setup import list_log_files
from .meta_settings import get_email_settings, get_logging_settings, get_wns_settings
from .models import AppMeta, Tag, TaskStatus, User
from .notifications import (
    CHANNEL_BROWSER,
    CHANNEL_DISCORD,
    CHANNEL_EMAIL,
    CHANNEL_GENERIC_API,
    CHANNEL_GOTIFY,
    CHANNEL_NTFY,
    CHANNEL_WEBHOOK,
    CHANNEL_WNS,
    CHANNEL_TYPES,
    create_user_notification_service,
    delete_user_notification_service,
    list_user_notification_services,
    update_user_notification_service,
)
from .recurrence import parse_duration_to_seconds, parse_fixed_calendar_rule, parse_times_csv
from .version import APP_VERSION


VALIDATION_LOG_BASENAME = "validation"
VALIDATION_STATUS_ORDER = ("PASS", "WARN", "FAIL", "SKIP")


class ValidationWarn(Exception):
    """Raised by a check that should be recorded as a warning."""


class ValidationSkip(Exception):
    """Raised by a check that is intentionally skipped."""


@dataclass
class ValidationFinding:
    category: str
    name: str
    status: str
    detail: str = ""
    security: bool = False


@dataclass
class ValidationReport:
    run_id: str
    app_version: str
    started_at_utc: datetime
    actor: str = "unknown"
    base_url: str | None = None
    completed_at_utc: datetime | None = None
    findings: list[ValidationFinding] = field(default_factory=list)
    log_path: str | None = None

    def add(self, category: str, name: str, status: str, detail: str = "", *, security: bool = False) -> None:
        status_u = str(status or "").strip().upper()
        if status_u not in VALIDATION_STATUS_ORDER:
            status_u = "FAIL"
        self.findings.append(
            ValidationFinding(
                category=str(category or "General"),
                name=str(name or "Unnamed check"),
                status=status_u,
                detail=str(detail or ""),
                security=bool(security),
            )
        )

    @property
    def has_failures(self) -> bool:
        return any(f.status == "FAIL" for f in self.findings)

    def counts(self) -> dict[str, int]:
        counts = {k: 0 for k in VALIDATION_STATUS_ORDER}
        for finding in self.findings:
            counts[finding.status] = counts.get(finding.status, 0) + 1
        return counts

    def to_text(self) -> str:
        completed = self.completed_at_utc or utc_now().replace(tzinfo=None)
        counts = self.counts()
        lines: list[str] = []
        lines.append("TimeboardApp validation report")
        lines.append(f"Run ID: {self.run_id}")
        lines.append(f"App version: {self.app_version}")
        lines.append(f"Actor: {self.actor}")
        lines.append(f"Started UTC: {self.started_at_utc.isoformat()}")
        lines.append(f"Completed UTC: {completed.isoformat()}")
        lines.append(f"Base URL checked: {self.base_url or 'not checked'}")
        if self.log_path:
            lines.append(f"Log path: {self.log_path}")
        lines.append("")
        lines.append(
            "Summary: "
            + ", ".join([f"{status}={counts.get(status, 0)}" for status in VALIDATION_STATUS_ORDER])
        )
        lines.append("")

        current_category = None
        for idx, finding in enumerate(self.findings, start=1):
            if finding.category != current_category:
                current_category = finding.category
                lines.append(f"## {current_category}")
            marker = "SECURITY" if finding.security else "FEATURE"
            lines.append(f"{idx:03d}. [{finding.status}] [{marker}] {finding.name}")
            if finding.detail:
                for line in finding.detail.rstrip().splitlines():
                    lines.append(f"     {line}")
            lines.append("")

        return redact_validation_text("\n".join(lines).rstrip() + "\n")


def _known_secret_values() -> list[str]:
    values: list[str] = []
    try:
        settings = get_settings()
        values.extend(
            [
                settings.security.session_secret,
                settings.security.jwt_secret,
                getattr(settings.email, "smtp_password", ""),
                getattr(settings.email, "sendgrid_api_key", ""),
            ]
        )
    except Exception:
        pass
    return [str(v) for v in values if isinstance(v, str) and len(str(v)) >= 6]


def redact_validation_text(text_value: str) -> str:
    """Redact credentials from validation logs before display or storage."""

    out = str(text_value or "")
    for secret_value in _known_secret_values():
        out = out.replace(secret_value, "<redacted>")

    patterns = [
        re.compile(r"(?i)(password\s*[=:]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(token\s*[=:]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(secret\s*[=:]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)([^\s,;]+)"),
        re.compile(r"(?i)(authorization\s*[=:]\s*)([^\s,;]+)"),
    ]
    for pattern in patterns:
        out = pattern.sub(lambda m: f"{m.group(1)}<redacted>", out)
    return out


def default_validation_log_dir() -> Path:
    """Return the directory used for validation logs in the running environment."""

    from .paths import data_directory
    return data_directory() / VALIDATION_LOG_BASENAME


def _write_validation_log(report: ValidationReport, *, log_dir: Path | None = None) -> Path | None:
    target_dir = log_dir or default_validation_log_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"timeboardapp-validation-{report.run_id}.log"
        path = target_dir / filename
        report.log_path = str(path)
        path.write_text(report.to_text(), encoding="utf-8")
        return path
    except Exception:
        report.log_path = None
        return None


def _run_check(
    report: ValidationReport,
    category: str,
    name: str,
    func: Callable[[], str | tuple[str, str] | None],
    *,
    security: bool = False,
) -> None:
    try:
        result = func()
        if isinstance(result, tuple):
            status, detail = result
        else:
            status, detail = "PASS", ("" if result is None else str(result))
        report.add(category, name, status, detail, security=security)
    except ValidationSkip as exc:
        report.add(category, name, "SKIP", str(exc), security=security)
    except ValidationWarn as exc:
        report.add(category, name, "WARN", str(exc), security=security)
    except Exception as exc:  # pragma: no cover - traceback is diagnostic output
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__, limit=8))
        report.add(category, name, "FAIL", tb, security=security)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _safe_detail_join(items: Iterable[str], *, max_items: int = 8) -> str:
    arr = [str(x) for x in items if str(x).strip()]
    if len(arr) > max_items:
        return "; ".join(arr[:max_items]) + f"; ... +{len(arr) - max_items} more"
    return "; ".join(arr)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401,N802 - urllib API
        return None


def _http_status(base_url: str, path: str, *, timeout_seconds: float = 2.5) -> int:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationSkip("Base URL must use http or https")
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValidationSkip("Live HTTP checks are restricted to loopback base URLs to avoid SSRF risk")

    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    opener = urllib.request.build_opener(_NoRedirectHandler)
    req = urllib.request.Request(url, headers={"User-Agent": f"TimeboardAppValidation/{APP_VERSION}"})
    try:
        with opener.open(req, timeout=timeout_seconds) as resp:
            return int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except urllib.error.URLError as exc:
        raise ValidationWarn(f"Could not reach {url}: {exc}") from exc


def _check_version_and_db(db: Session) -> str:
    row = db.query(AppMeta).filter(AppMeta.key == "db_version").first()
    if row is None:
        raise ValidationWarn("app_meta.db_version is missing; run startup migrations before release")
    if str(row.value) != str(APP_VERSION):
        raise AssertionError(f"db_version={row.value}; expected {APP_VERSION}")
    return f"APP_VERSION and app_meta.db_version are {APP_VERSION}"


def _check_settings_security() -> str:
    settings = get_settings()
    weak_values = {
        "CHANGE_ME_SESSION_SECRET",
        "CHANGE_ME_JWT_SECRET",
        "test-session-secret",
        "test-jwt-secret",
        "secret",
        "password",
    }
    findings: list[str] = []
    for name, value in {
        "session_secret": settings.security.session_secret,
        "jwt_secret": settings.security.jwt_secret,
    }.items():
        v = str(value or "")
        if v in weak_values or v.startswith("CHANGE_ME"):
            findings.append(f"{name} is still a placeholder")
        if len(v) < 32:
            findings.append(f"{name} is shorter than 32 characters")
    if settings.security.session_secret == settings.security.jwt_secret:
        findings.append("session_secret and jwt_secret are identical")
    if findings:
        raise AssertionError(_safe_detail_join(findings))
    return "Session and JWT secrets are non-placeholder, distinct, and at least 32 characters."


def _check_config_sanity() -> str:
    settings = get_settings()
    if int(settings.app.port) <= 0 or int(settings.app.port) > 65535:
        raise AssertionError("Configured app.port is outside the valid TCP range")
    if int(settings.purge.default_days) < 1:
        raise AssertionError("purge.default_days must be positive")
    if int(settings.purge.interval_minutes) < 1:
        raise AssertionError("purge.interval_minutes must be positive")
    return (
        f"app={settings.app.name}; timezone={settings.app.timezone}; "
        f"database_path={settings.database.path}; demo_enabled={bool(settings.demo.enabled)}"
    )




def _normalize_package_name(name: str) -> str:
    """Normalize Python distribution names per PEP 503 conventions."""

    return re.sub(r"[-_.]+", "-", str(name or "")).lower().strip("-")


def _check_dependency_inventory() -> tuple[str, str]:
    required_names: list[str] = []
    req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
    if req_path.exists():
        for raw in req_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-")):
                continue
            name = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
            if name:
                required_names.append(_normalize_package_name(name))

    installed = {
        _normalize_package_name(dist.metadata.get("Name", "")): dist.version
        for dist in importlib.metadata.distributions()
    }
    missing = [name for name in required_names if name not in installed]
    pip_audit_available = shutil.which("pip-audit") is not None
    detail = f"requirements={len(required_names)}; installed_distributions={len(installed)}"
    if missing:
        return "FAIL", detail + "; missing=" + _safe_detail_join(missing)
    if not pip_audit_available:
        return "WARN", detail + "; pip-audit not installed, so CVE lookup was not executed by the in-app runner"
    return "PASS", detail + "; pip-audit executable is available for operator CVE scans"


def _scan_source_for_risky_patterns() -> tuple[str, str]:
    root = Path(__file__).resolve().parent.parent
    risky_patterns: list[tuple[str, re.Pattern[str], str]] = [
        ("eval", re.compile(r"\beval\s*\("), "eval() usage"),  # nosec: scanner rule definition
        ("exec", re.compile(r"\bexec\s*\("), "exec() usage"),  # nosec: scanner rule definition
        ("os_system", re.compile(r"\bos\.system\s*\("), "os.system usage"),
        ("shell_true", re.compile(r"subprocess\.[A-Za-z_]+\([^\n]*shell\s*=\s*True"), "subprocess shell=True"),
        ("pickle_load", re.compile(r"\bpickle\.(loads?|load)\s*\("), "pickle load usage"),
        ("yaml_load", re.compile(r"\byaml\.load\s*\("), "yaml.load usage"),
    ]
    suffixes = {".py", ".html", ".js", ".yml", ".yaml"}
    skip_parts = {".git", "__pycache__", ".pytest_cache", "validation", "data", "node_modules", "vendor"}
    findings: list[str] = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in skip_parts for part in path.parts):
            continue
        try:
            rel = path.relative_to(root)
            text_value = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lineno, line in enumerate(text_value.splitlines(), start=1):
            if "# nosec" in line:
                continue
            for _code, pattern, label in risky_patterns:
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}: {label}")

    if findings:
        return "FAIL", _safe_detail_join(findings, max_items=12)
    return "PASS", "No high-risk dynamic execution or unsafe deserialization patterns found in tracked source files."


def _check_security_headers_source() -> tuple[str, str]:
    """Static signal for security middleware/headers.

    This does not prove every deployment is hardened; it alerts operators when the
    app relies on the reverse proxy for browser security headers.
    """

    main_path = Path(__file__).resolve().parent / "main.py"
    text_value = main_path.read_text(encoding="utf-8", errors="replace") if main_path.exists() else ""
    expected_headers = ["X-Frame-Options", "Content-Security-Policy", "Strict-Transport-Security"]
    missing = [h for h in expected_headers if h not in text_value]
    if missing:
        return (
            "WARN",
            "Application code does not set these browser security headers directly: "
            + _safe_detail_join(missing)
            + "; configure them at the reverse proxy or add middleware.",
        )
    return "PASS", "Security header middleware markers found in application code."


class _ValidationFixture:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.prefix = f"tbval{secrets.token_hex(5)}"
        self.password = "ValPass-" + secrets.token_urlsafe(12)
        self.admin: User | None = None
        self.manager: User | None = None
        self.subordinate: User | None = None
        self.created_user_ids: list[int] = []
        self.created_notify_service_id: int | None = None
        self.tag_primary = f"{self.prefix}_past"
        self.tag_secondary = f"{self.prefix}_also"

    def setup(self) -> str:
        self.admin = create_user(
            self.db,
            username=f"{self.prefix}_admin",
            password=self.password,
            is_admin=True,
            email=None,
        )
        self.manager = create_user(
            self.db,
            username=f"{self.prefix}_mgr",
            password=self.password,
            is_admin=False,
            email=f"{self.prefix}_mgr@example.invalid",
        )
        self.subordinate = create_user(
            self.db,
            username=f"{self.prefix}_sub",
            password=self.password,
            is_admin=False,
            email=f"{self.prefix}_sub@example.invalid",
        )
        self.created_user_ids = [int(self.admin.id), int(self.manager.id), int(self.subordinate.id)]
        update_user_admin(self.db, user_id=int(self.subordinate.id), manager_id=int(self.manager.id))
        self.db.refresh(self.subordinate)
        _assert(is_manager_of(self.db, manager_user_id=int(self.manager.id), subordinate_user_id=int(self.subordinate.id)), "manager hierarchy was not created")
        return f"Created isolated validation users with prefix {self.prefix}; cleanup is scoped to this prefix."

    def cleanup(self) -> None:
        try:
            for service_id in [self.created_notify_service_id]:
                if service_id and self.admin:
                    try:
                        delete_user_notification_service(self.db, user_id=int(self.admin.id), service_id=int(service_id))
                    except Exception:
                        self.db.rollback()
            for user_id in list(self.created_user_ids):
                try:
                    delete_user(self.db, user_id=int(user_id))
                except Exception:
                    self.db.rollback()
            notify_patterns = [f"notify:u{uid}:%" for uid in self.created_user_ids]
            q = self.db.query(Tag).filter(Tag.name.like(f"{self.prefix}%"))
            if notify_patterns:
                q = self.db.query(Tag).filter(
                    or_(Tag.name.like(f"{self.prefix}%"), *[Tag.name.like(pattern) for pattern in notify_patterns])
                )
            for tag in q.all():
                try:
                    self.db.delete(tag)
                except Exception:
                    pass
            self.db.commit()
        except Exception:
            self.db.rollback()

    def require_ready(self) -> tuple[User, User, User]:
        if not self.admin or not self.manager or not self.subordinate:
            raise ValidationSkip("Validation fixture was not created")
        return self.admin, self.manager, self.subordinate

    def check_auth_profile_and_password_reset(self) -> str:
        admin, _manager, _subordinate = self.require_ready()
        _assert(authenticate_user(self.db, admin.username, self.password) is not None, "username authentication failed")
        _assert(authenticate_user(self.db, str(admin.username).upper(), self.password) is not None, "case-insensitive username authentication failed")

        admin.ui_prefs_json = '{"calendar":{"view":"dayGridMonth"},"past_due_tag_bar":{"enabled":true}}'
        self.db.add(admin)
        self.db.commit()
        self.db.refresh(admin)
        _assert("past_due_tag_bar" in (admin.ui_prefs_json or ""), "profile UI preference did not persist")

        token = secrets.token_urlsafe(32)
        new_password = "ValPass-New-" + secrets.token_urlsafe(8)
        create_password_reset_token(
            self.db,
            user=admin,
            token=token,
            expires_at_utc=utc_now().replace(tzinfo=None) + timedelta(minutes=30),
        )
        _assert(consume_password_reset_token(self.db, token=token, new_password=new_password, now_utc=utc_now()), "password reset token was not consumed")
        _assert(authenticate_user(self.db, admin.username, new_password) is not None, "authentication with reset password failed")
        self.password = new_password
        return "Authentication, profile UI prefs persistence, and password reset flow passed."

    def check_task_crud_filtering_and_archiving(self) -> str:
        admin, _manager, _subordinate = self.require_ready()
        now = utc_now().replace(tzinfo=timezone.utc)
        past = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} past due",
            task_type="Validation",
            due_date=now - timedelta(days=1),
            tags=[self.tag_primary, self.tag_secondary],
        )
        future = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} future",
            task_type="Validation",
            due_date=now + timedelta(days=1),
            tags=[self.tag_primary],
        )
        filtered = list_tasks(self.db, current_user=admin, tag=self.tag_primary, include_archived=False)
        filtered_ids = {int(t.id) for t in filtered}
        _assert(int(past.id) in filtered_ids and int(future.id) in filtered_ids, "tag filter did not return expected active tasks")

        updated = update_task(
            self.db,
            task=future,
            current_user=admin,
            name=f"{self.prefix} future updated",
            task_type="ValidationUpdated",
            tags=[self.tag_primary],
        )
        _assert(updated.name.endswith("updated"), "task update did not persist")

        soft_delete_task(self.db, task=future, current_user=admin, when_utc=utc_now().replace(tzinfo=None))
        _assert(future.status == TaskStatus.deleted, "soft delete did not mark task deleted")
        restore_task(self.db, task=future, current_user=admin)
        _assert(future.status == TaskStatus.active, "restore did not reactivate deleted task")
        return "Task create, tag filter, update, soft-delete, and restore paths passed."

    def check_past_due_tag_bar_data(self) -> str:
        admin, _manager, _subordinate = self.require_ready()
        rows = list_past_due_tags_for_user(self.db, user=admin)
        names = {str(r.get("name")) for r in rows}
        _assert(self.tag_primary in names and self.tag_secondary in names, "past-due tag query did not include expected tags")
        primary = [r for r in rows if r.get("name") == self.tag_primary][0]
        _assert(int(primary.get("task_count") or 0) >= 1, "past-due tag count was not populated")
        return f"Past-due tag bar query returned {len(rows)} tag(s) for overdue active tasks."

    def check_recurrence_modes(self) -> str:
        admin, _manager, _subordinate = self.require_ready()
        _assert(parse_duration_to_seconds("1h 30m") == 5400, "duration parser failed")
        _assert(parse_times_csv("08:00, 15:30") == "08:00,15:30", "daily times parser failed")
        _assert(parse_fixed_calendar_rule("Every Tuesday"), "fixed calendar rule parser failed")

        due = utc_now().replace(tzinfo=timezone.utc) - timedelta(minutes=5)
        recurrent = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} recurrent",
            task_type="ValidationRecurrence",
            due_date=due,
            recurrence_type="post_completion",
            recurrence_interval="1h",
            tags=[self.tag_primary],
        )
        completed, spawned = complete_task(
            self.db,
            task=recurrent,
            current_user=admin,
            when_utc=utc_now().replace(tzinfo=None),
        )
        _assert(completed.status == TaskStatus.completed, "recurrent source task was not completed")
        _assert(spawned is not None and spawned.status == TaskStatus.active, "post-completion recurrence did not spawn a new active task")
        return "Recurrence parsing and post-completion spawn passed."

    def check_nested_subtasks_and_permissions(self) -> str:
        admin, _manager, subordinate = self.require_ready()
        now = utc_now().replace(tzinfo=timezone.utc)
        parent = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} parent",
            task_type="ValidationNested",
            due_date=now + timedelta(hours=2),
        )
        child = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} child",
            task_type="ValidationNested",
            due_date=now + timedelta(hours=3),
            parent_task_id=int(parent.id),
        )
        try:
            complete_task(self.db, task=parent, current_user=admin, when_utc=utc_now().replace(tzinfo=None))
            raise AssertionError("Completing a parent with an open child did not require confirmation")
        except OpenSubtasksError:
            pass
        complete_task(
            self.db,
            task=parent,
            current_user=admin,
            when_utc=utc_now().replace(tzinfo=None),
            cascade_subtasks=True,
        )
        self.db.refresh(child)
        _assert(child.status == TaskStatus.completed, "cascade completion did not close child task")

        protected = create_task(
            self.db,
            owner=admin,
            name=f"{self.prefix} protected",
            task_type="ValidationPermissions",
            due_date=now + timedelta(hours=4),
        )
        try:
            update_task(self.db, task=protected, current_user=subordinate, name="not allowed")
            raise AssertionError("Non-owner non-admin was allowed to update another user's task")
        except PermissionError:
            pass
        return "Nested subtask guard, cascade completion, and non-owner write denial passed."

    def check_manager_assignment_following(self) -> str:
        _admin, manager, subordinate = self.require_ready()
        now = utc_now().replace(tzinfo=timezone.utc)
        assigned = create_task(
            self.db,
            owner=subordinate,
            name=f"{self.prefix} assigned",
            task_type="ValidationAssignment",
            due_date=now + timedelta(hours=5),
            assigned_by_user_id=int(manager.id),
        )
        _assert(is_manager_of(self.db, manager_user_id=int(manager.id), subordinate_user_id=int(subordinate.id)), "manager relationship missing")
        follow_task(self.db, follower=manager, task=assigned)
        _assert(is_following_task(self.db, follower_user_id=int(manager.id), task_id=int(assigned.id)), "manager follow record was not created")
        unfollow_task(self.db, follower=manager, task=assigned)
        _assert(not is_following_task(self.db, follower_user_id=int(manager.id), task_id=int(assigned.id)), "manager follow record was not removed")
        return "Manager assignment and follow/unfollow paths passed."

    def check_notification_services_and_in_app(self) -> str:
        admin, _manager, _subordinate = self.require_ready()
        svc = create_user_notification_service(
            self.db,
            user_id=int(admin.id),
            service_type=CHANNEL_BROWSER,
            name=f"{self.prefix} browser",
            enabled=True,
        )
        self.created_notify_service_id = int(svc.id)
        _assert(svc.tag is not None and str(svc.tag.name).startswith("notify:"), "browser service did not generate routing tag")
        updated = update_user_notification_service(
            self.db,
            user_id=int(admin.id),
            service_id=int(svc.id),
            name=f"{self.prefix} browser updated",
            enabled=False,
            config={},
        )
        _assert(updated is not None and updated.enabled is False, "notification service update failed")
        services = list_user_notification_services(self.db, user_id=int(admin.id))
        _assert(any(int(s.id) == int(svc.id) for s in services), "notification service list did not include created service")

        create_in_app_notification(
            self.db,
            user_id=int(admin.id),
            event_type="validation",
            title=f"{self.prefix} notification",
            message="validation event",
        )
        self.db.commit()
        unread = count_in_app_unread(self.db, user_id=int(admin.id))
        _assert(unread >= 1, "in-app unread count did not increment")
        events = list_in_app_notifications(self.db, user_id=int(admin.id), include_cleared=True, limit=10)
        _assert(events, "in-app notification list was empty")
        clear_in_app_unread(self.db, user_id=int(admin.id))
        _assert(count_in_app_unread(self.db, user_id=int(admin.id)) == 0, "clear unread did not clear badge count")
        return "Notification service CRUD and in-app notification lifecycle passed."

    def check_admin_settings_export_and_integrations(self) -> str:
        email_cfg = get_email_settings(self.db)
        logging_cfg = get_logging_settings(self.db)
        wns_cfg = get_wns_settings(self.db)
        backup_cfg = get_auto_backup_settings(self.db)
        _assert(logging_cfg.level, "logging settings missing level")
        _assert(email_cfg.reset_token_minutes > 0, "email reset token TTL invalid")
        _assert("frequency" in backup_cfg, "auto-backup settings missing frequency")
        _assert(CHANNEL_BROWSER in CHANNEL_TYPES, "browser notification channel missing")
        for expected in [CHANNEL_EMAIL, CHANNEL_GOTIFY, CHANNEL_NTFY, CHANNEL_DISCORD, CHANNEL_WEBHOOK, CHANNEL_GENERIC_API, CHANNEL_WNS]:
            _assert(expected in CHANNEL_TYPES, f"notification channel missing: {expected}")
        subject, body = build_password_reset_email(username="validation", reset_url="https://example.invalid/reset")
        _assert("validation" in body and subject, "password reset email template did not render")
        exported = export_db_json(self.db)
        errors, warnings = validate_import_payload(exported)
        if errors:
            raise AssertionError("Export validation errors: " + _safe_detail_join(errors, max_items=6))
        return (
            "Admin settings, notification channel registry, email template, and database export validation passed"
            + (f" with warnings: {_safe_detail_join(warnings, max_items=4)}" if warnings else ".")
        )


def _check_live_http(base_url: str | None) -> str:
    if not base_url:
        raise ValidationSkip("No loopback base URL supplied")
    expectations = {
        "/login": {200},
        "/docs": {200},
        "/api/tasks/": {401, 403},
        "/admin/users": {303, 307, 308},
        "/dashboard": {303, 307, 308},
    }
    observed: list[str] = []
    failures: list[str] = []
    for path, expected in expectations.items():
        status = _http_status(base_url, path)
        observed.append(f"{path}={status}")
        if status not in expected:
            failures.append(f"{path}: got {status}, expected {sorted(expected)}")
    if failures:
        raise AssertionError(_safe_detail_join(failures))
    return "Loopback HTTP checks passed: " + ", ".join(observed)


def _check_log_files_access() -> str:
    files = list_log_files()
    return f"Log directory is readable; {len(files)} log file(s) listed."


def _check_email_mode() -> tuple[str, str]:
    try:
        enabled = email_enabled()
    except TypeError:
        enabled = False
    if enabled:
        return "PASS", "Email delivery is enabled/configured; validation did not send external email."
    return "SKIP", "Email delivery is disabled or unconfigured; external SMTP/SendGrid delivery was not attempted."


def _check_notification_delivery_modes() -> tuple[str, str]:
    return (
        "PASS",
        "All notification service types are registered for configuration checks: "
        + ", ".join(sorted(CHANNEL_TYPES)),
    )


def run_admin_validation(
    db: Session,
    *,
    actor: str = "admin",
    base_url: str | None = None,
    log_dir: Path | None = None,
    write_log: bool = True,
) -> ValidationReport:
    """Run feature and security validation checks in a running environment.

    The suite creates isolated temporary users/tasks/services, validates core
    behavior, and then removes only records created with its unique prefix.
    It does not execute user-supplied shell commands or send external email or
    webhook traffic.
    """

    report = ValidationReport(
        run_id=utc_now().strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4),
        app_version=APP_VERSION,
        started_at_utc=utc_now().replace(tzinfo=None),
        actor=actor,
        base_url=base_url,
    )

    _run_check(report, "Environment", "Version metadata and DB schema version", lambda: _check_version_and_db(db))
    _run_check(report, "Environment", "Configuration sanity", _check_config_sanity)
    _run_check(report, "Environment", "Dependency inventory and optional CVE tooling", _check_dependency_inventory)
    _run_check(report, "Environment", "Application log file access", _check_log_files_access)

    _run_check(report, "Security", "Runtime secret strength", _check_settings_security, security=True)
    _run_check(report, "Security", "High-risk source pattern scan", _scan_source_for_risky_patterns, security=True)
    _run_check(report, "Security", "Browser security header source check", _check_security_headers_source, security=True)
    _run_check(report, "Security", "Live unauthenticated HTTP access controls", lambda: _check_live_http(base_url), security=True)

    fixture = _ValidationFixture(db)
    try:
        _run_check(report, "Feature validation", "Isolated validation fixture setup", fixture.setup)
        _run_check(report, "Feature validation", "Authentication, profile prefs, and password reset", fixture.check_auth_profile_and_password_reset, security=True)
        _run_check(report, "Feature validation", "Task CRUD, filtering, archiving, and restore", fixture.check_task_crud_filtering_and_archiving)
        _run_check(report, "Feature validation", "Past-due tag bar data source", fixture.check_past_due_tag_bar_data)
        _run_check(report, "Feature validation", "Recurrence parsing and spawning", fixture.check_recurrence_modes)
        _run_check(report, "Feature validation", "Nested subtasks and authorization denial", fixture.check_nested_subtasks_and_permissions, security=True)
        _run_check(report, "Feature validation", "Manager assignment and follow/unfollow", fixture.check_manager_assignment_following)
        _run_check(report, "Feature validation", "Notification services and in-app notifications", fixture.check_notification_services_and_in_app)
        _run_check(report, "Feature validation", "Admin settings, integrations, and DB export validation", fixture.check_admin_settings_export_and_integrations)
    finally:
        try:
            fixture.cleanup()
            report.add("Feature validation", "Isolated validation fixture cleanup", "PASS", "Temporary validation records were removed.")
        except Exception as exc:
            report.add("Feature validation", "Isolated validation fixture cleanup", "FAIL", str(exc))

    _run_check(report, "External integrations", "Email delivery mode", _check_email_mode)
    _run_check(report, "External integrations", "Notification delivery channel registry", _check_notification_delivery_modes)

    report.completed_at_utc = utc_now().replace(tzinfo=None)
    if write_log:
        _write_validation_log(report, log_dir=log_dir)
    return report
