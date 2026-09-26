# Contributing to TimeboardApp

Contributions may address bugs, tests, security hardening, accessibility, documentation, translations, or carefully scoped features. Read the [Code of Conduct](CODE_OF_CONDUCT.md), [security policy](SECURITY.md), and [versioning procedure](VERSIONING.md) before opening a contribution. Submissions are reviewed by maintainers; opening a pull request does not guarantee acceptance or a release date.

## Get help and choose the right report

Start with the [README](README.md) and [website documentation](docs/README.md). Search existing issues and pull requests before opening another report.

Use the **Bug report** form for reproducible incorrect behavior, **Feature request** for a problem and proposed capability, or **Documentation correction** for an inaccurate page, missing instruction, or broken link. For example, report a failing calendar preference as a bug, a requested export option as a feature, and an incorrect Docker command as documentation. Include the application version or commit, not merely "latest."

Do not file security vulnerabilities in public issues. Follow [SECURITY.md](SECURITY.md) and establish a private channel before sending exploit details. Conduct concerns follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md#reporting). Public issue and pull request bodies are not private support inboxes. Never attach a production database, settings file, access token, password, authorization header, or screenshot containing private tasks.

## Discuss scope before substantial work

For a small correction, a focused pull request with a reproducing test is sufficient. For API changes, dependency major upgrades, new integrations, or feature removal, first describe the use case, alternatives, compatibility impact, and acceptance criteria in a feature request. A fix-or-deprecate proposal should explain who relies on the feature and how existing data or clients would migrate. Do not silently remove supported behavior.

ChatGPT support currently means private GPT Actions. Native MCP, shared per-user OAuth, and model-serving APIs are different capabilities and must not be presented as already implemented.

## Create a branch

Fork the repository when you do not have write access. Start from current `main`, not an old release or another contributor's unfinished branch. One pull request should address one coherent change; linked follow-up work can be separate.

```sh
git clone https://github.com/paulkakell/timeboardapp.git
cd timeboardapp
git switch main
git pull --ff-only
git switch -c docs/clarify-local-setup
```

For a fork, substitute your fork's clone URL and keep an `upstream` remote pointing to this repository. Do not force-push shared branches or rewrite published tags. Keep changes on your branch until review is complete.

## Local development

