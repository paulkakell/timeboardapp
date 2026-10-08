"""Check the public documentation layout using only local site files."""
from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import runpy
from threading import Thread

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PAGES = [("home", "/"), ("chatgpt", "/docs/chatgpt.html"),
         ("deployment", "/docs/deployment.html")]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def main():
    version = runpy.run_path(str(ROOT / "app/version.py"))["APP_VERSION"]
    evidence = ROOT / "audit-evidence"
    evidence.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "docs"))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                context = browser.new_context()
                # Badge services are not part of the website's local contract.
                context.route(
                    "**/*",
                    lambda route: route.continue_()
                    if route.request.url.startswith(base + "/") else route.abort(),
                )
                page = context.new_page()
                for layout, width, height in [("desktop", 1440, 1000), ("mobile", 390, 844)]:
                    page.set_viewport_size({"width": width, "height": height})
                    for name, path in PAGES:
                        response = page.goto(base + path, wait_until="networkidle", timeout=15000)
                        assert response and response.status == 200, (layout, path)
                        assert version in page.locator(".release-status").inner_text()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        ), (layout, path, "horizontal page overflow")
                        if name == "chatgpt":
                            assert page.locator("#issuer-identification").count() == 1
                            assert page.locator("#troubleshooting").count() == 1
                            assert "authorization_response_iss_parameter_supported" in page.locator("main").inner_text()
                        page.screenshot(path=str(evidence / f"site-{name}-{layout}.png"))
                        print(f"PASS: documentation {name}, {layout}, version {version}")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
