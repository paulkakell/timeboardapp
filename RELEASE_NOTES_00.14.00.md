# TimeboardApp 00.14.00

Date: October 8, 2026. Release tag: `v00.14.00`. Previous published release: `v00.13.02`. Classification: additive feature release. Release PR: #37. Native MCP implementation: PR #34; dependency updates: PRs #35 and #36.

## Native ChatGPT integration

Add an experimental Streamable HTTP MCP endpoint at `/mcp`, with per-user Timeboard sign-in, OAuth consent, and eleven scoped account/task/assignment tools. Each connection belongs to the authorizing account. Tasks remain owner-only even for administrators; assignment follows existing eligibility rules. Existing private GPT Actions and their tokens remain available through their existing interfaces.

OAuth includes S256 PKCE, exact callback allowlisting, resource-bound hashed credentials, rotating refresh tokens, and connection revocation. Users can disconnect applications from Profile. Creation and assignment require retry keys to avoid duplicate mutations. Completing a recurring task retains the existing recurrence rules. No permanent-delete or administrator-management tool is exposed.

MCP is disabled by default and remains experimental. Automated protocol, browser, and container checks have been exercised; live ChatGPT account acceptance, public deployment TLS, live providers, and multi-worker/load behavior still need operator validation. Publication is not a certification of those unperformed checks.

## Upgrade and enable

1. Back up the full SQLite database and settings consistently, and retain the previous source/image.
2. Fetch the release in your existing checkout: `git fetch origin --tags`, then `git switch --detach v00.14.00`. Preserve local configuration and review local edits before changing revisions.
3. Build and recreate from the directory containing the Dockerfile and Compose file: `docker compose up -d --build --force-recreate`. This creates a local image tagged `timeboardapp:00.14.00`; no registry image is published by the source release.
4. To enable MCP, set `TIMEBOARDAPP_MCP_ENABLED=true` and `TIMEBOARDAPP_BASE_URL=https://your-timeboard-hostname` in the Compose environment or `.env`, then recreate the service. This release's regular Compose file passes the flag through. Custom stacks must pass it explicitly. An empty flag preserves `mcp.enabled` in `/data/settings.yml`; explicit true/false overrides it.
5. Use a public HTTPS origin without a path prefix and forward the whole site through the reverse proxy. Discovery at `/.well-known/oauth-protected-resource/mcp` should return JSON; unauthenticated `/mcp` should return 401 with a Bearer challenge.
6. Add the HTTPS `/mcp` endpoint as a custom MCP server in ChatGPT Plugins using OAuth, then sign in and approve the requested permissions. No OpenAI API key is required. Verify the exact callback allowlist if your client presents a different URI.

See [MCP setup and testing](MCP_TESTING.md) for detailed connection, isolated deployment, verification, and revocation steps. The version displayed by the application and health endpoint is now 00.14.00; older containers must be rebuilt or replaced to receive this code.

## Included maintenance and documentation

Retain FastAPI 0.142.2, PyJWT 2.15.1, and MCP SDK 2.2.0 in the reviewed runtime lock. Refresh application help, README, operator/security/contributor guidance, all current website pages, configuration examples, generated reference dates, and release badges. Synchronize application/OpenAPI/UI version 00.14.00, npm 0.14.0, and container metadata. Historical notes and tags remain unchanged.

## Compatibility and rollback

No existing API contract is intentionally broken. Enabling MCP creates five additive tables through startup migration; existing tasks and tables remain intact. JSON task exports omit OAuth state and MCP retry history; a protected full SQLite/settings backup preserves them. Public demo mode and MCP cannot be enabled together. Upgrades from before 00.13.00 still need that release's separate authentication migration precautions.

For feature rollback, revoke connections if new consent will be required later, set `TIMEBOARDAPP_MCP_ENABLED=false`, and recreate the service. This removes MCP/OAuth/connection routes; stored grants and task mutations are not undone. The additional tables can remain when returning to 00.13.02 source. To roll back the application, restore a matching old image and database/settings snapshot after preserving any post-upgrade task changes. Never move or overwrite a published tag.

## Validation and publication

The release workflow requires the full Python 3.12/3.13 suite, source/website/version checks, dependency/static audits, scoped lint/types, browser flows, and container checks before publication. `VALIDATION_REPORT_00.14.00.md` describes the test scope and remaining limits. The release's `RELEASE_MANIFEST.json` identifies the exact checked commit and publishing workflow; `SHA256SUMS` covers uploaded source archives and documentation. GitHub Pages publication and running-application upgrades are separate from the source release.

## Commit notes

```text
feat(release): publish native MCP and OAuth in 00.14.00

Release experimental default-off MCP, per-user OAuth, and eleven scoped tools.
Retain GPT Actions and reviewed FastAPI/PyJWT dependency updates.
Expose the MCP flag in regular Compose without overriding YAML by default.
Refresh application help, setup/security guidance, website, and release metadata.
Preserve historical tags and document migration, rollback, and live-test limits.

Previous published release: v00.13.02
Feature implementation: #34; dependency updates: #35 and #36
```
