from __future__ import annotations

from .clock import utc_now

import html
import json
import logging
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib import parse, request

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload, sessionmaker

from .config import get_settings
from .emailer import send_email
from .meta_settings import get_wns_settings
from .models import (
    NotificationEvent,
    Tag,
    Task,
    User,
    UserNotificationChannel,
    UserNotificationService,
    UserNotificationTag,
)

logger = logging.getLogger("timeboardapp.notifications")

# ---- Task event types (stable API) -------------------------------------------------

EVENT_CREATED = "created"
EVENT_UPDATED = "updated"
EVENT_PAST_DUE = "past_due"
EVENT_COMPLETED = "completed"
EVENT_ARCHIVED = "archived"

EVENT_TYPES = {
    EVENT_CREATED,
    EVENT_UPDATED,
    EVENT_PAST_DUE,
    EVENT_COMPLETED,
    EVENT_ARCHIVED,
}

# ---- Notification service types ----------------------------------------------------
#
# These strings are persisted in the database.
#
CHANNEL_BROWSER = "browser"
CHANNEL_EMAIL = "email"
CHANNEL_GOTIFY = "gotify"
CHANNEL_NTFY = "ntfy"
CHANNEL_WEBHOOK = "webhook"
CHANNEL_GENERIC_API = "generic_api"
CHANNEL_WNS = "wns"
# Legacy/extra (still supported, not required):
CHANNEL_DISCORD = "discord"

CHANNEL_TYPES = [
    CHANNEL_BROWSER,
    CHANNEL_EMAIL,
    CHANNEL_GOTIFY,
    CHANNEL_NTFY,
    CHANNEL_WEBHOOK,
    CHANNEL_GENERIC_API,
    CHANNEL_WNS,
    CHANNEL_DISCORD,
]

# Notification routing tags created for service entries.
NOTIFY_TAG_PREFIX = "notify:"


# ---- Async delivery status --------------------------------------------------------

DELIVERY_QUEUED = "queued"
DELIVERY_SENDING = "sending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_SKIPPED = "skipped"

DELIVERY_STATUSES = {
    DELIVERY_QUEUED,
    DELIVERY_SENDING,
    DELIVERY_SENT,
    DELIVERY_FAILED,
    DELIVERY_SKIPPED,
}


def _json_loads(s: str | None) -> dict:
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _json_dumps(obj: dict | None) -> str:
    try:
        return json.dumps(obj or {}, separators=(",", ":"), sort_keys=True)
    except Exception:
        return "{}"


def _truncate(s: str, n: int) -> str:
    txt = str(s or "")
    if len(txt) <= int(n):
        return txt
    return txt[: max(0, int(n) - 1)] + "…"


def _now_utc_naive() -> datetime:
    return utc_now().replace(tzinfo=None)


def _safe_url_for_log(url: str) -> str:
    """Return a log-safe URL string.

    Omits query strings (commonly contain tokens).
    """

    raw = str(url or "")

    # Redact likely-secret path segments (e.g., Discord webhook tokens which
    # live in the URL path).
    tokenish = re.compile(r"^[A-Za-z0-9._~-]{24,}$")
    hexish = re.compile(r"^[a-fA-F0-9]{32,}$")

    try:
        p = parse.urlparse(raw)
        if not (p.scheme and p.netloc):
            return raw

        path = p.path or ""
        parts = [seg for seg in path.split("/") if seg]
        redacted: list[str] = []
        for seg in parts:
            if tokenish.match(seg) or hexish.match(seg):
                redacted.append("<redacted>")
            else:
                redacted.append(seg)

        safe_path = ("/" + "/".join(redacted)) if path.startswith("/") else "/".join(redacted)
        safe = f"{p.scheme}://{p.netloc}{safe_path}"
        return _truncate(safe, 200)
    except Exception:
        return _truncate(raw, 200)


def _format_exception(e: Exception, *, max_len: int = 800) -> str:
    name = type(e).__name__
    msg = ""
    try:
        msg = str(e)
    except Exception:
        msg = ""
    base = f"{name}: {msg}" if msg else name
    return _truncate(base, int(max_len))


def _normalize_service_type(service_type: str) -> str:
    st = str(service_type or "").strip().lower()
    if st not in CHANNEL_TYPES:
        raise ValueError("Invalid service_type")
    return st


