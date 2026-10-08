"""Focused RFC 9207 response-adapter tests without a running OAuth provider."""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.responses import JSONResponse, RedirectResponse
from starlette.testclient import TestClient

from app.mcp.issuer import (
    authorization_issuer,
    identify_authorization_responses,
    with_issuer_parameter,
)

ISSUER = "https://timeboard.example/"
CALLBACK = "https://chatgpt.com/connector_platform_oauth_redirect"


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("https://timeboard.example", ISSUER),
        ("https://timeboard.example/", ISSUER),
        ("https://timeboard.example:8443", "https://timeboard.example:8443/"),
        ("https://TIMEBOARD.example", ISSUER),
    ],
)
def test_issuer_serialization_matches_existing_metadata(base, expected):
    assert authorization_issuer(base) == expected


@pytest.mark.parametrize(
    "query",
    [
        "code=abc&state=s",
        "error=access_denied&state=s",
        "code=abc&iss=https%3A%2F%2Fwrong.example",
        "error=invalid_scope&iss=one&iss=two&%69ss=three",
    ],
)
def test_one_authoritative_issuer_replaces_existing_values(query):
    result = with_issuer_parameter(CALLBACK + "?" + query, ISSUER)
    parsed = parse_qs(urlsplit(result).query, keep_blank_values=True)
    assert parsed["iss"] == [ISSUER]
    original = parse_qs(query, keep_blank_values=True)
    original.pop("iss", None)
    parsed.pop("iss")
    assert parsed == original


def test_query_encoding_repeated_fields_and_fragment_are_preserved():
    query = "tenant=x%2fy&tenant=two&empty=&code=a%2Bb&state=x%20y%2Bz%26%25"
    result = with_issuer_parameter(CALLBACK + "?" + query + "#local", ISSUER)
    assert result == (
        CALLBACK + "?" + query + "&iss=https%3A%2F%2Ftimeboard.example%2F#local"
    )


@pytest.mark.parametrize("status", [302, 303, 307, 308])
@pytest.mark.parametrize("result", ["code=issued", "error=access_denied"])
def test_adapter_identifies_only_callback_response_headers(status, result):
    response = RedirectResponse(
        CALLBACK + "?state=example&" + result,
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Test": "unchanged"},
    )
    wrapped = identify_authorization_responses(response, ISSUER)
    client = TestClient(wrapped, follow_redirects=False)
    received = client.get("/authorize")
    assert received.status_code == status
    assert parse_qs(urlsplit(received.headers["location"]).query)["iss"] == [ISSUER]
    assert received.headers["cache-control"] == "no-store"
    assert received.headers["x-test"] == "unchanged"
    assert received.content == b""


@pytest.mark.parametrize(
    "target",
    [
        "/mcp/consent?request_id=opaque",
        "https://timeboard.example/mcp/consent?request_id=opaque",
        "/login?next=%2Fmcp%2Fconsent&state=error%3Dnot-a-response",
    ],
)
def test_internal_navigation_is_not_an_authorization_response(target):
    response = RedirectResponse(target, status_code=303)
    client = TestClient(
        identify_authorization_responses(response, ISSUER), follow_redirects=False
    )
    received = client.get("/authorize")
    assert received.headers["location"] == target


def test_direct_error_is_not_turned_into_a_redirect():
    response = JSONResponse({"error": "invalid_request"}, status_code=400)
    client = TestClient(identify_authorization_responses(response, ISSUER))
    received = client.get("/authorize")
    assert received.status_code == 400
    assert received.json() == {"error": "invalid_request"}
    assert "location" not in received.headers


def test_non_http_scope_is_forwarded_without_modification():
    messages = []

    async def application(scope, receive, send):
        assert scope == {"type": "websocket"}
        await send({"type": "websocket.close", "code": 1000})

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    asyncio.run(
        identify_authorization_responses(application, ISSUER)(
            {"type": "websocket"}, receive, send
        )
    )
    assert messages == [{"type": "websocket.close", "code": 1000}]
