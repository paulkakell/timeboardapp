from __future__ import annotations

from .clock import utc_now, utc_from_timestamp

import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from .paths import log_directory


LOG_PREFIX = "timeboardapp"


def _safe_level(level: str | None, default: str = "INFO") -> int:
    raw = (level or default).strip().upper()
    return getattr(logging, raw, logging.INFO)


class DailyDateFileHandler(logging.Handler):
    """Write logs to /data/logs/timeboardapp-YYYY-MM-DD.log.

    The handler checks the date on each emit and transparently rolls over to a
    new file when the local date changes.
    """

    def __init__(self, *, base_dir: Path | None = None, prefix: str = LOG_PREFIX, level: int = logging.INFO):
        super().__init__(level=level)
        self.base_dir = Path(base_dir) if base_dir is not None else log_directory()
        self.prefix = str(prefix)
        self._lock = threading.RLock()
        self._current_date = self._today()
        self._stream = None
        self._open_for_date(self._current_date)

    def _today(self) -> str:
        # Use container's local time (TZ env is commonly set by docker-compose).
        return datetime.now().strftime("%Y-%m-%d")

    def _path_for_date(self, date_str: str) -> Path:
        return self.base_dir / f"{self.prefix}-{date_str}.log"

    def _open_for_date(self, date_str: str) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for_date(date_str)
        # Line-buffered text mode.
        self._stream = open(path, "a", encoding="utf-8", buffering=1)

    def _close_stream(self) -> None:
        if self._stream:
            try:
                self._stream.flush()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
        self._stream = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        with self._lock:
            try:
                today = self._today()
                if today != self._current_date:
                    self._close_stream()
                    self._current_date = today
                    self._open_for_date(today)

                if not self._stream:
                    self._open_for_date(self._current_date)

                self._stream.write(msg + "\n")
            except Exception:
                self.handleError(record)

    def close(self) -> None:
        with self._lock:
            self._close_stream()
        super().close()


_FILE_HANDLER: DailyDateFileHandler | None = None


def setup_logging(*, level: str = "INFO") -> None:
    """Attach a daily file handler under /data/logs.

    This is designed to be safe to call multiple times.
    """

    global _FILE_HANDLER

    lvl = _safe_level(level)

    # Base formatter: ISO-ish timestamps.
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(lvl)

    # Ensure we always have a stream handler (stdout) in addition to file.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(lvl)
        sh.setFormatter(formatter)
        root.addHandler(sh)

    if _FILE_HANDLER is None:
        fh = DailyDateFileHandler(level=lvl)
        fh.setFormatter(formatter)
        root.addHandler(fh)
        _FILE_HANDLER = fh
    else:
        _FILE_HANDLER.setLevel(lvl)
        _FILE_HANDLER.setFormatter(formatter)

    # Make common framework loggers propagate to root so they also hit the file.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "sqlalchemy.engine"):
        lg = logging.getLogger(name)
        lg.propagate = True


def apply_log_level(level: str) -> None:
    """Update log levels at runtime."""
    lvl = _safe_level(level)
    logging.getLogger().setLevel(lvl)
    for lg in ("timeboardapp", "timeboardapp.ui", "timeboardapp.auth", "timeboardapp.email"):
        logging.getLogger(lg).setLevel(lvl)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(lvl)
    if _FILE_HANDLER is not None:
        _FILE_HANDLER.setLevel(lvl)


_LOGFILE_RE = re.compile(rf"^{re.escape(LOG_PREFIX)}-(\d{{4}}-\d{{2}}-\d{{2}})\.log$")


def list_log_files(*, log_dir: Path | None = None) -> list[Path]:
    """Return log files in newest-first order."""
    d = Path(log_dir) if log_dir is not None else log_directory()
    if not d.exists() or not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and _LOGFILE_RE.match(p.name)]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def purge_old_logs(*, retention_days: int, log_dir: Path | None = None, now: datetime | None = None) -> int:
    """Delete log files older than retention_days.

    Uses file mtime as the age source.
    """
    try:
        days = int(retention_days)
    except Exception:
        days = 0
    if days <= 0:
        return 0

    d = Path(log_dir) if log_dir is not None else log_directory()
    if not d.exists() or not d.is_dir():
        return 0

    n = now or utc_now()
    cutoff = n - timedelta(days=days)

    deleted = 0
    for p in list_log_files(log_dir=d):
        try:
            mtime = utc_from_timestamp(p.stat().st_mtime)
            if mtime < cutoff:
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            # best-effort
            continue

    return deleted
