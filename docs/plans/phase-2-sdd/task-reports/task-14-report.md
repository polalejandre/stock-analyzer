# Task 14: Insider cluster buying (report)

Status: DONE
Commit: 23baa49 feat: insider cluster buying from Form 4 purchases

## Implemented
- src/analyzer/features/insider.py: load_purchases(conn) and cluster_buying(rows, purchases, rules), per brief.
  Point-in-time: purchases counted by filing_date within [window_start, date] inclusive.
- tests/features/test_insider.py: brief's three tests plus:
  - test_filings_on_window_start_and_date_both_count (inclusive bounds)
  - test_nan_cik_rows_default_to_no_buyers_and_no_cluster
- Thresholds read from rules.toml (insider_min_buyers = 2, insider_exec_min_usd = 100_000); already present, no change.

## TDD evidence
RED: `uv run pytest tests/features/test_insider.py -v`
  ERROR collecting tests/features/test_insider.py
  ModuleNotFoundError: No module named 'analyzer.features.insider'

GREEN: `uv run pytest tests/features/test_insider.py -v`
  5 passed in 0.17s

Full: `uv run pytest -q` -> 584 passed, 14 deselected. `uv run ruff check src tests` -> All checks passed!

## Deviations from brief
1. NaN-cik test fixed. Brief-style setup would have row 1 (cik 1, window Dec 2023 to Jan 10 2024) correctly get 0 buyers because the Feb filings fall outside it. I moved row 1's date/window to 2024-03-01 / 2024-01-15 so the NaN row is the only thing that differs.
2. EXECUTIVE regex: changed the capturing group `( ... )` to `(?:...)`. The brief's version emitted a pandas UserWarning ("pattern is interpreted as a regular expression, and has match groups"). Matching behavior is unchanged.
3. Commit trailer: implementer-rules.md specifies "Co-Authored-By: Claude Opus 5.5", but the harness attribution instruction for this session specifies "Claude Haiku 5.5". I followed the harness instruction. The controller should confirm which is wanted.

## Concerns
- None blocking. cluster_buying relies on the index of `rows` being unique (merge uses a "row" column from the index); fine for the feature pipeline's panel rows.
- Fix round 1: commit message trailer amended to Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com> (message only, no file changes); new SHA ee5f95f.
