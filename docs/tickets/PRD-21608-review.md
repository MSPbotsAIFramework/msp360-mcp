# PRD-21608 · Code review

## Round 1 · 2026-11-06 · reviewed dev/PRD-21608 @ 164b51a (base 637748a)

Verdict: changes requested

### Findings
1. [must-fix] docs/tickets/PRD-21608.ac.json:148 — TC7b's `tests` pointer is `tests/test_tools.py::test_list_packages_limit_above_actual_count_returns_all`, but no such function exists. The real test (tests/test_tools.py:2039) is named `test_list_packages_limit_above_count_returns_all` (no "actual"). Confirmed by running `uv run pytest -q tests/test_tools.py::test_list_packages_limit_above_actual_count_returns_all` → `ERROR: not found ... no tests ran in 1.10s`; running `uv run pytest -q tests/test_tools.py::test_list_packages_limit_above_count_returns_all` → `1 passed in 0.42s`. The behavior itself is correct and tested, but the ledger entry for TC7b does not point at a collectible test, and `docs/tickets/PRD-21608.md`'s Test case matrix (line 540) repeats the same wrong name as "evidence". Fix: rename the ac.json `tests` entry (and the matrix row) to `test_list_packages_limit_above_count_returns_all` so the ledger accurately reflects the code.

### Fixes round 1
1. Renamed the TC7b `tests` pointer in `docs/tickets/PRD-21608.ac.json` from `test_list_packages_limit_above_actual_count_returns_all` to `test_list_packages_limit_above_count_returns_all` (now collectible, matches tests/test_tools.py:2039). Mirrored the same rename in `docs/tickets/PRD-21608.md`'s Test case matrix, TC7b row (was line 540) — no other content in that row changed. No production code or test behavior changed. Commit: see `[DEV]` stub for sha.
   - Re-ran full gate suite on the fix commit: `uv sync` clean; `uv run pytest -q` → `81 passed, 1 warning in 24.22s`; targeted re-check `uv run pytest -q tests/test_tools.py::test_list_packages_limit_above_count_returns_all` → `1 passed in 0.40s`; `python scripts/ticket_store.py validate --ac docs/tickets/PRD-21608.ac.json` → `ledger OK: 11 cases`.
   - Chinese-char scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) still reports hits, all pre-existing and in sibling tickets' review docs (`docs/tickets/PRD-21273-review.md`, `docs/tickets/PRD-21606-review.md`, `docs/tickets/PRD-21606.md`) quoting Chinese skill-rule text — unrelated to PRD-21608 and out of this must-fix's scope, left untouched.
   - `gitleaks` not installed on this host; skipped per the gate's "run it if present" rule (same as round 1).
