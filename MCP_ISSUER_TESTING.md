# OAuth issuer-identification testing branch

Branch: `fix/mcp-oauth-issuer-identification`.
Base: `4d4f9e01351cfd48c31e3cae3da01476b3ae5aa1` (source release `00.14.00`).
This is an unreleased compatibility fix. The `v00.14.00` tag does not include it;
the application version is intentionally unchanged until a release is requested.

## Changes and compatibility

The server implements RFC 9207 authorization-response issuer identification.
OAuth metadata advertises `authorization_response_iss_parameter_supported: true`.
Approval, cancellation, and SDK-generated authorization error callbacks include
one `iss` query parameter matching the metadata `issuer` exactly, including the
trailing slash on an origin such as `https://tasks.example.com/`.

The `/authorize` adapter only modifies existing redirects carrying `code` or
`error`, after the SDK has validated the client and callback. Internal consent
navigation and direct JSON errors remain unchanged. Browser approval and denial
use the same helper after their existing validation. Request headers and incoming
query parameters cannot supply the issuer.

OAuth, S256 PKCE, resource binding, exact redirect allowlists, token lifetimes,
consent CSRF, dependency locks, and the database schema are unchanged. Never add a
wildcard callback or advertise the metadata flag without implementing responses.

OpenAI documents that eligible new connections use the stable callback
`https://chatgpt.com/connector_platform_oauth_redirect` when issuer identification
is implemented and advertised. That callback is in the default allowlist.
Existing connections may retain a callback-specific URI. Preserve their exact
callbacks, or create a new test connection. Custom
`TIMEBOARDAPP_MCP_REDIRECT_URIS` overrides still apply and may exclude the stable
callback; preserve required entries when updating an override.

This addresses a protocol compatibility gap. Successful diagnostic registration
alone does not establish live ChatGPT compatibility or identify every cause of a
generic plugin-creation error. A real connection test remains necessary.

## Isolated deployment

Use [MCP_TESTING.md](MCP_TESTING.md) with synthetic accounts, a separate persistent
directory, HTTPS hostname, and container. Do not copy live OAuth state or use the
production database. In the isolated checkout:

```sh
git fetch origin fix/mcp-oauth-issuer-identification
git switch --detach origin/fix/mcp-oauth-issuer-identification
MCP_TEST_BASE_URL=https://timeboard-test.example.com \
  docker compose -p timeboard-issuer-test -f docker-compose.mcp-test.yml up -d --build
```

Set `MCP_TEST_DATA` to an unused private directory if the default already contains
another test instance. Follow the existing guide's ownership, TLS, reverse-proxy,
credential-reset, and backup precautions. Pushing a branch does not update a
running container, and rebuilding `v00.14.00` does not include this fix.

From Windows Command Prompt, substitute the actual test hostname:

```cmd
curl.exe -q -sS "https://timeboard-test.example.com/.well-known/oauth-authorization-server"
curl.exe -q -sS -i "https://timeboard-test.example.com/mcp"
```

Discovery must advertise the issuer-support flag. Unauthenticated MCP must still
return 401 with a resource-metadata challenge. Keep certificate verification on.
Create a new OAuth/DCR connection in ChatGPT and test sign-in, approval,
cancellation, one disposable task, and profile disconnection. Redact cookies,
authorization codes, and tokens from reports.

## Tests

```sh
python -m pytest -q tests/test_mcp_issuer_unit.py tests/test_mcp_issuer.py
python -m pytest -q
python scripts/sync_docs.py --check
python scripts/release_metadata.py --check
python scripts/mcp_browser_smoke.py
```

The helper suite covers issuer normalization, duplicate removal, query encoding,
redirect status/header preservation, internal navigation, and direct errors.
The real-provider suite covers metadata consistency, approval and code exchange
for all three client authentication methods, denial without credentials,
SDK/provider errors, absent state, invalid clients/callbacks, issuer spoofing,
unchanged registration allowlists, and CSRF rejection.

Record results against the exact commit. Helper tests are not full application,
container, public TLS, or live ChatGPT acceptance tests.

## Rollback and scope

Stop the isolated test project without deleting its data and rebuild the prior
verified source when required. This branch has no database migration, main-branch
merge, release publication, or production rollout. Source rollback does not undo
live task changes.

## References

- [RFC 9207](https://www.rfc-editor.org/rfc/rfc9207.html)
- [OpenAI plugin authentication](https://developers.openai.com/plugins/build/auth)
