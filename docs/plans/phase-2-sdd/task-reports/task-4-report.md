# Task 4 report: bank revenue and REIT property concepts

Status: DONE
Commit: d9896fa feat: bank revenue and REIT property concepts in the tag map

## Implemented
- `src/analyzer/data/edgar/tags.py`: added three concepts after `shares_outstanding`:
  - `net_interest_income` (duration, USD, `InterestIncomeExpenseNet`)
  - `noninterest_income` (duration, USD, `NoninterestIncome`)
  - `real_estate_property` (instant, USD, `RealEstateInvestmentPropertyNet`, `RealEstateInvestmentPropertyAtCost`)
- `tests/data/test_tags.py` (new): the brief's test, verbatim.
- `TAG_MAP_FINGERPRINT` changes automatically (it hashes `CONCEPTS`).

## TDD evidence
RED: `uv run pytest tests/data/test_tags.py -v`
```
>       assert concepts == {"net_interest_income", "noninterest_income", "real_estate_property"}
E       AssertionError: assert set() == {'net_interes...ate_property'}
FAILED tests/data/test_tags.py::test_bank_and_property_concepts_are_extracted
1 failed in 0.03s
```

GREEN: `uv run pytest -q` -> `470 passed, 14 deselected in 6.00s`
`uv run ruff check src tests` -> `All checks passed!`

## Existing tests updated
None. No existing test broke from the new concepts; no test pins the concept count or the fingerprint value (the fingerprint test in `tests/data/edgar/test_companyfacts.py` uses a monkeypatched threshold and compares against itself).
The 14 deselected tests are `live` (real external APIs, opt-in via `-m live`); they were not run and do not reference concept lists.

## Deviations
- Commit trailer: used `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` per the controller's implementer rules. The session attribution reminder asks for Haiku 5.5. Controller should confirm which trailer is wanted.

## Concerns
- None blocking. The brief's code needed no fixes.
