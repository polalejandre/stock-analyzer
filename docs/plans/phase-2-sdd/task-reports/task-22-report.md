# Task 22 report
Implemented src/analyzer/research/outcomes.py (brief code verbatim) and tests/research/test_outcomes.py (brief tests + simulate_plans two-symbol test + adjusted-price test).
RED: `uv run pytest tests/research/test_outcomes.py -q` -> ModuleNotFoundError: No module named 'analyzer.research.outcomes' (collection error).
GREEN: same command, 9 passed; full suite 642 passed, 14 deselected; ruff clean.
Deviations: none to the brief code. My first draft of the adjusted-price test had an arithmetic error (wrong exit close); fixed in the test, not the code.

## Fix round 1 (R28)
(a) forward_returns: exit at last valid adjusted close at/before pos+h (must be >= entry row); benchmarks still run to pos+h; new bool `delisted_h` = stock has no valid close after that last valid one through the end of data AND that row is not the final data row (a series ending exactly on the last data day cannot be told from a live one); NaN if data ends before pos+h; MAE uses nanmin of valid lows inside warnings.catch_warnings. simulate_plan: "delisted" outcome (last valid close, days = lv-e+1) when series ends before the holding window does; "time" uses last valid close at/before the time-stop row; "thesis" with NaN next open exits at that day's close.
(b) thesis only fires when d < time-stop row, so the time stop wins on its own day (stop/target still checked first).
(c) assert stocks.dates.equals(etfs.dates).
(d) 6 new tests (open!=close/exact MAE/distinct ref ETF; delisted; gap; incomplete data; thesis on time-stop day; plan delisted).
(e) done.
Commands: `uv run pytest tests/research -v` -> 20 passed in 0.20s; full suite and ruff: see commit run (all passed, ruff clean).
