# TimeboardApp source audit and maintenance candidate

Review date: September 26, 2026. Original main commit: `1a9d32a4e6770870b4d0a1094cb2c9177d040706`. Base application version: `00.12.03`. Changes are proposed on an isolated audit branch, not merged or deployed. This report is a source assessment and regression record, not a penetration-test certificate.

## Baseline evidence

GitHub Actions run `36252534894` executed a fresh Python 3.12 environment against the original source plus an evidence-only workflow. It recorded **49 passing tests and one failure**: importing the application tried to write `/data/logs` despite a different configured database directory. The run preserved pytest/JUnit output, installed versions, dependency audit JSON, Bandit output, outdated-package inventory, and a source archive.

The dependency audit flagged **ecdsa 0.19.2**, transitively installed through python-jose, for CVE-2024-23342 / GHSA-wj6h-64fc-37mp. The app used HS256, so that advisory alone did not establish an exploitable ECDSA signing path in TimeboardApp. The candidate removes python-jose and the unused ECDSA dependency path, using PyJWT with an explicit algorithm/issuer/audience/claim policy instead. Passlib is replaced by Argon2id plus a bounded reader for legacy PBKDF2 records. Existing direct requirements used open-ended minimums; a fresh install already resolved many current versions. It would be inaccurate to describe every installed baseline package as obsolete.

## Findings and disposition

| Priority | Source finding | Candidate disposition and remaining conditions |
|---|---|---|
| High | HTTP login could create/promote an administrator and log a recovered password. | Removed automatic recovery from authentication. Fresh-install credentials use an owner-only file; recovery requires explicit local operator access. Known demo credentials remain intentional in isolated demo mode. |
| High | Cookie-authenticated mutation forms lacked comprehensive CSRF protection; logout changed state via GET. | Session CSRF token, same-origin checks, bounded form/body handling, same-origin fetch header, and POST logout. Test browser flows and proxy origin configuration before deployment. |
| High | Password changes did not invalidate existing username-based API tokens/browser sessions. | Stable user-ID claims, required issuer/audience/type/timestamps, current-DB role checks, and keyed password-state fingerprints. All old tokens/sessions are invalidated on upgrade. |
| High | User-configured notification endpoints could access unintended network destinations or follow redirects. | Validate all DNS answers; connect a pinned numeric address; block metadata/loopback/reserved and IPv6 transition destinations; verify TLS; reject redirects, unsafe headers, and oversized responses. Exact operator LAN allowlists remain an explicit trust decision. Add an egress firewall. |
| High | WNS bearer credentials could be attached without a sufficiently narrow channel-URI boundary. | Enforce HTTPS and the `notify.windows.com` hostname family before sending. Provider credentials and an actual compatible Windows client remain untested. |
| High, conditional | Reset-link origin could depend on request Host data where no canonical origin was configured. | Reset requires a configured public origin; host restrictions are enabled for configured deployments. Correct reverse-proxy trust remains essential. |
| Medium | Notification API configuration could disclose secrets in nested/custom fields or credential-bearing URLs. | Recursive dictionary redaction and masked-value round-trip preservation. Administrative database exports still intentionally contain sensitive information and require external protection. |
| Medium | Task URLs could use unsafe schemes in rendered links. | Shared HTTP(S)-only validation for writes and inert rendering of unsafe legacy values. Existing HTML escaping/linkification remains in place. |
| Medium | Unbounded task listings and repeated completion could harm clients or spawn duplicate recurrence. | Bounded pagination, deterministic ordering, conflict responses, and an atomic database completion claim. Full recursive subtree writes are still not one comprehensive transaction; interrupted-subtree recovery needs staging tests. |
| Medium | Settings/backup files could be written without an explicit owner-only atomic-write policy. | Atomic 0600 writes. Failure to persist repaired signing keys now fails closed. Directory permissions, volume encryption, backups outside the container, and old log copies are operator responsibilities. |
| Operational | Compose interpolated a service key, required unrelated external networks, and PUID/PGID did not select the process user. | Stable service key, no mandatory external networks, actual Compose user setting, non-root image, loopback default exposure, capability restrictions, private persistent directory, health check. |
| Functional | `PATCH /api/users/me` was shadowed by the earlier `/{user_id}` route. | Static profile routes precede dynamic numeric-user routes. Regression test performs a real authenticated password change. |
| Functional | Closing a task with open descendants could produce an unhandled API error. | Return 409 with a safe conflict description; keep the explicit website cascade confirmation. |
| Functional | FullCalendar referenced a nonexistent v6 global stylesheet; assets lagged patch releases. | Remove nonexistent stylesheet; vendor Bootstrap 5.3.8 and FullCalendar 6.1.21 with licenses and npm integrity lock. Major v7 migration deferred rather than silently breaking calendar behavior. |
| Maintenance | Deprecated UTC/lifecycle/Pydantic configuration APIs and uncontrolled dependency resolution. | Use a UTC helper preserving the existing naive-UTC database contract, FastAPI lifespan, ConfigDict, bounded direct requirements, generated hash locks, and current pinned CI actions. |

