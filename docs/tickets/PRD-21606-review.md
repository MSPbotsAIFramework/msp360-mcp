# PRD-21606 · Code review

Status: frozen @ e73e45d

## Round 1 · 2026-11-05 · reviewed dev/PRD-21606 @ 7893462 (base 8c3c28d)

Verdict: changes requested

### Findings

1. [must-fix] docs/tickets/PRD-21606.md:595-596 — The Self-test section's Chinese-character scan
   states "Nothing in this ticket's own changed files contains Chinese text," but the very next
   sentence in this same (new) file — "skipped per the gate's \"有就 跑\" (run it if present) rule"
   — contains Chinese characters. This violates the MCP-track rule that the repo must have no
   Chinese (`references/mcp-track.md` §4.3, and `legs/design.md` §2.4's "only write English"
   requirement for `docs/tickets/PRD-XXXXX.md`), and it contradicts the Self-test's own claim in the
   same paragraph. Fix: rephrase that clause in English (e.g. "skipped per the gate's run-if-present
   rule") and re-run the Chinese-character scan to confirm the only remaining hit is the
   already-documented pre-existing one in `docs/tickets/PRD-21273-review.md:56`.

2. [must-fix] tests/test_tools.py:919-938 (`test_list_users_hard_cap_wins_over_requested_limit`) —
   The mock vendor response only contains 60 users, so the assertions (`len(users) <= 200` and
   `== 60`) would pass identically whether or not the `MAX_LIMIT = 200` hard cap (`src/msp360_mcp/tools/_common.py:9,33`)
   is actually enforced — the test's own inline comment admits this: "fewer than the hard cap exist;
   hard cap just doesn't expand beyond actual count." AC7's "hard cap wins over the requested limit"
   claim (`docs/tickets/PRD-21606.ac.json` TC7c) is therefore not verified by automation, despite the
   Self-test section claiming "All 9 ACs have at least one automated, passing test." A future
   regression that removes or raises the 200 cap would not be caught by this suite. Fix: mock more
   than 200 users (e.g. 250) and assert the result returns exactly 200 with `has_more: true`.

Everything else reviewed clean: gates independently re-run (`uv run pytest -q` → 30 passed, matching
the claimed count); `_as_items` in `tools/users.py` duplicates the identical private helper already in
`tools/monitoring.py` (pre-existing convention from PRD-21273, `tools/plans.py` has its own
key-name variant) — flagged only as a note, not a finding, per `REVIEW.md` §3's explicit "residual
duplicate code in the module this ticket touches" WARN carve-out (it is a brand-new file following an
established per-module pattern, not a newly-introduced duplication of a shared component); README
"MSPbots Connector" section untouched by this diff and still matches the template; no Chinese found
anywhere else in the diff; credential handling, multi-tenant isolation, and error-envelope mapping
reuse the existing `api_client.py`/`_common.py`/`errors.py` infrastructure correctly with no new
duplication.

## Round 2 · 2026-11-05 · reviewed dev/PRD-21606 @ e73e45d (base main @ 82a5d54)

Verdict: approved

### Findings

None.

### Fixes

1. docs/tickets/PRD-21606.md:595-596 — rephrased in commit `30e3eda` to "skipped per the gate's
   run-if-present rule". Re-ran the Chinese-character scan
   (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) on `e73e45d`: the
   only remaining hits are meta-references that quote the original Chinese phrase to describe the
   bug being fixed (`docs/tickets/PRD-21606-review.md:11` — this review's own Round 1 finding text;
   `docs/tickets/PRD-21606.md:618,680` — the ticket doc's own account of the fix), plus the
   already-documented pre-existing hit in `docs/tickets/PRD-21273-review.md:56`. No live prose
   contains untranslated Chinese. Confirmed fixed.
2. tests/test_tools.py (`test_list_users_hard_cap_wins_over_requested_limit`) — commit `30e3eda`
   now mocks 250 upstream users (was 60) with `access_token: tok-users-250c`, and asserts
   `len(body["users"]) == 200` and `body["has_more"] is True`. Checked `cap_records`
   (`src/msp360_mcp/tools/_common.py:33`): `size = min(max(limit, 1), MAX_LIMIT)` with `limit=500`
   and `MAX_LIMIT=200` evaluates to 200, so this assertion only passes if the hard cap is actually
   enforced — it is no longer possible for this test to pass by coincidence. Confirmed fixed.

### Notes (not findings, for the record)

- Re-ran `uv sync && uv run pytest -q` on `dev/PRD-21606 @ e73e45d`: **71 passed, 1 warning**
  (pre-existing `pytest-asyncio` deprecation notice) — matches the Self-test section's claim of
  71/71. Targeted re-run of `-k list_users`: 10 passed.
- Scope since Round 1 (`7893462...e73e45d`): the only commit carrying code changes is `30e3eda`
  (the two fixes above). `06049f6` is a merge that pulls in sibling tickets' (PRD-21604, PRD-21605,
  PRD-21607, PRD-21609) already-reviewed tool additions via `main` — additive only, touches no file
  this ticket owns (`users.py`, and the shared `client.py`/`server.py`/`README.md` edits are each a
  clean addition, not a rewrite of this ticket's lines). `3430135`, `46ddb80`, `b44706d`, `e73e45d`
  are docs-only (Self-test/Handoff update and header freeze in the main ticket doc).
- Diffed README's `## MSPbots Connector` section (lines 5-75) byte-for-byte against
  `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md` — identical.
- `pyproject.toml` / `uv.lock` unchanged since base (`main @ 82a5d54`) — no new dependency
  introduced.
- No schema/migration in this repo (MCP repo; not applicable).
- Credential handling, multi-tenant isolation (`test_list_users_isolates_concurrent_tenant_results`)
  and token/password non-exposure (`test_list_users_never_exposes_raw_token_or_password`) spot-read:
  correct, reuse existing `client.py`/`middleware.py` infrastructure, no new state shared across
  requests.
