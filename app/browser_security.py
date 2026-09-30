"""Cookie-session CSRF protection and bounded request bodies (pure ASGI)."""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException
from .rate_limit import LoginLimiter

_SAFE = {"GET", "HEAD", "OPTIONS"}


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or (443 if parsed.scheme == "https" else 80)


class BrowserSecurityMiddleware:
    def __init__(self, app, *, base_url: str = "", max_body_bytes: int = 10 * 1024 * 1024, mcp_enabled: bool = False):
        self.app = app
        self.base_url = base_url
        self.max_body_bytes = max_body_bytes
        self.login_limiter = LoginLimiter()
        self.mcp_enabled = mcp_enabled
        self.mcp_limiter = LoginLimiter(limit=120, window=60)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        session = scope["session"]  # SessionMiddleware must wrap this middleware.
        token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
        if request.method in _SAFE:
            return await self.app(scope, receive, send)

        path = request.url.path
        mcp_machine = self.mcp_enabled and path in {"/mcp", "/token", "/register", "/revoke"}
        auth_request = path in {"/login", "/api/auth/token", "/forgot-email", "/reset-password"} or (self.mcp_enabled and path in {"/register", "/mcp/consent"})
        if mcp_machine:
            retry = self.mcp_limiter.retry_after(request.client.host if request.client else "unknown")
            if retry:
                return await JSONResponse({"detail": "Too many integration requests"}, status_code=429, headers={"Retry-After": str(retry)})(scope, receive, send)
        if auth_request:
            retry = self.login_limiter.retry_after(request.client.host if request.client else "unknown")
            if retry:
                return await JSONResponse({"detail": "Too many authentication attempts"}, status_code=429, headers={"Retry-After": str(retry)})(scope, receive, send)
        max_body = min(self.max_body_bytes, 16384) if auth_request else self.max_body_bytes
        if mcp_machine:
            max_body = min(max_body, 1024 * 1024 if path == "/mcp" else 16384)
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_body:
                return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
        body = b"".join(chunks)
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        # API routes use Authorization bearer credentials, never browser cookies.
        if not request.url.path.startswith("/api/") and not mcp_machine:
            try:
                incoming = request.headers.get("origin")
                if incoming and _origin(incoming) != _origin(self.base_url or str(request.base_url)):
                    raise ValueError("Cross-origin request")
                if request.headers.get("sec-fetch-site") == "cross-site":
                    raise ValueError("Cross-site request")
                supplied = request.headers.get("x-csrf-token", "")
                if not supplied:
                    form_request = Request(scope, replay)
                    async with form_request.form(max_files=5, max_fields=200, max_part_size=self.max_body_bytes) as form:
                        supplied = str(form.get("csrf_token", ""))
                    consumed = False
                if not supplied or not secrets.compare_digest(str(token), supplied):
                    raise ValueError("Invalid CSRF token")
            except (ValueError, TypeError, HTTPException):
                return await JSONResponse({"detail": "Invalid CSRF request; reload the page and retry"}, status_code=403)(scope, receive, send)
        return await self.app(scope, replay, send)
