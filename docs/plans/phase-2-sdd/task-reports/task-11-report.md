# Task 11 report
Implemented windows.py and dislocation.py per brief; tests per brief plus an extra chunk-spanning (70 columns, with a NaN gap) truncation-invariance test for since_max/reduce_since/days_since.
RED: `uv run pytest tests/features/test_windows.py tests/features/test_dislocation.py -q` -> 2 collection errors, ModuleNotFoundError: No module named 'analyzer.features.dislocation' (windows likewise).
GREEN: same command -> 10 passed. Full suite: 556 passed, 14 deselected; ruff clean.
Deviations (minor, behavior-preserving): reduce_since validates `how` up front (before any work, so n==0 also raises); wrapped the `age <= since` comparison in np.errstate(invalid="ignore"). Chunking (64 columns) kept.
Concern: reduce_since temporaries are ~n*64*window floats (~145MB at 4,500 dates, window 63) per chunk; acceptable but could use a smaller chunk if memory tight.
