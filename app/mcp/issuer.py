"""RFC 9207 issuer identification for validated OAuth callback responses."""

from __future__ import annotations

from urllib.parse import parse_qs, unquote_plus, urlencode, urlsplit, urlunsplit

from pydantic import AnyHttpUrl
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def authorization_issuer(base_url: str) -> str:
    """Use the same serialization as the SDK's discovery documents.

    In particular, a bare HTTPS origin has a trailing slash in AnyHttpUrl.
    Do not substitute the slashless URL used to build application endpoints.
    """
    return str(AnyHttpUrl(base_url))


def with_issuer_parameter(redirect_uri: str, issuer: str) -> str:
    """Add one authoritative issuer without changing other query encoding.

    The caller must already have validated the client and callback. This
    helper does not authorize destinations or relax redirect allowlists.
    """
    parts = urlsplit(redirect_uri)
    fields = [
        field
        for field in parts.query.split("&")
        if field and unquote_plus(field.partition("=")[0]) != "iss"
    ]
    fields.append(urlencode({"iss": issuer}))
    return urlunsplit(parts._replace(query="&".join(fields)))


def identify_authorization_responses(app: ASGIApp, issuer: str) -> ASGIApp:
    """Identify SDK authorization callbacks, including early error redirects.

    Wrap only the /authorize handler, after its existing client/redirect
    validation. Internal consent redirects and direct JSON errors remain
    unchanged. Browser approval/cancellation use with_issuer_parameter too.
    """

    async def identified(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_response(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] in {
                302,
                303,
                307,
                308,
            }:
                headers = []
                for name, value in message.get("headers", []):
                    if name.lower() == b"location":
                        location = value.decode("latin-1")
                        query = parse_qs(urlsplit(location).query, keep_blank_values=True)
                        if "code" in query or "error" in query:
                            value = with_issuer_parameter(location, issuer).encode(
                                "latin-1"
                            )
                    headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_response)

    return identified
