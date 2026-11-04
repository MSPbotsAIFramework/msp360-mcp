# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Fixed error vocabulary and token-economical JSON serialization for tool
results (SOP sections 2.5 and 4.2).
"""

import json
from typing import Any

from .client import Msp360ApiError

MAX_CHARS = 20_000

ERROR_CODES = {
    "not_configured",
    "unauthorized",
    "not_found",
    "invalid_argument",
    "rate_limited",
    "upstream_error",
}

# Vendor HTTP status -> (envelope code, retryable). Per the design doc's
# "Error envelope" table.
_STATUS_TO_CODE: dict[int, tuple[str, bool]] = {
    400: ("invalid_argument", False),
    401: ("unauthorized", False),
    403: ("unauthorized", False),
    404: ("not_found", False),
    422: ("invalid_argument", False),
    429: ("rate_limited", True),
}


def classify_status(status_code: int) -> tuple[str, bool]:
    """Map a vendor HTTP status (0 = network/connection failure) to (code, retryable)."""
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if status_code >= 500 or status_code == 0:
        return "upstream_error", True
    return "invalid_argument", False


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str)


def error_envelope(code: str, message: str, retryable: bool) -> str:
    assert code in ERROR_CODES, code
    return _compact({"error": {"code": code, "message": message, "retryable": retryable}})


def to_envelope(exc: Msp360ApiError) -> str:
    code, retryable = classify_status(exc.status_code)
    return error_envelope(code, exc.message, retryable)


def dump_json(data: Any, max_chars: int = MAX_CHARS) -> str:
    """Serialize data compactly; if too large, trim the largest list field and flag truncation.

    Response sizes are already bounded by the tool-level `limit` cap (AC11),
    so this is a backstop, not the primary pagination mechanism.
    """
    text = _compact(data)
    if len(text) <= max_chars:
        return text

    if isinstance(data, dict):
        list_keys = [k for k, v in data.items() if isinstance(v, list)]
        if list_keys:
            key = max(list_keys, key=lambda k: len(_compact(data[k])))
            items = data[key]
            lo, hi = 0, len(items)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                candidate = dict(data)
                candidate[key] = items[:mid]
                candidate["has_more"] = True
                if len(_compact(candidate)) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            candidate = dict(data)
            candidate[key] = items[:lo]
            candidate["has_more"] = True
            return _compact(candidate)

    return _compact(
        {
            "truncated": True,
            "note": f"Result is {len(text)} characters, above the {max_chars} limit. Narrow the request.",
        }
    )
