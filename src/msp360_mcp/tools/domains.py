# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Domain-level backup storage listing (CloudBackup tag, GET /api/apps/domains).

Distinct from msp360_list_user_computers (PRD-21607, Computers tag): this
endpoint returns per-domain storage/license records, not computers. See
docs/tickets/PRD-21609.md "Requirement understanding" and OQ1.
"""

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


def _domain_record(raw: dict) -> dict:
    storage_limit = raw.get("StorageLimit") or {}
    storage_usage = raw.get("StorageUsage") or {}
    return {
        "domain": raw.get("Domain", raw.get("domain")),
        "storage": raw.get("Storage", raw.get("storage")),
        "storage_limit": {
            "total_limit": storage_limit.get("TotalLimit", storage_limit.get("total_limit")),
            "command_drive_limit": storage_limit.get(
                "CommandDriveLimit", storage_limit.get("command_drive_limit")
            ),
            "other_services_limit": storage_limit.get(
                "OtherServicesLimit", storage_limit.get("other_services_limit")
            ),
        },
        "storage_usage_total": storage_usage.get("Total", storage_usage.get("total")),
        "activated_licenses": raw.get("ActivatedLicenses", raw.get("activated_licenses")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_domains(
        limit: Annotated[
            int | None, Field(description="Max records to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Records to skip, for paging past limit.")] = None,
    ) -> str:
        """List domain-level backup storage records (apps/domains) under the MSP360 provider account."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.list_domains()
        except Msp360ApiError as exc:
            return to_envelope(exc)
        records = [_domain_record(item) for item in _as_items(result)]
        page, has_more = cap_records(records, limit, offset)
        return dump_json({"domains": page, "count": len(page), "has_more": has_more})
