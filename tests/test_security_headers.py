from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_browser_security_headers_are_emitted(tmp_path, monkeypatch) -> None:
    from app.config import get_settings

    settings_path = tmp_path / "settings.yml"
    settings_path.write_text(
        f"""
app:
  name: "TimeboardApp"
  timezone: "UTC"
  port: 8888
  base_url: ""
security:
  session_secret: "headers-session-secret-123456789012345"
  jwt_secret: "headers-jwt-secret-123456789012345678"
database:
  path: "{tmp_path / 'headers.db'}"
purge:
  default_days: 15
  interval_minutes: 5
logging:
  level: "INFO"
email:
  enabled: false
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TIMEBOARDAPP_SETTINGS", str(settings_path))
    get_settings.cache_clear()

    import app.main as main

    main = importlib.reload(main)
    client = TestClient(main.app, base_url="https://testserver")
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=31536000")
