# Task 18 report
Implemented src/analyzer/signals/plans.py (brief code verbatim, no deviations) and tests/signals/test_plans.py (brief tests + test_valuation_target_per_metric_and_median: P/E-only row 50*15/20=37.5, EV/EBITDA row (8*200-300)/(1000/10)=13, all-NaN row -> NaN).
RED: `uv run pytest tests/signals/test_plans.py -q` -> ModuleNotFoundError: No module named 'analyzer.signals.plans' (collection error).
GREEN: same command -> 4 passed. Full suite: 607 passed, 14 deselected; ruff clean.
No existing tests changed. No concerns.

## Fix round 1
Changes: plans.py R/R is NaN where entry - stop <= 0; valuation_target masks per-metric targets to NaN (EV/EBITDA: ebitda<=0 or shares<=0; others: ratio<=0 or p50<=0).
Tests added: zero-risk stop, stop above entry, NaN ATR, value target beats high, MA below retrace, negative EBITDA -> P/E target only.
Commands: `uv run pytest tests/signals -q` -> 22 passed; `uv run pytest -q` -> 613 passed, 14 deselected; `uv run ruff check src tests` -> clean.
