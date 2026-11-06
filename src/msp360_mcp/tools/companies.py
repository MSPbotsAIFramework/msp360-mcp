# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Customer-company listing (PRD-21605)."""

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


def _company_record(raw: dict) -> dict:
    # Vendor's `Id` is the ticket's "CompanyID" under a different name (design
    # doc OQ1) — only the four fields the ticket names are kept;
    # Destinations/DefaultLicense/AllowedLicenses are dropped (SOP section 2.5).
    return {
        "company_id": raw.get("Id", raw.get("company_id")),
        "name": raw.get("Name", raw.get("name")),
        "storage_limit_bytes": raw.get("StorageLimit", raw.get("storage_limit_bytes")),
        "license_settings": raw.get("LicenseSettings", raw.get("license_settings")),
    }


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_companies(
        limit: Annotated[
            int | None, Field(description="Max companies to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Companies to skip, for paging past limit.")] = None,
    ) -> str:
        """List the customer companies visible to the connected MSP360 provider account."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            result = await client.list_companies()
        except Msp360ApiError as exc:
            return to_envelope(exc)
        records = [_company_record(item) for item in _as_items(result)]
        page, has_more = cap_records(records, limit, offset)
        return dump_json({"companies": page, "count": len(page), "has_more": has_more})
