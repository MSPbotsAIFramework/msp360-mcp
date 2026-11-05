# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Fleet-wide backup health monitoring."""

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


def _monitoring_record(raw: dict) -> dict:
    return {
        "plan_id": raw.get("PlanId", raw.get("plan_id")),
        "plan_name": raw.get("PlanName", raw.get("plan_name")),
        "company_name": raw.get("CompanyName", raw.get("company_name")),
        "computer_name": raw.get("ComputerName", raw.get("computer_name")),
        "user_id": raw.get("UserID", raw.get("UserId", raw.get("user_id"))),
        "status": raw.get("Status", raw.get("status")),
        "last_start": raw.get("LastStart", raw.get("last_start")),
        "next_start": raw.get("NextStart", raw.get("next_start")),
        "error_message": raw.get("ErrorMessage", raw.get("error_message")),
        "data_copied_bytes": raw.get("DataCopied", raw.get("data_copied_bytes")),
        "total_data_bytes": raw.get("TotalData", raw.get("total_data_bytes")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_get_monitoring(
        user_id: Annotated[
            str | None, Field(description="Filter to one MSP360 backup user (a grouping of computers).")
        ] = None,
        limit: Annotated[
            int | None, Field(description="Max records to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Records to skip, for paging past limit.")] = None,
    ) -> str:
        """Get the latest backup run per plan across every company and computer the account manages."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.get_monitoring(user_id)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        records = [_monitoring_record(item) for item in _as_items(result)]
        page, has_more = cap_records(records, limit, offset)
        return dump_json({"runs": page, "count": len(page), "has_more": has_more})
