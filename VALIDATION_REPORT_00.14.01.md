# Validation evidence for TimeboardApp 00.14.01

This report defines the evidence and acceptance boundaries for PR #38. Test
results must be read from the exact PR-head and post-merge Actions runs, not
inferred from a previous commit or a source version string. The checked main
workflow gates source-release publication after the full Python matrix and
container jobs pass. Release artifacts identify the actual commit.

## Automated checks

- Full `python -m pytest -q` suites on Python 3.12 and 3.13, with JUnit/coverage.
- 43 issuer-specific tests covering metadata consistency, all supported client
  authentication methods, code exchange, denial without credentials, SDK/provider
  errors, invalid clients/callbacks, query encoding, issuer spoofing, and CSRF.
- Chromium application and OAuth smoke; the latter verifies the metadata flag
  and the real browser callback's exact issuer. Its callback is intercepted
  locally and never authorizes a real ChatGPT connection.
- Docker build, non-root/read-only startup, MCP-enabled discovery with matching
  issuer metadata, and rejection of unauthenticated MCP access.
- Source/website metadata, internal links/assets, scoped lint/type checks,
  JavaScript syntax, vendor reproducibility, pip/npm dependency audits, Bandit,
  and repository CodeQL checks.
- Preview publication-boundary tests retained, including verification that the
  completed branch-only publisher is not active in current CI.

## Historical baseline, not release certification

Preview commit `c5f7f4f464dbbfd77ad1ba659e64aa4be22e970d` passed 281 tests on each
Python runtime in CI run 37835944171. The published `mcp-issuer-test.1` tag points
to that exact commit. This is useful baseline evidence, not proof for later
release/documentation/CI changes. Existing warnings must not be reported as a
warning-free run. Consult PR #38 and the current run artifacts for final counts.

## Separate deployment acceptance

Public TLS and proxy routing, a real ChatGPT DCR connection, consent approval and
cancellation, task access, refresh, and disconnection require a live deployment
check. Notification delivery and real-data backup restoration also remain
operator tests. Nothing in the automated evidence establishes that the original
generic creation error had only one cause or that the user's container changed.

Website publication must be verified separately from source merge/release.
Confirm the deployed home and ChatGPT guide show 00.14.01 and issuer guidance;
do not treat a successful repository commit as proof that a CDN serves it yet.
