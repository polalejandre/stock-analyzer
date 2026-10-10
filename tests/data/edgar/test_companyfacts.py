from datetime import date

import pandas as pd
import pytest

from analyzer.data.edgar.companyfacts import extract_facts, fetch_companyfacts, store_facts
from analyzer.data.edgar.facts_read import latest_shares, load_concept
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client


def fact(start, end, val, filed, accn, form="10-Q"):
    d = {"end": end, "val": val, "filed": filed, "form": form, "accn": accn}
    if start:
        d["start"] = start
    return d


def usd(*facts):
    return {"units": {"USD": list(facts)}}


CF = {
    "cik": 1045810,
    "facts": {
        "us-gaap": {
            # NVDA-style tag switch: old periods on PPE tag, new periods on ProductiveAssets.
            "PaymentsToAcquirePropertyPlantAndEquipment": usd(
                fact("2022-01-31", "2022-05-01", 100, "2022-05-27", "a1"),
                fact("2022-05-02", "2022-07-31", 110, "2022-08-26", "a2"),
            ),
            "PaymentsToAcquireProductiveAssets": usd(
                fact("2022-05-02", "2022-07-31", 111, "2023-08-25", "b1"),
                fact("2023-05-01", "2023-07-30", 200, "2023-08-25", "b1"),
            ),
            "Revenues": usd(
                fact("2022-01-31", "2022-05-01", 1000, "2022-05-27", "a1"),
                fact("2022-01-31", "2022-05-01", 1050, "2023-03-01", "c1", form="10-K"),
                fact("2022-01-31", "2022-05-01", 9999, "2022-05-25", "e1", form="8-K"),
            ),
            "Assets": usd(
                fact(None, "2023-07-30", 5000, "2023-08-25", "b1"),
                fact("2023-05-01", "2023-07-30", 1, "2023-08-25", "b1"),  # wrong kind
            ),
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [fact(None, "2023-08-18", 2.47e9, "2023-08-25", "b1")]}
            }
        },
    },
}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    store_facts(c, extract_facts(CF, 1045810))
    return c


def test_extract_filters_forms_kinds_and_units():
    rows = extract_facts(CF, 1045810)
    concepts = sorted({(r[1], r[2]) for r in rows})
    assert ("total_assets", "us-gaap:Assets") in concepts
    assert ("shares_outstanding", "dei:EntityCommonStockSharesOutstanding") in concepts
    assert not any(r[8] == "8-K" for r in rows)
    assets = [r for r in rows if r[1] == "total_assets"]
    assert len(assets) == 1 and assets[0][4] == ""  # instant: empty period_start


def test_tag_switch_gives_continuous_history(conn):
    capex = load_concept(conn, 1045810, "capex")
    assert capex["value"].tolist() == [100, 110, 200]  # overlapping period uses the priority tag
    assert capex["tag"].tolist()[-1] == "us-gaap:PaymentsToAcquireProductiveAssets"


def test_restatement_is_point_in_time(conn):
    before = load_concept(conn, 1045810, "revenue", as_of=date(2022, 12, 31))
    after = load_concept(conn, 1045810, "revenue", as_of=date(2023, 6, 1))
    assert before["value"].tolist() == [1000]
    assert after["value"].tolist() == [1050]
    assert load_concept(conn, 1045810, "revenue", as_of=date(2022, 5, 26)).empty


def test_merges_fill_history_from_predecessor_cik(conn):
    old = {"cik": 34088, "facts": {"us-gaap": {"Revenues": usd(
        fact("2010-01-01", "2010-03-31", 500, "2010-05-01", "o1"),
        fact("2022-01-31", "2022-05-01", 7777, "2022-05-27", "o2"),
    )}}}
    store_facts(conn, extract_facts(old, 34088))
    rev = load_concept(conn, 1045810, "revenue", merges={1045810: [34088]})
    assert rev["value"].tolist() == [500, 1050]  # current CIK wins where both exist
    assert rev["period_start"].iloc[0] == pd.Timestamp("2010-01-01")


