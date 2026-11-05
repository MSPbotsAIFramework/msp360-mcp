# PRD-21604 · Code review

Status: frozen @ a689905

## Round 1 · 2026-11-05 · reviewed dev/PRD-21604 a689905 (base main 8c3c28d)

Verdict: approved

### Findings

None.

### Notes (not findings, for the record)

- Re-ran `uv sync && uv run pytest -q` on this exact commit: **28 passed, 1 warning** (pre-existing
  `pytest-asyncio` deprecation notice) — matches the Self-test section's claim.
- Diffed `README.md`'s `## MSPbots Connector` block (lines 5-75) byte-for-byte against
  `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md` — identical.
- Re-ran the Chinese-text scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`)
  — one hit, `docs/tickets/PRD-21273-review.md:56`, a pre-existing quote in a sibling ticket's own
  frozen review doc; not introduced by this ticket, not a file this ticket touches.
- Scope matches the Handoff section: only `README.md`, `docs/tickets/PRD-21604.{md,ac.json}`,
  `src/msp360_mcp/{client.py,server.py,tools/auth.py}`, `tests/test_tools.py` changed. No sibling
  ticket's files, `plans.py`, `monitoring.py`, `_common.py`, `middleware.py`, `settings.py`,
  `__main__.py`, `pyproject.toml`, or `Dockerfile` touched.
- `client.py`'s `_call_login_endpoint()` extraction: `_login()` (used by every other tool's token
  cache path) and the new `verify_login()` (PRD-21604) now share one HTTP call site. The
  `try/except httpx.RequestError` added around the login POST is a genuine bug fix (previously a
  network failure on login would raise unhandled instead of mapping to `upstream_error`) — correctly
  called out in Self-test/Handoff as scoped-but-justified, not an unrelated refactor.
  `verify_login()` discards the raw token in a local `_` binding; it is never cached, logged, or
  returned — matches AC1/AC5.
  `tools/auth.py`'s `msp360_provider_login` tool description: 2 lines, first line 68 chars, well
  under the SOP §14 limits (≤500 total, ≤100 first line); `msp360_` prefix present; `readOnlyHint=True`
  set; no parameters, matching the design's "zero parameters" contract.
- 8/8 ac.json cases map to real, passing tests (TC5 reuses the shared middleware test, as documented
  — correct, since the 401-on-missing-header path is identical for every tool in this repo and a
  per-tool duplicate would be redundant, not a coverage gap).
- No must-fix against `references/REVIEW.md` §2 (correctness, scope, reuse, security, tests, migration
  n/a) or the MCP-specific checklist in `references/mcp-track.md` §4.3.
