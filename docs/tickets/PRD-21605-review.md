# PRD-21605 · Code review

## Round 1 · 2026-11-05 · reviewed dev/PRD-21605 @ 783fd0e (base 8c3c28d)

Status: frozen @ 783fd0e

Verdict: approved

### Findings

No must-fix findings.

1. [suggestion] src/msp360_mcp/tools/companies.py:11 — `_as_items` duplicates the identical helper in `tools/monitoring.py` and a near-identical one in `tools/plans.py`. Pre-existing pattern in this repo (not introduced worse by this ticket); worth collapsing into a shared helper in `_common.py` in a future ticket, not a blocker here.
2. [suggestion] tests/test_middleware.py:47 — `test_missing_credential_header_returns_401`'s docstring says it covers "the same middleware path" as `test_mcp_without_credentials_returns_401_with_required_headers`, but it actually exercises a different combination (password-only vs. no headers) that was previously untested, complementing `test_mcp_with_only_username_returns_401`. The docstring undersells its own coverage; consider rewording, no functional issue.

### Verification performed

- `uv sync && uv run pytest -q` on `dev/PRD-21605 @ 783fd0e` (synced with `main @ 8c3c28d`, per Handoff): `30 passed, 1 warning in 1.09s` — confirms the dev leg's reported gate result independently.
- `grep -rnP '[\x{4e00}-\x{9fff}]'` over every file this ticket touched (`companies.py`, `client.py`, `server.py`, `README.md`, `test_tools.py`, `test_middleware.py`): no hits.
- Diff scope `8c3c28d..783fd0e` read in full: `README.md`, `docs/tickets/PRD-21605.{md,ac.json}`, `src/msp360_mcp/{client.py,server.py,tools/companies.py}`, `tests/{test_tools.py,test_middleware.py}`.
- Credential handling: `msp360_list_companies` goes through the existing `client_or_none()` / `Msp360Client` / token-cache path shared with the already-merged `monitoring`/`plans` tools — no new credential handling code, no header/token/password logged or returned (TC6 covers this, read and matches AC6).
- Tool contract matches tech design: `msp360_list_companies(limit?: int = 50, offset?: int = 0) -> {"companies": [...], "count": N, "has_more": bool}`, fields trimmed to `company_id`/`name`/`storage_limit_bytes`/`license_settings` exactly as specified.
- All 10 `.ac.json` test IDs map to real, present test functions; `tools/list` snapshot test updated to 7 tools including `msp360_list_companies` with `readOnlyHint: true`.
- README Overview/tool-table/Known-gaps updates match the shipped tool and openly list OQ1-OQ3 as unconfirmed assumptions, consistent with the design doc.
- No scope creep: only this ticket's tool and its direct dependencies (client.py's one new method, server.py's registration) are touched; no other MSP360 sibling ticket's files are touched.
