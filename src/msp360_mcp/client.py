# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Thin async client for MSP360's Managed Backup Service (MBS) REST API.

Reference: the public method index at
https://help.mspbackups.com/mbs-api-specification/managed-backup-api/ confirms the base URL and
the `POST API/Provider/Login` auth contract (matches PRD-21604's description field-for-field). The
`/Plans/{id}/info`, `/Plans/{id}/history`, `/Plans/{id}/start`, and `/Plans/{id}/stop` sub-resource
paths are corroborated by a third-party reference implementation
(github.com/WYRE-AI/msp360-mcp, inspected read-only, not copied — see docs/tickets/PRD-21273.md
"Current state" and "Open questions" OQ1) rather than directly readable vendor text. If a live
account later shows a different path, only this module's path strings change — the tool-facing
contract in tools/*.py does not (see docs/tickets/PRD-21273.md "Decisions made by DEV01").
`GET /api/Companies` (used by `list_companies()`, PRD-21605) is confirmed directly from the live
vendor Swagger document at api.mspbackups.com/help/v2/swagger — see docs/tickets/PRD-21605.md
"Current state".

Credential handling: MSP360 has no API-key auth, only a username/password login that exchanges for
a short-lived bearer token (`POST /api/Provider/Login` -> {access_token, expires_in}). Per SOP
section 3.4, a derived token MAY be cached across requests as long as the cache key includes a
credential fingerprint and the entry has an expiry — this module does exactly that: the cache key
is a SHA-256 fingerprint of the credential pair (never the raw password), and every entry carries
the expiry MSP360 itself reported.
"""

import asyncio
import hashlib
import time
from typing import Any

import httpx

BASE_URL = "https://api.mspbackups.com"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

# Refresh this many seconds before the vendor-reported expiry, so an
# in-flight request never races a token that is about to lapse.
_EXPIRY_SAFETY_MARGIN_SECONDS = 30.0

# One shared connection pool for the process lifetime (SOP section 5: reuse a
# client, don't open one per call). It holds no credentials of its own.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


class Msp360ApiError(Exception):
    """Raised for any non-2xx response or network failure.

    Tools catch this and map it to the structured error envelope (see
    errors.py) — it never propagates out of a tool handler.
    """

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"MSP360 API error {status_code}: {message}")


def _fingerprint(username: str, password: str) -> str:
    # Never use the raw (username, password) pair as a cache key or log it —
    # only this opaque fingerprint identifies a cache entry (SOP section 6).
    return hashlib.sha256(f"{username}:{password}".encode("utf-8")).hexdigest()


class _TokenCache:
    """Per-credential-fingerprint bearer token cache with expiry (SOP section 3.4).

    Keyed by a SHA-256 fingerprint of (username, password), never by the
    credentials themselves. A per-fingerprint lock serializes logins so two
    concurrent requests for the same tenant don't both call
    POST /api/Provider/Login at once.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, fingerprint: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(fingerprint)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[fingerprint] = lock
            return lock

    def get(self, fingerprint: str) -> str | None:
        entry = self._tokens.get(fingerprint)
        if entry is None:
            return None
        token, expires_at = entry
        if time.monotonic() >= expires_at:
            return None
        return token

    def set(self, fingerprint: str, token: str, expires_in: float) -> None:
        expires_at = time.monotonic() + max(expires_in - _EXPIRY_SAFETY_MARGIN_SECONDS, 0.0)
        self._tokens[fingerprint] = (token, expires_at)

    def invalidate(self, fingerprint: str) -> None:
        self._tokens.pop(fingerprint, None)


_token_cache = _TokenCache()


class Msp360Client:
    """Wraps one tenant's MSP360 provider-account credentials against the MBS REST API."""

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._fingerprint = _fingerprint(username, password)

    async def _call_login_endpoint(self) -> tuple[str, int | None]:
        """POST /api/Provider/Login and parse the response.

        Shared by `_login` (which also populates the token cache) and
        `verify_login` (PRD-21604's connectivity check, which does not touch
        the cache). Raises Msp360ApiError on any non-2xx response, a network
        failure/timeout (status 0, same convention as `_request`), or a
        missing `access_token`. `expires_in` is None when the vendor
        response omits the field (see docs/tickets/PRD-21604.md OQ1).
        """
        client = _get_http_client()
        try:
            resp = await client.post(
                f"{BASE_URL}/api/Provider/Login",
                json={"UserName": self._username, "Password": self._password},
            )
        except httpx.RequestError as exc:
            raise Msp360ApiError(0, f"Could not reach MSP360: {exc or type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise Msp360ApiError(resp.status_code, self._extract_message(resp))
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise Msp360ApiError(resp.status_code, "MSP360 login response had no access_token")
        expires_in = body.get("expires_in")
        return token, int(expires_in) if isinstance(expires_in, (int, float)) else None

    async def _login(self) -> str:
        token, expires_in = await self._call_login_endpoint()
        _token_cache.set(self._fingerprint, token, float(expires_in or 0))
        return token

    async def verify_login(self) -> int | None:
        """Perform a fresh MSP360 login purely to confirm the credentials work.

        Used by msp360_provider_login (PRD-21604), a connectivity check: it
        always calls the vendor (never reads the shared token cache) and
        returns only `expires_in` — the raw token stays local to this call
        and is discarded immediately after, never cached, logged, or
        returned to the caller (AC1, AC5).
        """
        _, expires_in = await self._call_login_endpoint()
        return expires_in

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = _token_cache.get(self._fingerprint)
            if cached:
                return cached
        lock = await _token_cache._lock_for(self._fingerprint)
        async with lock:
            # Another task may have refreshed it while we were waiting on the lock.
            if not force_refresh:
                cached = _token_cache.get(self._fingerprint)
                if cached:
                    return cached
            return await self._login()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json_body: Any = None) -> Any:
        return await self._request("POST", path, json_body=json_body)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        client = _get_http_client()
        url = f"{BASE_URL}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        token = await self._get_token()
        relogin_attempted = False

        for attempt in range(_MAX_RETRIES + 1):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            try:
                resp = await client.request(
                    method, url, headers=headers, params=clean_params, json=json_body
                )
            except httpx.RequestError as exc:
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise Msp360ApiError(0, f"Could not reach MSP360: {exc or type(exc).__name__}") from exc

            # Login endpoint itself returning 401/403 is a credentials problem,
            # not a lapsed token — never re-login-and-retry on the login call.
            if resp.status_code in (401, 403) and not relogin_attempted:
                relogin_attempted = True
                _token_cache.invalidate(self._fingerprint)
                token = await self._get_token(force_refresh=True)
                continue

            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue

            return self._handle(resp)

        # Unreachable: the loop above always returns or raises. Kept so type
        # checkers see every path return, and as a guard against future edits.
        raise Msp360ApiError(0, "request failed with no response")

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.status_code == 204:
            return {}
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            raise Msp360ApiError(resp.status_code, self._extract_message(resp, body))
        return body if body is not None else {}

    def _extract_message(self, resp: httpx.Response, body: Any = None) -> str:
        if body is None:
            try:
                body = resp.json()
            except ValueError:
                body = None
        if isinstance(body, dict):
            for key in ("message", "error", "Message", "ErrorMessage"):
                value = body.get(key)
                if isinstance(value, str) and value:
                    return value
        text = resp.text.strip()[:300]
        return text or f"MSP360 returned {resp.status_code} with no body"

    # -- Vendor calls -----------------------------------------------------

    async def list_plans(self, hid: str) -> Any:
        return await self.get(f"/api/Computers/{hid}/Plans")

    async def get_plan_info(self, hid: str, plan_id: str) -> Any:
        return await self.get(f"/api/Computers/{hid}/Plans/{plan_id}/info")

    async def get_plan_history(
        self, hid: str, plan_id: str, year: int | None, month: int | None, day: int | None
    ) -> Any:
        return await self.get(
            f"/api/Computers/{hid}/Plans/{plan_id}/history",
            params={"date.year": year, "date.month": month, "date.day": day},
        )

    async def start_plan(self, hid: str, plan_id: str, mode: str | None) -> Any:
        body = {"Mode": mode} if mode is not None else {}
        return await self.post(f"/api/Computers/{hid}/Plans/{plan_id}/start", json_body=body)

    async def stop_plan(self, hid: str, plan_id: str, force: bool | None) -> Any:
        body = {"Force": force} if force is not None else {}
        return await self.post(f"/api/Computers/{hid}/Plans/{plan_id}/stop", json_body=body)

    async def get_monitoring(self, user_id: str | None) -> Any:
        path = f"/api/Monitoring/{user_id}" if user_id else "/api/Monitoring"
        return await self.get(path)

    async def list_domains(self) -> Any:
        # No trailing slash: the live Swagger renders the path this way (the
        # ticket's own text has a trailing slash; OQ2 treats these as the same
        # endpoint and the machine-readable Swagger form as authoritative).
        return await self.get("/api/apps/domains")

    async def list_companies(self) -> Any:
        return await self.get("/api/Companies")
