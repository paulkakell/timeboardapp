"""OAuth protocol routes, browser consent, and user-controlled disconnection."""

from __future__ import annotations

import base64
import binascii
import hmac
import re
import secrets
import time
from urllib.parse import unquote, urlencode

from fastapi import APIRouter, HTTPException, Request
from mcp.server.auth.middleware.client_auth import (
    AuthenticationError,
    ClientAuthenticator,
)
from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from mcp.server.auth.routes import (
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..auth import authenticate_user, credential_fingerprint, session_user
from .auth import SCOPE_LABELS, SCOPES, digest
from .issuer import (
    authorization_issuer,
    identify_authorization_responses,
    with_issuer_parameter,
)
from .models import MCPConnection, MCPGrant


def normalize_basic_client_id(app):
    """Supply the body client ID required by SDK 2.2, then let it verify Basic.

    OAuth permits HTTP Basic clients to omit client_id from the form. This
    adapter changes no credentials and never authenticates a client itself.
    """

    async def handle(scope, receive, send):
        if scope["method"] != "POST":
            return await app(scope, receive, send)
        request = Request(scope, receive)
        authorization = request.headers.get("authorization", "")
        if not authorization.lower().startswith("basic "):
            return await app(scope, receive, send)
        form = await request.form()
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            client_id, _ = decoded.split(":", 1)
            client_id = unquote(client_id)
            if (
                not client_id
                or len(client_id) > 64
                or "client_secret" in form
                or any(len(form.getlist(k)) != 1 for k in form)
            ):
                raise ValueError("Ambiguous client authentication")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return await JSONResponse(
                {"error": "invalid_request"},
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)
        fields = list(form.multi_items())
        if "client_id" not in form:
            fields.append(("client_id", client_id))
        body = urlencode(fields).encode()
        headers = [
            (k, v)
            for k, v in scope["headers"]
            if k.lower() not in {b"content-length", b"content-type", b"authorization"}
        ]
        headers.extend(
            [
                (b"content-length", str(len(body)).encode()),
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"authorization", ("Basic " + authorization[6:]).encode()),
            ]
        )
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await app({**scope, "headers": headers}, replay, send)

    return handle


