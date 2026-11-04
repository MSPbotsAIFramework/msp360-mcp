# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for tool handlers: client construction and result capping."""

from ..client import Msp360Client
from ..errors import error_envelope
from ..middleware import current_credentials

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Defensive fallback: the credential middleware already returns 401 before any
# tool runs when the username/password headers are missing (AC9), so
# client_or_none() should never actually return None in production. Kept per
# SOP's not_configured code, which exists for exactly this "should be
# unreachable" case.
NO_CREDENTIALS = error_envelope(
    "not_configured",
    "No MSP360 credentials in request context. Send the X-Msp360-Username and "
    "X-Msp360-Password headers.",
    False,
)


def client_or_none() -> Msp360Client | None:
    creds = current_credentials()
    if creds is None:
        return None
    return Msp360Client(creds.username, creds.password)


def cap_records(items: list, limit: int | None, offset: int | None) -> tuple[list, bool]:
    """Slice `items` into one page per the limit/offset contract (AC11).

    Returns (page, has_more). The caller (errors.dump_json) applies a
    character-count backstop on top of this record-count cap.
    """
    size = min(max(int(limit), 1), MAX_LIMIT) if limit else DEFAULT_LIMIT
    start = max(int(offset), 0) if offset else 0
    page = items[start : start + size]
    has_more = start + size < len(items)
    return page, has_more
