# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Backup/restore plan tools: list, inspect, and start/stop a run."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import Msp360ApiError
from ..errors import dump_json, to_envelope
from ._common import NO_CREDENTIALS, client_or_none


def _as_items(result: Any) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("Plans", "plans", "Items", "items"):
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _plan_summary(raw: dict) -> dict:
    return {
        "id": raw.get("Id", raw.get("id")),
        "name": raw.get("Name", raw.get("name")),
        "type": raw.get("Type", raw.get("type")),
    }


def _history_run(raw: dict) -> dict:
    return {
        "start_time": raw.get("StartTime", raw.get("start_time")),
        "duration_seconds": raw.get("Duration", raw.get("duration_seconds")),
        "status": raw.get("Status", raw.get("status")),
        "data_copied_bytes": raw.get("DataCopied", raw.get("data_copied_bytes")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_plans(
        hid: Annotated[str, Field(description="MSP360 computer HID to list backup/restore plans for.")],
    ) -> str:
        """List the backup/restore plans configured on one computer."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.list_plans(hid)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        plans = [_plan_summary(item) for item in _as_items(result)]
        return dump_json({"plans": plans, "count": len(plans)})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_get_plan_info(
        hid: Annotated[str, Field(description="MSP360 computer HID the plan runs on.")],
        plan_id: Annotated[str, Field(description="Backup/restore plan id.")],
    ) -> str:
        """Get one plan's current status, last/next run time, and most recent error."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.get_plan_info(hid, plan_id)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        out = {
            "id": plan_id,
            "status": result.get("Status", result.get("status")),
            "last_start": result.get("LastStart", result.get("last_start")),
            "next_start": result.get("NextStart", result.get("next_start")),
            "error_message": result.get("ErrorMessage", result.get("error_message")),
        }
        return dump_json(out)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_get_plan_history(
        hid: Annotated[str, Field(description="MSP360 computer HID the plan runs on.")],
        plan_id: Annotated[str, Field(description="Backup/restore plan id.")],
        year: Annotated[int | None, Field(description="Filter: 4-digit year.")] = None,
        month: Annotated[int | None, Field(description="Filter: month 1-12. Requires year.")] = None,
        day: Annotated[int | None, Field(description="Filter: day 1-31. Requires year and month.")] = None,
    ) -> str:
        """List a plan's past runs (start time, duration, status, data volume), optionally filtered by date."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.get_plan_history(hid, plan_id, year, month, day)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        runs = [_history_run(item) for item in _as_items(result)]
        return dump_json({"runs": runs, "count": len(runs), "has_more": False})

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=False, idempotentHint=False))
    async def msp360_start_plan(
        hid: Annotated[str, Field(description="MSP360 computer HID the plan runs on.")],
        plan_id: Annotated[str, Field(description="Backup/restore plan id.")],
        mode: Annotated[
            str | None, Field(description="Optional vendor-defined run mode override.")
        ] = None,
    ) -> str:
        """Start a run of a backup/restore plan now (fire-and-forget; does not wait for completion)."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            await client.start_plan(hid, plan_id, mode)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        return dump_json({"id": plan_id, "status": "started"})

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def msp360_stop_plan(
        hid: Annotated[str, Field(description="MSP360 computer HID the plan runs on.")],
        plan_id: Annotated[str, Field(description="Backup/restore plan id.")],
        force: Annotated[
            bool | None, Field(description="Optional vendor-defined forced-cancel flag.")
        ] = None,
    ) -> str:
        """Stop/cancel a plan's in-progress run. Stopping an already-stopped plan is a no-op, not an error."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            await client.stop_plan(hid, plan_id, force)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        return dump_json({"id": plan_id, "status": "stopped"})
