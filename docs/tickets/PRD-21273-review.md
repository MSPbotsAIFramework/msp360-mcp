# PRD-21273 · Code review

## Round 1 · 2026-11-04 · reviewed e454bd3 (base c4c9439)

Verdict: changes requested

### Findings

1. [must-fix] docs/tickets/PRD-21273.md:614-615 — The English-only design doc contains untranslated
   Chinese text (a quote of the pipeline skill's rule name, `判定什么是上游定的，怎么判是你定的`).
   MCP repos must be entirely English (`vendor-mcp-development` SOP §0.4; `references/mcp-track.md`
   §4.3's mandatory "no Chinese anywhere in the repo" check). Re-running the scan the Self-test
   section itself references confirms the hit: `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git
   --exclude-dir=.venv .` returns these two lines, which contradicts the Self-test section's own
   claim ("Chinese-text scan ... 0 hits — clean", PRD-21273.md:568). Fix: replace the quote with an
   English paraphrase or drop it, and re-run the scan before re-submitting.

2. [must-fix] docs/tickets/PRD-21273.ac.json — Several `tests` pointers name pytest functions that do
   not exist in `tests/test_tools.py`, so the evidence the ledger claims cannot actually be run or
   verified:
   - Line 7 (TC-MSP-001): `test_get_monitoring_returns_company_and_computer_fields` — not found.
   - Line 14 (TC-MSP-002): `test_get_monitoring_filters_by_user_id` — not found.
   - Line 21 (TC-MSP-003): `test_get_monitoring_caps_result_size_and_sets_has_more` — not found.
   - Line 28 (TC-MSP-004): `test_list_plans_returns_id_name_type` — not found.
   - Line 35 (TC-MSP-005): `test_get_plan_info_surfaces_error_status` — not found.
   - Line 42 (TC-MSP-006): `test_get_plan_history_returns_filtered_runs` — not found.
   - Lines 64-66 (TC-MSP-009): `test_get_plan_info_maps_404_to_not_found`,
     `test_start_plan_maps_404_to_not_found`, `test_stop_plan_maps_404_to_not_found` — none found.
   Confirmed by running each name directly: `uv run pytest tests/test_tools.py::<name> -q` returns
   "no tests ran" for all nine. The actual tests covering these cases exist under different names
   (correctly listed in PRD-21273.md's own "Test case matrix", e.g.
   `test_monitoring_filters_by_user_id_and_trims_fields`,
   `test_monitoring_caps_result_size_and_sets_has_more`,
   `test_login_then_vendor_call_returns_trimmed_plan_fields`,
   `test_plan_history_returns_filtered_runs`, `test_vendor_404_maps_to_not_found_envelope`,
   `test_not_found_404_on_start_and_stop_maps_cleanly`) — only `.ac.json`'s pointers are stale. The
   design doc's Self-test section (PRD-21273.md:612-615) states `.ac.json`'s `tests` field "was
   updated twice to point at the actual function names chosen during implementation (TC-MSP-010,
   TC-MSP-011)", which shows the renaming-drift failure mode was known, but the same fix was not
   applied to the other seven affected cases.

   Separately, TC-MSP-005 (AC4, line 31-36) is marked `auto`/pass for "a mocked plan-info response
   showing `Status: "Error"`" with `error_message` reflected — but no test in `tests/test_tools.py`
   asserts a `Status: "Error"` result from `msp360_get_plan_info` with a populated `error_message`.
   The only `get_plan_info` happy-path assertion is `test_stale_cached_token_relogs_in_once_then_succeeds`,
   which mocks `{"Status": "Idle", "ErrorMessage": None}` (plans.py:65-80 is otherwise correctly
   implemented — `error_message` is wired through — but the AC4/TC-MSP-005 "Error status surfaced"
   scenario specifically has no test backing it). This is a "claimed pass without supporting evidence"
   per `references/REVIEW.md` §3's test rule.

   Fix: correct the nine stale `tests` pointers to the real function names, and add (or correctly
   re-point) a test that actually exercises a `Status: "Error"` + non-null `error_message` response
   from `msp360_get_plan_info`, or mark TC-MSP-005 accurately if that scenario is intentionally
   covered only indirectly.

No other must-fix found. The error envelope, credential/multi-tenant isolation (`middleware.py`,
`client.py`'s fingerprint-keyed token cache), retry/backoff behavior, result capping
(`tools/_common.py:32-42`, `errors.py:58-93`), and the six tool contracts all match the tech design
and are exercised by passing tests (`uv run pytest -q` → 19 passed, reproduced independently). The
README Connector section is byte-for-byte identical to
`~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md` (diff confirmed
empty). No dependency, parameter-stacking, or scope issue found; this is a brand-new repo with
nothing to clean up, consistent with the design's own conclusion.

### Fixes (round 1)

1. **Chinese text in `docs/tickets/PRD-21273.md:614-615`** — replaced the Chinese quote of the
   pipeline skill's rule name with an English paraphrase of the same rule (what a test case
   asserts is fixed by the upstream design and cannot be changed here; which test function is
   used to check it is the implementer's choice). Re-ran the scan on the live deliverable surface
   (design doc, code, `.ac.json`, README — i.e. everything except this frozen review record, which
   necessarily quotes the original Chinese string as evidence of the finding):
   `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv --exclude=PRD-21273-review.md .`
   → 0 hits (exit 1). The only remaining hit in the repo is this review record's own quote of the
   defect at line 10 above, which is left as-is since it documents what the finding was, not a
   live instance of the problem.

2. **Nine stale `tests` pointers in `docs/tickets/PRD-21273.ac.json`** — corrected all nine to the
   real function names already used elsewhere in the repo (per the design doc's own Test case
   matrix): TC-MSP-001 → `test_monitoring_filters_by_user_id_and_trims_fields` +
   `test_login_then_vendor_call_returns_trimmed_plan_fields`; TC-MSP-002 →
   `test_monitoring_filters_by_user_id_and_trims_fields`; TC-MSP-003 →
   `test_monitoring_caps_result_size_and_sets_has_more`; TC-MSP-004 →
   `test_login_then_vendor_call_returns_trimmed_plan_fields`; TC-MSP-006 →
   `test_plan_history_returns_filtered_runs`; TC-MSP-009 →
   `test_not_found_404_on_start_and_stop_maps_cleanly` + `test_vendor_404_maps_to_not_found_envelope`.
   Verified every pointer in the corrected `.ac.json` resolves to a real test function
   (`tests/test_tools.py`), not just by name-matching but by running each one individually
   (`uv run pytest tests/test_tools.py::<name> -q`) — all pass.

   For **TC-MSP-005**, the missing coverage was real, not just a naming drift: no existing test
   asserted a `Status: "Error"` + populated `error_message` result from `msp360_get_plan_info`.
   Added `test_get_plan_info_surfaces_error_status` to `tests/test_tools.py`, mocking
   `GET /api/Computers/H1/Plans/P1/info` with `{"Status": "Error", "ErrorMessage": "backup failed:
   disk full", ...}` and asserting both `body["status"] == "Error"` and
   `body["error_message"] == "backup failed: disk full"`. TC-MSP-005's `tests` pointer in
   `.ac.json` now points at this new, real test. This is new test coverage, not a reinterpretation
   of what TC-MSP-005 asserts — the AC4 content is unchanged.

   `uv run pytest -q` after the fix: **20 passed** (19 previous + 1 new), 0 failed, same cosmetic
   deprecation warning as round 1.
