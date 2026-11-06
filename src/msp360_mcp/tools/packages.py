# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Storage/license package listing (PRD-21608)."""

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


def _package_record(raw: dict) -> dict:
    # Only the fields the ticket's own ask plus AC9 (enabled flag) name are
    # kept; Description, isGlacierRestoreLimit, UseRestoreLimit, and
    # GlacierRestoreType are backup-engine-internal fields unrelated to "list
    # packages" and are dropped per SOP section 2.5 field trimming (design doc
    # PRD-21608 "Data & API contract"). StorageLimit/RestoreLimit are
    # vendor-confirmed GB values for this endpoint, so they are named
    # accordingly instead of assumed as bytes.
    return {
        "package_id": raw.get("ID", raw.get("package_id")),
        "name": raw.get("Name", raw.get("name")),
        "cost": raw.get("Cost", raw.get("cost")),
        "storage_limit_gb": raw.get("StorageLimit", raw.get("storage_limit_gb")),
        "restore_limit_gb": raw.get("RestoreLimit", raw.get("restore_limit_gb")),
        "enabled": raw.get("Enabled", raw.get("enabled")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_packages(
        limit: Annotated[
            int | None, Field(description="Max packages to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Packages to skip, for paging past limit.")] = None,
    ) -> str:
        """List the storage/license packages configured on the connected MSP360 provider account."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.list_packages()
        except Msp360ApiError as exc:
            return to_envelope(exc)
        # Disabled packages are not filtered out (AC9) -- same call PRD-21606
        # made for disabled users; the Agent sees the `enabled` flag per
        # package and decides what to do with it.
        records = [_package_record(item) for item in _as_items(result)]
        page, has_more = cap_records(records, limit, offset)
        return dump_json({"packages": page, "count": len(page), "has_more": has_more})
