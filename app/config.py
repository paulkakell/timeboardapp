from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit
from .private_files import write_private_text

import yaml
from pydantic import BaseModel, Field


ENV_PREFIX = "TIMEBOARDAPP"
# Backward-compatibility: accept the legacy prefix (without the "APP" suffix).
LEGACY_ENV_PREFIX = ENV_PREFIX[:-3]

def _env(suffix: str) -> str | None:
    """Return env var value for TIMEBOARDAPP_* with fallback to the legacy prefix vars."""

    key = f"{ENV_PREFIX}_{suffix}"
    legacy_key = f"{LEGACY_ENV_PREFIX}_{suffix}"
    return os.environ.get(key) or os.environ.get(legacy_key)


DEFAULT_SETTINGS_PATH = _env("SETTINGS") or "/data/settings.yml"


WEAK_SECRET_VALUES = {
    "",
    "CHANGE_ME_SESSION_SECRET",
    "CHANGE_ME_JWT_SECRET",
    "test-session-secret",
    "test-jwt-secret",
    "secret",
    "password",
}


def _is_weak_runtime_secret(value: str | None) -> bool:
    """Return True when a runtime signing secret is unsafe for deployment."""

    v = str(value or "").strip()
    return (not v) or v in WEAK_SECRET_VALUES or v.startswith("CHANGE_ME") or len(v) < 32


def _new_runtime_secret() -> str:
    """Generate a high-entropy URL-safe signing secret."""

    return secrets.token_urlsafe(48)


