from pathlib import Path
import json
R=Path(__file__).resolve().parents[1]
def edit(name, old, new, count=1):
 p=R/name; t=p.read_text(); assert old in t, (name,old[:90]); p.write_text(t.replace(old,new,count))
def write(name,text): (R/name).write_text(text)
# Patch-release identity follows VERSIONING.md. Historical notes and tags remain intact.
for name in ['app/version.py','Dockerfile','docker-compose.yml']:
 p=R/name;p.write_text(p.read_text().replace('00.14.00','00.14.01'))
r=json.loads((R/'release.json').read_text());r.update(previous_version='00.14.00',change_type='fix',pull_request=38)
write('release.json',json.dumps(r,indent=2)+'\n')
for name in ['package.json','package-lock.json']:
 p=R/name;t=p.read_text(); t=t.replace('"version": "0.14.0"','"version": "0.14.1"');p.write_text(t)
# Current installation references, not historical feature-introduction notes.
for name in ['README.md','MCP_TESTING.md','CONTRIBUTING.md','docs/README.md']:
 p=R/name;p.write_text(p.read_text().replace('00.14.00','00.14.01'))
for p in (R/'docs').rglob('*.html'):
 p.write_text(p.read_text().replace('00.14.00','00.14.01'))
edit('README.md','This release adds an experimental native MCP endpoint with per-user OAuth for ChatGPT, eleven account/task/assignment tools, and connection controls.','This patch adds OAuth authorization-response issuer identification (RFC 9207) for ChatGPT connections. It preserves the experimental native MCP endpoint introduced in 00.14.00, its eleven scoped tools, and per-user consent.')
edit('README.md','A real ChatGPT/TLS acceptance test remains necessary;', 'OAuth discovery now advertises `authorization_response_iss_parameter_supported: true`, and approval, cancellation, and authorization-error callbacks carry one `iss` value matching the published issuer exactly. This supports the stable ChatGPT callback already in the default exact allowlist. Preserve callback-specific URLs for existing connections and any explicit `TIMEBOARDAPP_MCP_REDIRECT_URIS` override. See [issuer verification](MCP_ISSUER_TESTING.md) and [connection troubleshooting](MCP_TESTING.md#troubleshoot-a-chatgpt-connection).\n\nA real ChatGPT/TLS acceptance test remains necessary;')
edit('MCP_TESTING.md','Source release 00.14.01 includes an experimental native MCP server for ChatGPT in the existing FastAPI application.','Source release 00.14.01 includes the experimental native MCP server introduced in 00.14.00 and adds RFC 9207 authorization-response issuer identification for ChatGPT callbacks.')
edit('MCP_TESTING.md','Both discovery requests should return HTTP 200 with JSON naming your HTTPS origin.','Both discovery requests should return HTTP 200 with JSON naming your HTTPS origin. Authorization-server metadata must include `authorization_response_iss_parameter_supported: true`. Approval, cancellation, and authorization-error callbacks include one `iss` parameter equal to the metadata issuer, including its trailing slash. The old `v00.14.00` tag does not contain this correction.')
edit('MCP_TESTING.md','choose **OAuth**, and select **Create as a plugin**.','choose **OAuth** with **Dynamic Client Registration (DCR)** when a registration-method choice is shown, and select **Create as a plugin**. Leave manual client credentials empty for DCR; do not use a Timeboard password, signing key, or GPT Actions token.')
edit('MCP_TESTING.md','The default callback allowlist contains `https://chatgpt.com/connector_platform_oauth_redirect`.','Version 00.14.01 implements and advertises issuer identification, which OpenAI documents as enabling the stable callback for eligible new connections. The default callback allowlist contains `https://chatgpt.com/connector_platform_oauth_redirect`. Existing connections may retain a callback-specific URI; preserve those exact URLs when changing overrides. For a regular custom Compose deployment, pass `TIMEBOARDAPP_MCP_REDIRECT_URIS` explicitly when overriding the allowlist.')
insert='''## Troubleshoot a ChatGPT connection

Keep TLS verification and OAuth enabled. A generic creation error alone does not
identify the rejected setting. Diagnose the stages separately:

1. Confirm the public origin has a current, publicly trusted certificate and no
   proxy login wall on discovery or machine OAuth paths. Test without cookies.
2. Confirm both discovery endpoints return JSON, not an HTML login page. Check
   the issuer-support flag and exact `/mcp` resource. A bare `/mcp` request should
   return 401 with `WWW-Authenticate`; that is expected before authorization.
3. During one ChatGPT attempt, inspect the reverse-proxy/application request paths
   and status codes. If discovery succeeds but `POST /register` fails, inspect
   its error description and requested callback. A diagnostic registration using
   the stable callback does not prove ChatGPT requested that same callback.
4. `invalid_redirect_uri` requires the exact callback to be allowed. Add it to
   `mcp.allowed_redirect_uris` or a passed-through `TIMEBOARDAPP_MCP_REDIRECT_URIS`
   override, preserve callbacks already in use, and recreate the container for
   environment changes. Never use wildcards. Recreating a ChatGPT connection may
   be needed after a server upgrade; do not assume a failed draft refreshes its
   cached settings.

Windows Command Prompt checks (replace the hostname):

```cmd
curl.exe -q -fsS --connect-timeout 10 --max-time 20 "https://tasks.example.com/healthz"
curl.exe -q -fsS --connect-timeout 10 --max-time 20 "https://tasks.example.com/.well-known/oauth-protected-resource/mcp"
curl.exe -q -fsS --connect-timeout 10 --max-time 20 "https://tasks.example.com/.well-known/oauth-authorization-server"
curl.exe -q -sS -i --connect-timeout 10 --max-time 20 "https://tasks.example.com/mcp"
```

Do not use `-k` or `--insecure`. Redact `Set-Cookie`, authorization headers,
authorization codes, and tokens before sharing output. If creation fails before
server requests appear, capture only the relevant redacted browser Network
response, not an unredacted HAR. Manual client credentials require an actually
registered OAuth client; they are not a workaround for failed discovery.

'''
edit('MCP_TESTING.md','## Tools and access\n',insert+'## Tools and access\n')
p=R/'tests/test_mcp_preview.py';t=p.read_text();start=t.index('def test_preview_workflow_is_separate_from_main_release');t=t[:start]+'''def test_preview_publication_job_is_retired_before_main_merge(publisher):
    workflow = yaml.safe_load((ROOT / ".github/workflows/audit.yml").read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert triggers["push"]["branches"] == ["main"]
    assert "mcp-preview" not in workflow["jobs"]
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release"]
    assert "github.ref == 'refs/heads/main'" in job["if"]
    assert "needs.release-intent.outputs.publish == 'true'" in job["if"]
    assert set(job["needs"]) == {"regression", "container", "release-intent"}
    assert not publisher.TAG.startswith("v")
''';p.write_text(t)
# End-to-end browser smoke also verifies issuer discovery and the real callback.
edit('scripts/mcp_browser_smoke.py','                    password = (work / "initial-admin-password.txt").read_text().strip()', '''                    discovery = context.request.get(
                        "/.well-known/oauth-authorization-server"
                    )
                    assert discovery.status == 200
                    metadata = discovery.json()
                    assert metadata["authorization_response_iss_parameter_supported"] is True
                    assert metadata["issuer"] == BASE + "/"
                    password = (work / "initial-admin-password.txt").read_text().strip()''')
