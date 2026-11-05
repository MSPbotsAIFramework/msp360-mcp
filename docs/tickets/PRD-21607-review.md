# PRD-21607 · Code review

## Round 1 · 2026-11-05 · reviewed dev/PRD-21607 @ e47e651 (base 8c3c28d)

Status: frozen @ e47e651

Verdict: approved

### Findings

No must-fix findings.

1. [suggestion] src/msp360_mcp/tools/user_computers.py:145 — an empty `user_id` (`""`) is not rejected up front. The inventory call goes to `/api/Users//Computers` (most likely a vendor 404 → `not_found`), but if the vendor ever answered it with 200, the follow-up `client.get_monitoring("")` would take the falsy branch in `client.py:261` and fetch the **fleet-wide** `/api/Monitoring`, which the join would then filter by computer name. A `user_id.strip()` guard that returns `invalid_argument` would make this impossible. This is not a BA-visible failure today because the Agent sources `user_id` from `msp360_list_users`.
2. [suggestion] src/msp360_mcp/client.py:264 — `user_id` goes into the URL path without percent-encoding. The repo already does this for every path parameter (`hid`, `plan_id`, `user_id` in `get_monitoring`), so this ticket did not introduce the pattern or make it worse. A repo-wide `urllib.parse.quote(..., safe="")` belongs in a separate ticket.
3. [suggestion] src/msp360_mcp/tools/user_computers.py:124 — `last_backup_date` is `max()` over the raw `LastStart` strings. That is correct only while the vendor returns one uniform ISO-8601 format and offset, which the Swagger `date-time` type suggests but does not guarantee. Parsing before comparing would be more robust. TC13 (live account) would confirm the format.
4. [suggestion] src/msp360_mcp/tools/user_computers.py:65 — `_as_items` is the same helper already copied into `tools/monitoring.py` / `tools/plans.py` (this was also noted in the PRD-21605 review). Pre-existing duplication; collapse it into `_common.py` in a later ticket.

### Verification performed

- `uv run pytest -q` on `dev/PRD-21607 @ e47e651` (synced with `main @ 8c3c28d`, per Handoff): `34 passed, 1 warning in 10.31s`. This independently confirms the dev leg's reported gate result.
- Diff scope `8c3c28d..e47e651` read in full: `README.md`, `docs/tickets/PRD-21607.{md,ac.json}`, `src/msp360_mcp/{client.py,server.py,tools/user_computers.py}`, `tests/{test_tools.py,test_middleware.py}`. No files from other MSP360 sibling tickets are touched.
- Credential handling: the new tool uses the existing `client_or_none()` / `Msp360Client` / token-cache path. It adds no credential code, and nothing logs or returns the header, token, or password. The 401-before-tool guarantee comes from `CredentialMiddleware` (TC7 test added in `tests/test_middleware.py`). No per-request state is shared (TC9 concurrency test present).
- The tool contract matches the tech design: `msp360_list_user_computers(user_id, limit?, offset?) -> {"computers": [{destination_id, computer_name, status, last_backup_date, plans[]}], "count", "has_more"}`. The join is option C (inventory + `/api/Monitoring/{userId}` joined by `ComputerName`, no N+1). Status labels follow the full `MonitoringPlanStatus` enum (AC5). The computer-level status uses the OQ1 most-severe-first order. A computer with no monitoring record is kept with `no_data` (AC3).
- Errors are structured through the shared `to_envelope` path for both vendor calls (AC11/AC12 tests present). OQ2 (the vendor's real response to an unknown `user_id`) stays open and is recorded as TC13 `not run` (no vendor test account). Per `mcp-track.md` this is not a blocker.
- `grep -rlP '[\x{4e00}-\x{9fff}]'` over the tree finds only `docs/tickets/PRD-21273-review.md`. That file is pre-existing, belongs to a sibling ticket, and this ticket does not touch it.
- The README changes are limited to Overview and the tool table. The Connector section is not part of the diff.
