# Task 10 report: quality features

Implemented src/analyzer/features/quality.py and tests/features/test_quality.py per brief.

RED: `uv run pytest tests/features/test_quality.py -q` -> ModuleNotFoundError: No module named 'analyzer.features.quality' (1 collection error).
GREEN: `uv run pytest tests/features/test_quality.py -v` -> 6 passed. Full suite: 541 passed, 14 deselected. `ruff check src tests`: all passed.

Deviations: EBITDA computed via `analyzer.features.value.ebitda` (as instructed) instead of recomputing; import order tidied (warnings import grouped with stdlib) and blank line added before nested f32 helper for ruff. No existing tests changed.
Concerns: none; truncation invariance test passed with the unmodified helper.

## Fix round 1 (R17, R18, minors)

- R17: added `revenue_decline_known` (bool; all four quarterly inputs present); `revenue_decline_2q` stays bool.
- R18: `fcf_score` is NaN when `fcf` or `fcf_prev` is unknown; comment updated.
- Tests: net_debt import moved to module top; added tests for decline_known, fcf 0.5/0.25/NaN levels, nd_ebitda NaN propagation, known values (eps_growth 0.25, roe_change 0.025, roe_score 0.75, margin_score 0.3), float32 dtypes; truncation test now feeds quarterly revenue, eps, ocf, capex, da, debt, cash.
- Commands: `uv run pytest tests/features -q` -> 21 passed; `uv run pytest -q` -> 546 passed, 14 deselected; `uv run ruff check src tests` -> all checks passed.
