# PRD-21608 · Code review

## Round 1 · 2026-11-06 · reviewed dev/PRD-21608 @ 164b51a (base 637748a)

Verdict: changes requested

### Findings
1. [must-fix] docs/tickets/PRD-21608.ac.json:148 — TC7b's `tests` pointer is `tests/test_tools.py::test_list_packages_limit_above_actual_count_returns_all`, but no such function exists. The real test (tests/test_tools.py:2039) is named `test_list_packages_limit_above_count_returns_all` (no "actual"). Confirmed by running `uv run pytest -q tests/test_tools.py::test_list_packages_limit_above_actual_count_returns_all` → `ERROR: not found ... no tests ran in 1.10s`; running `uv run pytest -q tests/test_tools.py::test_list_packages_limit_above_count_returns_all` → `1 passed in 0.42s`. The behavior itself is correct and tested, but the ledger entry for TC7b does not point at a collectible test, and `docs/tickets/PRD-21608.md`'s Test case matrix (line 540) repeats the same wrong name as "evidence". Fix: rename the ac.json `tests` entry (and the matrix row) to `test_list_packages_limit_above_count_returns_all` so the ledger accurately reflects the code.