## Scope of security review

Reviewed authentication, authorization, role changes, session and reset flows, password storage/migration, API schemas and route order, task/subtask mutations, recurrence, templated links, notification transport and secret handling, configuration persistence, logs/backups, container/Compose defaults, dependency graph, workflow permissions, website claims, and source references for cleanup. The review combines manual tracing with executable regression tests; a low static-scanner count is not treated as proof of safety.

This was not a full Git-history secret scan, a test of a running production host, an OS-image vulnerability certification, a multi-tenant load test, or a live-provider acceptance test. No production credentials, external notification deliveries, real user task data, live ChatGPT account, or production restore was used. Do not infer those tests passed.

## Test evidence and release gates

The original tests were retained, with narrowly necessary updates for the stronger JWT signature and HTTPS header test. New tests exercise legacy-password migration, no implicit administrator recovery, malformed hashes and JWT claims, current role enforcement, password-based revocation, integration-token hashing/scopes/expiry/revocation, cross-user denial across every Actions mutation, parent conflicts, concurrency-safe recurrence completion, payload limits, timezone-aware input, private writes, CSRF/origin checks, request limits, limiter bounds, secret masking, SSRF/DNS/redirect/header defenses, WNS host checks, and template/source/website consistency.

The workflow installs resolved hash-locked dependencies, runs the complete suite on Python 3.12 and 3.13, validates the generated OpenAPI contract, records coverage and installed versions, audits dependencies, runs Bandit, checks vendored asset hashes and docs links, exercises real Chromium workflows against a disposable instance, and checks a built non-root container. Read the exact final commit's run/artifacts for actual counts and outcomes. Merely defining these jobs is not a passing result. Any failing gate keeps the pull request a candidate rather than a production-ready release.

## Fix-or-deprecate decision tree

```text
Feature appears broken or obsolete
|
+-- Is there a demonstrated security/data-integrity failure?
|   +-- Yes -> disable the unsafe path or fail closed now.
|   |          Preserve data; patch; add a reproducing negative test.
|   |          Re-enable only after regression and deployment acceptance.
|   +-- No  -> reproduce on the current locked stack and identify its owner.
|
+-- Is an upstream service or supported client still available?
|   +-- Yes -> does a bounded fix preserve useful behavior?
|   |          +-- Yes -> fix and test contract + browser/provider behavior.
|   |          +-- No  -> plan a major migration with a compatibility period.
|   +-- No  -> announce deprecation, offer export/replacement, stop new setup,
|              remove only after references and data migration are checked.
|
+-- Is failure actually missing configuration/credentials?
|   +-- Yes -> keep feature; show a clear disabled/configuration-required state.
|   +-- No  -> do not advertise it as verified until the failure is resolved.
|
+-- Is a file merely old?
    +-- Referenced, licensed, or historical evidence -> retain.
    +-- Unreferenced duplicate/generated artifact -> remove and test references.
```

