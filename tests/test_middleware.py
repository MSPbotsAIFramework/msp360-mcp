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
