"""Real OAuth consent/disconnection in Chromium on disposable local TLS.

The OAuth callback is intercepted locally. No tokens or requests go to ChatGPT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://localhost:8766"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "v" * 64


def certificate(work):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    (work / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (work / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main():
    with tempfile.TemporaryDirectory(prefix="timeboard-mcp-browser-") as raw:
        work = Path(raw)
        certificate(work)
        config = work / "settings.yml"
        config.write_text(
            json.dumps(
                {
                    "app": {"base_url": BASE},
                    "mcp": {"enabled": True},
                    "database": {"path": str(work / "test.db")},
                }
            )
        )
        env = dict(
            os.environ,
            TIMEBOARDAPP_SETTINGS=str(config),
            TIMEBOARDAPP_BASE_URL=BASE,
            TIMEBOARDAPP_MCP_ENABLED="true",
            TIMEBOARDAPP_MCP_REDIRECT_URIS=CALLBACK,
        )
        with (work / "server.log").open("w") as logs:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8766",
                    "--ssl-keyfile",
                    str(work / "key.pem"),
                    "--ssl-certfile",
                    str(work / "cert.pem"),
                ],
                cwd=ROOT,
                env=env,
                stdout=logs,
                stderr=subprocess.STDOUT,
            )
            try:
                with sync_playwright() as engine:
                    browser = engine.chromium.launch()
                    # Only this disposable local server uses an untrusted certificate.
                    context = browser.new_context(
                        base_url=BASE, ignore_https_errors=True
                    )
                    for _ in range(100):
                        if process.poll() is not None:
                            raise RuntimeError(
                                "Disposable MCP server exited before readiness"
                            )
                        try:
                            if (
                                context.request.get("/healthz", timeout=1000).status
                                == 200
                            ):
                                break
                        except PlaywrightError:
                            time.sleep(0.2)
                    else:
                        raise RuntimeError("Disposable MCP server did not become ready")
                    password = (work / "initial-admin-password.txt").read_text().strip()
                    reg = context.request.post(
                        "/register",
                        data={
                            "client_name": "Browser test app",
                            "redirect_uris": [CALLBACK],
                            "token_endpoint_auth_method": "none",
                            "grant_types": ["authorization_code", "refresh_token"],
                            "scope": "tasks:read",
                        },
                    )
                    assert reg.status == 201
                    client_id = reg.json()["client_id"]
                    challenge = (
                        base64.urlsafe_b64encode(
                            hashlib.sha256(VERIFIER.encode()).digest()
                        )
                        .decode()
                        .rstrip("=")
                    )
                    query = urlencode(
                        {
                            "client_id": client_id,
                            "response_type": "code",
                            "redirect_uri": CALLBACK,
                            "resource": BASE + "/mcp",
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                            "scope": "tasks:read",
                            "state": "local-browser-smoke",
                        }
                    )
                    # Fulfill the final redirect inside the browser; no external call.
                    context.route(
                        CALLBACK + "*",
                        lambda route: route.fulfill(
                            status=200, body="Local callback received"
                        ),
                    )
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto("/authorize?" + query)
                    page.locator("input[name=username]").fill("admin")
                    page.locator("input[name=password]").fill(password)
                    with page.expect_response(
                        lambda response: (
                            urlsplit(response.url).path == "/mcp/consent"
                            and response.request.method == "POST"
                        )
                    ) as signed_in:
                        page.get_by_role("button", name="Sign in", exact=True).click()
                    login = signed_in.value
                    assert login.status == 303, (
                        f"Consent login HTTP {login.status}; Origin={login.request.headers.get('origin')}"
                    )
                    assert login.request.headers.get("origin") == BASE
                    page.get_by_role("button", name="Allow access").wait_for()
                    assert "Read your tasks" in page.locator("body").inner_text()
                    assert "Create, change" not in page.locator("body").inner_text()
                    evidence = ROOT / "audit-evidence"
                    evidence.mkdir(exist_ok=True)
                    page.screenshot(
                        path=str(evidence / "mcp-consent-desktop.png"), full_page=True
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    page.screenshot(
                        path=str(evidence / "mcp-consent-mobile.png"), full_page=True
                    )
                    page.get_by_role("button", name="Allow access").click()
                    page.wait_for_url(CALLBACK + "*")
                    callback = parse_qs(urlsplit(page.url).query)
                    assert callback["state"] == ["local-browser-smoke"]
                    token = context.request.post(
                        BASE + "/token",
                        form={
                            "grant_type": "authorization_code",
                            "client_id": client_id,
                            "code": callback["code"][0],
                            "code_verifier": VERIFIER,
                            "redirect_uri": CALLBACK,
                            "resource": BASE + "/mcp",
                        },
                    )
                    assert token.status == 200
                    headers = {
                        "Authorization": "Bearer " + token.json()["access_token"],
                        "Accept": "application/json, text/event-stream",
                    }
                    rpc = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/list",
                        "params": {},
                    }
                    assert (
                        context.request.post(
                            BASE + "/mcp", headers=headers, data=rpc
                        ).status
                        == 200
                    )
                    page.goto(BASE + "/profile")
                    page.get_by_role("link", name="Connected applications").click()
                    page.get_by_role("button", name="Disconnect").click()
                    assert (
                        "No applications are connected"
                        in page.locator("body").inner_text()
                    )
                    assert (
                        context.request.post(
                            BASE + "/mcp", headers=headers, data=rpc
                        ).status
                        == 401
                    )
                    assert not errors, "\n".join(errors)
                    browser.close()
                print(
                    "PASS: Chromium OAuth sign-in, desktop/mobile consent, callback redirect, MCP access, and disconnection"
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
