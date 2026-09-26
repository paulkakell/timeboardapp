"""Restricted notification transport: validated DNS, pinned sockets, no redirects.

Outbound HTTP proxy environment variables are intentionally ignored. Configure
exact trusted LAN hostnames in security.outbound_allowed_hosts when needed.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit

from .config import get_settings

MAX_RESPONSE_BYTES = 65536
_FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization", "proxy-connection", "upgrade", "te", "trailer"}


def resolve_destination(url: str) -> tuple[str, str, int, list[tuple]]:
    if not isinstance(url, str) or any(ord(c) < 33 for c in url) or len(url) > 4096:
        raise ValueError("Invalid notification URL")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Invalid notification URL: use HTTP(S) without user credentials or fragments")
    allowed = {h.lower().rstrip(".") for h in get_settings().security.outbound_allowed_hosts}
    trusted = host in allowed
    if parsed.scheme != "https" and not trusted:
        raise ValueError("Public notification destinations require HTTPS")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid notification port")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("Notification destination could not be resolved")
    for family, kind, proto, canonical, address in addresses:
        ip = ipaddress.ip_address(address[0])
        # Even explicitly trusted hosts cannot reach loopback, link-local cloud
        # metadata, multicast, unspecified or reserved destinations.
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError("Notification destination is not permitted")
        if getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None):
            raise ValueError("IPv6 transition addresses are not permitted")
        if not ip.is_global and not (trusted and ip.is_private):
            raise ValueError("Private notification destinations require an explicit administrator allowlist")
    path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    return host, path, port, addresses


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, *, address: tuple, secure: bool, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self.address = address
        self.secure = secure

    def connect(self):
        family, kind, proto, canonical, address = self.address
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(self.timeout)
            sock.connect(address)  # Numeric sockaddr already validated; no second DNS lookup.
            if self.secure:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


def send_request(*, url: str, method: str = "POST", headers: dict[str, str] | None = None,
                 data: bytes | None = None, timeout: int = 10) -> tuple[int, str]:
    host, path, port, addresses = resolve_destination(url)
    method = str(method).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError("Unsupported notification HTTP method")
    safe_headers = {"User-Agent": "TimeboardApp", "Accept-Encoding": "identity"}
    for key, value in (headers or {}).items():
        key, value = str(key), str(value)
        if key.lower() in _FORBIDDEN_HEADERS or any(c in key + value for c in "\r\n"):
            raise ValueError("Unsafe notification header")
        safe_headers[key] = value
    if sum(len(k) + len(v) for k, v in safe_headers.items()) > 16384:
        raise ValueError("Notification headers too large")
    connection = _PinnedConnection(host, port, address=addresses[0], secure=urlsplit(url).scheme == "https", timeout=min(max(timeout, 1), 15))
    try:
        connection.request(method, path, body=data, headers=safe_headers)
        response = connection.getresponse()
        # Never follow redirects, nor expose remote response bodies/credentials in errors.
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Notification endpoint returned HTTP {response.status}")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Notification response exceeded 64 KiB")
        return response.status, body.decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        raise RuntimeError("Notification transport failed") from None
    finally:
        connection.close()
