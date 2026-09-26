# TimeboardApp 00.13.00

Release date: September 26, 2026. Tag: `v00.13.00`. Previous version: `00.12.03`. Source changes: PR #30.

## Additive

- Add eight owner-only private GPT Actions, generated OpenAPI, and scoped, hashed, expiring, individually revocable integration tokens. This is not native MCP or shared multi-user OAuth.
- Add reproducible Python/npm locks, vendored browser assets with licenses, current website reference data, and Python 3.12/3.13, Chromium, security, and container validation.
- Synchronize version identity across the application/API/footer, npm metadata (`0.13.0`), Docker labels, Compose image tag, website metadata, README, and badges. Add automated drift tests and gated exact-commit tag/release publication.

## Fixes

- Remove HTTP-triggered admin recovery and password logging; use private first-install credentials and explicit operator recovery.
- Harden JWT validation, password migration/revocation, CSRF/origin checks, outbound request destinations, reset-link origins, secret redaction, and private file writes.
- Correct configured log paths, `/api/users/me` route precedence, parent/subtask conflict responses, recurrence completion claims, calendar assets, and Compose/non-root settings.
- Remove seven unreferenced icon aliases and the superseded Docker-only workflow. Preserve canonical icons, licenses, and historical release evidence.

## Breaking changes and upgrade requirements

- Existing sessions/JWTs are invalidated. Successful logins migrate PBKDF2 password hashes to Argon2id; an old image cannot verify those migrated hashes.
- Cookie-authenticated writes require CSRF protection; logout uses POST. Automated clients must use the appropriate authenticated API.
- Task API lists are bounded and paginated; callers must handle limit/offset. Notification destinations require the documented secure network policy.
- The container runs non-root with restricted defaults. Ensure the persistent directory belongs to the configured PUID/PGID. Compose uses a stable `timeboardapp` service name and loopback exposure by default.
- Set the public HTTPS application origin for password resets and GPT Actions. Do not use the static project website as the application endpoint.

Back up the database and settings and retain the matching previous image before upgrading. Test migration/restoration and provider delivery on staging. A rollback requires the old image and its pre-upgrade data together, with reconciliation of newer writes. See `VERSIONING.md` and `AUDIT_REPORT.md`.

## Validation and limits

The earlier reviewed implementation passed 110 tests on each of Python 3.12/3.13 in run 36256060593 at head `0b20c8f511888e25236920884bc75547af973d07`. Those results precede the release-metadata tests and are not substituted for final release validation. The publishing workflow requires a fresh full regression, dependency audit, static-security gate, website consistency check, real Chromium smoke, and non-root/read-only container check on the merged source. Exact commit and workflow references accompany the release assets.

Remaining work includes production restore testing, live notification providers, live GPT import/operations, OS-image and full Git-history scanning, stronger CSP, shared rate limiting/scheduling, and comprehensive transactional subtree recovery. Low static-analysis findings and legacy test/dependency warnings remain; source publication is not a security certificate or a production deployment.

## Commit notes

```text
release(00.13.00): merge security, dependency, website and private GPT Actions updates

- Synchronize application, npm, Docker, Compose, docs, and badge versions
- Add release metadata drift checks and guarded exact-commit publication
- Preserve classified changes, migration warnings, rollback guidance, and audit history
- Run full Python 3.12/3.13, security, browser, website, and container gates

Refs: #30
Previous release: v00.12.03
Release tag: v00.13.00
```
