# OAuth issuer-identification verification

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
MCP_TEST_BASE_URL=https://timeboard-test.example.com \
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
