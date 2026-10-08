"""Synchronize release identity and badges without importing the application."""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/paulkakell/timeboardapp"
START = "<!-- release-badges:start -->"
END = "<!-- release-badges:end -->"


def release_data() -> dict[str, Any]:
    """Read the source version; reject malformed or non-increasing releases."""
    tree = ast.parse((ROOT / "app/version.py").read_text())
    version = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
    )
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", version):
        raise ValueError("APP_VERSION must use the project xx.xx.xx format")
    data = json.loads((ROOT / "release.json").read_text())
    previous = data["previous_version"]
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", previous):
        raise ValueError("previous_version must use xx.xx.xx")
    parts = tuple(map(int, version.split(".")))
    if parts <= tuple(map(int, previous.split("."))):
        raise ValueError("Release version must increase")
    data.update(version=version, tag=f"v{version}", npm_version=".".join(map(str, parts)))
    return data


def badges(*, markdown: bool = False) -> str:
    """CI status is supplied by GitHub, never a hardcoded passing image."""
    data = release_data()
    version_image = "docs/assets/badges/version.svg" if markdown else "/assets/badges/version.svg"
    python_versions = " | ".join(data["python_versions"])
    items = [
        (f"Source version {data['version']}", version_image, f"{REPO}/releases/tag/{data['tag']}"),
        ("CI on main", f"{REPO}/actions/workflows/audit.yml/badge.svg?branch=main&event=push", f"{REPO}/actions/workflows/audit.yml?query=branch%3Amain"),
        (f"Tested Python {' and '.join(data['python_versions'])}", f"https://img.shields.io/badge/python-{quote(python_versions, safe='')}-blue", f"{REPO}/blob/main/.github/workflows/audit.yml"),
        ("MIT license", "https://img.shields.io/badge/license-MIT-blue", f"{REPO}/blob/main/LICENSE"),
        ("Private GPT Actions", "https://img.shields.io/badge/ChatGPT-private%20GPT%20Actions-blue", "https://timeboardapp.com/docs/chatgpt.html"),
        ("Native MCP (opt-in)", "https://img.shields.io/badge/MCP-opt--in-blue", "https://timeboardapp.com/docs/chatgpt.html#native-mcp"),
    ]
    if markdown:
        content = "\n".join(f"[![{label}]({image})]({link})" for label, image, link in items)
    else:
        images = "\n".join(
            f'<a href="{html.escape(link, quote=True)}"><img src="{html.escape(image, quote=True)}" alt="{html.escape(label, quote=True)}" height="20"></a>'
            for label, image, link in items
        )
        content = (
            '<section class="release-status" aria-label="Source release information"><div class="container">'
            f'<p>Source version <strong>{data["version"]}</strong> · {html.escape(data["date"])}. '
            'Existing application deployments require an upgrade.</p>'
            f'<div class="release-badges">{images}</div></div></section>'
        )
    return f"{START}\n{content}\n{END}"


def decorate_html(text: str) -> str:
    """Replace exactly one generated block, or insert it inside main."""
    block = badges()
    if START in text:
        if text.count(START) != 1 or text.count(END) != 1:
            raise ValueError("HTML must contain exactly one complete release badge block")
        return re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.DOTALL)
    updated, count = re.subn(r"<main\b[^>]*>", lambda m: m[0] + "\n" + block, text, count=1)
    if count != 1:
        raise ValueError("HTML page requires a main element")
    return updated


def outputs() -> dict[Path, str]:
    data = release_data()
    version = data["version"]
    title = f"source version: {version}"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="165" height="20" role="img" '
        f'aria-label="{title}"><title>{title}</title>'
        '<rect width="88" height="20" fill="#555"/><rect x="88" width="77" height="20" fill="#007ec6"/>'
        '<g fill="#fff" font-family="Verdana,sans-serif" font-size="11" text-anchor="middle">'
        f'<text x="44" y="14">source version</text><text x="126" y="14">{version}</text></g></svg>\n'
    )
    files = {ROOT / "docs/assets/badges/version.svg": svg}
    readme = (ROOT / "README.md").read_text()
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README requires one release badge block")
    files[ROOT / "README.md"] = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: badges(markdown=True), readme, flags=re.DOTALL)
    files.update({path: decorate_html(path.read_text()) for path in (ROOT / "docs").rglob("*.html")})
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail instead of writing stale badge metadata")
    args = parser.parse_args()
    stale = []
    for path, expected in outputs().items():
        if args.check:
            if not path.exists() or path.read_text() != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected)
    if stale:
        raise SystemExit("Regenerate release badges: " + ", ".join(stale))
    print("Release badges and source identity are consistent")


if __name__ == "__main__":
    main()
