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

import httpx
import pytest
import respx
from httpx import Response

from msp360_mcp.client import BASE_URL, _fingerprint, _token_cache
from msp360_mcp.middleware import Credentials, _credentials_var
from msp360_mcp.server import create_mcp_server
from msp360_mcp.settings import Settings

# required params + annotation hints expected for each of the 7 tools.
# Any signature change here is a breaking contract change (SOP section 13).
EXPECTED_TOOLS = {
    "msp360_provider_login": (set(), {"readOnlyHint": True}),
    "msp360_get_monitoring": (set(), {"readOnlyHint": True}),
    "msp360_list_companies": (set(), {"readOnlyHint": True}),
    "msp360_list_plans": ({"hid"}, {"readOnlyHint": True}),
    "msp360_get_plan_info": ({"hid", "plan_id"}, {"readOnlyHint": True}),
    "msp360_get_plan_history": ({"hid", "plan_id"}, {"readOnlyHint": True}),
    "msp360_start_plan": ({"hid", "plan_id"}, {"destructiveHint": False, "idempotentHint": False}),
    "msp360_stop_plan": ({"hid", "plan_id"}, {"destructiveHint": True, "idempotentHint": True}),
    "msp360_list_user_computers": ({"user_id"}, {"readOnlyHint": True}),
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
    assert len(tools) == 9

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
async def test_get_plan_info_surfaces_error_status():
    """TC-MSP-005 (AC4): a plan-info response with Status: "Error" must surface
    both the error status and the populated error_message, not just the
    happy-path Idle/None fields covered by test_stale_cached_token_relogs_in_once_then_succeeds.
    """
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-plan-error", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Computers/H1/Plans/P1/info").mock(
        return_value=Response(
            200,
            json={
                "Status": "Error",
                "LastStart": "t1",
                "NextStart": "t2",
                "ErrorMessage": "backup failed: disk full",
            },
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-plan-error")
    try:
        result = await mcp.call_tool("msp360_get_plan_info", {"hid": "H1", "plan_id": "P1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["status"] == "Error"
    assert body["error_message"] == "backup failed: disk full"


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
async def test_provider_login_returns_authenticated_without_token():
    """TC1 (AC1): valid credentials -> {authenticated: true, expires_in: <int>}, no access_token key."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "secret-tok-1", "expires_in": 1800})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-ok")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"authenticated": True, "expires_in": 1800}
    assert "access_token" not in body


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_missing_expires_in_returns_null():
    """TC2 (AC1): vendor response omits expires_in -> expires_in: null, no raise."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "secret-tok-2"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-no-expiry")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"authenticated": True, "expires_in": None}


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_rejected_credentials_returns_unauthorized():
    """TC3 (AC2): vendor 401 on the login call -> unauthorized/retryable:false envelope."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(401, json={"message": "invalid username or password"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-401")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "unauthorized"
    assert envelope["error"]["retryable"] is False


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_forbidden_returns_unauthorized():
    """TC4 (AC2): vendor 403 on the login call -> same unauthorized/retryable:false envelope as 401."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(403, json={"message": "invalid username or password"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-403")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "unauthorized"
    assert envelope["error"]["retryable"] is False


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_never_exposes_raw_token_or_password():
    """TC7 (AC5): a distinctive access_token string from a mocked 200 response must not appear
    anywhere in the tool's returned JSON text."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "secret-tok-leak-check", "expires_in": 1800})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-leak-check")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    assert "secret-tok-leak-check" not in _result_text(result)


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_does_not_use_or_populate_token_cache():
    """verify_login() is a fresh-login-every-time connectivity check (PRD-21604 non-functional):
    it must not read a pre-existing cached token for this credential pair, and must not leave a
    new cache entry behind for other tools to pick up."""
    _token_cache.invalidate(_fingerprint("user-login-nocache", "pw"))
    login_route = respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-nocache", "expires_in": 60})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-nocache")
    try:
        await mcp.call_tool("msp360_provider_login", {})
        await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    # Two calls -> two fresh vendor logins, unlike the other tools' cached-token path.
    assert login_route.call_count == 2
    assert _token_cache.get(_fingerprint("user-login-nocache", "pw")) is None


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_upstream_error_on_5xx_or_timeout():
    """TC8 (AC6): vendor 5xx and a connection timeout on the login call both map to the
    upstream_error/retryable:true envelope, not an unhandled exception."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(return_value=Response(503, json={"message": "down"}))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-login-5xx")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "upstream_error"
    assert envelope["error"]["retryable"] is True

    respx.post(f"{BASE_URL}/api/Provider/Login").mock(side_effect=httpx.ConnectTimeout("timed out"))
    token = _set_creds("user-login-timeout")
    try:
        result = await mcp.call_tool("msp360_provider_login", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "upstream_error"
    assert envelope["error"]["retryable"] is True


@pytest.mark.asyncio
@respx.mock
async def test_provider_login_isolates_concurrent_tenant_credentials():
    """TC6 (AC4): two tenants calling msp360_provider_login concurrently, one with valid
    credentials and one invalid, never see each other's outcome."""

    def login_side_effect(request):
        body = json.loads(request.content)
        if body["UserName"] == "good-account":
            return Response(200, json={"access_token": "tokGood", "expires_in": 900})
        return Response(401, json={"message": "invalid username or password"})

    respx.post(f"{BASE_URL}/api/Provider/Login").mock(side_effect=login_side_effect)
    mcp = create_mcp_server(Settings())

    async def call_as(username: str) -> dict:
        token = _set_creds(username, password="pw-" + username)
        try:
            result = await mcp.call_tool("msp360_provider_login", {})
        finally:
            _credentials_var.reset(token)
        return json.loads(_result_text(result))

    result_good, result_bad = await asyncio.gather(call_as("good-account"), call_as("bad-account"))

    assert result_good == {"authenticated": True, "expires_in": 900}
    assert result_bad["error"]["code"] == "unauthorized"


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


# -- msp360_list_companies (PRD-21605) ------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_returns_trimmed_fields():
    """TC1 (AC1)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-1", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Companies").mock(
        return_value=Response(
            200,
            json=[
                {
                    "Id": "C1",
                    "Name": "Acme",
                    "StorageLimit": 1000,
                    "LicenseSettings": 0,
                    "Destinations": ["d1"],
                    "DefaultLicense": 1,
                    "AllowedLicenses": [1, 2],
                },
                {"Id": "C2", "Name": "Contoso", "StorageLimit": 2000, "LicenseSettings": 1},
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-basic")
    try:
        result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {
        "companies": [
            {"company_id": "C1", "name": "Acme", "storage_limit_bytes": 1000, "license_settings": 0},
            {"company_id": "C2", "name": "Contoso", "storage_limit_bytes": 2000, "license_settings": 1},
        ],
        "count": 2,
        "has_more": False,
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_empty_result_is_not_an_error():
    """TC2 (AC2)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-empty", "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Companies").mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-empty")
    try:
        result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"companies": [], "count": 0, "has_more": False}
    assert "error" not in body


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_login_rejected_returns_unauthorized_without_calling_list():
    """TC3 (AC3)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(401, json={"message": "invalid username or password"})
    )
    list_route = respx.get(f"{BASE_URL}/api/Companies").mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-bad-creds")
    try:
        result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "unauthorized"
    assert envelope["error"]["retryable"] is False
    assert list_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_isolates_concurrent_tenant_results():
    """TC5 (AC5)."""

    def login_side_effect(request):
        body = json.loads(request.content)
        token = "tokCompaniesA" if body["UserName"] == "companiesAccountA" else "tokCompaniesB"
        return Response(200, json={"access_token": token, "expires_in": 3600})

    def companies_side_effect(request):
        auth = request.headers["Authorization"]
        if auth == "Bearer tokCompaniesA":
            return Response(200, json=[{"Id": "C1", "Name": "Acme", "StorageLimit": 1, "LicenseSettings": 0}])
        return Response(200, json=[{"Id": "C2", "Name": "Contoso", "StorageLimit": 2, "LicenseSettings": 0}])

    respx.post(f"{BASE_URL}/api/Provider/Login").mock(side_effect=login_side_effect)
    respx.get(f"{BASE_URL}/api/Companies").mock(side_effect=companies_side_effect)

    mcp = create_mcp_server(Settings())

    async def call_as(username: str) -> dict:
        token = _set_creds(username, password="pw-" + username)
        try:
            result = await mcp.call_tool("msp360_list_companies", {})
        finally:
            _credentials_var.reset(token)
        return json.loads(_result_text(result))

    result_a, result_b = await asyncio.gather(
        call_as("companiesAccountA"), call_as("companiesAccountB")
    )

    assert result_a["companies"] == [{"company_id": "C1", "name": "Acme", "storage_limit_bytes": 1, "license_settings": 0}]
    assert result_b["companies"] == [{"company_id": "C2", "name": "Contoso", "storage_limit_bytes": 2, "license_settings": 0}]


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_never_exposes_raw_token_or_password(caplog):
    """TC6 (AC6)."""
    distinctive_token = "SECRET-TOKEN-xyz789-do-not-leak"
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": distinctive_token, "expires_in": 3600})
    )
    respx.get(f"{BASE_URL}/api/Companies").mock(
        return_value=Response(200, json=[{"Id": "C1", "Name": "Acme", "StorageLimit": 1, "LicenseSettings": 0}])
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-token-leak", password="SuperSecretPassword123")
    try:
        with caplog.at_level("DEBUG"):
            result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    response_text = _result_text(result)
    assert distinctive_token not in response_text
    assert "SuperSecretPassword123" not in response_text
    for record in caplog.records:
        assert distinctive_token not in record.getMessage()
        assert "SuperSecretPassword123" not in record.getMessage()


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_default_limit_is_50():
    """TC7a (AC7)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-limit50", "expires_in": 3600})
    )
    records = [{"Id": f"C{i}", "Name": f"Company {i}", "StorageLimit": i, "LicenseSettings": 0} for i in range(60)]
    respx.get(f"{BASE_URL}/api/Companies").mock(return_value=Response(200, json=records))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-limit50")
    try:
        result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["companies"]) == 50
    assert body["has_more"] is True


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_limit_above_count_returns_all():
    """TC7b (AC7)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-limit200", "expires_in": 3600})
    )
    records = [{"Id": f"C{i}", "Name": f"Company {i}", "StorageLimit": i, "LicenseSettings": 0} for i in range(60)]
    respx.get(f"{BASE_URL}/api/Companies").mock(return_value=Response(200, json=records))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-limit200")
    try:
        result = await mcp.call_tool("msp360_list_companies", {"limit": 200})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["companies"]) == 60
    assert body["has_more"] is False


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_hard_cap_wins_over_requested_limit():
    """TC7c (AC7)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-hardcap", "expires_in": 3600})
    )
    records = [{"Id": f"C{i}", "Name": f"Company {i}", "StorageLimit": i, "LicenseSettings": 0} for i in range(250)]
    respx.get(f"{BASE_URL}/api/Companies").mock(return_value=Response(200, json=records))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-hardcap")
    try:
        result = await mcp.call_tool("msp360_list_companies", {"limit": 500})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["companies"]) <= 200
    assert body["has_more"] is True