| Feature | Decision in this candidate | Next acceptance or deprecation gate |
|---|---|---|
| Login/profile update, configured log paths, Compose startup | Fix | Full regression and container smoke checks. |
| Parent completion/archive, recurrence duplication | Fix | Conflict, retry, and concurrent-completion regressions; interrupted subtree recovery remains separate. |
| Calendar and Bootstrap assets | Patch-upgrade and keep | Real browser month/week/day, preference persistence, mobile layout; plan FullCalendar 7 separately. |
| Login-triggered administrator recovery and GET logout | Remove unsafe behavior | Explicit operator recovery and POST logout replacements are documented/tested. |
| PBKDF2 password records | Keep read compatibility; deprecate creation | Upgrade after successful login; retain the compatibility reader until stored legacy records are migrated. |
| SMTP, SendGrid, Discord, webhook, Gotify, ntfy | Keep, configuration-dependent | Live controlled provider delivery and failures must be exercised with operator credentials. |
| Legacy UWP WNS | Retain but label legacy; deprecation candidate | Identify an actual supported client/customer. If none exists, stop advertising/setup and remove with notice; otherwise implement and test a distinct modern Windows App SDK path. |
| Connected-page browser notifications | Keep with accurate wording | Do not advertise offline Web Push. Native push requires a new service-worker/subscription design. |
| Private GPT Actions | Implement a narrow owner-only adapter | Validate a real HTTPS deployment and private GPT editor; verify user approval and token revocation. |
| Shared per-user ChatGPT / native MCP app | Not implemented by this adapter | Requires authorization-code OAuth/consent and/or MCP, plus their own threat model and acceptance tests. |
| Historical release/validation notes | Retain | They are labeled historical evidence, not current test results. |
| Seven unreferenced icon aliases | Remove | Keep canonical assets and test all source/static references. Exact paths: `scripts/removed-files.json`. |

## ChatGPT integration boundary

Eight operations are generated from real server routes, not a manually drifting schema. Tokens default to read-only, expire after at most 90 days, are individually revocable, are hashed at rest, and are invalidated by password changes. An administrator cannot use an integration token to read other users' tasks. The interface excludes administrator exports/imports, global settings, arbitrary network calls, user management, and notification credential management.

OpenAI's Actions requirements include a public HTTPS endpoint, request/response limits, and client confirmation for consequential operations. The adapter marks all writes consequential and budgets list responses. Authentication is API-key/Bearer in a **private GPT**. Sharing that GPT would share the token owner's capabilities; multi-user OAuth must be designed separately. Task text is untrusted content and must not be treated as instructions. Partial/truncated descriptions must not be silently written back as complete content.

No claim is made that this implements MCP, OAuth authorization-code consent, OpenAI Chat Completions/Responses model serving, or universal compatibility with every ChatGPT surface. The existing password grant is not authorization-code OAuth. A real GPT-editor import and live user-approved operation are still necessary before claiming deployment-level compatibility.

## Remaining work before production approval

Back up the full database and settings; stage the upgrade; verify provider delivery and real-data restore; configure HTTPS/trusted proxy and edge limits; inspect base-image advisories; rotate any old admin passwords that may have been exposed through logs; protect/remove retained logs according to policy; reissue user sessions/tokens; review credential-storage and encrypted-backup requirements; verify public docs publication; and exercise the real GPT integration. Retain a matched pre-upgrade image/database/settings rollback set, recognizing that rollback can discard newer writes.

The CSP still permits inline scripts/styles, and the limiter/scheduler are process-local. Outbound DNS and socket timeouts are not a strict end-to-end deadline guarantee. Those are explicit remaining hardening/design tasks, not concealed passing audit findings.

## Primary references

- OpenAI Actions authentication: https://developers.openai.com/api/docs/actions/authentication
- OpenAI Actions production requirements: https://developers.openai.com/api/docs/actions/production
- OWASP password storage guidance: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- Docker Compose interpolation (values, not service keys): https://docs.docker.com/reference/compose-file/interpolation/
- Microsoft WNS overview and channel validation: https://learn.microsoft.com/en-us/windows/apps/develop/notifications/push-notifications/wns-overview
- FullCalendar v6 global bundle: https://legacy.fullcalendar.io/v6/initialize-globals
- FullCalendar v7 migration: https://fullcalendar.io/docs/upgrading-from-v6
- Bootstrap current 5.3 documentation: https://getbootstrap.com/docs/5.3/getting-started/introduction/
- PyJWT release metadata: https://pypi.org/project/PyJWT/
