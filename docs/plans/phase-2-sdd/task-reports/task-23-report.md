# Task 23 report
Implemented src/analyzer/research/stats.py (brief code) and tests/research/test_stats.py (brief tests + empty-summary, is_win both bases, breakdowns two columns, verdict quintile failure).
RED: `uv run pytest tests/research/test_stats.py -q` -> ModuleNotFoundError: No module named 'analyzer.research.stats' (collection error).
GREEN: 8 passed; full suite 656 passed; ruff clean (after import-order autofix in the test).
Deviations (minimal): summarize guards empties with a `has` flag (no numpy warnings); verdict uses pd.Series(dtype=float) for empty quintile table and wraps n test in bool().
Concern: none.

## Fix round 1
1. quintiles: NaN scores excluded; ties broken by lexsort on (score, hash of symbol/date; date only if no symbol), ordinal positions then qcut. Test: shuffled rows give identical tables with heavy ties.
2. quintiles and breakdowns labels now from `g.size().index`.
3. Tests added: verdict NaN median/win_rate and empty quintile table; bootstrap whole-month integrity (stat is 1 only if every value appears in blocks of 10); NaN-score quintile exclusion (n sums to 40).
Commands: `uv run pytest tests/research/test_stats.py -v` -> 12 passed in 0.22s; `uv run pytest -q` -> 660 passed, 14 deselected; `uv run ruff check src tests` -> All checks passed!