edit('scripts/mcp_browser_smoke.py','                    assert callback["state"] == ["local-browser-smoke"]','                    assert callback["state"] == ["local-browser-smoke"]\n                    assert callback["iss"] == [metadata["issuer"]]')
# Operator-facing reference and in-application help.
edit('CONTRIBUTING.md','MCP is disabled by default. Keep automated protocol/browser checks separate','MCP is disabled by default. Keep RFC 9207 issuer discovery and all successful/error callback responses aligned; tests must preserve exact redirect checks, PKCE, and resource binding. Keep automated protocol/browser checks separate')
edit('docs/README.md','The 00.14.01 website documents optional native MCP alongside the existing GPT Actions interface.','The 00.14.01 website documents RFC 9207 issuer identification, stable and callback-specific redirect handling, Windows discovery checks, and optional native MCP alongside the existing GPT Actions interface.')
edit('app/templates/help.html','TimeboardApp 00.14.00 includes','TimeboardApp 00.14.01 includes')
edit('app/templates/help.html','Enter that address, choose <strong>OAuth</strong>, and create the plugin.','Enter that address, choose <strong>OAuth</strong> and dynamic client registration when offered, and create the plugin. Leave manual client credentials empty for dynamic registration.')
p=R/'app/templates/help.html';t=p.read_text();needle='  Native MCP is disabled on this installation.';assert needle in t
needle='  MCP is disabled by default; an administrator must enable it on a publicly reachable HTTPS installation.'
assert needle in t
t=t.replace(needle,needle+'''\n  Version 00.14.01 adds authorization-response issuer identification for the stable ChatGPT callback.
  If connection setup is rejected, ask the administrator to check public TLS, OAuth discovery,
  and the exact callback allowlist. Do not substitute a password or signing key for OAuth client credentials.''',1);p.write_text(t)
