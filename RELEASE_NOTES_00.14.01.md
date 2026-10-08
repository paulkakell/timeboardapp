# TimeboardApp 00.14.01

Date: 2026-10-08. Classification: fix. Previous version: 00.14.00. PR: #38.

## Changes

This patch implements RFC 9207 authorization-response issuer identification for
native MCP OAuth. Authorization-server metadata advertises
`authorization_response_iss_parameter_supported: true`. Approval, cancellation,
and validated authorization-error callbacks carry one `iss` equal to the
published issuer, including its trailing slash. Internal consent navigation and
direct errors for invalid clients/callbacks are unchanged.

The stable ChatGPT callback remains in the exact default allowlist. Existing
callback-specific URLs and explicit environment overrides remain the operator's
responsibility. No wildcard callbacks or authentication bypasses are introduced.
MCP remains experimental and disabled by default. Private GPT Actions, existing
scopes, PKCE, resource binding, CSRF, and token handling remain available as before.
There are no dependency, database-schema, or signing-key changes in this patch.

The release updates README, setup and troubleshooting instructions, application
help, versioning documentation, website guides, and generated reference/badges.
It retires the completed branch-only preview publishing job, leaving historical
preview source, tag, assets, and publication-boundary tests intact.

## Upgrade

Back up the full database and settings consistently and retain the previous image.
Preserve the data directory, ownership, signing keys, and environment. In a clean
checkout use `git fetch origin --tags` and `git switch --detach v00.14.01`, then
`docker compose up -d --build --force-recreate timeboardapp`. For a custom remote
build, select `https://github.com/paulkakell/timeboardapp.git#v00.14.01` and use a
new local image tag. A container restart or `docker compose pull` alone does not
install this source release. No registry image is published by this workflow.

Check `/healthz` for `00.14.01`, verify the issuer-support metadata, then test a new
ChatGPT OAuth connection. Preserve exact callbacks used by existing connections.
Follow [MCP_TESTING.md](MCP_TESTING.md) and
[MCP_ISSUER_TESTING.md](MCP_ISSUER_TESTING.md). Older authentication migrations
remain documented in their historical release notes.

## Validation and limitations

The existing CI gates run full Python 3.12/3.13 regression suites, dependency and
static audits, documentation/lint/type checks, Chromium application/OAuth smoke,
and fresh normal/MCP-enabled container builds. The issuer suite contains 43
cases; browser and container checks now assert issuer support as well. Use the
exact release commit's Actions run and artifacts for results. See
[VALIDATION_REPORT_00.14.01.md](VALIDATION_REPORT_00.14.01.md).

Automated checks do not prove a live ChatGPT connection to a particular public
server. The original generic plugin-creation rejection was not conclusively
traced to this compatibility gap. Public TLS, proxy policy, client registration,
approval, cancellation, token refresh, and disconnection still require acceptance
on the intended deployment. Source publication does not deploy the application.

## Rollback

Retain the previous image and a matching consistent full backup. There is no
additional schema migration relative to 00.14.00. Rolling back removes issuer
support and can prevent new stable-callback connections; source rollback does
not undo task writes. Reconcile post-upgrade data before restoring a snapshot.
Do not move or overwrite existing release tags, including the preview.

## Commit notes

```text
fix(mcp): release 00.14.01 OAuth issuer identification

Include canonical iss on approval, denial, and authorization error callbacks.
Preserve existing OAuth boundaries; verify metadata and browser/container flows.
Update application help, operator documentation, website, and source identity.
Retire the completed one-time preview job without changing historical releases.
Live ChatGPT acceptance and deployment remain separate operator checks.
```