def protocol_routes(provider):
    issuer = authorization_issuer(provider.issuer)
    routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(provider.issuer),
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    authorize_route = next(r for r in routes if r.path == "/authorize")
    authorize_route.app = identify_authorization_responses(authorize_route.app, issuer)
    token_route = next(r for r in routes if r.path == "/token")
    original_token_app = token_route.app

    async def guarded_token(scope, receive, send):
        if scope["method"] != "POST":
            return await original_token_app(scope, receive, send)
        request = Request(scope, receive)
        body = await request.body()
        form = await request.form()
        # The SDK checks PKCE and redirects. Resource binding must also be
        # enforced on the token request, before invoking its protocol handler.
        if any(len(form.getlist(k)) != 1 for k in form):
            response = JSONResponse({"error": "invalid_request"}, status_code=400)
        elif form.get("resource") != provider.resource:
            response = JSONResponse({"error": "invalid_target"}, status_code=400)
        elif form.get("grant_type") == "authorization_code" and not re.fullmatch(
            r"[A-Za-z0-9._~-]{43,128}", str(form.get("code_verifier", ""))
        ):
            response = JSONResponse({"error": "invalid_grant"}, status_code=400)
        else:
            delivered = False

            async def replay():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await original_token_app(scope, replay, send)
        response.headers["Cache-Control"] = "no-store"
        return await response(scope, receive, send)

    token_route.app = guarded_token

    # Advertise all three methods the provider and SDK actually implement.
    async def metadata(request):
        from mcp.server.auth.routes import build_metadata

        result = build_metadata(
            AnyHttpUrl(provider.issuer),
            None,
            ClientRegistrationOptions(
                enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
            ),
            RevocationOptions(enabled=True),
        ).model_dump(mode="json", exclude_none=True)
        # Keep the exact canonical issuer advertised in resource discovery.
        result["issuer"] = issuer
        result["authorization_response_iss_parameter_supported"] = True
        result["token_endpoint_auth_methods_supported"] = [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ]
        result["revocation_endpoint_auth_methods_supported"] = result[
            "token_endpoint_auth_methods_supported"
        ]
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    routes[0] = Route(
        "/.well-known/oauth-authorization-server",
        cors_middleware(metadata, ["GET", "OPTIONS"]),
        methods=["GET", "OPTIONS"],
    )
    routes.extend(
        create_protected_resource_routes(
            AnyHttpUrl(provider.resource),
            [AnyHttpUrl(provider.issuer)],
            scopes_supported=SCOPES,
            resource_name="TimeboardApp",
        )
    )

    async def revoke(request):
        # MCP SDK 2.2's revocation form requires client_secret even for public
        # clients and client_id in the body for HTTP Basic clients. Keep its
        # authenticator, but accept all advertised methods per RFC 7009.
        headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
        form = await request.form()
        if (
            any(len(form.getlist(k)) != 1 for k in form)
            or not isinstance(form.get("token"), str)
            or len(form["token"]) > 128
        ):
            return JSONResponse(
                {"error": "invalid_request"}, status_code=400, headers=headers
            )
        try:
            client = await ClientAuthenticator(provider).authenticate_request(request)
        except AuthenticationError:
            return JSONResponse(
                {"error": "invalid_client"}, status_code=401, headers=headers
            )
        token = await provider.load_access_token(form["token"])
        if token is None:
            token = await provider.load_refresh_token(client, form["token"])
        if token and token.client_id == client.client_id:
            await provider.revoke_token(token)
        return Response(status_code=200, headers=headers)

    for index, route in enumerate(routes):
        if route.path == "/revoke":
            routes[index] = Route(
                "/revoke",
                cors_middleware(revoke, ["POST", "OPTIONS"]),
                methods=["POST", "OPTIONS"],
            )
    for route in routes:
        if route.path in {"/token", "/revoke"}:
            route.app = CORSMiddleware(
                normalize_basic_client_id(route.app),
                allow_origins=["*"],
                allow_methods=["POST", "OPTIONS"],
                allow_headers=["MCP-Protocol-Version", "Authorization", "Content-Type"],
            )
    return routes