p=R/'SECURITY.md';t=p.read_text();p.write_text(t.replace('00.14.00','00.14.01'))
# Upgrade public guides without changing their layout or unrelated content.
edit('docs/index.html','This release includes experimental native MCP with per-user OAuth and eleven scoped tools for ChatGPT, alongside existing private GPT Actions.','Patch 00.14.01 adds OAuth issuer identification for ChatGPT callbacks. Experimental native MCP, per-user consent, eleven scoped tools, and existing private GPT Actions remain available.')
edit('docs/docs/chatgpt.html','<h2 id="scopes">', '''<h2 id="issuer-identification">Issuer identification and callbacks</h2>
<p>Version 00.14.01 implements RFC 9207. Authorization-server discovery advertises <code>authorization_response_iss_parameter_supported: true</code>, and approval, cancellation, and authorization-error callbacks include one <code>iss</code> value matching the published issuer exactly, including its trailing slash. This is actual response support, not only a metadata flag.</p>
<p>OpenAI documents that eligible new connections can use the stable callback <code>https://chatgpt.com/connector_platform_oauth_redirect</code> with issuer identification. Existing connections may retain callback-specific URLs. Preserve those exact callbacks and any <code>TIMEBOARDAPP_MCP_REDIRECT_URIS</code> override. Never configure a wildcard or disable OAuth to work around registration.</p>
<h2 id="troubleshooting">Connection troubleshooting</h2>
<p>A generic creation error does not identify the failing stage. Check current public TLS first, then anonymous discovery, then the actual <code>POST /register</code> response during one ChatGPT attempt. A 201 diagnostic registration proves only that its submitted callback and metadata were accepted. It does not establish that ChatGPT submitted the same request.</p>
<p>For dynamic client registration, leave manual client credentials empty. Timeboard passwords, JWT signing keys, and GPT Actions bearer tokens are not OAuth client credentials. For <code>invalid_redirect_uri</code>, allow the exact callback shown by ChatGPT and preserve existing entries, then recreate the container if environment settings changed. Try a new ChatGPT connection after upgrading; existing connection settings may need refreshing.</p>
<p>Run these in Windows Command Prompt against your own deployment:</p>
<div class="code"><pre><code>curl.exe -q -fsS --connect-timeout 10 --max-time 20 "https://tasks.example.com/.well-known/oauth-protected-resource/mcp"
curl.exe -q -fsS --connect-timeout 10 --max-time 20 "https://tasks.example.com/.well-known/oauth-authorization-server"
curl.exe -q -sS -i --connect-timeout 10 --max-time 20 "https://tasks.example.com/mcp"</code></pre></div>
<p>Both discovery endpoints must return JSON over verified HTTPS. The authorization metadata must include the issuer-support flag; an unauthenticated MCP request should return 401 with a discovery challenge. Do not use <code>-k</code>. Redact cookies, authorization codes, and tokens from diagnostics. Read the <a href="https://github.com/paulkakell/timeboardapp/blob/main/MCP_TESTING.md#troubleshoot-a-chatgpt-connection">full troubleshooting procedure</a> and <a href="https://developers.openai.com/plugins/build/auth">OpenAI authentication guidance</a>.</p>
<h2 id="scopes">''')
edit('docs/docs/chatgpt.html','configure <strong>OAuth</strong>, and choose','configure <strong>OAuth</strong> with dynamic client registration when offered, and choose')
edit('docs/docs/deployment.html','The release adds experimental native MCP with per-user OAuth.','Patch 00.14.01 adds OAuth authorization-response issuer identification to the experimental native MCP introduced in 00.14.00. There is no new database migration relative to 00.14.00.')
edit('docs/docs/deployment.html','<h2>MCP storage and backup</h2>', '''<h2>Verify the issuer-identification upgrade</h2><p>The old <code>v00.14.00</code> tag remains unchanged. Select <code>v00.14.01</code> in your checkout or remote Compose build context, rebuild, and recreate. Check that <code>/.well-known/oauth-authorization-server</code> advertises <code>authorization_response_iss_parameter_supported: true</code>, then test a new ChatGPT OAuth connection. A GitHub release and a website deployment do not update your running application.</p><p>The historical <code>mcp-issuer-test.1</code> preview remains available at its original commit and still reports base version 00.14.00. Prefer the numbered patch for ongoing use. Keep the prior image and consistent data backup. A rollback to 00.14.00 removes issuer identification and can prevent new stable-callback connections; it does not undo task edits.</p><h2>MCP storage and backup</h2>''')
edit('docs/docs/deployment.html','the additive 00.13.02 to 00.14.01 MCP upgrade','the additive 00.13.02 to 00.14.00 MCP upgrade')
edit('docs/docs/configuration.html','Copy the exact callback shown by your ChatGPT setup','Version 00.14.01 advertises RFC 9207 issuer identification and includes the exact published issuer in authorization callbacks. Its default stable callback supports eligible new ChatGPT connections; existing connections may use callback-specific URLs. Copy the exact callback shown by your ChatGPT setup')
edit('docs/docs/security.html','Each tool checks its required scope and current database permissions.','Authorization responses identify their issuer under RFC 9207. The metadata issuer and each callback <code>iss</code> value match exactly; request headers cannot choose that identifier. Exact callback checks remain in place. Each tool checks its required scope and current database permissions.')
edit('docs/docs/api.html','MCP clients use individual Timeboard sign-in, S256 PKCE, exact resource binding, and per-tool scopes.','MCP clients use individual Timeboard sign-in, S256 PKCE, exact resource binding, and per-tool scopes. Version 00.14.01 also advertises <code>authorization_response_iss_parameter_supported: true</code> and includes the canonical <code>iss</code> identifier in successful and error authorization callbacks (RFC 9207).')
write('MCP_ISSUER_TESTING.md','''# OAuth issuer-identification verification

Source release: `00.14.01`, PR #38. The fix originated on
`fix/mcp-oauth-issuer-identification` and in the historical `mcp-issuer-test.1`
preview. The old `v00.14.00` release does not include it.

## Protocol behavior

The server implements RFC 9207 authorization-response issuer identification.
Discovery advertises `authorization_response_iss_parameter_supported: true`.
Approval, cancellation, and SDK/provider authorization-error callbacks carry one
`iss` query parameter equal to the published metadata issuer, including the
trailing slash on an origin such as `https://tasks.example.com/`.

The `/authorize` adapter modifies only existing validated callback redirects
containing `code` or `error`. Internal consent navigation and direct errors for
invalid clients/callbacks remain unchanged. Request headers and incoming query
parameters cannot choose the issuer. Exact callbacks, S256 PKCE, resource binding,
CSRF, token lifetimes, dependency locks, and the database schema are unchanged.

OpenAI documents that eligible new connections use the stable callback
`https://chatgpt.com/connector_platform_oauth_redirect` when issuer identification
is implemented and advertised. It is already in the default allowlist. Existing
connections may retain callback-specific URLs. Preserve those exact URLs and any
`TIMEBOARDAPP_MCP_REDIRECT_URIS` override; a custom override can exclude the stable
callback. Do not add wildcards or only set the metadata flag.

## Deploy and verify

Prefer the isolated deployment in [MCP_TESTING.md](MCP_TESTING.md), with synthetic
accounts and a separate private data directory and HTTPS hostname. Never copy
live OAuth state into a test environment. Build from `v00.14.01`, not the old tag:

```sh
git fetch origin --tags
git switch --detach v00.14.01
MCP_TEST_BASE_URL=https://timeboard-test.example.com \\
  docker compose -p timeboard-issuer-test -f docker-compose.mcp-test.yml up -d --build
```

For an existing Compose deployment, take a consistent full database/settings
backup, preserve its data directory, ownership, signing keys, and environment,
and select the patch source before rebuilding and recreating. A custom remote
build can use:

```yaml
image: timeboardapp:00.14.01
build:
  context: "https://github.com/paulkakell/timeboardapp.git#v00.14.01"
  dockerfile: Dockerfile
```

From Windows Command Prompt, use your actual HTTPS hostname:

```cmd
curl.exe -q -fsS --max-time 20 "https://timeboard-test.example.com/healthz"
curl.exe -q -fsS --max-time 20 "https://timeboard-test.example.com/.well-known/oauth-authorization-server"
curl.exe -q -sS -i --max-time 20 "https://timeboard-test.example.com/mcp"
```

Health should identify `00.14.01`; discovery should advertise the issuer flag.
Unauthenticated MCP must still return 401 with a resource-metadata challenge.
Do not bypass certificate verification. Redact cookies and credentials.

Create a new OAuth/DCR connection in ChatGPT, sign in, and test approval, one
disposable task, refresh, and profile disconnection. Test cancellation on a
separate attempt. Inspect actual registration errors rather than inferring their
cause from a generic creation message. A successful local registration does not
prove that ChatGPT sent the same callback. See the [troubleshooting
procedure](MCP_TESTING.md#troubleshoot-a-chatgpt-connection).

## Automated tests and acceptance boundary

```sh
python -m pytest -q tests/test_mcp_issuer_unit.py tests/test_mcp_issuer.py
python -m pytest -q
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
python scripts/mcp_browser_smoke.py
```

The 43 issuer-specific cases cover normalization, query preservation, metadata,
approval/code exchange for all supported client authentication methods, denial,
SDK/provider errors, missing state, invalid callbacks/clients, issuer spoofing,
unchanged registration allowlists, and CSRF rejection. Browser smoke verifies
the real callback issuer, and container smoke checks issuer discovery. These use
isolated data; the intercepted browser callback sends nothing to ChatGPT.

Consult the exact commit's CI run and artifacts. Automated protocol, browser,
container, and audit checks do not establish live ChatGPT acceptance or prove
the original generic creation error had only one cause.

## Historical preview and rollback

The published `mcp-issuer-test.1` tag remains at
`c5f7f4f464dbbfd77ad1ba659e64aa4be22e970d`, with source archives, a manifest, and
checksums. That preview reports base version `00.14.00`. Its one-time publishing
job is retired from current CI; the historical publisher and safety tests are
retained for provenance. Existing tags/assets are not moved or replaced.

Retain the previous image and consistent backup. A source rollback removes
issuer support, not task changes; reconcile newer writes before restoring data.
The patch has no additional migration relative to 00.14.00. Website publication
and source release publication do not deploy the user's application.

## References

- [RFC 9207](https://www.rfc-editor.org/rfc/rfc9207.html)
- [OpenAI authentication](https://developers.openai.com/plugins/build/auth)
''')
edit('VERSIONING.md','Feature release `00.14.00` adds experimental native MCP and per-user OAuth from PR #34, refreshes setup/help/website documentation, and carries forward the FastAPI and PyJWT updates from PRs #35 and #36. The previous published release is `00.13.02`.','Patch release `00.14.01` incorporates PR #38: RFC 9207 OAuth issuer identification, regression coverage, and updated application help and website guides. The previous published release is `00.14.00`, which introduced experimental native MCP and per-user OAuth.')
edit('VERSIONING.md','See `RELEASE_NOTES_00.14.00.md`','See `RELEASE_NOTES_00.14.01.md`')
edit('VERSIONING.md','`00.14.00` maps to `0.14.0`','`00.14.01` maps to `0.14.1`')
edit('VERSIONING.md','## Rollback\n','''## Historical MCP preview

The fixed `mcp-issuer-test.1` tag and assets remain at their original tested
commit. Its one-time branch publication job was retired for the main merge so
later commits cannot try to republish that fixed tag. The historical publisher
and its safety tests remain in source. Current numbered patches use the normal
checked main-branch release process, not the preview publisher.

## Rollback

For 00.14.01, retain the 00.14.00 image and consistent database/settings backup.
There is no additional schema migration or signing-key change. A rollback removes
issuer identification and can prevent new stable-callback ChatGPT connections;
it does not undo task edits. Preserve exact callback overrides already in use.

''')
edit('CHANGELOG.md','## 00.14.00 - 2026-10-08','''## 00.14.01 - 2026-10-08

- Fix: Add RFC 9207 authorization-response issuer identification for native MCP OAuth (PR #38). Discovery advertises support only with matching `iss` values on approval, cancellation, and authorization-error callbacks.
- Fix: Preserve exact issuer serialization, callback validation, PKCE, resource binding, consent CSRF, and invalid-client/redirect rejection. No dependency or database-schema change relative to 00.14.00.
- Tests: Cover issuer handling with 43 focused cases; verify discovery and the real callback in browser/container smoke checks. Retain publication-boundary tests and retire the completed one-time preview job.
- Documentation: Update README, MCP setup/troubleshooting, in-app help, release/versioning guidance, and the public website. Synchronize source version, install references, Docker/npm metadata, and generated badges/reference data.
- Operations: Preserve the published preview and old release tags. Rebuild and recreate from 00.14.01 to deploy; merging source and publishing the website do not update existing applications. Live ChatGPT acceptance remains deployment-specific and unverified here.

## 00.14.00 - 2026-10-08''')
write('RELEASE_NOTES_00.14.01.md','''# TimeboardApp 00.14.01

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
''')
write('VALIDATION_REPORT_00.14.01.md','''# Validation evidence for TimeboardApp 00.14.01

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
''')
write('tests/test_mcp_release_docs.py','''"""Keep issuer setup instructions and current release identity aligned."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def test_current_issuer_guides_describe_the_implemented_contract():
    for name in ("README.md", "MCP_TESTING.md", "MCP_ISSUER_TESTING.md",
                 "docs/docs/chatgpt.html"):
        text = (ROOT / name).read_text()
        assert "authorization_response_iss_parameter_supported" in text, name
        assert "TIMEBOARDAPP_MCP_REDIRECT_URIS" in text, name
        assert "ChatGPT" in text and "exact" in text, name
    guide = (ROOT / "docs/docs/chatgpt.html").read_text()
    assert 'id="issuer-identification"' in guide
    assert 'id="troubleshooting"' in guide
    assert "curl.exe" in guide
    assert "invalid_redirect_uri" in guide


def test_current_installation_guides_select_the_authoritative_patch():
    version = runpy.run_path(str(ROOT / "app/version.py"))["APP_VERSION"]
    for name in ("README.md", "MCP_TESTING.md", "MCP_ISSUER_TESTING.md",
                 "docs/docs/deployment.html", "docs/docs/getting-started.html"):
        assert "v" + version in (ROOT / name).read_text(), name
    assert version in (ROOT / "app/templates/help.html").read_text()
    assert version in (ROOT / "docs/README.md").read_text()
''')
print('PREPARED')