def test_latest_shares(conn):
    assert latest_shares(conn, 1045810, date(2023, 9, 1)) == 2.47e9
    assert latest_shares(conn, 1045810, date(2023, 8, 1)) is None


def test_load_cik_merges(tmp_path):
    cfg = tmp_path / "m.toml"
    cfg.write_text('[[merge]]\nticker = "XOM"\npredecessor_ciks = [34088]\n'
                   '[[merge]]\nticker = "SAME"\npredecessor_ciks = [5]\n'
                   '[[merge]]\nticker = "GONE"\npredecessor_ciks = [9]\n')
    tm = pd.DataFrame({"cik": [2000001, 5], "ticker": ["XOM", "SAME"], "name": ["", ""],
                       "exchange": ["", ""]})
    assert load_cik_merges(tm, cfg) == {2000001: [34088]}


# --- extra coverage beyond the plan's six tests: point-in-time edge cases ---


def test_as_of_never_returns_rows_filed_later(conn):
    store_facts(conn, extract_facts(CF, 1045810))  # idempotent upsert
    as_of = date(2023, 8, 1)
    capex = load_concept(conn, 1045810, "capex", as_of=as_of)
    # PaymentsToAcquireProductiveAssets was filed 2023-08-25: neither its 2023 period nor
    # its 111 value for the overlapping period is known yet.
    assert capex["value"].tolist() == [100, 110]
    assert capex["tag"].unique().tolist() == ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"]
    assert (capex["filed"] <= pd.Timestamp(as_of)).all()
    # filed on the as_of day itself counts as known
    assert load_concept(conn, 1045810, "capex", as_of=date(2023, 8, 25))["value"].tolist() == [
        100, 110, 200]


def test_same_day_refiling_prefers_the_later_accession(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    cf = {"cik": 7, "facts": {"us-gaap": {"Revenues": usd(
        fact("2024-01-01", "2024-03-31", 10, "2024-05-01", "0000007-24-000001", form="10-K"),
        fact("2024-01-01", "2024-03-31", 20, "2024-05-01", "0000007-24-000002", form="10-K/A"),
    )}}}
    store_facts(c, extract_facts(cf, 7))
    rev = load_concept(c, 7, "revenue", as_of=date(2024, 5, 1))
    assert rev["value"].tolist() == [20]
    assert rev["accession"].tolist() == ["0000007-24-000002"]


def test_instant_concept_has_nat_period_start_sorted_by_end(conn):
    store_facts(conn, extract_facts({"cik": 1045810, "facts": {"us-gaap": {"Assets": usd(
        fact(None, "2022-07-31", 4000, "2022-08-26", "a2"),
    )}}}, 1045810))
    assets = load_concept(conn, 1045810, "total_assets")
    assert assets["value"].tolist() == [4000, 5000]
    assert assets["period_start"].isna().all()
    assert pd.api.types.is_datetime64_any_dtype(assets["period_start"])
    assert assets["period_end"].is_monotonic_increasing


def test_unknown_concept_or_cik_is_empty_frame(conn):
    out = load_concept(conn, 999, "revenue")
    assert out.empty
    assert list(out.columns) == ["cik", "tag", "period_start", "period_end", "value", "filed",
                                 "accession"]


def test_fetch_companyfacts_returns_none_on_404_and_json_otherwise(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(404)])
    assert fetch_companyfacts(client, 1045810) is None
    assert session.calls[0]["url"].endswith("/companyfacts/CIK0001045810.json")
    client, _ = fake_client(tmp_path / "ok", responses=[FakeResponse(200, json_data=CF)])
    assert fetch_companyfacts(client, 1045810)["cik"] == 1045810


