# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Connection/credential check: confirm the configured MSP360 provider account can log in."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..client import Msp360ApiError
from ..errors import dump_json, to_envelope
from ._common import NO_CREDENTIALS, client_or_none


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_provider_login() -> str:
        """Verify the MSP360 provider-account credentials work by logging in.

        Confirms the account can authenticate against MSP360's own login call before any other
        MSP360 tool is used. Never returns the vendor's access_token — only whether the login
        succeeded and (if the vendor reports it) how long that session would last.
        """
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            expires_in = await client.verify_login()
        except Msp360ApiError as exc:
            return to_envelope(exc)
        return dump_json({"authenticated": True, "expires_in": expires_in})
