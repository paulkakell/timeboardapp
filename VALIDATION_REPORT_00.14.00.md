# Validation scope for 00.14.00

This release packages the merged native MCP/OAuth feature and dependency updates, exposes the enable flag in regular Compose, and updates version identity, application help, operator documentation, and the website. Publication is gated on checks of the exact release source. Use [release PR #37](https://github.com/paulkakell/timeboardapp/pull/37) and the workflow referenced in `RELEASE_MANIFEST.json` for executed results, commit identity, and artifacts.

## Baseline evidence

Before release preparation, main commit `0a6cc155e473f80b2f2da3ff340b6eeac12db337` passed [CI run #40](https://github.com/paulkakell/timeboardapp/actions/runs/37810278886). The merged feature head also passed 218 tests on each of Python 3.12 and 3.13, browser OAuth/application flows, dependency/security audits, and enabled/disabled container checks. These are baseline results, not a substitute for testing the changed release commit.

## Release gates

- Full regression/coverage on Python 3.12 and 3.13, including OAuth PKCE/resource/redirect checks, scope and account isolation, credential replay/revocation, retry concurrency, assignment, recurrence, and actual-app migration/feature rollback.
- Hash-locked dependency installation and `pip check`; pip/npm vulnerability audits and the medium/high Bandit gate. Deprecation warnings and lower-severity findings remain visible in artifacts.
- Generated website/reference/version consistency, internal links/assets, OpenAPI, Python compilation, and JavaScript syntax. Release metadata tests verify padded application and unpadded npm identities, container labels/tags, and immutable publication safeguards.
- Existing scoped Ruff and Pyright checks; no claim that all legacy code is strictly typed.
- Chromium application login, authenticated pages including updated help, task creation, calendar preferences, mobile layout, and logout; OAuth sign-in/consent/callback/MCP/disconnection with synthetic accounts.
- Both Compose configurations, regular Compose MCP flag propagation, fresh image build, non-root/read-only startup, and MCP-enabled discovery/authentication rejection.

Local environments may lack Docker or a usable Chromium installation; successful repository CI supplies those gates. Record such local limitations explicitly in the release PR. Rebuilding browser vendor assets must leave their content unchanged except application metadata in npm manifests.

## Operator acceptance still required

Live ChatGPT connection/account acceptance, actual public HTTPS ingress, token refresh after real expiry, second-user isolation through the client, read-only rejection, cancellation, assignment, and interrupted-response retry need a deployed test origin and disposable accounts. Live notification delivery, multiple workers/load, and a matching full backup/restore also require operator validation. Browser smoke uses an isolated TLS instance and intercepted OAuth callback; it is not a live ChatGPT test.

MCP remains experimental and disabled by default. Enabling creates five additive tables; JSON task backups omit OAuth credentials and retry history. The full database/settings backup is required to preserve that state. Test data and evidence must be synthetic or sanitized, with no credentials, settings, databases, or private tasks in source or public artifacts.

## Release provenance and rollback

The main-branch publisher runs only after the required gates and verifies that main still names the checked commit. It creates a new tag, source archives, versioned notes/validation guidance, manifest, and checksum file without replacing old tags or releases. No registry image or running application is deployed by this workflow. GitHub Pages state must be verified separately.

Keep v00.13.02 and its corresponding image/database/settings snapshot. Revoke connections before disabling MCP if future consent must be renewed. Disabling does not reverse task changes. See the release notes and MCP guide for feature and application rollback procedures.