def _format_due(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        # Keep stable / unambiguous format for notifications.
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(dt)


def _event_action(event_type: str) -> str:
    et = str(event_type or "").strip().lower()
    if et == EVENT_CREATED:
        return "CREATED"
    if et == EVENT_UPDATED:
        return "UPDATED"
    if et == EVENT_PAST_DUE:
        return "PAST_DUE"
    if et == EVENT_COMPLETED:
        return "COMPLETED"
    if et == EVENT_ARCHIVED:
        return "ARCHIVED"
    return et.upper() or "EVENT"


def _task_internal_url(task_id: int) -> str:
    settings = get_settings()
    base = str(getattr(settings.app, "base_url", "") or "").strip().rstrip("/")
    if base:
        return f"{base}/tasks/{int(task_id)}/edit"
    return f"/tasks/{int(task_id)}/edit"


def _task_link_url(task: Task) -> str:
    # Prefer user-provided URL if present, otherwise link to the in-app task editor.
    u = str(getattr(task, "url", "") or "").strip()
    if u:
        return u
    tid = getattr(task, "id", None)
    if tid is None:
        return ""
    return _task_internal_url(int(tid))


def _build_task_notification(*, task: Task, event_type: str) -> tuple[str, str, str | None, dict]:
    """Return (title, message_text, message_html, payload)."""
    et = str(event_type or "").strip().lower()
    action = _event_action(et)
    link_url = _task_link_url(task)

    try:
        tags = [t.name for t in (task.tags or [])]
    except Exception:
        tags = []
    tags_str = ", ".join(tags)

    due = _format_due(getattr(task, "due_date_utc", None))

    if et == EVENT_CREATED:
        title = "Task created"
    elif et == EVENT_UPDATED:
        title = "Task updated"
    elif et == EVENT_PAST_DUE:
        title = "Task overdue"
    elif et == EVENT_COMPLETED:
        title = "Task completed"
    elif et == EVENT_ARCHIVED:
        title = "Task archived"
    else:
        title = "Task notification"

    # Canonical text format (works across most services).
    # Template (conceptual): <CHANGE_ACTION>:<URL><TASK_NAME></URL> -<DUE_DATE> [<TAGS>]
    name = str(getattr(task, "name", "") or "")
    message_text = f"{action}:{link_url} {name} -{due} [{tags_str}]".strip()

    # HTML variant (used by email if enabled).
    message_html = None
    if link_url:
        safe_name = html.escape(name)
        safe_url = html.escape(link_url, quote=True)
        message_html = f"<p><strong>{html.escape(action)}</strong>: <a href=\"{safe_url}\">{safe_name}</a> -{html.escape(due)} [{html.escape(tags_str)}]</p>"
    else:
        message_html = f"<p><strong>{html.escape(action)}</strong>: {html.escape(name)} -{html.escape(due)} [{html.escape(tags_str)}]</p>"

    payload: dict[str, Any] = {
        "event_type": et,
        "change_action": action,
        "message_text": message_text,
        "message_html": message_html,
        "url": link_url,
        "due_date_display": due,
        "task": {
            "id": int(task.id) if getattr(task, "id", None) is not None else None,
            "user_id": int(task.user_id) if getattr(task, "user_id", None) is not None else None,
            "name": task.name,
            "task_type": getattr(task, "task_type", None),
            "status": str(getattr(task, "status", "")),
            "due_date_utc": getattr(task, "due_date_utc", None).isoformat() if getattr(task, "due_date_utc", None) else None,
            "url": getattr(task, "url", None),
        },
        "tags": tags,
        "occurred_at_utc": utc_now().replace(tzinfo=None).isoformat(),
    }

    return title, message_text, message_html, payload


# ---- Service entry management ------------------------------------------------------


def list_user_notification_services(db: Session, *, user_id: int) -> list[UserNotificationService]:
    return (
        db.query(UserNotificationService)
        .filter(UserNotificationService.user_id == int(user_id))
        .order_by(UserNotificationService.id.asc())
        .all()
    )


def user_has_enabled_browser_service(db: Session, *, user_id: int) -> bool:
    row = (
        db.query(UserNotificationService.id)
        .filter(UserNotificationService.user_id == int(user_id))
        .filter(UserNotificationService.service_type == CHANNEL_BROWSER)
        .filter(UserNotificationService.enabled.is_(True))
        .first()
    )
    return bool(row)


def _generate_notification_tag_name(*, user_id: int, service_type: str) -> str:
    st = _normalize_service_type(service_type)
    token = secrets.token_hex(4)
    # Keep tags reasonably short; Tag.name max is 64.
    # Example: notify:u12:ntfy:deadbeef
    return f"{NOTIFY_TAG_PREFIX}u{int(user_id)}:{st}:{token}".lower()


def create_user_notification_service(
    db: Session,
    *,
    user_id: int,
    service_type: str,
    name: str | None = None,
    enabled: bool = True,
    config: dict | None = None,
) -> UserNotificationService:
    """Create a new notification service entry and its routing tag."""
    st = _normalize_service_type(service_type)

    # Demo safety: disallow enabling external services.
    try:
        s = get_settings()
        if getattr(s, "demo", None) and bool(getattr(s.demo, "enabled", False)):
            if bool(getattr(s.demo, "disable_external_apis", True)) and st != CHANNEL_BROWSER:
                enabled = False
    except Exception:
        pass

    # Create a unique tag.
    tag = None
    for _ in range(10):
        tag_name = _generate_notification_tag_name(user_id=int(user_id), service_type=st)
        tag = Tag(name=tag_name)
        db.add(tag)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            tag = None
            continue
    if tag is None or tag.id is None:
        raise RuntimeError("Failed to generate unique notification tag")

    svc = UserNotificationService(
        user_id=int(user_id),
        service_type=st,
        name=(str(name).strip() if name else None),
        enabled=bool(enabled),
        config_json=_json_dumps(config or {}),
        tag_id=int(tag.id),
    )
    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


def update_user_notification_service(
    db: Session,
    *,
    user_id: int,
    service_id: int,
    name: str | None = None,
    enabled: bool | None = None,
    config: dict | None = None,
) -> UserNotificationService | None:
    svc = (
        db.query(UserNotificationService)
        .filter(UserNotificationService.id == int(service_id))
        .filter(UserNotificationService.user_id == int(user_id))
        .first()
    )
    if not svc:
        return None

    if name is not None:
        svc.name = str(name).strip() if str(name).strip() else None
    if enabled is not None:
        desired = bool(enabled)
        # Demo safety: disallow enabling external services.
        try:
            s = get_settings()
            if getattr(s, "demo", None) and bool(getattr(s.demo, "enabled", False)):
                if bool(getattr(s.demo, "disable_external_apis", True)) and str(svc.service_type or "").lower() != CHANNEL_BROWSER:
                    desired = False
        except Exception:
            pass
        svc.enabled = desired
    if config is not None:
        svc.config_json = _json_dumps(config)

    db.add(svc)
    db.commit()
    db.refresh(svc)
    return svc


def delete_user_notification_service(db: Session, *, user_id: int, service_id: int) -> bool:
    svc = (
        db.query(UserNotificationService)
        .filter(UserNotificationService.id == int(service_id))
        .filter(UserNotificationService.user_id == int(user_id))
        .first()
    )
    if not svc:
        return False

    # Delete the routing tag as well (it is service-generated).
    tag_id = int(svc.tag_id)

    db.delete(svc)
    try:
        db.flush()
    except Exception:
        db.rollback()
        return False

    # Tag deletion cascades through task_tags.
    tag = db.query(Tag).filter(Tag.id == tag_id).first()
    if tag:
        db.delete(tag)

    db.commit()
    return True


# ---- Legacy tag subscription + per-type channel config ----------------------------
#
# These remain for backward compatibility and for migration helpers. New deployments
# should prefer UserNotificationService entries.


def get_user_notification_tag_ids(db: Session, *, user_id: int) -> set[int]:
    rows = db.query(UserNotificationTag.tag_id).filter(UserNotificationTag.user_id == int(user_id)).all()
    return {int(r[0]) for r in rows}


def set_user_notification_tag_ids(db: Session, *, user_id: int, tag_ids: set[int]) -> None:
    # Replace subscriptions.
    db.query(UserNotificationTag).filter(UserNotificationTag.user_id == int(user_id)).delete()
    for tid in sorted({int(t) for t in (tag_ids or set())}):
        db.add(UserNotificationTag(user_id=int(user_id), tag_id=int(tid)))
    db.commit()


def get_user_channels(db: Session, *, user_id: int) -> dict[str, UserNotificationChannel]:
    out: dict[str, UserNotificationChannel] = {}
    rows = db.query(UserNotificationChannel).filter(UserNotificationChannel.user_id == int(user_id)).all()
    for r in rows:
        out[str(r.channel_type)] = r
    return out


def upsert_user_channel(
    db: Session,
    *,
    user_id: int,
    channel_type: str,
    enabled: bool,
    config: dict | None = None,
) -> UserNotificationChannel:
    ctype = _normalize_service_type(channel_type)

    ch = (
        db.query(UserNotificationChannel)
        .filter(UserNotificationChannel.user_id == int(user_id))
        .filter(UserNotificationChannel.channel_type == ctype)
        .first()
    )
    if not ch:
        ch = UserNotificationChannel(user_id=int(user_id), channel_type=ctype)

    ch.enabled = bool(enabled)
    if config is not None:
        ch.config_json = _json_dumps(config)

    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


# ---- HTTP helpers + integrations --------------------------------------------------


def _http_request(
    *,
    url: str,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 10,
) -> tuple[int, str]:
    from .outbound import send_request
    return send_request(url=url, method=method, headers=headers, data=data, timeout=timeout)


def _send_gotify(*, config: dict, title: str, message: str) -> None:
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    token = str(config.get("token") or "").strip()
    if not base_url or not token:
        raise ValueError("Gotify requires base_url and token")

    try:
        priority = int(config.get("priority") or 5)
    except Exception:
        priority = 5
    if priority < 0:
        priority = 0
    if priority > 10:
        priority = 10

    # Prefer auth via header instead of query string so reverse proxies/WAFs
    # that strip or block sensitive query params do not break delivery.
    url = f"{base_url}/message"
    payload = {"title": title, "message": message, "priority": priority}
    data = json.dumps(payload).encode("utf-8")
    _http_request(
        url=url,
        headers={
            "Content-Type": "application/json",
            "X-Gotify-Key": token,
        },
        data=data,
    )


def _send_ntfy(*, config: dict, title: str, message: str, click_url: str | None = None) -> None:
    server = str(config.get("server_url") or "https://ntfy.sh").strip().rstrip("/")
    topic = str(config.get("topic") or "").strip()
    if not topic:
        raise ValueError("ntfy requires topic")

    url = f"{server}/{parse.quote(topic)}"
    headers: dict[str, str] = {
        "Title": title,
        "Content-Type": "text/plain; charset=utf-8",
    }
    token = str(config.get("token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    priority = str(config.get("priority") or "")
    if priority:
        headers["Priority"] = priority
    if click_url:
        headers["Click"] = str(click_url)

    _http_request(url=url, headers=headers, data=(message or "").encode("utf-8"))


def _send_discord(*, config: dict, message: str | None, embeds: list[dict[str, Any]] | None = None) -> None:
    """Send a Discord webhook message.

    Discord accepts either `content` and/or `embeds`.
    """

    # Accept a few legacy/common keys.
    webhook_url = str(config.get("webhook_url") or config.get("url") or config.get("webhook") or "").strip()
    if not webhook_url:
        raise ValueError("Discord requires webhook_url")

    # Discord webhook content supports Markdown but not HTML.
    content = str(message or "")
    if len(content) > 2000:
        content = content[:1997] + "..."

    payload: dict[str, Any] = {
        "content": content,
        # Prevent accidental @mentions from task names/tags.
        "allowed_mentions": {"parse": []},
    }
    if embeds:
        payload["embeds"] = embeds

    data = json.dumps(payload).encode("utf-8")
    _http_request(url=webhook_url, headers={"Content-Type": "application/json"}, data=data)


def _build_discord_markdown(*, title: str, payload: dict) -> str:
    """Build a Discord-friendly Markdown message.

    Discord supports a Markdown-like syntax in message content, but not HTML.
    """

    action = str(payload.get("change_action") or "").strip() or str(title or "Notification")
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    name = str(task.get("name") or "").strip()
    task_type = str(task.get("task_type") or "").strip()
    due_disp = str(payload.get("due_date_display") or "").strip()
    url = str(payload.get("url") or payload.get("task_internal_url") or "").strip()

    tags_val = payload.get("tags")
    tags: list[str] = []
    if isinstance(tags_val, list):
        tags = [str(t).strip() for t in tags_val if str(t).strip()]
    tags_str = ", ".join(tags)

    header = f"**{action}**"
    if task_type:
        header += f" [{task_type}]"
    if name:
        header += f" {name}"

    lines: list[str] = [header.strip()]
    if url:
        lines.append(url)
    if due_disp:
        lines.append(f"Due: {due_disp}")
    if tags_str:
        lines.append(f"Tags: {tags_str}")

    return "\n".join([ln for ln in lines if ln]).strip()


def _is_http_url(url: str) -> bool:
    """Return True when URL is an absolute http(s) URL."""
    try:
        p = parse.urlparse(str(url or "").strip())
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def _truncate(s: str, max_len: int) -> str:
    s2 = str(s or "")
    if max_len <= 0:
        return ""
    if len(s2) <= max_len:
        return s2
    if max_len <= 3:
        return s2[:max_len]
    return s2[: max_len - 3] + "..."


def _build_discord_embeds(*, title: str, payload: dict) -> list[dict[str, Any]]:
    """Build Discord embeds.

    Primary goal: make the task name a clickable link to the task entry.

    Discord only supports masked links (hyperlinked text) in embeds, not in
    standard message content.
    """

    action = str(payload.get("change_action") or "").strip() or str(title or "Notification")
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    name = str(task.get("name") or "").strip()
    task_type = str(task.get("task_type") or "").strip()
    due_disp = str(payload.get("due_date_display") or "").strip()
    url = str(payload.get("url") or payload.get("task_internal_url") or "").strip()

    tags_val = payload.get("tags")
    tags: list[str] = []
    if isinstance(tags_val, list):
        tags = [str(t).strip() for t in tags_val if str(t).strip()]
    tags_str = ", ".join(tags)

    # Only attach an embed URL when it is a valid absolute URL. Discord
    # rejects invalid URLs at the API layer.
    if not name or not _is_http_url(url):
        return []

    desc = f"**{action}**"
    if task_type:
        desc += f" [{task_type}]"

    embed: dict[str, Any] = {
        "title": _truncate(name, 256),
        "url": url,
        "description": _truncate(desc, 4096),
    }

    fields: list[dict[str, Any]] = []
    if due_disp:
        fields.append({"name": "Due", "value": _truncate(due_disp, 1024), "inline": True})
    if tags_str:
        fields.append({"name": "Tags", "value": _truncate(tags_str, 1024), "inline": False})
    if fields:
        embed["fields"] = fields

    return [embed]


def _send_webhook(*, config: dict, payload: dict) -> None:
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("Webhook requires url")

    headers: dict[str, str] = {"Content-Type": "application/json"}
    secret = str(config.get("secret") or "").strip()
    if secret:
        secret_header = "X-TimeboardApp-Secret"
        legacy_secret_header = secret_header.replace("App", "")
        headers[secret_header] = secret
        # Backward-compatibility: also send the legacy header name.
        headers[legacy_secret_header] = secret

    data = json.dumps(payload).encode("utf-8")
    _http_request(url=url, headers=headers, data=data)


def _send_generic_api(*, config: dict, payload: dict) -> None:
    url = str(config.get("url") or "").strip()
    if not url:
        raise ValueError("Generic API requires url")

    method = str(config.get("method") or "POST").strip().upper()
    headers: dict[str, str] = {}
    raw_headers = config.get("headers")
    if isinstance(raw_headers, dict):
        headers.update({str(k): str(v) for k, v in raw_headers.items() if k and v is not None})
    token = str(config.get("token") or "").strip()
    if token:
        headers.setdefault("Authorization", f"Bearer {token}")

    data = None
    if method in {"POST", "PUT", "PATCH"}:
        headers.setdefault("Content-Type", "application/json")
        data = json.dumps(payload).encode("utf-8")

    _http_request(url=url, method=method, headers=headers, data=data)


def _wns_get_access_token(*, package_sid: str, client_secret: str) -> str:
    """Return a WNS access token using OAuth client_credentials."""
    token_url = "https://login.live.com/accesstoken.srf"
    form = {
        "grant_type": "client_credentials",
        "client_id": package_sid,
        "client_secret": client_secret,
        "scope": "notify.windows.com",
    }
    data = parse.urlencode(form).encode("utf-8")
    status, body = _http_request(
        url=token_url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=10,
    )
    if status < 200 or status >= 300:
        raise RuntimeError(f"WNS token request failed ({status})")

    try:
        obj = json.loads(body)
        token = str(obj.get("access_token") or "")
        if not token:
            raise RuntimeError("WNS token response missing access_token")
        return token
    except Exception as e:
        raise RuntimeError("Failed to parse WNS token response") from e


def _xml_escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _send_wns_toast(*, channel_uri: str, access_token: str, title: str, message: str) -> None:
    uri = str(channel_uri or "").strip()
    if not uri:
        raise ValueError("WNS requires channel_uri")

    toast_xml = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
        "<toast>"
        "  <visual>"
        "    <binding template=\"ToastGeneric\">"
        f"      <text>{_xml_escape(title)}</text>"
        f"      <text>{_xml_escape(message)}</text>"
        "    </binding>"
        "  </visual>"
        "</toast>"
    )

    headers = {
        "Content-Type": "text/xml",
        "Authorization": f"Bearer {access_token}",
        "X-WNS-Type": "wns/toast",
    }
    destination = parse.urlsplit(uri)
    host = (destination.hostname or "").lower().rstrip(".")
    if destination.scheme != "https" or not (host == "notify.windows.com" or host.endswith(".notify.windows.com")):
        raise ValueError("WNS channel URI must use HTTPS on notify.windows.com")
    _http_request(url=uri, headers=headers, data=toast_xml.encode("utf-8"), timeout=10)


# ---- Event persistence ------------------------------------------------------------


def create_notification_event(
    db: Session,
    *,
    user_id: int,
    task_id: int | None,
    event_type: str,
    title: str,
    message: str,
    event_key: str | None = None,
    service_id: int | None = None,
    service_type: str | None = None,
    delivery_status: str | None = None,
    delivery_error: str | None = None,
    delivery_attempts: int | None = None,
    last_attempt_at_utc: datetime | None = None,
    delivered_at_utc: datetime | None = None,
) -> NotificationEvent | None:
    et = str(event_type or "").strip().lower()
    if et not in EVENT_TYPES:
        raise ValueError("Invalid event_type")

    ev = NotificationEvent(
        user_id=int(user_id),
        task_id=(int(task_id) if task_id is not None else None),
        service_id=(int(service_id) if service_id is not None else None),
        service_type=(str(service_type).strip().lower() if service_type else None),
        event_type=et,
        event_key=(str(event_key) if event_key else None),
        title=str(title or "Notification"),
        message=str(message or ""),
        delivery_status=(str(delivery_status).strip().lower() if delivery_status else None),
        delivery_error=(str(delivery_error) if delivery_error else None),
        delivery_attempts=(int(delivery_attempts) if delivery_attempts is not None else None),
        last_attempt_at_utc=(last_attempt_at_utc.replace(tzinfo=None) if last_attempt_at_utc else None),
        delivered_at_utc=(delivered_at_utc.replace(tzinfo=None) if delivered_at_utc else None),
    )
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(ev)
    return ev


# ---- Sending ----------------------------------------------------------------------


def _send_notification_via_service(
    db: Session,
    *,
    svc: UserNotificationService,
    user: User,
    title: str,
    message_text: str,
    message_html: str | None,
    payload: dict,
) -> str:
    """Best-effort send wrapper.

    Returns "ok" on success, otherwise "error:...".

    Async delivery uses `_send_notification_via_service_impl` directly so it can
    attach full context (event_id/user_id/service_id) to logs and persist an
    error summary into `notification_events`.
    """
    st = str(svc.service_type or "").strip().lower()
    try:
        _send_notification_via_service_impl(
            db,
            svc=svc,
            user=user,
            title=title,
            message_text=message_text,
            message_html=message_html,
            payload=payload,
        )
        return "ok"
    except Exception as e:
        err = _format_exception(e)
        logger.warning("Notification send failed (%s svc_id=%s): %s", st, getattr(svc, "id", None), err)
        return f"error:{err}"


def _send_notification_via_service_impl(
    db: Session,
    *,
    svc: UserNotificationService,
    user: User,
    title: str,
    message_text: str,
    message_html: str | None,
    payload: dict,
) -> None:
    """Send a notification to a single configured service entry.

    Raises on failure.
    """
    st = str(svc.service_type or "").strip().lower()
    cfg = _json_loads(svc.config_json)

    # Demo safety: disable outbound integrations.
    try:
        s = get_settings()
        if getattr(s, "demo", None) and bool(getattr(s.demo, "enabled", False)):
            if bool(getattr(s.demo, "disable_external_apis", True)) and st != CHANNEL_BROWSER:
                logger.info("Demo mode: blocked external notification send (service_type=%s svc_id=%s)", st, getattr(svc, "id", None))
                return
    except Exception:
        pass

    if st == CHANNEL_BROWSER:
        # Browser notifications are delivered via SSE from NotificationEvent rows.
        return

    if st == CHANNEL_EMAIL:
        to_address = str(cfg.get("to_address") or "").strip() or str(getattr(user, "email", "") or "").strip()
        if not to_address:
            raise ValueError("No recipient email address")
        subject = f"TimeboardApp: {title}"
        send_email(to_address=to_address, subject=subject, body_text=message_text, body_html=message_html, db=db)
        return

    if st == CHANNEL_GOTIFY:
        _send_gotify(config=cfg, title=title, message=message_text)
        return

    if st == CHANNEL_NTFY:
        _send_ntfy(config=cfg, title=title, message=message_text, click_url=str(payload.get("url") or "").strip() or None)
        return

    if st == CHANNEL_DISCORD:
        embeds = _build_discord_embeds(title=title, payload=payload)
        if embeds:
            _send_discord(config=cfg, message="", embeds=embeds)
        else:
            md = _build_discord_markdown(title=title, payload=payload)
            _send_discord(config=cfg, message=md or message_text)
        return

    if st == CHANNEL_WEBHOOK:
        _send_webhook(config=cfg, payload=payload)
        return

    if st == CHANNEL_GENERIC_API:
        _send_generic_api(config=cfg, payload=payload)
        return

    if st == CHANNEL_WNS:
        wns_cfg = get_wns_settings(db)
        if not wns_cfg.enabled:
            raise ValueError("WNS is disabled by admin")
        if not wns_cfg.package_sid or not wns_cfg.client_secret:
            raise ValueError("WNS is not configured (missing package_sid/client_secret)")

        channel_uri = str(cfg.get("channel_uri") or "").strip()
        if not channel_uri:
            raise ValueError("WNS channel_uri not set")

        token = _wns_get_access_token(package_sid=wns_cfg.package_sid, client_secret=wns_cfg.client_secret)
        _send_wns_toast(channel_uri=channel_uri, access_token=token, title=title, message=message_text)
        return

    raise ValueError("Unknown notification service_type")


def _commit_with_retry(db: Session, *, attempts: int = 5, base_sleep: float = 0.05) -> None:
    """Commit a transaction with a small retry loop for SQLite lock contention."""

    tries = max(1, int(attempts))
    delay = float(base_sleep)
    for i in range(tries):
        try:
            db.commit()
            return
        except OperationalError:
            db.rollback()
            if i >= tries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2.0, 1.0)


def _session_engine(db: Session):
    """Return an Engine for the provided Session.

    Avoid passing a live Connection across threads.
    """

    bind = getattr(db, "bind", None) or db.get_bind()
    try:
        # If bind is a Connection, prefer its engine.
        eng = getattr(bind, "engine", None)
        return eng or bind
    except Exception:
        return bind


@dataclass(frozen=True)
class _NotificationSendJob:
    engine: Any
    event_id: int
    user_id: int
    task_id: int | None
    service_id: int | None
    service_type: str
    title: str
    message_text: str
    message_html: str | None
    payload: dict
    # For legacy channel sends only.
    legacy_config_json: str | None = None


class _AsyncNotificationDispatcher:
    def __init__(self, *, max_workers: int = 4, queue_size: int = 1000):
        self._q: queue.Queue[_NotificationSendJob | None] = queue.Queue(maxsize=int(queue_size))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

        n = max(1, int(max_workers))
        for i in range(n):
            t = threading.Thread(
                target=self._worker,
                name=f"timeboardapp-notify-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)

    def submit(self, job: _NotificationSendJob) -> bool:
        if self._stop.is_set():
            return False
        try:
            self._q.put(job, block=False)
            return True
        except queue.Full:
            return False

    def shutdown(self) -> None:
        self._stop.set()
        # Best-effort stop signals.
        for _ in self._threads:
            try:
                self._q.put_nowait(None)
            except Exception:
                break

    def wait_for_idle(self, *, timeout: float = 5.0) -> bool:
        """Best-effort drain for tests."""

        end = time.monotonic() + float(timeout)
        while time.monotonic() < end:
            try:
                if int(getattr(self._q, "unfinished_tasks", 0)) == 0:
                    return True
            except Exception:
                return True
            time.sleep(0.05)
        try:
            return int(getattr(self._q, "unfinished_tasks", 0)) == 0
        except Exception:
            return True

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            if job is None:
                try:
                    self._q.task_done()
                except Exception:
                    pass
                break

            try:
                _process_notification_send_job(job)
            except Exception:
                logger.exception("Unhandled exception in async notification worker")
            finally:
                try:
                    self._q.task_done()
                except Exception:
                    pass


_DISPATCHER: _AsyncNotificationDispatcher | None = None
_DISPATCHER_LOCK = threading.Lock()


def _get_dispatcher() -> _AsyncNotificationDispatcher:
    global _DISPATCHER
    with _DISPATCHER_LOCK:
        if _DISPATCHER is None:
            _DISPATCHER = _AsyncNotificationDispatcher()
        return _DISPATCHER


def shutdown_notification_dispatcher() -> None:
    """Shutdown the async notification dispatcher (best-effort)."""

    global _DISPATCHER
    with _DISPATCHER_LOCK:
        if _DISPATCHER is not None:
            try:
                _DISPATCHER.shutdown()
            finally:
                _DISPATCHER = None


def wait_for_notification_dispatcher_idle(*, timeout: float = 5.0) -> bool:
    """Test helper to wait for queued async notification sends to finish."""

    d = _DISPATCHER
    if d is None:
        return True
    return bool(d.wait_for_idle(timeout=float(timeout)))


def _process_notification_send_job(job: _NotificationSendJob) -> None:
    """Execute a single queued send and persist delivery result on the event."""

    SessionMaker = sessionmaker(bind=job.engine, autocommit=False, autoflush=False)
    db = SessionMaker()
    try:
        ev = db.query(NotificationEvent).filter(NotificationEvent.id == int(job.event_id)).first()
        if not ev:
            return

        # Resolve service/user at send time (respects disabled/deleted services).
        svc: UserNotificationService
        if job.service_id is not None:
            svc_obj = (
                db.query(UserNotificationService)
                .options(joinedload(UserNotificationService.tag))
                .filter(UserNotificationService.id == int(job.service_id))
                .first()
            )
            if not svc_obj:
                ev.delivery_status = DELIVERY_SKIPPED
                ev.delivery_error = "Notification service deleted"
                _commit_with_retry(db)
                return
            if not bool(getattr(svc_obj, "enabled", False)):
                ev.delivery_status = DELIVERY_SKIPPED
                ev.delivery_error = "Notification service disabled"
                _commit_with_retry(db)
                return
            svc = svc_obj
        else:
            # Legacy channel send.
            svc = UserNotificationService(
                user_id=int(job.user_id),
                service_type=str(job.service_type),
                name=None,
                enabled=True,
                config_json=job.legacy_config_json,
                tag_id=0,
            )

        user = db.query(User).filter(User.id == int(job.user_id)).first()
        if not user:
            ev.delivery_status = DELIVERY_FAILED
            ev.delivery_error = "User not found"
            _commit_with_retry(db)
            return

        # Mark attempt.
        now = _now_utc_naive()
        try:
            ev.delivery_attempts = int(getattr(ev, "delivery_attempts", 0) or 0) + 1
        except Exception:
            ev.delivery_attempts = 1
        ev.last_attempt_at_utc = now
        ev.delivery_status = DELIVERY_SENDING
        ev.delivery_error = None
        _commit_with_retry(db)

        try:
            _send_notification_via_service_impl(
                db,
                svc=svc,
                user=user,
                title=job.title,
                message_text=job.message_text,
                message_html=job.message_html,
                payload=dict(job.payload or {}),
            )
        except Exception as e:
            err = _format_exception(e)
            logger.exception(
                "Notification delivery failed (event_id=%s service=%s svc_id=%s user_id=%s task_id=%s): %s",
                int(job.event_id),
                str(job.service_type),
                (int(job.service_id) if job.service_id is not None else None),
                int(job.user_id),
                (int(job.task_id) if job.task_id is not None else None),
                err,
            )
            ev.delivery_status = DELIVERY_FAILED
            ev.delivery_error = err
            ev.delivered_at_utc = None
            _commit_with_retry(db)
            return

        # Success.
        ev.delivery_status = DELIVERY_SENT
        ev.delivery_error = None
        ev.delivered_at_utc = _now_utc_naive()
        _commit_with_retry(db)
    finally:
        try:
            db.close()
        except Exception:
            pass


def notify_task_event(
    db: Session,
    *,
    task: Task,
    event_type: str,
    relevant_tag_ids: set[int] | None = None,
    event_key: str | None = None,
) -> None:
    """Send a task lifecycle notification.

    New model:
      - Each notification service entry has a generated routing tag.
      - If the task contains that tag, a notification is sent to that service.

    Legacy fallback:
      - If no services match, fall back to tag subscriptions + enabled channels.
    """
    et = str(event_type or "").strip().lower()
    if et not in EVENT_TYPES:
        raise ValueError("Invalid event_type")

    # Ensure tags are loaded.
    if not hasattr(task, "tags"):
        task = db.query(Task).options(joinedload(Task.tags)).filter(Task.id == int(task.id)).first() or task

    tag_ids = relevant_tag_ids
    if tag_ids is None:
        try:
            tag_ids = {int(t.id) for t in (task.tags or [])}
        except Exception:
            tag_ids = set()

    user = db.query(User).filter(User.id == int(task.user_id)).first()
    if not user:
        return

    # Find enabled services whose routing tag is present on this task.
    services: list[UserNotificationService] = []
    if tag_ids:
        services = (
            db.query(UserNotificationService)
            .filter(UserNotificationService.user_id == int(user.id))
            .filter(UserNotificationService.enabled.is_(True))
            .filter(UserNotificationService.tag_id.in_(sorted(tag_ids)))
            .order_by(UserNotificationService.id.asc())
            .all()
        )

    if services:
        title, msg_text, msg_html, payload = _build_task_notification(task=task, event_type=et)

        # Enrich payload with full tag list (names) and the internal task URL for convenience.
        payload.setdefault("task_internal_url", _task_internal_url(int(task.id)))

        for svc in services:
            st = str(svc.service_type or "").strip().lower()
            svc_key = f"{event_key}:svc{int(svc.id)}" if event_key else None

            initial_status = DELIVERY_SENT if st == CHANNEL_BROWSER else DELIVERY_QUEUED
            delivered_at = _now_utc_naive() if st == CHANNEL_BROWSER else None
            # Persist event for browser delivery and for dedupe of scheduled events.
            ev = create_notification_event(
                db,
                user_id=int(user.id),
                task_id=int(task.id) if getattr(task, "id", None) is not None else None,
                event_type=et,
                title=title,
                message=msg_text,
                event_key=svc_key,
                service_id=int(svc.id),
                service_type=str(svc.service_type),
                delivery_status=initial_status,
                delivery_error=None,
                delivery_attempts=0,
                last_attempt_at_utc=None,
                delivered_at_utc=delivered_at,
            )
            if ev is None:
                # deduped
                continue

            # Enrich payload per service (useful for webhooks/APIs).
            payload2 = dict(payload)
            payload2["service"] = {
                "id": int(svc.id),
                "service_type": str(svc.service_type),
                "name": svc.name,
                "tag": (svc.tag.name if getattr(svc, "tag", None) is not None else None),
            }

            if st != CHANNEL_BROWSER:
                eng = _session_engine(db)
                if not eng:
                    try:
                        ev.delivery_status = DELIVERY_FAILED
                        ev.delivery_error = "async_dispatch_no_db_bind"
                        _commit_with_retry(db)
                    except Exception:
                        logger.exception(
                            "Failed to persist notification dispatch failure (no db bind) (event_id=%s)",
                            int(ev.id),
                        )
                    continue
                job = _NotificationSendJob(
                    engine=eng,
                    event_id=int(ev.id),
                    user_id=int(user.id),
                    task_id=int(task.id) if getattr(task, "id", None) is not None else None,
                    service_id=int(svc.id),
                    service_type=st,
                    title=title,
                    message_text=msg_text,
                    message_html=msg_html,
                    payload=payload2,
                )
                if not _get_dispatcher().submit(job):
                    # Queue is full or dispatcher stopped: persist the failure.
                    try:
                        ev.delivery_status = DELIVERY_FAILED
                        ev.delivery_error = "async_dispatch_queue_full"
                        _commit_with_retry(db)
                    except Exception:
                        logger.exception(
                            "Failed to persist notification dispatch failure (event_id=%s service=%s svc_id=%s)",
                            int(ev.id),
                            st,
                            int(svc.id),
                        )
        return

    # ---- Legacy fallback ---------------------------------------------------------

    if not tag_ids:
        return
    subscribed = get_user_notification_tag_ids(db, user_id=int(user.id))
    if not subscribed.intersection(tag_ids):
        return

    title, msg_text, msg_html, payload = _build_task_notification(task=task, event_type=et)

    # Deliver via enabled legacy channels.
    channels = (
        db.query(UserNotificationChannel)
        .filter(UserNotificationChannel.user_id == int(user.id))
        .filter(UserNotificationChannel.enabled.is_(True))
        .all()
    )

    if not channels:
        return

    for ch in channels:
        st = str(ch.channel_type or "").strip().lower()
        # Persist one NotificationEvent per channel so browser delivery can
        # filter on service_type, while also allowing per-channel dedupe for
        # scheduled events (e.g. past_due).
        ch_key = f"{event_key}:ch{str(ch.channel_type)}" if event_key else None

        initial_status = DELIVERY_SENT if st == CHANNEL_BROWSER else DELIVERY_QUEUED
        delivered_at = _now_utc_naive() if st == CHANNEL_BROWSER else None
        ev = create_notification_event(
            db,
            user_id=int(user.id),
            task_id=int(task.id) if getattr(task, "id", None) is not None else None,
            event_type=et,
            title=title,
            message=msg_text,
            event_key=ch_key,
            service_id=None,
            service_type=str(ch.channel_type),
            delivery_status=initial_status,
            delivery_error=None,
            delivery_attempts=0,
            last_attempt_at_utc=None,
            delivered_at_utc=delivered_at,
        )
        if ev is None:
            continue

        if st != CHANNEL_BROWSER:
            eng = _session_engine(db)
            if not eng:
                try:
                    ev.delivery_status = DELIVERY_FAILED
                    ev.delivery_error = "async_dispatch_no_db_bind"
                    _commit_with_retry(db)
                except Exception:
                    logger.exception(
                        "Failed to persist legacy notification dispatch failure (no db bind) (event_id=%s)",
                        int(ev.id),
                    )
                continue
            job = _NotificationSendJob(
                engine=eng,
                event_id=int(ev.id),
                user_id=int(user.id),
                task_id=int(task.id) if getattr(task, "id", None) is not None else None,
                service_id=None,
                service_type=st,
                title=title,
                message_text=msg_text,
                message_html=msg_html,
                payload=payload,
                legacy_config_json=ch.config_json,
            )
            if not _get_dispatcher().submit(job):
                try:
                    ev.delivery_status = DELIVERY_FAILED
                    ev.delivery_error = "async_dispatch_queue_full"
                    _commit_with_retry(db)
                except Exception:
                    logger.exception(
                        "Failed to persist legacy notification dispatch failure (event_id=%s service=%s)",
                        int(ev.id),
                        st,
                    )