def _repair_runtime_secrets(raw: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Replace legacy placeholder secrets in settings.yml with durable values.

    Earlier packages shipped sample placeholders and only generated runtime
    secrets when the settings file did not exist. Existing deployments that
    already had a placeholder-bearing settings.yml could therefore keep weak
    secrets indefinitely. Repairing the YAML mapping here keeps startup and
    Admin -> Validation secure without requiring manual file edits.
    """

    security = raw.get("security")
    if security is None:
        security = {}
        raw["security"] = security
    if not isinstance(security, dict):
        raise ValueError("settings.yml security section must be a YAML mapping")

    session_secret = str(security.get("session_secret") or "")
    jwt_secret = str(security.get("jwt_secret") or "")
    changed = False

    if _is_weak_runtime_secret(session_secret):
        session_secret = _new_runtime_secret()
        security["session_secret"] = session_secret
        changed = True

    if _is_weak_runtime_secret(jwt_secret) or jwt_secret == session_secret:
        jwt_secret = _new_runtime_secret()
        while jwt_secret == session_secret:
            jwt_secret = _new_runtime_secret()
        security["jwt_secret"] = jwt_secret
        changed = True

    if changed:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            write_private_text(p, yaml.safe_dump(raw, sort_keys=False))
        except Exception:
            raise RuntimeError("Cannot persist signing secrets; configure a writable settings file") from None

    return raw


class AppSettings(BaseModel):
    name: str = "TimeboardApp"
    timezone: str = "UTC"
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8888
    base_url: str = ""


class SecuritySettings(BaseModel):
    session_secret: str = "CHANGE_ME_SESSION_SECRET"
    jwt_secret: str = "CHANGE_ME_JWT_SECRET"
    secure_cookies: bool | None = None
    # Exact hostname exceptions for explicitly trusted self-hosted notification services.
    outbound_allowed_hosts: list[str] = Field(default_factory=list)


class DatabaseSettings(BaseModel):
    path: str = "/data/timeboardapp.db"


class PurgeSettings(BaseModel):
    default_days: int = 15
    interval_minutes: int = 60


class EmailSettings(BaseModel):
    # When disabled, password reset and reminders are not sent.
    enabled: bool = False

    # Delivery provider: "smtp" or "sendgrid".
    provider: str = "smtp"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""

    smtp_from: str = "timeboardapp@localhost"

    # If True, use STARTTLS.
    use_tls: bool = True

    # SendGrid v3 API key (used when provider == "sendgrid").
    sendgrid_api_key: str = ""

    # Overdue reminder cadence.
    reminder_interval_minutes: int = 60

    # Password reset token TTL.
    reset_token_minutes: int = 60


class LoggingSettings(BaseModel):
    level: str = "INFO"


class DemoSettings(BaseModel):
    # When enabled, the application behaves as an always-resetting demo instance.
    enabled: bool = False

    # How often to purge + rebuild demo data.
    # Set to 0 to disable automatic resets.
    reset_interval_minutes: int = 360

    # Prevent abuse by disabling outbound notifications/webhooks/email.
    disable_external_apis: bool = True


class MCPSettings(BaseModel):
    enabled: bool = False
    # Copy exact callbacks from ChatGPT's connection page. No wildcard matches.
    allowed_redirect_uris: list[str] = Field(default_factory=lambda: [
        "https://chatgpt.com/connector_platform_oauth_redirect",
    ])
    access_token_seconds: int = Field(default=900, ge=60, le=3600)
    refresh_token_days: int = Field(default=30, ge=1, le=90)
    max_clients: int = Field(default=200, ge=1, le=10000)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    purge: PurgeSettings = Field(default_factory=PurgeSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    demo: DemoSettings = Field(default_factory=DemoSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)


def _ensure_settings_file(path: str) -> None:
    p = Path(path)
    if p.exists():
        return

    p.parent.mkdir(parents=True, exist_ok=True)

    # Copy sample settings into place while replacing secret placeholders.
    session_secret = _new_runtime_secret()
    jwt_secret = _new_runtime_secret()
    sample = Path(__file__).resolve().parent.parent / "settings.sample.yml"
    if sample.exists():
        text = sample.read_text(encoding="utf-8")
        text = text.replace("CHANGE_ME_SESSION_SECRET", session_secret)
        text = text.replace("CHANGE_ME_JWT_SECRET", jwt_secret)
        write_private_text(p, text)
    else:
        # Minimal fallback
        write_private_text(p,
            "app:\n  name: 'TimeboardApp'\n  timezone: 'UTC'\n  host: '0.0.0.0'\n  port: 8888\n"
            f"security:\n  session_secret: '{session_secret}'\n  jwt_secret: '{jwt_secret}'\n"
            "database:\n  path: '/data/timeboardapp.db'\n"
            "purge:\n  default_days: 15\n  interval_minutes: 60\n"
            "demo:\n  enabled: false\n  reset_interval_minutes: 360\n  disable_external_apis: true\n"
            "email:\n  enabled: false\n  provider: 'smtp'\n  smtp_host: ''\n  smtp_port: 587\n  smtp_username: ''\n  smtp_password: ''\n  smtp_from: 'timeboardapp@localhost'\n  use_tls: true\n  sendgrid_api_key: ''\n  reminder_interval_minutes: 60\n  reset_token_minutes: 60\n"
        )


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("settings.yml must contain a YAML mapping at the root")
    return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings_path = _env("SETTINGS") or DEFAULT_SETTINGS_PATH
    _ensure_settings_file(settings_path)
    raw = _repair_runtime_secrets(_load_yaml(settings_path), settings_path)
    s = Settings.model_validate(raw)

    # Allow env overrides for strong secrets. Weak placeholder overrides are
    # ignored so legacy compose/env files cannot force insecure runtime signing.
    session_secret = _env("SESSION_SECRET")
    jwt_secret = _env("JWT_SECRET")
    if session_secret and not _is_weak_runtime_secret(session_secret):
        s.security.session_secret = session_secret
    if jwt_secret and not _is_weak_runtime_secret(jwt_secret):
        s.security.jwt_secret = jwt_secret

    # Never invent a non-durable signing key after environment overrides.
    if _is_weak_runtime_secret(s.security.session_secret) or _is_weak_runtime_secret(s.security.jwt_secret):
        raise ValueError("Runtime signing secrets must be strong")
    if s.security.session_secret == s.security.jwt_secret:
        raise ValueError("Session and JWT signing secrets must be distinct")

    # Public base URL override (useful for external notifications).
    base_url_env = _env("BASE_URL")
    if base_url_env:
        s.app.base_url = str(base_url_env).strip()

    # Port override is occasionally useful in container orchestration.
    port_env = os.environ.get("PORT") or _env("PORT")
    if port_env:
        try:
            s.app.port = int(port_env)
        except ValueError:
            pass

    if s.app.base_url:
        parsed = urlsplit(s.app.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("app.base_url must be an absolute HTTP(S) URL without credentials, query or fragment")
        _ = parsed.port  # Validate the port before accepting the configuration.
    if s.security.session_secret == s.security.jwt_secret:
        raise ValueError("Session and JWT signing secrets must be distinct")
    mcp_enabled = _env("MCP_ENABLED")
    if mcp_enabled is not None:
        if mcp_enabled.lower() not in {"true", "false", "1", "0"}:
            raise ValueError("TIMEBOARDAPP_MCP_ENABLED must be true or false")
        s.mcp.enabled = mcp_enabled.lower() in {"true", "1"}
    redirects = _env("MCP_REDIRECT_URIS")
    if redirects:
        s.mcp.allowed_redirect_uris = [u.strip() for u in redirects.split(",") if u.strip()]
    if s.mcp.enabled:
        base = urlsplit(s.app.base_url)
        if base.scheme != "https" or not base.hostname or base.path not in {"", "/"}:
            raise ValueError("MCP requires an HTTPS TIMEBOARDAPP_BASE_URL without a path prefix")
        if s.demo.enabled:
            raise ValueError("MCP cannot be enabled in the public demo environment")
        if not s.mcp.allowed_redirect_uris:
            raise ValueError("MCP requires at least one exact OAuth redirect URI")
        for uri in s.mcp.allowed_redirect_uris:
            from pydantic import AnyHttpUrl
            AnyHttpUrl(uri)  # Validate hostnames before using callback origins in CSP.
            target = urlsplit(uri)
            loopback = target.scheme == "http" and target.hostname in {"localhost", "127.0.0.1", "::1"}
            if (target.scheme != "https" and not loopback) or not target.hostname or target.username or target.password or target.fragment or "*" in uri:
                raise ValueError("MCP redirect URIs must be exact HTTPS URLs (HTTP loopback is allowed for Inspector)")
            _ = target.port
    return s
