# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Per-request credential isolation.

Reads X-Msp360-Username / X-Msp360-Password off each /mcp request and stores
them in a ContextVar for the lifetime of that request only. asyncio copies
the context per task, so concurrent requests on the same server instance
never observe each other's credentials (AC10) — there is no global or
module-level variable holding a credential pair anywhere in this service.

Never falls back to environment variables: this is a single container
serving many tenants concurrently, and a tenant's credentials are only ever
valid for the request that carried them (SOP section 3.1).
"""

import contextvars
from dataclasses import dataclass

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

USERNAME_HEADER = "X-Msp360-Username"
PASSWORD_HEADER = "X-Msp360-Password"


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


_credentials_var: contextvars.ContextVar[Credentials | None] = contextvars.ContextVar(
    "msp360_credentials", default=None
)


def current_credentials() -> Credentials | None:
    """The credential pair bound to the in-flight request, or None outside a request context."""
    return _credentials_var.get()


def _missing_credential_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": "Missing credentials",
            "message": (
                "Send the MSP360 Managed Backup provider-account username and password in the "
                f"{USERNAME_HEADER} and {PASSWORD_HEADER} headers."
            ),
            "required_headers": [USERNAME_HEADER, PASSWORD_HEADER],
            "optional_headers": [],
        },
        status_code=401,
    )


class CredentialMiddleware:
    """Reads the MSP360 username/password headers on /mcp requests and binds them to the request context."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        headers = Request(scope).headers
        username = headers.get(USERNAME_HEADER)
        password = headers.get(PASSWORD_HEADER)
        if not username or not password:
            response = _missing_credential_response()
            await response(scope, receive, send)
            return

        token = _credentials_var.set(Credentials(username=username, password=password))
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(token)
