import numpy as np
import pandas as pd

from analyzer.config import load_rules
from analyzer.features.insider import cluster_buying, load_purchases
from analyzer.store.db import connect, migrate

F = load_rules().features


def _purchases(rows):
    df = pd.DataFrame(rows, columns=["cik", "filing_date", "owner", "executive", "usd"])
    df["filing_date"] = pd.to_datetime(df["filing_date"])
    return df


ROWS = pd.DataFrame({"cik": [1.0, 1.0, 2.0],
                     "date": pd.to_datetime(["2024-03-01", "2024-01-10", "2024-03-01"]),
                     "window_start": pd.to_datetime(["2024-01-15", "2023-12-01", "2024-01-15"])})


def test_two_distinct_buyers_in_the_window_make_a_cluster():
    p = _purchases([(1, "2024-02-01", "Ann", False, 10_000), (1, "2024-02-05", "Bob", False, 10_000),
                    (1, "2024-03-05", "Cy", False, 10_000)])          # filed after the date
    out = cluster_buying(ROWS, p, F)
    assert out.loc[0, "buyers"] == 2 and out.loc[0, "cluster"]
    assert not out.loc[1, "cluster"] and out.loc[1, "buyers"] == 0
    assert not out.loc[2, "cluster"]


def test_one_large_executive_purchase_is_enough():
    p = _purchases([(2, "2024-02-01", "Dee", True, 150_000)])
    out = cluster_buying(ROWS, p, F)
    assert out.loc[2, "executive_buy"] and out.loc[2, "cluster"]


def test_filings_on_window_start_and_date_both_count():
    p = _purchases([(1, "2024-01-15", "Ann", False, 10_000), (1, "2024-03-01", "Bob", False, 10_000)])
    out = cluster_buying(ROWS, p, F)
    assert out.loc[0, "buyers"] == 2 and out.loc[0, "cluster"]


def test_nan_cik_rows_default_to_no_buyers_and_no_cluster():
    rows = ROWS.copy()
    rows.loc[0, "cik"] = np.nan
    rows.loc[1, "date"], rows.loc[1, "window_start"] = pd.Timestamp("2024-03-01"), pd.Timestamp("2024-01-15")
    p = _purchases([(1, "2024-02-01", "Ann", False, 10_000), (1, "2024-02-05", "Bob", False, 10_000)])
    out = cluster_buying(rows, p, F)
    assert out.loc[0, "buyers"] == 0 and not out.loc[0, "cluster"] and not out.loc[0, "executive_buy"]
    assert out.loc[1, "buyers"] == 2 and out.loc[1, "cluster"]


def test_load_purchases_marks_executives(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    conn.execute("INSERT INTO insider_transactions (accession, seq, issuer_cik, filing_date, owner_names, "
                 "relationship, officer_title, trans_date, trans_code, shares, price, acquired_disposed, "
                 "source) VALUES ('a', 1, 7, '2024-02-01', 'Doe Jane', 'Officer', 'Chief Executive Officer', "
                 "'2024-01-30', 'P', 1000, 120.0, 'A', 'bulk')")
    conn.execute("INSERT INTO insider_transactions (accession, seq, issuer_cik, filing_date, owner_names, "
                 "trans_code, shares, price, source) VALUES ('b', 1, 7, '2024-02-01', 'X', 'S', 1, 1, 'bulk')")
    p = load_purchases(conn)
    assert len(p) == 1 and p.iloc[0]["executive"] and p.iloc[0]["usd"] == 120_000
