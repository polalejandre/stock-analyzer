# Task 12 report
Implemented technical.py verbatim from brief (Technical has no pairs field, as in brief) and the brief's tests.
RED: `uv run pytest tests/features/test_technical.py` -> ModuleNotFoundError: No module named 'analyzer.features.technical' (collection error).
GREEN: 8 passed; full suite 564 passed, ruff clean.
Deviation: the lambda `t` in test_daily_features_are_truncation_invariant rewritten as a def (ruff E731); same behavior.
Perf: technical() on 4500x1800 random-walk panel took ~1.4s.

## Fix round 1
- reclaimed_support: now anchored on the break (level = pairs.last_price at s; break when close_s < level_s; flag t if close_t > level_s for a break s in [t-bars, t-1]); loop over `bars` shifts, full-frame numpy ops. Tests: quick reclaim (True at bar 15), reclaim after window False, no break False; old test unchanged and passing.
- Weekly test: seed 1 (real divergences within 3 weeks of each cut, asserted); new test for partial final week: False on Wed 2018-06-06 and Fri 06-08, True Mon 06-11.
- confirmed_lows strict left / non-strict right (double-bottom test); RSI 50 when flat; RSI/ATR NaN where close NaN; `ma` added to daily truncation test.
- Commands: `uv run pytest tests/features -q` -> 44 passed; `uv run pytest -q` -> 569 passed; ruff clean; technical() on 4500x1800 ~ see below.
