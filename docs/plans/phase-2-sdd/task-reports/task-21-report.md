# Task 21 report
Implemented src/analyzer/research/events.py (brief code verbatim), empty __init__ files, tests/research/test_events.py (3 brief tests + select_events three-group/cooldown test + assign_period data-ends-before-oos_start test).
RED: `uv run pytest tests/research/test_events.py -q` -> ModuleNotFoundError: No module named 'analyzer.research.events' (collection error).
GREEN: 5 passed; full suite 633 passed; ruff clean.
Deviations: test file only - dropped unused numpy import and replaced the brief's lambda with a def (ruff F401/E731). No impl changes.
Concerns: none.