@pytest.mark.asyncio
@respx.mock
async def test_list_companies_upstream_error_on_5xx_or_timeout():
    """TC8 (AC8)."""
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": "tok-companies-5xx", "expires_in": 3600})
    )
    route = respx.get(f"{BASE_URL}/api/Companies").mock(
        side_effect=[
            Response(500, headers={"Retry-After": "0"}, json={"message": "internal error"}),
            Response(500, headers={"Retry-After": "0"}, json={"message": "internal error"}),
            Response(500, headers={"Retry-After": "0"}, json={"message": "internal error"}),
            Response(500, headers={"Retry-After": "0"}, json={"message": "internal error"}),
        ]
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-companies-5xx")
    try:
        result = await mcp.call_tool("msp360_list_companies", {})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "upstream_error"
    assert envelope["error"]["retryable"] is True
    assert route.call_count == 4


# -- msp360_list_user_computers (PRD-21607) --------------------------------
#
# Joins GET /api/Users/{userid}/Computers (inventory: name + policy id only)
# with GET /api/Monitoring/{userId} (per-plan status) by ComputerName. See
# docs/tickets/PRD-21607.md "Tech design" for why neither vendor endpoint
# alone satisfies the ticket.


def _computers_route(user_id: str = "U1"):
    return respx.get(f"{BASE_URL}/api/Users/{user_id}/Computers")


def _monitoring_route(user_id: str = "U1"):
    return respx.get(f"{BASE_URL}/api/Monitoring/{user_id}")


def _mock_login(token: str = "tok-uc"):
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": token, "expires_in": 3600})
    )


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_joins_inventory_and_monitoring():
    _mock_login()
    _computers_route().mock(
        return_value=Response(
            200,
            json=[
                {"DestinationId": "D1", "ComputerName": "PC1"},
                {"DestinationId": "D2", "ComputerName": "PC2"},
            ],
        )
    )
    _monitoring_route().mock(
        return_value=Response(
            200,
            json=[
                {
                    "ComputerName": "PC1",
                    "PlanId": "P1",
                    "PlanName": "Daily",
                    "Status": 0,
                    "LastStart": "2026-11-01T00:00:00Z",
                    "ErrorMessage": None,
                },
                {
                    "ComputerName": "PC2",
                    "PlanId": "P2",
                    "PlanName": "Weekly",
                    "Status": 0,
                    "LastStart": "2026-11-02T00:00:00Z",
                    "ErrorMessage": None,
                },
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-join")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["count"] == 2
    assert body["has_more"] is False
    for entry in body["computers"]:
        assert set(entry) == {"destination_id", "computer_name", "status", "last_backup_date", "plans"}
        assert entry["plans"]
        for plan in entry["plans"]:
            assert set(plan) == {"plan_id", "plan_name", "status", "last_start", "error_message"}


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_empty_result_is_not_an_error():
    _mock_login()
    _computers_route().mock(return_value=Response(200, json=[]))
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-empty")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body == {"computers": [], "count": 0, "has_more": False}


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_no_monitoring_data_is_no_data_status():
    _mock_login()
    _computers_route().mock(
        return_value=Response(200, json=[{"DestinationId": "D1", "ComputerName": "NEW-PC"}])
    )
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-nodata")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert body["count"] == 1
    entry = body["computers"][0]
    assert entry["status"] == "no_data"
    assert entry["last_backup_date"] is None
    assert entry["plans"] == []


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_aggregates_most_severe_status_and_latest_backup_date():
    _mock_login()
    _computers_route().mock(
        return_value=Response(200, json=[{"DestinationId": "D1", "ComputerName": "PC1"}])
    )
    _monitoring_route().mock(
        return_value=Response(
            200,
            json=[
                {
                    "ComputerName": "PC1",
                    "PlanId": "P1",
                    "PlanName": "Daily",
                    "Status": 0,  # Success
                    "LastStart": "2026-11-01T00:00:00Z",
                    "ErrorMessage": None,
                },
                {
                    "ComputerName": "PC1",
                    "PlanId": "P2",
                    "PlanName": "Weekly",
                    "Status": 2,  # Error
                    "LastStart": "2026-11-03T00:00:00Z",
                    "ErrorMessage": "disk full",
                },
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-severity")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    entry = body["computers"][0]
    assert entry["status"] == "error"
    assert entry["last_backup_date"] == "2026-11-03T00:00:00Z"


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_maps_all_status_codes_to_labels():
    _mock_login()
    labels = [
        "success",
        "overdue",
        "error",
        "running",
        "unknown",
        "interrupted",
        "unexpectedly_closed",
        "warning",
    ]
    _computers_route().mock(
        return_value=Response(
            200,
            json=[{"DestinationId": f"D{i}", "ComputerName": f"PC{i}"} for i in range(8)],
        )
    )
    _monitoring_route().mock(
        return_value=Response(
            200,
            json=[
                {
                    "ComputerName": f"PC{i}",
                    "PlanId": f"P{i}",
                    "PlanName": "Daily",
                    "Status": i,
                    "LastStart": "2026-11-01T00:00:00Z",
                    "ErrorMessage": None,
                }
                for i in range(8)
            ],
        )
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-labels")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    by_name = {c["computer_name"]: c for c in body["computers"]}
    for i, label in enumerate(labels):
        assert by_name[f"PC{i}"]["status"] == label
        assert by_name[f"PC{i}"]["plans"][0]["status"] == label


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_login_rejected_returns_unauthorized_without_calling_vendor():
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(401, json={"message": "invalid username or password"})
    )
    computers_route = _computers_route()
    monitoring_route = _monitoring_route()
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-badcreds")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "unauthorized"
    assert envelope["error"]["retryable"] is False
    assert not computers_route.called
    assert not monitoring_route.called


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_isolates_concurrent_tenant_results():
    """TC8 (AC8): two tenants, two mocked backends behind the same base URL,
    driven by concurrent asyncio Tasks — same technique as
    test_concurrent_requests_with_different_credentials_never_cross_talk.
    """

    def login_side_effect(request):
        body = json.loads(request.content)
        token = "tokA" if body["UserName"] == "accountA-uc" else "tokB"
        return Response(200, json={"access_token": token, "expires_in": 3600})

    def computers_side_effect(request):
        auth = request.headers["Authorization"]
        name = "ALICE-PC" if auth == "Bearer tokA" else "BOB-PC"
        return Response(200, json=[{"DestinationId": "D1", "ComputerName": name}])

    def monitoring_side_effect(request):
        auth = request.headers["Authorization"]
        name = "ALICE-PC" if auth == "Bearer tokA" else "BOB-PC"
        return Response(
            200,
            json=[
                {
                    "ComputerName": name,
                    "PlanId": "P1",
                    "PlanName": "Daily",
                    "Status": 0,
                    "LastStart": "2026-11-01T00:00:00Z",
                }
            ],
        )

    respx.post(f"{BASE_URL}/api/Provider/Login").mock(side_effect=login_side_effect)
    _computers_route().mock(side_effect=computers_side_effect)
    _monitoring_route().mock(side_effect=monitoring_side_effect)

    mcp = create_mcp_server(Settings())

    async def call_as(username: str) -> dict:
        token = _set_creds(username, password="pw-" + username)
        try:
            result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
        finally:
            _credentials_var.reset(token)
        return json.loads(_result_text(result))

    result_a, result_b = await asyncio.gather(call_as("accountA-uc"), call_as("accountB-uc"))

    assert result_a["computers"][0]["computer_name"] == "ALICE-PC"
    assert result_b["computers"][0]["computer_name"] == "BOB-PC"

    fp_a = _fingerprint("accountA-uc", "pw-accountA-uc")
    fp_b = _fingerprint("accountB-uc", "pw-accountB-uc")
    assert _token_cache.get(fp_a) == "tokA"
    assert _token_cache.get(fp_b) == "tokB"


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_never_exposes_raw_token_or_password(caplog):
    secret_token = "sekret-access-token-value-xyz"
    secret_password = "my-s3cret-password"
    respx.post(f"{BASE_URL}/api/Provider/Login").mock(
        return_value=Response(200, json={"access_token": secret_token, "expires_in": 3600})
    )
    _computers_route().mock(
        return_value=Response(200, json=[{"DestinationId": "D1", "ComputerName": "PC1"}])
    )
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-nosecret", password=secret_password)
    try:
        with caplog.at_level("DEBUG"):
            result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    text = _result_text(result)
    assert secret_token not in text
    assert secret_password not in text
    log_text = caplog.text
    assert secret_token not in log_text
    assert secret_password not in log_text


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_default_limit_caps_at_50():
    _mock_login()
    _computers_route().mock(
        return_value=Response(
            200, json=[{"DestinationId": f"D{i}", "ComputerName": f"PC{i}"} for i in range(60)]
        )
    )
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-cap50")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["computers"]) == 50
    assert body["has_more"] is True


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_limit_above_actual_count_returns_all():
    _mock_login()
    _computers_route().mock(
        return_value=Response(
            200, json=[{"DestinationId": f"D{i}", "ComputerName": f"PC{i}"} for i in range(60)]
        )
    )
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-cap200")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1", "limit": 200})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["computers"]) == 60
    assert body["has_more"] is False


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_hard_cap_wins_over_requested_limit():
    _mock_login()
    _computers_route().mock(
        return_value=Response(
            200, json=[{"DestinationId": f"D{i}", "ComputerName": f"PC{i}"} for i in range(60)]
        )
    )
    _monitoring_route().mock(return_value=Response(200, json=[]))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-hardcap")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1", "limit": 500})
    finally:
        _credentials_var.reset(token)
    body = json.loads(_result_text(result))
    assert len(body["computers"]) <= 200


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_monitoring_upstream_error_maps_to_upstream_error():
    _mock_login()
    _computers_route().mock(
        return_value=Response(200, json=[{"DestinationId": "D1", "ComputerName": "PC1"}])
    )
    _monitoring_route().mock(return_value=Response(503, json={"message": "backend unavailable"}))
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-upstream")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "U1"})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "upstream_error"
    assert envelope["error"]["retryable"] is True


@pytest.mark.asyncio
@respx.mock
async def test_list_user_computers_unrecognized_user_id_maps_to_not_found():
    _mock_login()
    _computers_route(user_id="missing").mock(
        return_value=Response(404, json={"message": "user not found"})
    )
    mcp = create_mcp_server(Settings())
    token = _set_creds("user-uc-notfound")
    try:
        result = await mcp.call_tool("msp360_list_user_computers", {"user_id": "missing"})
    finally:
        _credentials_var.reset(token)
    envelope = json.loads(_result_text(result))
    assert envelope["error"]["code"] == "not_found"
    assert envelope["error"]["retryable"] is False