def browser_router(provider):
    router = APIRouter(include_in_schema=False)
    issuer = authorization_issuer(provider.issuer)

    def pending(db, request_id, request):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", request_id):
            raise HTTPException(400, "Invalid authorization request")
        row = db.get(MCPGrant, digest(request_id))
        if (
            not row
            or row.kind != "pending"
            or row.used
            or row.expires_at <= time.time()
        ):
            raise HTTPException(
                400, "Authorization request expired; reconnect from ChatGPT"
            )
        binding = request.session.setdefault(
            "mcp_consent_binding", secrets.token_urlsafe(32)
        )
        binding_hash = digest(binding)
        if row.session_hash is None:
            db.query(MCPGrant).filter_by(
                token_hash=row.token_hash, session_hash=None
            ).update({"session_hash": binding_hash})
            db.commit()
            db.refresh(row)
        if not hmac.compare_digest(row.session_hash or "", binding_hash):
            raise HTTPException(
                400, "Authorization request belongs to another browser session"
            )
        return row

    def render(request, db, row, user, error=None):
        from ..routers.ui import _template_context, templates

        params = AuthorizationParams.model_validate_json(row.params_json)
        client = provider.client(db, row.client_id)
        if not client:
            raise HTTPException(400, "Client is no longer registered")
        response = templates.TemplateResponse(
            request,
            "mcp_consent.html",
            _template_context(
                request,
                user,
                db=db,
                client_name=client.client_name or "MCP application",
                redirect_uri=str(params.redirect_uri),
                requested_scopes=[SCOPE_LABELS[s] for s in params.scopes],
                request_id=request.query_params.get("request_id", ""),
                error=error,
            ),
        )
        response.headers["Cache-Control"] = "no-store"
        # no-referrer turns native form POST Origin into null in browsers,
        # which correctly fails the website's origin/CSRF check. same-origin
        # preserves that check and still hides the consent URL from callbacks.
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @router.get("/mcp/consent")
    def consent_get(request: Request, request_id: str):
        with provider.sessions() as db:
            row = pending(db, request_id, request)
            return render(request, db, row, session_user(request, db))

    @router.post("/mcp/consent")
    async def consent_post(request: Request, request_id: str):
        form = dict(await request.form())
        # Password hashing and SQLite work run off the ASGI loop.
        from .auth import threaded

        @threaded
        def finish():
            with provider.sessions() as db:
                row = pending(db, request_id, request)
                user = session_user(request, db)
                if form.get("action") == "login":
                    username, password = (
                        str(form.get("username", "")),
                        str(form.get("password", "")),
                    )
                    user = (
                        authenticate_user(db, username[:255], password)
                        if len(password) <= 1024
                        else None
                    )
                    if not user:
                        return render(
                            request, db, row, None, "Invalid username/email or password"
                        )
                    binding = request.session["mcp_consent_binding"]
                    request.session.clear()
                    request.session.update(
                        user_id=user.id,
                        credential_fingerprint=credential_fingerprint(user),
                        mcp_consent_binding=binding,
                        csrf_token=secrets.token_urlsafe(32),
                    )
                    return RedirectResponse(
                        "/mcp/consent?request_id=" + request_id, status_code=303
                    )
                if not user:
                    raise HTTPException(401, "Sign in before authorizing access")
                if form.get("action") not in {"approve", "deny"}:
                    raise HTTPException(400, "Choose Allow access or Cancel")
                params = AuthorizationParams.model_validate_json(row.params_json)
                client = provider.client(db, row.client_id)
                if not client or str(params.redirect_uri) not in [
                    str(u) for u in client.redirect_uris
                ]:
                    raise HTTPException(400, "Invalid registered redirect URI")
                claimed = (
                    db.query(MCPGrant)
                    .filter_by(token_hash=row.token_hash, used=False)
                    .update({"used": True})
                )
                if not claimed:
                    raise HTTPException(400, "Authorization request was already used")
                if form["action"] == "deny":
                    db.commit()
                    target = construct_redirect_uri(
                        str(params.redirect_uri),
                        error="access_denied",
                        state=params.state,
                    )
                else:
                    code = secrets.token_urlsafe(32)
                    db.add(
                        MCPGrant(
                            token_hash=digest(code),
                            kind="code",
                            client_id=row.client_id,
                            user_id=user.id,
                            fingerprint=credential_fingerprint(user),
                            params_json=params.model_dump_json(),
                            expires_at=int(time.time()) + 120,
                        )
                    )
                    db.commit()
                    target = construct_redirect_uri(
                        str(params.redirect_uri), code=code, state=params.state
                    )
                return RedirectResponse(
                    with_issuer_parameter(target, issuer),
                    status_code=303,
                    headers={
                        "Cache-Control": "no-store",
                        "Referrer-Policy": "no-referrer",
                    },
                )

        return await finish()

    @router.get("/profile/connections")
    def connections(request: Request):
        from ..routers.ui import _template_context, templates

        with provider.sessions() as db:
            user = session_user(request, db)
            if not user:
                return RedirectResponse("/login", status_code=303)
            rows = (
                db.query(MCPConnection)
                .filter_by(user_id=user.id, revoked=False)
                .filter(MCPConnection.expires_at > time.time())
                .all()
            )
            entries = []
            for row in rows:
                client = provider.client(db, row.client_id)
                entries.append(
                    {
                        "id": row.id,
                        "name": (client.client_name if client else None)
                        or "MCP application",
                        "scopes": row.scopes,
                    }
                )
            return templates.TemplateResponse(
                request,
                "mcp_connections.html",
                _template_context(request, user, db=db, connections=entries),
            )

    @router.post("/profile/connections/{connection_id}/revoke")
    def disconnect(request: Request, connection_id: str):
        with provider.sessions() as db:
            user = session_user(request, db)
            if not user:
                raise HTTPException(401, "Not authenticated")
            db.query(MCPConnection).filter_by(id=connection_id, user_id=user.id).update(
                {"revoked": True}
            )
            db.commit()
        return RedirectResponse("/profile/connections", status_code=303)

    return router
