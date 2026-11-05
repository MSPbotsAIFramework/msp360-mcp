# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""List MSP360 backup users (PRD-21606)."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import Msp360ApiError
from ..errors import dump_json, to_envelope
from ._common import NO_CREDENTIALS, cap_records, client_or_none


def _as_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("Records", "records", "Items", "items"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _user_record(raw: dict) -> dict:
    # Only the five fields the ticket names are exposed (SOP section 2.5
    # field-trimming rule) -- FirstName/LastName/NotificationEmails/
    # LicenseManagmentMode/DestinationList are dropped (see docs/tickets/
    # PRD-21606.md "Non-goals" and OQ1).
    return {
        "user_id": raw.get("ID", raw.get("id")),
        "email": raw.get("Email", raw.get("email")),
        "company": raw.get("Company", raw.get("company")),
        "enabled": raw.get("Enabled", raw.get("enabled")),
        "space_used_kb": raw.get("SpaceUsed", raw.get("space_used_kb")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_users(
        limit: Annotated[
            int | None, Field(description="Max records to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Records to skip, for paging past limit.")] = None,
    ) -> str:
        """List the MSP360 backup users visible to the connected provider account."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.list_users()
        except Msp360ApiError as exc:
            return to_envelope(exc)
        records = [_user_record(item) for item in _as_items(result)]
        page, has_more = cap_records(records, limit, offset)
        return dump_json({"users": page, "count": len(page), "has_more": has_more})