The CI matrix runs Python 3.12 and 3.13. Use Python 3.13 for this POSIX-shell example. Node/npm are build-time tools for vendored assets; the application does not need a Node server. Docker Engine and its Compose plugin are needed for container validation.

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt -r requirements-dev.txt
python -m pip check
umask 077
export TIMEBOARDAPP_SETTINGS="$(mktemp -d)/settings.yml"
export TIMEBOARDAPP_BASE_URL=
python - <<'PY'
import json
import os
from pathlib import Path
settings = Path(os.environ['TIMEBOARDAPP_SETTINGS'])
settings.write_text(json.dumps({
    'app': {'host': '127.0.0.1', 'port': 8888, 'timezone': 'UTC'},
    'database': {'path': str(settings.parent / 'development.db')},
}))
PY
python -m app.run
```

Keep the temporary settings path in the same shell for subsequent commands. This example binds to loopback and keeps development data outside the checkout. Obtain the first administrator password from `initial-admin-password.txt` beside that settings file; change it after signing in and remove the initial credential file. Do not paste that password into an issue or test fixture. Stop the server before running tests or browser smoke checks. Use disposable local users and data, never production configuration or notification credentials.

For container development, use the [README's Compose setup](README.md#start-locally), including private data-directory ownership. Do not enable demo mode against real data.

## Tests and code quality

Run the full suite, not only a test you added. Report failures and skips honestly, including missing local dependencies. The clean-runner results belong to the exact commit that was tested.

```sh
mkdir -p audit-evidence
python -m pytest -q --cov=app --cov-report=xml:audit-evidence/coverage.xml --junitxml=audit-evidence/pytest.xml
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
python -m compileall -q app scripts
python -m pip_audit --format=json
python -m bandit -r app -ll
npm ci --ignore-scripts
npm audit
python scripts/vendor_assets.py
git diff --exit-code -- app/static/vendor
```

Add a regression test for a bug and tests for new behavior, including unauthorized and invalid-input cases where relevant. Prefer deterministic fixtures and mocked provider/network interactions. Do not weaken assertions, remove coverage, or suppress a finding simply to make CI pass. The community-file tests also check form structure, privacy guidance, and links.

The checksum-pinned Ruff and Pyright commands in [.github/workflows/audit.yml](.github/workflows/audit.yml) are authoritative for release tooling. CI lints the release and community tests and type-checks the two release scripts; it does not claim that the entire legacy application is strictly typed. Explain any changed lint scope or suppression in the pull request.

For UI changes, exercise the real browser flow:

```sh
python -m playwright install --with-deps chromium
python scripts/browser_smoke.py
```

This launches its own disposable instance on loopback port 8765. Include relevant desktop/mobile observations and sanitized screenshots for changed UI behavior. Live notification delivery and real ChatGPT acceptance require separate credentials and explicit operator testing; do not claim those checks based on mocked tests.

For container changes, validate Compose, perform a fresh build, and follow the non-root/read-only runtime checks in the CI workflow:

```sh
docker compose config -q
docker build --no-cache --build-arg VCS_REF="$(git rev-parse HEAD)" -t timeboardapp:review .
```

## Dependencies and security review

Runtime inputs are in `requirements.in`, development inputs in `requirements-dev.in`, and resolved hash locks in the corresponding `.txt` files. Update inputs and regenerate both affected locks together with `pip-compile --generate-hashes` in a separate tooling environment; record the resolver version and commands. Then install with `--require-hashes` and rerun compatibility/audit checks. Do not edit a transitive package independently of its parent constraints.

Browser dependencies are recorded in `package.json` and `package-lock.json`. After an intentional package update, regenerate vendored assets with `python scripts/vendor_assets.py`, retain licenses, and commit the updated manifest/checksums. A dependency major version needs its own migration and browser tests. Do not bypass the current FullCalendar or APScheduler major-version boundaries merely because a newer version exists.

Review authentication, authorization, input validation, secret handling, outbound requests, logs, and dependency changes. Never commit `.env`, local settings, databases, generated credentials, or private backup files. Check the full staged diff before pushing. Use synthetic evidence and redact diagnostic output.

## Versioning, documentation, and compatibility

Follow [VERSIONING.md](VERSIONING.md): the application uses `xx.xx.xx`, tags use `vxx.xx.xx`, and npm uses the unpadded equivalent. Coordinate the next version with the maintainer rather than guessing an already-used tag. Classify changes as additive, fix, or breaking in the changelog and explain why the change is needed. Ordinary unversioned source commits are not automatically releases.

For a release-bearing change, synchronize `app/version.py`, `release.json`, package/lock metadata, container labels/tag, release notes, validation scope, and current README/website notices. Run the generators in this order:

```sh
python scripts/sync_docs.py
python scripts/release_metadata.py
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
```

Update API documentation, configuration examples, architecture notes, and user instructions when behavior changes. Website changes belong in `docs/`; preview them with `python -m http.server 8080 --directory docs`. The community page links to the root policies instead of maintaining conflicting copies. Preserve historical evidence and existing release tags.

For schema changes, describe forward/backward compatibility, test the migration on disposable representative data, and test rollback. For changed API/configuration/data formats, document compatibility and deprecation periods. For core queries or I/O changes, include a repeatable performance comparison and explain its limitations. Verify that logs, health checks, metrics, and alerts remain useful where applicable.

## Submit for review

Use the pull request template. Explain what changed and why, link issues, identify additive/fix/breaking behavior, list exact test commands and results, and provide migration, rollback, release-note, and copyable commit-note sections. Mark a section "Not applicable" with a reason rather than claiming an unperformed check. Keep the PR in draft while required checks are incomplete.

Only maintainers approve merges and releases. The version-aware publisher does not move an existing tag or overwrite an existing release. A source merge, GitHub Pages publication, source release, registry image, and running-application rollout are separate events; report only those actually verified. Contributions remain under the repository's [MIT license](LICENSE); include only work you have the right to submit and retain third-party attribution.