def test_default_merge_file_ships_with_package_and_resolves_xom():
    tm = pd.DataFrame({"cik": [34088 + 1], "ticker": ["XOM"], "name": [""], "exchange": [""]})
    assert load_cik_merges(tm) == {34089: [34088]}


# --- fix round 1 ---


def shares(*facts):
    return {"units": {"shares": list(facts)}}


def test_dual_class_cover_page_shares_are_summed_into_one_row(conn):
    cf = {"cik": 11, "facts": {"dei": {"EntityCommonStockSharesOutstanding": shares(
        fact(None, "2024-01-31", 2.0e9, "2024-02-05", "k1", form="10-K"),  # class A
        fact(None, "2024-01-31", 5.0e8, "2024-02-05", "k1", form="10-K"),  # class B
    )}}}
    rows = [r for r in extract_facts(cf, 11) if r[1] == "shares_outstanding"]
    assert len(rows) == 1 and rows[0][6] == 2.5e9
    assert rows[0][2] == "dei:EntityCommonStockSharesOutstanding"
    assert rows[0][7] == "2024-02-05" and rows[0][8] == "10-K" and rows[0][9] == "k1"
    store_facts(conn, extract_facts(cf, 11))
    stored = conn.execute(
        "SELECT value FROM facts WHERE cik = 11 AND concept = 'shares_outstanding'").fetchall()
    assert stored == [(2.5e9,)]
    assert latest_shares(conn, 11, date(2024, 3, 1)) == 2.5e9


def test_exact_duplicate_cover_page_shares_are_not_double_counted():
    cf = {"cik": 11, "facts": {"dei": {"EntityCommonStockSharesOutstanding": shares(
        fact(None, "2024-01-31", 2.0e9, "2024-02-05", "k1", form="10-K"),
        fact(None, "2024-01-31", 2.0e9, "2024-02-05", "k1", form="10-K"),
    )}}}
    rows = extract_facts(cf, 11)
    assert [r[6] for r in rows] == [2.0e9]


def test_share_classes_are_only_summed_within_one_tag_date_and_accession(conn, caplog):
    cf = {"cik": 11, "facts": {
        "dei": {"EntityCommonStockSharesOutstanding": shares(
            fact(None, "2024-01-31", 2.0e9, "2024-02-05", "k1", form="10-K"),
            fact(None, "2024-01-31", 5.0e8, "2024-02-05", "k1", form="10-K"),
            fact(None, "2024-04-30", 2.1e9, "2024-05-06", "q1"),  # other filing: not summed
        )},
        "us-gaap": {"CommonStockSharesOutstanding": shares(
            fact(None, "2024-01-31", 7.0e8, "2024-02-05", "k1", form="10-K"),  # other tag
        )},
    }}
    with caplog.at_level("DEBUG", logger="analyzer.data.edgar.companyfacts"):
        rows = extract_facts(cf, 11)
    got = sorted((r[2], r[5], r[6]) for r in rows)
    assert got == [
        ("dei:EntityCommonStockSharesOutstanding", "2024-01-31", 2.5e9),
        ("dei:EntityCommonStockSharesOutstanding", "2024-04-30", 2.1e9),
        ("us-gaap:CommonStockSharesOutstanding", "2024-01-31", 7.0e8),
    ]
    summed = [m for m in caplog.messages if "summed" in m]
    assert len(summed) == 1  # logged once, for the two distinct values only


def test_other_duration_concepts_are_not_summed():
    cf = {"cik": 11, "facts": {"us-gaap": {"Revenues": usd(
        fact("2024-01-01", "2024-03-31", 10, "2024-05-01", "q1"),
        fact("2024-01-01", "2024-03-31", 20, "2024-05-01", "q1"),
    )}}}
    assert sorted(r[6] for r in extract_facts(cf, 11)) == [10.0, 20.0]


def test_unknown_concept_raises_even_without_rows(conn):
    with pytest.raises(KeyError, match="unknown concept 'nope'"):
        load_concept(conn, 999, "nope")
    with pytest.raises(KeyError, match="unknown concept 'nope'"):
        load_concept(conn, 1045810, "nope", as_of=date(2023, 1, 1))


