"""Publish checked main source only; never move tags or overwrite releases."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import runpy
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "paulkakell/timeboardapp"


def command(args: list[str]) -> str:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def api(path: str, *, fields: dict[str, str] | None = None, missing_ok: bool = False) -> Any:
    args = ["gh", "api", "--method", "POST" if fields else "GET", f"repos/{REPOSITORY}/{path}"]
    for key, value in (fields or {}).items():
        args.extend(["-f", f"{key}={value}"])
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode:
        if missing_ok and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(f"GitHub API request failed: {path}; no release overwrite attempted")
    return json.loads(result.stdout)


def validate_context(env: Mapping[str, str], checkout: str) -> str:
    """Refuse forks, PR events, non-main refs, and mismatched checkouts."""
    if env.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("Release publication is restricted to the intended repository")
    if env.get("GITHUB_REF") != "refs/heads/main" or env.get("GITHUB_EVENT_NAME") not in {"push", "workflow_dispatch"}:
        raise ValueError("Release publication requires a checked main-branch run")
    sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or checkout != sha:
        raise ValueError("Checkout must match the workflow's exact commit")
    if not env.get("GH_TOKEN"):
        raise ValueError("A scoped Actions token is required")
    return sha


def require_tag_commit(ref: dict[str, Any], expected: str) -> None:
    """Permit an existing tag only when it resolves to this exact commit."""
    obj = ref["object"]
    for _ in range(5):
        if obj["type"] == "commit":
            if obj["sha"] != expected:
                raise ValueError("Existing release tag targets another commit; refusing to move it")
            return
        if obj["type"] != "tag":
            break
        obj = api(f"git/tags/{obj['sha']}")["object"]
    raise ValueError("Release tag does not resolve to a commit")



def source_version(source: str) -> tuple[int, int, int]:
    """Read a literal version without executing code from an earlier commit."""
    assignments = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
    ]
    if len(assignments) != 1:
        raise ValueError("Expected one literal APP_VERSION assignment")
    version = ast.literal_eval(assignments[0].value)
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", version):
        raise ValueError("APP_VERSION must use xx.xx.xx")
    release, feature, fix = map(int, version.split("."))
    return release, feature, fix


def publication_requested(env: Mapping[str, str], sha: str) -> bool:
    """Ordinary pushes validate source; only version increases request a release."""
    if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return True
    if env.get("GITHUB_EVENT_NAME") != "push":
        raise ValueError("Unsupported release event")
    event_path = env.get("GITHUB_EVENT_PATH")
    if not event_path:
        raise ValueError("Push event payload is required to determine release intent")
    event = json.loads(Path(event_path).read_text())
    if not isinstance(event, dict) or event.get("after") != sha or event.get("ref") != "refs/heads/main":
        raise ValueError("Push event must match the checked main commit")
    before = event.get("before")
    if not isinstance(before, str) or not re.fullmatch(r"[0-9a-f]{40}", before) or before == "0" * 40:
        raise ValueError("A valid previous push commit is required; use a reviewed manual run for initial publication")
    # Fail closed on missing history or a rewritten main branch. No fetch or write
    # is performed here; checkout must provide the complete push range.
    command(["git", "merge-base", "--is-ancestor", before, sha])
    previous = source_version(command(["git", "show", f"{before}:app/version.py"]))
    current = source_version(command(["git", "show", f"{sha}:app/version.py"]))
    if current < previous:
        raise ValueError("Release version must not decrease")
    if current == previous:
        print("No release requested: APP_VERSION is unchanged across this push; existing tags and assets are untouched")
        return False
    return True


def plan() -> None:
    """Emit a read-only job decision before a write-capable job is scheduled."""
    sha = validate_context(os.environ, command(["git", "rev-parse", "HEAD"]))
    publish = publication_requested(os.environ, sha)
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        raise ValueError("GITHUB_OUTPUT is required for the release plan")
    with Path(output).open("a") as stream:
        stream.write(f"publish={'true' if publish else 'false'}\n")
    print(json.dumps({"commit": sha, "publish": publish}))


def main() -> None:
    sha = validate_context(os.environ, command(["git", "rev-parse", "HEAD"]))
    if not publication_requested(os.environ, sha):
        return
    if api("git/ref/heads/main")["object"]["sha"] != sha:
        raise ValueError("Main moved after validation; refusing to publish stale source")
    data = runpy.run_path(str(ROOT / "scripts/release_metadata.py"))["release_data"]()
    version, tag = data["version"], data["tag"]
    tag_ref = api(f"git/ref/tags/{tag}", missing_ok=True)
    if tag_ref is not None:
        require_tag_commit(tag_ref, sha)
    existing = api(f"releases/tags/{tag}", missing_ok=True)
    if existing is not None:
        if tag_ref is None or existing.get("draft"):
            raise ValueError("Existing incomplete release requires operator review")
        print(f"Release already exists at the checked commit: {tag}")
        return

    notes = ROOT / f"RELEASE_NOTES_{version}.md"
    if not notes.is_file():
        raise ValueError("Version-matched release notes are required")
    dist = ROOT / "release-dist"
    dist.mkdir(exist_ok=True)
    files = []
    for extension, archive_format in [("zip", "zip"), ("tar.gz", "tar.gz")]:
        target = dist / f"timeboardapp-{version}.{extension}"
        command(["git", "archive", f"--format={archive_format}", f"--prefix=timeboardapp-{version}/", "-o", str(target), sha])
        files.append(target)
    for name in [notes.name, f"VALIDATION_REPORT_{version}.md", "VERSIONING.md"]:
        target = dist / name
        target.write_bytes((ROOT / name).read_bytes())
        files.append(target)
    manifest = dist / "RELEASE_MANIFEST.json"
    manifest.write_text(json.dumps({
        "version": version, "tag": tag, "commit": sha, "date": data["date"],
        "previous_tag": f"v{data['previous_version']}",
        "workflow_run": f"https://github.com/{REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "scope": "Source release; no registry image or running application deployed",
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    }, indent=2) + "\n")
    files.append(manifest)
    checksums = dist / "SHA256SUMS"
    checksums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files))
    files.append(checksums)
    if api("git/ref/heads/main")["object"]["sha"] != sha:
        raise ValueError("Main moved during packaging; refusing stale publication")
    if tag_ref is None:
        tag_ref = api("git/refs", fields={"ref": f"refs/tags/{tag}", "sha": sha})
        require_tag_commit(tag_ref, sha)
    command([
        "gh", "release", "create", tag, "--repo", REPOSITORY, "--verify-tag",
        "--title", f"TimeboardApp {version}", "--notes-file", str(notes), "--latest",
        *[str(path) for path in files],
    ])
    require_tag_commit(api(f"git/ref/tags/{tag}"), sha)
    release = api(f"releases/tags/{tag}")
    if release.get("draft"):
        raise RuntimeError("Release remained a draft; verify uploads before publishing")
    print(f"Published {tag} at {sha}: {release['html_url']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Determine release intent without contacting GitHub or publishing")
    arguments = parser.parse_args()
    if arguments.plan:
        plan()
    else:
        main()
