# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""tools/list snapshot + error-envelope mapping + login/token-cache tests.

No real network calls: tool enumeration goes through FastMCP's in-process
list_tools()/call_tool(), and vendor responses are mocked with respx against
the real base URL (msp360_mcp.client.BASE_URL). Per-request credentials are
supplied by setting the same ContextVar the credential middleware sets
(msp360_mcp.middleware._credentials_var), the same technique
test_middleware.py uses to verify the middleware itself.

Each test uses its own unique username so the process-lifetime token cache
in client.py never leaks a cached token from one test into another.
"""

import asyncio
import json

import pytest
import respx
from httpx import Response

from msp360_mcp.client import BASE_URL, _fingerprint, _token_cache
from msp360_mcp.middleware import Credentials, _credentials_var
from msp360_mcp.server import create_mcp_server
from msp360_mcp.settings import Settings

# required params + annotation hints expected for each of the 6 tools.
# Any signature change here is a breaking contract change (SOP section 13).
EXPECTED_TOOLS = {
    "msp360_get_monitoring": (set(), {"readOnlyHint": True}),
    "msp360_list_plans": ({"hid"}, {"readOnlyHint": True}),
    "msp360_get_plan_info": ({"hid", "plan_id"}, {"readOnlyHint": True}),
    "msp360_get_plan_history": ({"hid", "plan_id"}, {"readOnlyHint": True}),
    "msp360_start_plan": ({"hid", "plan_id"}, {"destructiveHint": False, "idempotentHint": False}),
    "msp360_stop_plan": ({"hid", "plan_id"}, {"destructiveHint": True, "idempotentHint": True}),
}


def _result_text(call_tool_result) -> str:
    """call_tool() returns (content_blocks, {'result': str}); tools always return a string."""
    _, structured = call_tool_result
    return structured["result"]


def _set_creds(username: str, password: str = "pw"):
    return _credentials_var.set(Credentials(username=username, password=password))


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"
    assert len(tools) == 6

    by_name = {t.name: t for t in tools}
    for name, (expected_required, expected_annotations) in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"

        ann = tool.annotations
        for hint, value in expected_annotations.items():
            actual = getattr(ann, hint, None) if ann is not None else None
            assert actual == value, f"{name}: {hint}={actual}, expected {value}"

        description = tool.description or ""
        first_line = description.strip().splitlines()[0]
        assert len(first_line) <= 100, f"{name}: first line too long ({len(first_line)}): {first_line!r}"
        assert len(description) <= 500, f"{name}: description too long ({len(description)} chars)"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.asyncio
@respx.mock
async def test_login_then_vendor_call_returns_trimmed_plan_fields():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-1", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Computers/H1/Plans").mock(
        return_value=Response(
            200,
            json=[
                {"Id": "P1", "Name": "Daily", "Type": "Image"},
                {"Id": "P2", "Name": "Weekly", "Type": "File"},
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-list-plans")
    try:
        result = await mcp.call_tool("msp360_list_plans", {"hid": "H1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {
        "plans": [{"id": "P1", "name": "Daily", "type": "Image"}, {"id": "P2", "name": "Weekly", "type": "File"}],
        "count": 2,
    }


@pytest.mark.asyncio
@respx.mock
async def test_cached_token_skips_second_login():
    login_route = respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-2", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Computers/H1/Plans").mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-cache-reuse")
    try:
        await mcp.call_tool("msp360_list_plans", {"hid": "H1"})
        await mcp.call_tool("msp360_list_plans", {"hid": "H1"})
    finally:
        _credentials_var.reset(token)
    assert login_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_bad_credentials_login_401_maps_to_unauthorized_no_infinite_retry():
    login_route = respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(401, json={"message": "invalid username or password"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-bad-creds")
    try:
        result = await mcp.call_tool("msp360_get_plan_info", {"hid": "H1", "plan_id": "P1"})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "unauthorized"
    assert envelope["error"]["retryable"] is False
    assert login_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_stale_cached_token_relogs_in_once_then_succeeds():
    # Seed the cache directly with a token the vendor will reject, instead of
    # relying on an extra mocked call to get it there — deterministic and
    # avoids depending on unrelated routing behavior.
    _token_cache.set(_fingerprint("user-relogin-once", "pw"), "stale-tok", 3600)

    login_route = respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "fresh-tok", "expires_in": 3600})
    )
    plan_route = respx.get(f"{BASE_URL}/api/Computers/H1/Plans/P1/info").mock(
        side_effect=[
            Response(401, json={"message": "token expired"}),
            Response(200, json={"Status": "Idle", "LastStart": "t1", "NextStart": "t2", "ErrorMessage": None}),
        ]
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-relogin-once")
    try:
        result = await mcp.call_tool("msp360_get_plan_info", {"hid": "H1", "plan_id": "P1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["status"] == "Idle"
    assert plan_route.call_count == 2
    assert login_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_vendor_404_maps_to_not_found_envelope():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-404", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Computers/H1/Plans/missing/info").mock(
        return_value=Response(404, json={"message": "plan not found"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-404")
    try:
        result = await mcp.call_tool("msp360_get_plan_info", {"hid": "H1", "plan_id": "missing"})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "not_found"
    assert "plan not found" in envelope["error"]["message"]


@pytest.mark.asyncio
@respx.mock
async def test_429_triggers_bounded_retry_then_rate_limited_envelope():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-429", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}/api/Monitoring").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "0"}, json={"message": "rate limited"}),
            Response(429, headers={"Retry-After": "0"}, json={"message": "rate limited"}),
            Response(429, headers={"Retry-After": "0"}, json={"message": "rate limited"}),
            Response(429, headers={"Retry-After": "0"}, json={"message": "rate limited"}),
        ]
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-429")
    try:
        result = await mcp.call_tool("msp360_get_monitoring", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "rate_limited"
    assert envelope["error"]["retryable"] is True
    # 1 initial attempt + 3 retries = 4 calls total, then it gives up.
    assert route.call_count == 4


@pytest.mark.asyncio
@respx.mock
async def test_monitoring_filters_by_user_id_and_trims_fields():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-mon", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Monitoring/U1").mock(
        return_value=Response(
            200,
            json=[
                {
                    "PlanName": "Daily",
                    "CompanyName": "Acme",
                    "ComputerName": "PC1",
                    "UserID": "U1",
                    "Status": "Success",
                    "LastStart": "t1",
                    "NextStart": "t2",
                    "ErrorMessage": None,
                    "PlanId": "P1",
                    "DataCopied": 100,
                    "TotalData": 500,
                }
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-monitoring-filter")
    try:
        result = await mcp.call_tool("msp360_get_monitoring", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["count"] == 1
    assert body["runs"][0]["company_name"] == "Acme"
    assert body["runs"][0]["computer_name"] == "PC1"
    assert body["has_more"] is False


@pytest.mark.asyncio
@respx.mock
async def test_monitoring_caps_result_size_and_sets_has_more():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-cap", "expires_in": 3600})
    )
    records = [
        {
            "PlanId": f"P{i}",
            "PlanName": f"Plan {i}",
            "CompanyName": "Acme",
            "ComputerName": f"PC{i}",
            "UserID": "U1",
            "Status": "Success",
        }
        for i in range(300)
    ]
    respx.get(f"{BASE_URL}/api/Monitoring").mock(return_value=Response(200, json=records))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-cap")
    try:
        # Ask for more than the hard cap: the cap must still win over the caller's limit.
        result = await mcp.call_tool("msp360_get_monitoring", {"limit": 1000})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    # The 200-record hard cap and the 20,000-char serialization cap both apply
    # (SOP section 2.5, "whichever bound is hit first wins"): with this
    # fixture's record shape, the char cap bites first and trims below 200.
    assert len(body["runs"]) <= 200
    assert body["has_more"] is True
    assert len(_result_text(result)) < 20_000


@pytest.mark.asyncio
@respx.mock
async def test_start_plan_reports_started():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-start", "expires_in": 3600})
    )
    route = respx.post(f"{BASE_URL}/api/Computers/H1/Plans/P1/start").mock(
        return_value=Response(200, json={"Status": "started"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-start")
    try:
        result = await mcp.call_tool("msp360_start_plan", {"hid": "H1", "plan_id": "P1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"id": "P1", "status": "started"}
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_stop_plan_reports_stopped():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-stop", "expires_in": 3600})
    )
    respx.post(f"{BASE_URL}/api/Computers/H1/Plans/P1/stop").mock(
        return_value=Response(200, json={"Status": "stopped"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-stop")
    try:
        result = await mcp.call_tool("msp360_stop_plan", {"hid": "H1", "plan_id": "P1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"id": "P1", "status": "stopped"}


@pytest.mark.asyncio
@respx.mock
async def test_plan_history_returns_filtered_runs():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-hist", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}/api/Computers/H1/Plans/P1/history").mock(
        return_value=Response(
            200,
            json=[
                {"StartTime": "t1", "Duration": 60, "Status": "Success", "DataCopied": 100},
                {"StartTime": "t2", "Duration": 30, "Status": "Error", "DataCopied": 0},
                {"StartTime": "t3", "Duration": 90, "Status": "Success", "DataCopied": 200},
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-history")
    try:
        result = await mcp.call_tool(
            "msp360_get_plan_history", {"hid": "H1", "plan_id": "P1", "year": 2026, "month": 10}
        )
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["count"] == 3
    assert body["runs"][1] == {
        "start_time": "t2",
        "duration_seconds": 30,
        "status": "Error",
        "data_copied_bytes": 0,
    }
    assert route.calls.last.request.url.params["date.year"] == "2026"
    assert route.calls.last.request.url.params["date.month"] == "10"


@pytest.mark.asyncio
@respx.mock
async def test_not_found_404_on_start_and_stop_maps_cleanly():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-nf", "expires_in": 3600})
    )
    respx.post(f"{BASE_URL}/api/Computers/H1/Plans/missing/start").mock(
        return_value=Response(404, json={"message": "plan not found"})
    )
    respx.post(f"{BASE_URL}/api/Computers/H1/Plans/missing/stop").mock(
        return_value=Response(404, json={"message": "plan not found"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-404-start-stop")
    try:
        start_result = await mcp.call_tool("msp360_start_plan", {"hid": "H1", "plan_id": "missing"})
        stop_result = await mcp.call_tool("msp360_stop_plan", {"hid": "H1", "plan_id": "missing"})
    finally:
        _credentials_var.reset(token)
    for result in (start_result, stop_result):
        envelope = json.loads(_result_text(result))
        assert envelope["error"]["code"] == "not_found"


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_requests_with_different_credentials_never_cross_talk():
    """TC-MSP-012 (AC10): no real second MSP360 account was available at build
    time, so this exercises the same isolation guarantee the design doc
    proposed as a local-harness substitute — two mocked vendor backends
    behind the shared base URL, driven by two concurrent asyncio Tasks (each
    Task gets its own copy of the credential ContextVar, the same isolation
    the ASGI middleware relies on per-request in production).
    """

    def login_side_effect(request):
        body = json.loads(request.content)
        token = "tokA" if body["UserName"] == "accountA" else "tokB"
        return Response(200, json={"access_token": token, "expires_in": 3600})

    def monitoring_side_effect(request):
        auth = request.headers["Authorization"]
        company = "CompanyA" if auth == "Bearer tokA" else "CompanyB"
        return Response(200, json=[{"PlanId": "P1", "CompanyName": company, "ComputerName": "PC1"}])

    respx.post(f"{BASE_URL}/api/Provider/Login").mock(side_effect=login_side_effect)
    respx.get(f"{BASE_URL}/api/Monitoring").mock(side_effect=monitoring_side_effect)

    mcp = create_mcp_server(Settings())

    async def call_as(username: str) -> dict:
        token = _set_creds(username, password="pw-" + username)
        try:
            result = await mcp.call_tool("msp360_get_monitoring", {})
        finally:
            _credentials_var.reset(token)
        return json.loads(_result_text(result))

    # asyncio.gather schedules each coroutine as its own Task, and each Task
    # captures its own copy of the contextvars context at creation time — the
    # same mechanism CredentialMiddleware relies on per ASGI request.
    result_a, result_b = await asyncio.gather(call_as("accountA"), call_as("accountB"))

    assert result_a["runs"][0]["company_name"] == "CompanyA"
    assert result_b["runs"][0]["company_name"] == "CompanyB"

    fp_a = _fingerprint("accountA", "pw-accountA")
    fp_b = _fingerprint("accountB", "pw-accountB")
    assert _token_cache.get(fp_a) == "tokA"
    assert _token_cache.get(fp_b) == "tokB"
