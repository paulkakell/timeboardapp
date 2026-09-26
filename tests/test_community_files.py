"""Validate the GitHub form subset used here, policy links, and safe examples.

GitHub's documented form schema is the source for these structural checks.
These tests do not submit issues or assert that GitHub enabled a private inbox.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FORMS = ROOT / ".github/ISSUE_TEMPLATE"
NAMES = ("bug_report.yml", "feature_request.yml", "documentation.yml")
REPO_PREFIX = "https://github.com/paulkakell/timeboardapp/blob/main/"


class UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate keys instead of silently discarding form requirements."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate YAML key")
        return super().construct_mapping(node, deep=deep)


def load_yaml(text):
    return yaml.load(text, Loader=UniqueKeyLoader)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_form(form):
    """Accept only the documented attributes/types used by this repository."""
    require(isinstance(form, dict), "Form must be a mapping")
    require(set(form) == {"name", "description", "title", "body"}, "Unexpected form keys")
    for key in ("name", "description", "title"):
        require(isinstance(form[key], str) and form[key].strip(), f"Missing {key}")
    require(len(form["name"]) > 3, "Template name is too short")
    body = form["body"]
    require(isinstance(body, list) and body, "Missing form body")
    seen = set()
    allowed = {
        "markdown": {"value"},
        "input": {"label", "description", "placeholder", "value"},
        "textarea": {"label", "description", "placeholder", "value", "render"},
        "dropdown": {"label", "description", "multiple", "options"},
        "checkboxes": {"label", "description", "options"},
    }
    for field in body:
        require(isinstance(field, dict), "Field must be a mapping")
        require(set(field) <= {"type", "id", "attributes", "validations"}, "Unexpected field keys")
        kind = field.get("type")
        require(kind in allowed, "Unsupported field type")
        attrs = field.get("attributes")
        require(isinstance(attrs, dict) and set(attrs) <= allowed[kind], "Unexpected field attributes")
        if kind == "markdown":
            require(isinstance(attrs.get("value"), str) and attrs["value"].strip(), "Empty markdown")
            require("validations" not in field, "Markdown cannot require input")
            continue
        identifier = field.get("id", "")
        require(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_-]+", identifier), "Invalid field id")
        require(identifier not in seen, "Duplicate field id")
        seen.add(identifier)
        require(isinstance(attrs.get("label"), str) and attrs["label"].strip(), "Missing field label")
        for key in ("description", "placeholder", "value", "render"):
            if key in attrs:
                require(isinstance(attrs[key], str), "Field text must be a string")
        validations = field.get("validations", {})
        require(isinstance(validations, dict) and set(validations) <= {"required"}, "Invalid validations")
        if "required" in validations:
            require(type(validations["required"]) is bool, "Required must be a boolean")
        if kind in {"dropdown", "checkboxes"}:
            options = attrs.get("options")
            require(isinstance(options, list) and options, "Missing options")
            if kind == "dropdown":
                require(all(isinstance(option, str) and option.strip() for option in options), "Invalid option")
                require(len(options) == len(set(options)), "Duplicate dropdown option")
                require(type(attrs.get("multiple", False)) is bool, "Multiple must be a boolean")
            else:
                for option in options:
                    require(isinstance(option, dict) and set(option) <= {"label", "required"}, "Invalid checkbox option")
                    require(isinstance(option.get("label"), str) and option["label"].strip(), "Missing checkbox label")
                    require(type(option.get("required", False)) is bool, "Checkbox required must be a boolean")
    require(seen, "Form must request input")


@pytest.mark.parametrize("name", NAMES)
def test_issue_form_structure(name):
    validate_form(load_yaml((FORMS / name).read_text()))


def test_recognized_files_exist_without_overriding_duplicates():
    for name in ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md", ".github/pull_request_template.md"):
        assert (ROOT / name).is_file() and (ROOT / name).stat().st_size > 100
    for name in ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md"):
        assert not (ROOT / ".github" / name).exists()
        assert not (ROOT / "docs" / name).exists()
    templates = [p for folder in (ROOT, ROOT / "docs", ROOT / ".github") for p in folder.iterdir()
                 if p.name.lower() in {"pull_request_template.md", "pull_request_template.txt"}]
    assert templates == [ROOT / ".github/pull_request_template.md"]
    forms = [load_yaml((FORMS / name).read_text()) for name in NAMES]
    assert len({form["name"] for form in forms}) == len(forms)


@pytest.mark.parametrize("name", NAMES)
def test_forms_require_privacy_and_conduct_acknowledgment(name):
    form = load_yaml((FORMS / name).read_text())
    text = json.dumps(form)
    assert REPO_PREFIX + "SECURITY.md" in text
    assert REPO_PREFIX + "CODE_OF_CONDUCT.md" in text
    checks = next(field for field in form["body"] if field.get("id") == "prerequisites")
    options = checks["attributes"]["options"]
    assert len(options) == 3 and all(option["required"] is True for option in options)
    assert any("private data and credentials" in option["label"] for option in options)
    assert any("not a security vulnerability report" in option["label"] for option in options)
    assert "assignees" not in form and "labels" not in form


@pytest.mark.parametrize("name,identifiers", [
    ("bug_report.yml", {"version", "installation", "environment", "reproduction", "expected", "actual"}),
    ("feature_request.yml", {"problem", "area", "proposal", "alternatives", "acceptance", "compatibility"}),
    ("documentation.yml", {"location", "version", "problem", "correction"}),
])
def test_forms_require_actionable_information(name, identifiers):
    form = load_yaml((FORMS / name).read_text())
    required = {field["id"] for field in form["body"] if field.get("validations", {}).get("required")}
    assert identifiers <= required
    for field in form["body"]:
        if field.get("id") in {"diagnostics", "workaround"}:
            assert field["validations"]["required"] is False
    assert not any(field["type"] == "upload" for field in form["body"])


def test_issue_chooser_routes_private_concerns_to_policies():
    config = load_yaml((FORMS / "config.yml").read_text())
    assert set(config) == {"blank_issues_enabled", "contact_links"}
    assert config["blank_issues_enabled"] is False
    links = config["contact_links"]
    assert len(links) == 3
    for link in links:
        assert set(link) == {"name", "url", "about"}
        assert all(isinstance(value, str) and value.strip() for value in link.values())
        assert link["url"].startswith(REPO_PREFIX)
    assert any(link["url"].endswith("SECURITY.md") for link in links)
    assert any(link["url"].endswith("CODE_OF_CONDUCT.md#reporting") for link in links)


@pytest.mark.parametrize("mutation", ["id", "type", "required", "options", "top_keys", "checkbox"])
def test_form_validation_rejects_broken_or_weakened_structure(mutation):
    form = copy.deepcopy(load_yaml((FORMS / "bug_report.yml").read_text()))
    by_id = {field.get("id"): field for field in form["body"]}
    if mutation == "id":
        by_id["version"]["id"] = "prerequisites"
    elif mutation == "type":
        by_id["version"]["type"] = "password"
    elif mutation == "required":
        by_id["version"]["validations"]["required"] = "true"
    elif mutation == "options":
        by_id["installation"]["attributes"]["options"].append("Docker Compose")
    elif mutation == "top_keys":
        form["assignees"] = ["unverified-person"]
    else:
        by_id["prerequisites"]["attributes"]["options"][0]["required"] = "false"
    with pytest.raises(ValueError):
        validate_form(form)


def test_yaml_duplicate_keys_are_not_silently_accepted():
    with pytest.raises(ValueError, match="Duplicate YAML key"):
        load_yaml("name: First\nname: Second\n")


def markdown_ids(text):
    return {re.sub(r"[^\w -]", "", line.lstrip("# ").lower()).replace(" ", "-")
            for line in text.splitlines() if line.startswith("#")}


def test_community_links_point_to_tracked_policies_and_valid_sections():
    sources = [ROOT / name for name in ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md", ".github/pull_request_template.md", "docs/README.md")]
    sources += [FORMS / name for name in (*NAMES, "config.yml")]
    for path in sources:
        text = path.read_text()
        targets = re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", text)
        if path.name == "config.yml":
            targets += [link["url"] for link in load_yaml(text)["contact_links"]]
        for target in targets:
            if target.startswith(REPO_PREFIX):
                target = target.removeprefix(REPO_PREFIX)
                base = ROOT
            elif urlsplit(target).scheme:
                assert urlsplit(target).scheme == "https"
                continue
            else:
                base = path.parent
            parts = urlsplit(target)
            destination = (base / unquote(parts.path)).resolve()
            assert destination.is_relative_to(ROOT) and destination.is_file(), (path, target)
            if parts.fragment and destination.suffix == ".md":
                assert unquote(parts.fragment) in markdown_ids(destination.read_text()), (path, target)


def test_conduct_policy_includes_fair_reporting_and_enforcement():
    text = (ROOT / "CODE_OF_CONDUCT.md").read_text()
    for heading in ("Purpose and scope", "Expected behavior", "Unacceptable behavior", "Reporting", "Review and enforcement"):
        assert "## " + heading in text
    assert "https://github.com/paulkakell" in text
    assert "reporting-abuse-or-spam" in text
    assert "reconsideration" in text and "retaliation" in text
    assert "Do not assume" in text and "cannot be guaranteed" in text
    assert not re.search(r"\[[^\]]*(?:INSERT|EMAIL|CONTACT)[^\]]*\]", text)


def test_pull_request_template_covers_review_release_and_rollback():
    text = (ROOT / ".github/pull_request_template.md").read_text()
    for heading in ("Summary", "Version and changelog", "Validation evidence", "Security and operational review",
                    "Compatibility and database migrations", "Documentation, release notes, and artifacts", "Rollback plan", "Commit notes"):
        assert "## " + heading in text
    for term in ("integration", "regression", "Lint", "type checks", "Performance", "Environment variables", "metrics", "authorization", "lockfiles"):
        assert term in text
    assert "- [x]" not in text.lower()
    assert "Not applicable" in text and "not run" in text


def test_community_guide_is_discoverable_without_duplicate_policies():
    readme = (ROOT / "README.md").read_text()
    assert "## Community and contributing" in readme
    assert "CODE_OF_CONDUCT.md" in readme and "CONTRIBUTING.md" in readme
    assert 'href="community.html"' in (ROOT / "docs/docs/index.html").read_text()
    assert "https://timeboardapp.com/docs/community.html" in (ROOT / "docs/sitemap.xml").read_text()
    page = (ROOT / "docs/docs/community.html").read_text()
    assert REPO_PREFIX + "CODE_OF_CONDUCT.md" in page
    assert REPO_PREFIX + "CONTRIBUTING.md" in page
    assert "<!-- release-badges:start -->" in page
    assert "default branch" in page and "not a live GitHub form submission" in page


def test_contributing_shell_examples_have_valid_syntax():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    examples = re.findall(r"```sh\n(.*?)\n```", text, re.DOTALL)
    assert len(examples) >= 5
    for example in examples:
        subprocess.run(["bash", "-n"], input=example, text=True, check=True, capture_output=True)


def test_documented_setup_creates_private_loopback_configuration(tmp_path):
    text = (ROOT / "CONTRIBUTING.md").read_text()
    script = re.search(r"python - <<'PY'\n(.*?)\nPY", text, re.DOTALL)[1]
    settings = tmp_path / "settings.yml"
    env = dict(os.environ, TIMEBOARDAPP_SETTINGS=str(settings))
    subprocess.run([sys.executable, "-c", "import os; os.umask(0o077)\n" + script], env=env, check=True)
    config = json.loads(settings.read_text())
    assert config["app"]["host"] == "127.0.0.1"
    assert Path(config["database"]["path"]).parent == tmp_path
    assert settings.stat().st_mode & 0o777 == 0o600
