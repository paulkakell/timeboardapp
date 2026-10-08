"""Persistent OAuth provider using the SDK's protocol handlers and local accounts.

The authorization server and resource server share storage, not authentication
cookies. Opaque credentials are hashed. Client secrets are derived with a
separate HMAC context, so they need not be stored in the database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from functools import partial, wraps

import anyio
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..auth import credential_fingerprint
from ..models import User
from .models import MCPClient, MCPConnection, MCPCredential, MCPGrant

SCOPES = ["tasks:read", "tasks:write", "users:read", "tasks:assign"]
SCOPE_LABELS = {
    "tasks:read": "Read your tasks and task counts",
    "tasks:write": "Create, change, complete, archive, and restore your tasks",
    "users:read": "List people you are allowed to assign tasks to",
    "tasks:assign": "Assign your tasks to eligible users",
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def threaded(function):
    @wraps(function)
    async def call(*args, **kwargs):
        return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))

    return call


class TimeboardOAuth:
    def __init__(self, settings, session_factory):
        self.settings = settings
        self.sessions = session_factory
        self.issuer = settings.app.base_url.rstrip("/")
        self.resource = self.issuer + "/mcp"

    def _secret(self, client_id):
        key = self.settings.security.jwt_secret.encode()
        return hmac.new(
            key, ("timeboard-mcp-client-v1:" + client_id).encode(), hashlib.sha256
        ).hexdigest()

    def client(self, db, client_id):
        row = db.get(MCPClient, client_id)
        if not row:
            return None
        client = OAuthClientInformationFull.model_validate_json(row.metadata_json)
        if not all(
            str(uri) in self.settings.mcp.allowed_redirect_uris
            for uri in client.redirect_uris or []
        ):
            return None
        if client.token_endpoint_auth_method != "none":
            client.client_secret = self._secret(client_id)
        return client

    @threaded
    def get_client(self, client_id):
        with self.sessions() as db:
            return self.client(db, client_id)

    @threaded
    def register_client(self, client_info):
        uris = [str(uri) for uri in client_info.redirect_uris or []]
        if (
            not uris
            or len(uris) > 5
            or any(uri not in self.settings.mcp.allowed_redirect_uris for uri in uris)
        ):
            raise RegistrationError(
                "invalid_redirect_uri",
                "Use an exact redirect URI configured by the Timeboard operator",
            )
        if client_info.token_endpoint_auth_method not in {
            "none",
            "client_secret_post",
            "client_secret_basic",
        }:
            raise RegistrationError(
                "invalid_client_metadata", "Unsupported client authentication method"
            )
        if len(client_info.client_name or "") > 128:
            raise RegistrationError(
                "invalid_client_metadata", "Client name exceeds 128 characters"
            )
        if not set(client_info.grant_types) <= {"authorization_code", "refresh_token"}:
            raise RegistrationError("invalid_client_metadata", "Unsupported grant type")
        if client_info.token_endpoint_auth_method != "none":
            client_info.client_secret = self._secret(client_info.client_id)
        metadata = client_info.model_dump(mode="json", exclude={"client_secret"})
        if len(json.dumps(metadata)) > 16384:
            raise RegistrationError(
                "invalid_client_metadata", "Client metadata is too large"
            )
        with self.sessions() as db:
            # Serialize the limit check and insertion, including across processes.
            from sqlalchemy import text

            db.execute(text("BEGIN IMMEDIATE"))
            if db.query(MCPClient).count() >= self.settings.mcp.max_clients:
                raise RegistrationError(
                    "invalid_client_metadata",
                    "Client registration limit reached; contact the operator",
                )
            db.add(
                MCPClient(id=client_info.client_id, metadata_json=json.dumps(metadata))
            )
            db.commit()

    @threaded
    def authorize(self, client, params: AuthorizationParams):
        if params.resource != self.resource:
            raise AuthorizeError(
                "invalid_target", "resource must exactly match the MCP URL"
            )
        if str(
            params.redirect_uri
        ) not in self.settings.mcp.allowed_redirect_uris or str(
            params.redirect_uri
        ) not in [str(u) for u in client.redirect_uris]:
            raise AuthorizeError("invalid_request", "Redirect URI does not match")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.code_challenge):
            raise AuthorizeError(
                "invalid_request", "A valid S256 PKCE challenge is required"
            )
        if len(params.state or "") > 1024:
            raise AuthorizeError("invalid_request", "state is too long")
        scopes = params.scopes or ["tasks:read"]
        if not set(scopes) <= set(SCOPES):
            raise AuthorizeError("invalid_scope", "Unsupported scope")
        params.scopes = scopes
        raw = secrets.token_urlsafe(32)
        with self.sessions() as db:
            db.query(MCPGrant).filter(MCPGrant.expires_at < int(time.time())).delete()
            if db.query(MCPGrant).filter(MCPGrant.kind == "pending").count() >= 1000:
                raise AuthorizeError(
                    "temporarily_unavailable", "Too many pending authorizations"
                )
            db.add(
                MCPGrant(
                    token_hash=digest(raw),
                    kind="pending",
                    client_id=client.client_id,
                    params_json=params.model_dump_json(),
                    expires_at=int(time.time()) + 600,
                )
            )
            db.commit()
        return self.issuer + "/mcp/consent?request_id=" + raw

    def connection(self, db, connection_id):
        row = db.get(MCPConnection, connection_id)
        if (
            not row
            or row.revoked
            or row.expires_at <= time.time()
            or row.resource != self.resource
            or row.issuer != self.issuer
        ):
            return None
        user = db.get(User, row.user_id)
        if not user or not hmac.compare_digest(
            row.fingerprint, credential_fingerprint(user)
        ):
            return None
        if not self.client(db, row.client_id):
            return None
        return row

    def access(self, db, token):
        if not token.startswith("tbm_a_") or len(token) > 128:
            return None
        row = db.get(MCPCredential, digest(token))
        if not row or row.kind != "access" or row.used or row.expires_at <= time.time():
            return None
        con = self.connection(db, row.connection_id)
        if not con:
            return None
        return AccessToken(
            token=token,
            client_id=con.client_id,
            scopes=row.scopes.split(),
            expires_at=row.expires_at,
            resource=con.resource,
            subject=str(con.user_id),
            claims={"iss": con.issuer, "connection_id": con.id},
        )

    @threaded
    def verify_token(self, token):
        with self.sessions() as db:
            return self.access(db, token)

    load_access_token = verify_token

    @threaded
    def load_authorization_code(self, client, authorization_code):
        if len(authorization_code) > 128:
            return None
        with self.sessions() as db:
            row = db.get(MCPGrant, digest(authorization_code))
            if not row or row.kind != "code" or row.client_id != client.client_id:
                return None
            params = AuthorizationParams.model_validate_json(row.params_json)
            return AuthorizationCode(
                code=authorization_code,
                client_id=client.client_id,
                expires_at=row.expires_at,
                subject=str(row.user_id),
                **params.model_dump(exclude={"state"}),
            )

    def _tokens(self, db, connection, scopes):
        access, refresh = (
            "tbm_a_" + secrets.token_urlsafe(32),
            "tbm_r_" + secrets.token_urlsafe(32),
        )
        ttl = min(
            self.settings.mcp.access_token_seconds,
            connection.expires_at - int(time.time()),
        )
        for raw, kind, expiry in [
            (access, "access", int(time.time()) + ttl),
            (refresh, "refresh", connection.expires_at),
        ]:
            db.add(
                MCPCredential(
                    token_hash=digest(raw),
                    kind=kind,
                    connection_id=connection.id,
                    scopes=" ".join(scopes),
                    expires_at=expiry,
                )
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ttl,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    @threaded
    def exchange_authorization_code(self, client, authorization_code):
        with self.sessions() as db:
            row = db.get(MCPGrant, digest(authorization_code.code))
            if (
                not row
                or row.client_id != client.client_id
                or row.expires_at <= time.time()
            ):
                raise TokenError(
                    "invalid_grant", "Authorization code is invalid or expired"
                )
            user = db.get(User, row.user_id)
            if not user or not hmac.compare_digest(
                row.fingerprint or "", credential_fingerprint(user)
            ):
                raise TokenError(
                    "invalid_grant", "Account credentials changed; reconnect"
                )
            claimed = (
                db.query(MCPGrant)
                .filter(MCPGrant.token_hash == row.token_hash, MCPGrant.used.is_(False))
                .update({"used": True}, synchronize_session=False)
            )
            if not claimed:
                # A concurrent exchange can commit after our initial read.
                db.refresh(row)
                if row.connection_id:
                    db.query(MCPConnection).filter_by(id=row.connection_id).update(
                        {"revoked": True}
                    )
                    db.commit()
                raise TokenError("invalid_grant", "Authorization code was already used")
            if (
                db.query(MCPConnection)
                .filter_by(user_id=user.id, revoked=False)
                .filter(MCPConnection.expires_at > time.time())
                .count()
                >= 20
            ):
                raise TokenError(
                    "invalid_grant",
                    "Disconnect an existing application before connecting another",
                )
            con = MCPConnection(
                id=secrets.token_hex(24),
                client_id=client.client_id,
                user_id=user.id,
                fingerprint=credential_fingerprint(user),
                issuer=self.issuer,
                resource=self.resource,
                scopes=" ".join(authorization_code.scopes),
                created_at=int(time.time()),
                expires_at=int(time.time())
                + self.settings.mcp.refresh_token_days * 86400,
            )
            db.add(con)
            db.flush()
            row.connection_id = con.id
            tokens = self._tokens(db, con, authorization_code.scopes)
            db.commit()
            return tokens

    @threaded
    def load_refresh_token(self, client, refresh_token):
        if not refresh_token.startswith("tbm_r_") or len(refresh_token) > 128:
            return None
        with self.sessions() as db:
            row = db.get(MCPCredential, digest(refresh_token))
            con = (
                self.connection(db, row.connection_id)
                if row and row.kind == "refresh"
                else None
            )
            if not con or con.client_id != client.client_id:
                return None
            # Used tokens reach exchange so replay revokes the whole connection.
            return RefreshToken(
                token=refresh_token,
                client_id=client.client_id,
                scopes=row.scopes.split(),
                expires_at=row.expires_at,
                resource=con.resource,
                subject=str(con.user_id),
            )

    @threaded
    def exchange_refresh_token(self, client, refresh_token, scopes):
        with self.sessions() as db:
            row = db.get(MCPCredential, digest(refresh_token.token))
            con = self.connection(db, row.connection_id) if row else None
            if (
                not con
                or con.client_id != client.client_id
                or row.expires_at <= time.time()
            ):
                raise TokenError("invalid_grant", "Refresh token is invalid or expired")
            if not set(scopes) <= set(row.scopes.split()):
                raise TokenError("invalid_scope", "Refresh cannot expand permissions")
            claimed = (
                db.query(MCPCredential)
                .filter(
                    MCPCredential.token_hash == row.token_hash,
                    MCPCredential.used.is_(False),
                )
                .update({"used": True}, synchronize_session=False)
            )
            if not claimed:
                con.revoked = True
                db.commit()
                raise TokenError(
                    "invalid_grant", "Refresh token reuse detected; reconnect"
                )
            tokens = self._tokens(db, con, scopes)
            db.commit()
            return tokens

    @threaded
    def revoke_token(self, token):
        with self.sessions() as db:
            row = db.get(MCPCredential, digest(token.token))
            if row:
                db.query(MCPConnection).filter_by(
                    id=row.connection_id, client_id=token.client_id
                ).update({"revoked": True})
                db.commit()
