"""Task links are data, not executable URLs."""
from urllib.parse import urlsplit


def safe_task_url(value: str | None) -> str:
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
        if (len(value) <= 2048 and parsed.scheme in {"http", "https"} and parsed.hostname
                and not parsed.username and not parsed.password
                and not any(ord(c) < 32 for c in value)):
            return value
    except ValueError:
        pass
    return ""


def validate_task_url(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    result = safe_task_url(value)
    if not result:
        raise ValueError("Task URL must be an HTTP(S) URL without embedded credentials")
    return result
