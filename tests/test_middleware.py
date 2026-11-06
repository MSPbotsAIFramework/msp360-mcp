# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Credential middleware tests: missing-header 401 (AC9), and per-request
isolation of X-Msp360-Username/X-Msp360-Password across concurrent requests
(AC10).
"""

from starlette.testclient import TestClient

from msp360_mcp.middleware import (
    PASSWORD_HEADER,
    USERNAME_HEADER,
    CredentialMiddleware,
    current_credentials,
)
from msp360_mcp.server import create_app
from msp360_mcp.settings import Settings


def _make_app():
    settings = Settings()
    return create_app(settings)


def test_health_is_local_and_does_not_require_credentials():
    app = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_mcp_without_credentials_returns_401_with_required_headers():
    app = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert resp.status_code == 401
    body = resp.json()
    assert USERNAME_HEADER in body["required_headers"]
    assert PASSWORD_HEADER in body["required_headers"]


def test_missing_credential_header_returns_401():
    """TC4 (PRD-21605 AC4): missing X-Msp360-Username specifically — this is the
    same middleware path test_mcp_without_credentials_returns_401_with_required_headers
    covers, kept separately named to match docs/tickets/PRD-21605.ac.json's TC4.

    TC7 (PRD-21607, AC7): the credential check is ASGI middleware ahead of
    every tool, not per-tool code, so a tools/call of msp360_list_user_computers
    without credential headers gets the same 401-before-any-vendor-call
    guarantee. Both tickets' ledgers point at this one test id.
    """
    app = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                PASSWORD_HEADER: "s3cret",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        call_resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "msp360_list_user_computers", "arguments": {"user_id": "U1"}},
            },
        )
    assert resp.status_code == 401
    body = resp.json()
    assert USERNAME_HEADER in body["required_headers"]
    assert PASSWORD_HEADER in body["required_headers"]

    assert call_resp.status_code == 401
    body = call_resp.json()
    assert USERNAME_HEADER in body["required_headers"]
    assert PASSWORD_HEADER in body["required_headers"]


def test_missing_all_credential_headers_returns_401():
    """TC4 (PRD-21609, AC4): both headers absent (not just username). Merge-conflict
    rename (review round 1, 2026-11-05): this ticket's docs/tickets/PRD-21609.ac.json
    originally pointed at test_missing_credential_header_returns_401, which collided
    1:1 with PRD-21605's test of the same name (already on main, missing-username-only
    scenario) when dev/PRD-21609 synced with main. Behaviorally the same assertion as
    test_mcp_without_credentials_returns_401_with_required_headers (the credential
    middleware runs before any tool dispatch, so this is not specific to
    msp360_list_domains) — kept as a separate test, renamed rather than dropped, so the
    ledger still has a passing test under TC4 and the PRD-21609.ac.json pointer (updated
    in this same merge) stays accurate.
    """
    app = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert resp.status_code == 401
    body = resp.json()
    assert USERNAME_HEADER in body["required_headers"]
    assert PASSWORD_HEADER in body["required_headers"]


def test_mcp_with_only_username_returns_401():
    app = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                USERNAME_HEADER: "someone",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert resp.status_code == 401


async def test_credentials_land_in_context_and_are_reset_after_request():
    async def downstream(scope, receive, send):
        creds = current_credentials()
        assert creds is not None
        assert creds.username == "alice"
        assert creds.password == "s3cret"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = CredentialMiddleware(downstream)
    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [
            (USERNAME_HEADER.lower().encode(), b"alice"),
            (PASSWORD_HEADER.lower().encode(), b"s3cret"),
        ],
    }

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await middleware(scope, receive, send)

    # The ContextVar is reset in `finally`, so no leakage outside the request.
    assert current_credentials() is None
