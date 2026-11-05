# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""One backup user's computer inventory joined with backup status (PRD-21607).

The vendor's computer-inventory endpoint (`GET /api/Users/{userId}/Computers`)
only returns a computer's name and policy id; the backup-status fields this
ticket's own ask requires (status, last backup date, plans) live on a
separate endpoint (`GET /api/Monitoring/{userId}`, the same one
`msp360_get_monitoring` already calls for its own fleet-wide view). This
module makes both calls and joins them by `ComputerName` (see
docs/tickets/PRD-21607.md "Tech design" option C) rather than adding an N+1
per-computer call.
"""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..client import Msp360ApiError
from ..errors import dump_json, to_envelope
from ._common import NO_CREDENTIALS, cap_records, client_or_none

# Vendor's MonitoringPlanStatus enum (confirmed via the live Swagger spec and
# the help-site page, see docs/tickets/PRD-21607.md "Current state"). Never
# pass the raw integer through to the Agent (AC5).
_STATUS_LABELS = {
    0: "success",
    1: "overdue",
    2: "error",
    3: "running",
    4: "unknown",
    5: "interrupted",
    6: "unexpectedly_closed",
    7: "warning",
}

# Most severe first (OQ1's "most severe plan wins" convention — this design's
# own choice, not a vendor rule, so a single failing plan is never hidden
# behind a passing one). "no_data" is a computer-level default for a computer
# with no monitored plans at all; it is never a per-plan label.
_SEVERITY_ORDER = [
    "error",
    "unexpectedly_closed",
    "interrupted",
    "warning",
    "overdue",
    "running",
    "unknown",
    "success",
    "no_data",
]
_SEVERITY_RANK = {label: rank for rank, label in enumerate(_SEVERITY_ORDER)}


def _status_label(raw: Any) -> str:
    try:
        code = int(raw)
    except (TypeError, ValueError):
        return "unknown"
    return _STATUS_LABELS.get(code, "unknown")


def _as_items(result: Any, *list_keys: str) -> list:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in list_keys:
            value = result.get(key)
            if isinstance(value, list):
                return value
    return []


def _computer_name(raw: dict) -> Any:
    return raw.get("ComputerName", raw.get("computer_name"))


def _inventory_computer(raw: dict) -> dict:
    return {
        "destination_id": raw.get("DestinationId", raw.get("destination_id")),
        "computer_name": _computer_name(raw),
    }


def _plan_entry(raw: dict) -> dict:
    return {
        "plan_id": raw.get("PlanId", raw.get("plan_id")),
        "plan_name": raw.get("PlanName", raw.get("plan_name")),
        "status": _status_label(raw.get("Status", raw.get("status"))),
        "last_start": raw.get("LastStart", raw.get("last_start")),
        "error_message": raw.get("ErrorMessage", raw.get("error_message")),
    }


def _group_by_computer_name(monitoring_items: list) -> dict[Any, list[dict]]:
    groups: dict[Any, list[dict]] = {}
    for raw in monitoring_items:
        name = _computer_name(raw)
        if name is None:
            continue
        groups.setdefault(name, []).append(raw)
    return groups


def _join_computer(inventory_raw: dict, monitoring_group: list[dict]) -> dict:
    computer = _inventory_computer(inventory_raw)
    if not monitoring_group:
        # AC3: a computer with no matching monitoring record is still listed,
        # not dropped — the audit-report use case this ticket exists for must
        # show "never backed up" computers too.
        computer["status"] = "no_data"
        computer["last_backup_date"] = None
        computer["plans"] = []
        return computer

    plans = [_plan_entry(raw) for raw in monitoring_group]
    computer["status"] = min(
        (p["status"] for p in plans),
        key=lambda label: _SEVERITY_RANK.get(label, _SEVERITY_RANK["unknown"]),
    )
    last_starts = [p["last_start"] for p in plans if p["last_start"]]
    computer["last_backup_date"] = max(last_starts) if last_starts else None
    computer["plans"] = plans
    return computer


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def msp360_list_user_computers(
        user_id: Annotated[
            str, Field(description="MSP360 backup user id (from msp360_list_users) to list computers for.")
        ],
        limit: Annotated[
            int | None, Field(description="Max computers to return (default 50, hard max 200).")
        ] = None,
        offset: Annotated[int | None, Field(description="Computers to skip, for paging past limit.")] = None,
    ) -> str:
        """List one backup user's computers with each computer's backup status, last backup date, and plans."""
        client = client_or_none()
        if client is None:
            return NO_CREDENTIALS
        try:
            inventory = await client.list_user_computers(user_id)
        except Msp360ApiError as exc:
            return to_envelope(exc)
        try:
            monitoring = await client.get_monitoring(user_id)
        except Msp360ApiError as exc:
            return to_envelope(exc)

        inventory_items = _as_items(inventory, "Items", "items")
        monitoring_items = _as_items(monitoring, "Records", "records", "Items", "items")
        groups = _group_by_computer_name(monitoring_items)

        computers = [
            _join_computer(raw, groups.get(_computer_name(raw), [])) for raw in inventory_items
        ]
        page, has_more = cap_records(computers, limit, offset)
        return dump_json({"computers": page, "count": len(page), "has_more": has_more})
