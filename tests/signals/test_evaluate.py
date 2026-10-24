import pandas as pd
import pytest

from analyzer.signals.evaluate import evaluate, load_panels
from tests.fixture_db import DIP, DROP_START, build_fixture_db, fixture_rules

RULES = fixture_rules()


@pytest.fixture(scope="module")
def panels(tmp_path_factory):
    conn = build_fixture_db(tmp_path_factory.mktemp("db") / "a.db")
    return load_panels(conn, RULES, end=pd.Timestamp("2016-12-30").date())


@pytest.fixture(scope="module")
def evaluation(panels):
    return evaluate(panels, RULES)


def test_only_the_dip_becomes_a_setup(evaluation):
    setups = evaluation.rows[evaluation.rows["setup"]]
    assert set(setups["symbol"]) == {DIP}
    first = setups["date"].min()
    assert DROP_START < first < DROP_START + pd.Timedelta(days=70)


def test_setup_rows_carry_reasons_inputs(evaluation):
    row = evaluation.rows[evaluation.rows["setup"]].iloc[0]
    assert row["variant"] == "standard" and row["gate1"] and row["gate5"]
    assert row["median_pct"] <= 30 and row["cheaper"] >= 2
    assert 0 <= row["score"] <= 100 and row["tier"] in {"A", "B", "C"}
    assert row["missing_rules"] == ""


def test_evaluate_is_truncation_invariant(panels, evaluation):
    first = evaluation.rows.loc[evaluation.rows["setup"], "date"].min()
    for d in (first, first + pd.Timedelta(days=14)):
        d = panels.dates[panels.dates.searchsorted(d)]
        part = evaluate(panels.upto(d), RULES).rows
        a = evaluation.rows[evaluation.rows["date"] == d].reset_index(drop=True)
        b = part[part["date"] == d].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False)


def test_skipped_counts_are_reported(evaluation):
    assert set(evaluation.skipped) == {"variant", "history", "required", "market_cap"}
    assert evaluation.skipped["history"] > 0          # 2012-2014: less than 3 years of filings
