import numpy as np
import pandas as pd

from analyzer.research.coverage import coverage
from analyzer.signals.evaluate import evaluate, load_panels
from tests.fixture_db import STOCKS, build_fixture_db, fixture_rules


def test_coverage_on_the_fixture(tmp_path):
    conn = build_fixture_db(tmp_path / "a.db")
    conn.execute("INSERT INTO index_membership VALUES ('sp500', 'GONE', '2013-01-01', '2014-01-01')")
    conn.execute("INSERT INTO member_fates VALUES ('GONE', 'acquired', 'Acme acquired Gone')")
    conn.execute("INSERT INTO ticker_aliases VALUES ('OLD', 'DIP', NULL, 'manual')")
    rules = fixture_rules()
    panels = load_panels(conn, rules, end=pd.Timestamp("2016-12-30").date())
    ev = evaluate(panels, rules)
    c = coverage(conn, panels, ev)
    by_year = {r["year"]: r for r in c["members_by_year"]}
    n = len(STOCKS)
    assert by_year[2013]["members"] == n + 1 and by_year[2013]["with_prices"] == n
    assert by_year[2016]["share"] == 1.0
    assert c["excluded"] == {"acquired": 1}
    # OUT is an index member without a CIK, so its variant is excluded
    assert c["variants"] == {"standard": n - 1, "excluded:no_cik": 1}
    assert c["identity_unverified"] == n             # the fixture has no snapshot CIKs
    assert c["skipped_ticker_days"]["history"] > 0
    assert set(c["skipped_ticker_days"]) == {"variant", "history", "required", "market_cap"}
    assert c["skipped_ticker_days"]["variant"] > 0          # OUT is a member but excluded:no_cik
    assert c["skipped_ticker_days"] == ev.skipped

    # counted independently, cell by cell
    f = panels.fundamentals
    member, eligible = ev.member.to_numpy(), ev.eligible.to_numpy()
    diluted = f["diluted_shares"].to_numpy()
    annual, fallback = f["annual"].to_numpy(), f["fallback"].to_numpy()
    assert c["diluted_share_days"] == int(np.sum(member & diluted))
    assert c["annual_ttm_days"] == int(np.sum(eligible & annual))
    assert c["fallback_days"] == int(np.sum(eligible & fallback))
    # the fixture's cover-page share counts are fresh, so the diluted-share fallback is never
    # used; it has only quarterly facts (no annual rows) and standard tags (no fallback tags)
    assert c["diluted_share_days"] == 0
    assert c["annual_ttm_days"] == 0
    assert c["fallback_days"] == 0
    ref_fb = ev.ref_fallback.to_numpy()
    assert c["ref_fallback_days"] == {"member": int(np.sum(member & ref_fb)),
                                      "eligible": int(np.sum(eligible & ref_fb))}

    assert c["aliases"] == {"manual": 1}
    # GONE ends 2014-01-01: a member in 2013 only (end date exclusive)
    assert by_year[2014]["members"] == n
    assert not panels.entities["identity_verified"].isna().any()