def test_empty_result_has_the_same_typed_columns_as_a_populated_one(conn):
    populated = load_concept(conn, 1045810, "revenue")
    empty = load_concept(conn, 1045810, "revenue", as_of=date(2000, 1, 1))
    assert empty.empty
    assert list(empty.columns) == list(populated.columns)
    assert empty.dtypes.to_dict() == populated.dtypes.to_dict()
    assert empty["period_end"].dt.year.empty  # .dt works only on a real datetime dtype
    assert empty["period_start"].dt.year.empty
    assert empty["filed"].dt.year.empty


def test_period_start_and_end_share_a_datetime_unit_for_instants(conn):
    assets = load_concept(conn, 1045810, "total_assets")
    assert assets["period_start"].isna().all()
    assert assets["period_start"].dtype == assets["period_end"].dtype == assets["filed"].dtype
    capex = load_concept(conn, 1045810, "capex")
    assert capex["period_start"].dtype == capex["period_end"].dtype
    assert capex["period_end"].dtype == assets["period_end"].dtype


def test_merges_respect_as_of(conn):
    old = {"cik": 34088, "facts": {"us-gaap": {"Revenues": usd(
        fact("2010-01-01", "2010-03-31", 500, "2010-05-01", "o1"),
        fact("2022-01-31", "2022-05-01", 7777, "2022-05-20", "o2"),
        fact("2024-01-01", "2024-03-31", 8888, "2024-05-01", "o3"),  # after every as_of below
    )}}}
    store_facts(conn, extract_facts(old, 34088))
    merges = {1045810: [34088]}
    # The current CIK's 2022-05-27 and 2023-03-01 filings are not known yet: the predecessor's
    # earlier filing fills the period, and nothing later leaks in.
    early = load_concept(conn, 1045810, "revenue", as_of=date(2022, 5, 26), merges=merges)
    assert early["value"].tolist() == [500, 7777]
    assert early["cik"].tolist() == [34088, 34088]
    assert (early["filed"] <= pd.Timestamp(2022, 5, 26)).all()
    # Once the current CIK has filed, it beats the predecessor, restatement by restatement.
    mid = load_concept(conn, 1045810, "revenue", as_of=date(2022, 12, 31), merges=merges)
    assert mid["value"].tolist() == [500, 1000]
    late = load_concept(conn, 1045810, "revenue", as_of=date(2023, 6, 1), merges=merges)
    assert late["value"].tolist() == [500, 1050]
    assert 8888 not in late["value"].tolist()


def test_latest_shares_falls_back_to_diluted_shares_without_shares_outstanding(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    cf = {"cik": 21, "facts": {"us-gaap": {"WeightedAverageNumberOfDilutedSharesOutstanding": shares(
        fact("2023-01-01", "2023-03-31", 98.0, "2023-05-01", "q1"),
        fact("2023-04-01", "2023-06-30", 99.0, "2023-08-01", "q2"),
        fact("2023-07-01", "2023-09-30", 105.0, "2023-11-01", "q3"),
    )}}}
    store_facts(c, extract_facts(cf, 21))
    assert latest_shares(c, 21, date(2023, 9, 1)) == 99.0
    assert latest_shares(c, 21, date(2023, 12, 1)) == 105.0
    assert latest_shares(c, 21, date(2023, 4, 1)) is None
    # a cover-page count, once filed, takes precedence over the weighted average
    store_facts(c, extract_facts({"cik": 21, "facts": {"dei": {
        "EntityCommonStockSharesOutstanding": shares(
            fact(None, "2023-10-20", 101.0, "2023-11-01", "q3"))}}}, 21))
    assert latest_shares(c, 21, date(2023, 12, 1)) == 101.0
    assert latest_shares(c, 21, date(2023, 9, 1)) == 99.0
