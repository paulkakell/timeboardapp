"""Writable application paths follow the configured database, not the image root."""
from pathlib import Path
from sqlalchemy.engine import make_url
from .config import DEFAULT_SETTINGS_PATH, _env, get_settings


def data_directory() -> Path:
    raw = get_settings().database.path
    if raw.startswith("sqlite:"):
        raw = make_url(raw).database or ""
    if raw in {"", ":memory:"}:
        return Path(_env("SETTINGS") or DEFAULT_SETTINGS_PATH).expanduser().resolve().parent
    return Path(raw).expanduser().resolve().parent


def log_directory() -> Path:
    return data_directory() / "logs"


def backup_directory() -> Path:
    return data_directory() / "backups"
