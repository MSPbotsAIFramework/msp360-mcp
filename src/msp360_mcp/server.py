# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""FastMCP server factory and HTTP app assembly."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .middleware import CredentialMiddleware
from .settings import Settings
from .tools import auth, companies, monitoring, plans, user_computers

INSTRUCTIONS = (
    "MSP360 Managed Backup Service (MBS) monitors and controls scheduled backup/restore plans "
    "across the computers an MSP provider manages. msp360_provider_login confirms the configured "
    "provider account can authenticate — useful right after credentials are entered, before any "
    "other MSP360 tool is used. msp360_get_monitoring gives the fleet-wide view (every plan's "
    "latest run, status, and error across all companies and computers; filter to one backup user "
    "with user_id). msp360_list_companies lists the customer companies visible to the provider "
    "account. msp360_list_user_computers gives one backup user's computers with each "
    "computer's backup status, last backup date, and contributing plans — the audit view for "
    "one user, keyed off a user_id from msp360_list_users. For a single computer: "
    "msp360_list_plans finds its configured plans, "
    "msp360_get_plan_info / msp360_get_plan_history inspect one plan's current state and run "
    "history, and msp360_start_plan / msp360_stop_plan trigger or cancel a run. start/stop are "
    "fire-and-forget against an async backup engine — they report that the action was accepted, "
    "not that the run has finished."
)

MODULES = (auth, monitoring, plans, companies, user_computers)


def create_mcp_server(settings: Settings) -> FastMCP:
    mcp = FastMCP(
        name="msp360-mcp",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        # The gateway forwards requests with its own Host header; DNS-rebinding
        # protection would otherwise reject them with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    for module in MODULES:
        module.register(mcp)
    return mcp


def create_app(settings: Settings) -> Starlette:
    mcp = create_mcp_server(settings)
    mcp_app = mcp.streamable_http_app()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # Mount() does not run a sub-app's lifespan; without forwarding it the
    # streamable-http session manager's task group never starts, and /mcp
    # fails with "Task group is not initialized" while /health still passes.
    return Starlette(
        routes=[Route("/health", health), Mount("/", app=CredentialMiddleware(mcp_app))],
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )
