import pandas as pd

from analyzer.research.coverage import coverage
from analyzer.signals.evaluate import evaluate, load_panels
from tests.fixture_db import STOCKS, build_fixture_db, fixture_rules


def test_coverage_on_the_fixture(tmp_path):
    conn = build_fixture_db(tmp_path / "a.db")
    conn.execute("INSERT INTO index_membership VALUES ('sp500', 'GONE', '2013-01-01', '2014-01-01')")
    conn.execute("INSERT INTO member_fates VALUES ('GONE', 'acquired', 'Acme acquired Gone')")
    rules = fixture_rules()
    panels = load_panels(conn, rules, end=pd.Timestamp("2016-12-30").date())
    c = coverage(conn, panels, evaluate(panels, rules))
    by_year = {r["year"]: r for r in c["members_by_year"]}
    n = len(STOCKS)
    assert by_year[2013]["members"] == n + 1 and by_year[2013]["with_prices"] == n
    assert by_year[2016]["share"] == 1.0
    assert c["excluded"] == {"acquired": 1}
    # OUT is an index member without a CIK, so its variant is excluded
    assert c["variants"] == {"standard": n - 1, "excluded:no_cik": 1}
    assert c["identity_unverified"] == n             # the fixture has no snapshot CIKs
    assert c["skipped_ticker_days"]["history"] > 0
