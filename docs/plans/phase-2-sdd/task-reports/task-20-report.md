# Task 20 report
Implemented evaluate.py, tests/fixture_db.py, tests/signals/test_evaluate.py from the brief.
RED: `uv run pytest tests/signals/test_evaluate.py` -> ModuleNotFoundError: No module named 'analyzer.signals.evaluate' (captured by temporarily moving the module aside after writing it; fixture/tests existed).
GREEN: 4 passed; full suite 623 passed, ruff clean.
Adaptations: only `regime(p.macro, rules.regime)`. All other real APIs (columns_for, quality_features, flow, technical without pairs) already matched the brief code.
Fixture: no changes needed; DIP is the only setup (57 setup rows). Truncation-invariance passes with assert_frame_equal unchanged (no float32 noise).
Timing: load_panels 2.97s, evaluate 0.10s. skipped = variant 0, history 6372, required 0, market_cap 0.

## Fix round 1
1. R24: `flow(...)` now receives `p.member`; one-line comments at the `value_features` (peers: p.member) and `flow` call sites. since_high.fillna(0) replaced by an assertion that since_high is not NaN on rows.
2. Fixture (tests/fixture_db.py): 15 stocks (12 + EXP, SHK, TRAP, OUT; 14 priced peers share SIC 3674). EXP: financials x0.4 -> gate1 False (cheaper=0). SHK: -12% day inside a ~19% drop -> gate4 False. TRAP: revenue peaks 2013Q4 then -8%/quarter -> gate3 False (also gate1 False; not asserted). OUT: see deviation. adj_close = close x (0.85+0.00012 t) for every symbol incl. SPY/XLK. One CEO purchase (5000 x 75 = 375k, filed 2015-09-21) for DIP. Docstring updated with known limits.
3. New tests: each gate blocks its stock; OUT in dislocated_all not rows; DIP n_metrics == 4; first setup row close at window_start == high and insider_cluster/buyers==1/executive_buy; truncation test asserts >= 2 symbols have rows on the cut dates and no longer uses check_dtype=False (frames match exactly).
Deviation: OUT cannot be "not an index member" -- a symbol with no index_membership row has no panel column, so it could never be in dislocated_all. OUT is an index member with no CIK/facts (variant excluded:no_cik), which gives the intended in-dislocated_all / not-in-rows behavior.
Result: DIP still the only setup (57 rows, first 2015-10-05); EXP 57 rows, SHK 49, TRAP 56, OUT 56 dislocated_all.
Commands: `uv run pytest tests/signals -v` tail: 36 passed in 4.39s. Full `uv run pytest -q` and `uv run ruff check src tests` tails are above in this session (see final lines below).
...................................................                      [100%]
627 passed, 14 deselected in 12.01s
All checks passed!

## Fix round 2 (R27)
- TRAP now has 30 shares (cheap) and revenue falls 2%/quarter after 2013Q4: gate1 True, gate4 True, gate3 False on every row.
- New stock RR: 18% drop, +-5% daily range (wide ATR), so no plan reaches R/R 1.5: gate1/3/4 True, gate5 False, no setup.
- New test test_trap_fails_only_gate3_and_rr_only_gate5. Existing assertions kept; DIP still the only setup.
- Mutation probes (scratch edit of evaluate.py, restored after each):
  drop gate3 from setup formula -> `4 failed, 5 passed` (test_each_gate_blocks_its_stock, test_first_setup_row_window_and_insider, truncation, and the new test).
  drop gate5 -> `2 failed, 7 passed` (test_only_the_dip_becomes_a_setup, test_trap_fails_only_gate3_and_rr_only_gate5).
- Item 3 (pin R24) skipped: not cheap. flow_min_industries = 5 needs five reference ETFs with prices and eligible members each; the fixture has one ETF.
- `uv run pytest tests/signals -v`: 37 passed. Full suite: 628 passed, 14 deselected. ruff: All checks passed.
