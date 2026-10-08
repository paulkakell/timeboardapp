"""Real OAuth HTTP regression coverage for RFC 9207 and ChatGPT callbacks."""

from urllib.parse import parse_qs, urlsplit

import pytest
from test_mcp import (
    BASE,
    CHALLENGE,
    PASSWORD,
    REDIRECT,
    csrf,
    exchange,
    registration,
)
from test_mcp import system as system  # noqa: PLC0414 - Re-export the pytest fixture.

from app.mcp.models import MCPCredential, MCPGrant

STATE = "issuer-test +%&="


def parameters(reg, **overrides):
    return {
        "client_id": reg["client_id"],
        "response_type": "code",
        "redirect_uri": REDIRECT,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "state": STATE,
        "resource": BASE + "/mcp",
        "scope": "tasks:read",
        **overrides,
    }


def metadata(system):
    response = system["client"].get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    return response.json()


def callback_query(response, issuer, state=STATE):
    assert response.status_code in {302, 303}, response.text
    location = urlsplit(response.headers["location"])
    expected = urlsplit(REDIRECT)
    assert (location.scheme, location.netloc, location.path) == (
        expected.scheme,
        expected.netloc,
        expected.path,
    )
    query = parse_qs(location.query, keep_blank_values=True)
    assert query["iss"] == [issuer]
    if state is None:
        assert "state" not in query
    else:
        assert query["state"] == [state]
    assert response.headers["cache-control"] == "no-store"
    return query


def signed_in_consent(system, reg):
    client = system["client"]
    response = client.get("/authorize", params=parameters(reg))
    assert response.status_code == 302, response.text
    consent_url = response.headers["location"]
    assert urlsplit(consent_url).path == "/mcp/consent"
    assert "iss" not in parse_qs(urlsplit(consent_url).query)
    page = client.get(consent_url)
    assert page.status_code == 200
    response = client.post(
        consent_url,
        data={
            "action": "login",
            "username": "manager",
            "password": PASSWORD,
            "csrf_token": csrf(page),
        },
    )
    assert response.status_code == 303, response.text
    assert urlsplit(response.headers["location"]).path == "/mcp/consent"
    assert "iss" not in parse_qs(urlsplit(response.headers["location"]).query)
    page = client.get(consent_url)
    assert page.status_code == 200
    return consent_url, page


def test_discovery_advertises_consistent_issuer_identification(system):
    discovery = metadata(system)
    resource = system["client"].get(
        "/.well-known/oauth-protected-resource/mcp"
    ).json()
    assert discovery["authorization_response_iss_parameter_supported"] is True
    assert discovery["issuer"] == BASE + "/"
    assert resource["authorization_servers"] == [discovery["issuer"]]
    assert resource["resource"] == BASE + "/mcp"
    assert "S256" in discovery["code_challenge_methods_supported"]


@pytest.mark.parametrize("method", ["none", "client_secret_post", "client_secret_basic"])
def test_approved_callback_has_issuer_and_code_remains_exchangeable(system, method):
    reg = registration(system, scopes=["tasks:read"], method=method)
    consent_url, page = signed_in_consent(system, reg)
    response = system["client"].post(
        consent_url, data={"action": "approve", "csrf_token": csrf(page)}
    )
    query = callback_query(response, metadata(system)["issuer"])
    assert "error" not in query
    assert len(query["code"]) == 1
    assert exchange(system, reg, query["code"][0]).status_code == 200


def test_cancelled_callback_has_issuer_without_issuing_credentials(system):
    reg = registration(system, scopes=["tasks:read"])
    consent_url, page = signed_in_consent(system, reg)
    response = system["client"].post(
        consent_url, data={"action": "deny", "csrf_token": csrf(page)}
    )
    query = callback_query(response, metadata(system)["issuer"])
    assert query["error"] == ["access_denied"]
    assert "code" not in query
    with system["sessions"]() as db:
        assert db.query(MCPGrant).filter_by(kind="code").count() == 0
        assert db.query(MCPCredential).count() == 0
    replay = system["client"].post(
        consent_url, data={"action": "approve", "csrf_token": csrf(page)}
    )
    assert replay.status_code == 400
    assert "location" not in replay.headers


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"scope": "not:a:scope"}, "invalid_scope"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": "invalid"}, "invalid_request"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"state": "x" * 1025}, "invalid_request"),
    ],
)
def test_sdk_and_provider_error_callbacks_identify_issuer(system, overrides, error):
    reg = registration(system, scopes=["tasks:read"])
    response = system["client"].get("/authorize", params=parameters(reg, **overrides))
    query = callback_query(
        response, metadata(system)["issuer"], state=overrides.get("state", STATE)
    )
    assert query["error"] == [error]
    assert "code" not in query
    assert query.get("error_description")


def test_missing_required_parameter_error_has_issuer(system):
    reg = registration(system)
    params = parameters(reg)
    del params["code_challenge"]
    response = system["client"].get("/authorize", params=params)
    query = callback_query(response, metadata(system)["issuer"])
    assert query["error"] == ["invalid_request"]


def test_error_without_state_does_not_invent_state(system):
    reg = registration(system)
    params = parameters(reg, scope="not:a:scope")
    del params["state"]
    response = system["client"].get("/authorize", params=params)
    query = callback_query(response, metadata(system)["issuer"], state=None)
    assert query["error"] == ["invalid_scope"]


def test_unexpected_provider_error_callback_has_issuer(system, monkeypatch):
    reg = registration(system)

    async def fail(*args, **kwargs):
        raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr(system["provider"], "authorize", fail)
    response = system["client"].get("/authorize", params=parameters(reg))
    query = callback_query(response, metadata(system)["issuer"])
    assert query["error"] == ["server_error"]
    assert "Synthetic provider failure" not in response.headers["location"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": "unregistered-client"},
        {"redirect_uri": "https://attacker.example/callback"},
        {"redirect_uri": REDIRECT + "/unexpected"},
        {"redirect_uri": REDIRECT + "?unexpected=1"},
    ],
)
def test_untrusted_requests_still_fail_without_redirect(system, overrides):
    reg = registration(system)
    response = system["client"].get("/authorize", params=parameters(reg, **overrides))
    assert response.status_code == 400
    assert "location" not in response.headers
    assert "code" not in response.json()


def test_request_headers_and_iss_cannot_override_configured_issuer(system):
    reg = registration(system)
    headers = {
        "Host": "attacker.example",
        "X-Forwarded-Host": "attacker.example",
        "X-Forwarded-Proto": "http",
    }
    discovery = system["client"].get(
        "/.well-known/oauth-authorization-server", headers=headers
    ).json()
    assert discovery["issuer"] == BASE + "/"
    response = system["client"].get(
        "/authorize",
        params=parameters(reg, scope="invalid", iss="https://attacker.example/"),
        headers=headers,
    )
    query = callback_query(response, discovery["issuer"])
    assert query["error"] == ["invalid_scope"]


def test_issuer_support_does_not_broaden_redirect_registration(system):
    response = system["client"].post(
        "/register",
        json={
            "client_name": "Unlisted callback",
            "redirect_uris": ["https://chatgpt.com/connector/oauth/not-allowlisted"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": "tasks:read",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_consent_csrf_failure_is_not_a_callback(system):
    reg = registration(system)
    consent_url, _ = signed_in_consent(system, reg)
    response = system["client"].post(consent_url, data={"action": "approve"})
    assert response.status_code == 403
    assert "location" not in response.headers
    with system["sessions"]() as db:
        assert db.query(MCPGrant).filter_by(kind="code").count() == 0
