# PRD-21606 · Code review

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
