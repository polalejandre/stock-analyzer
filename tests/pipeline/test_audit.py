from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer import cli
from analyzer.data.price_sync import replace_prices
from analyzer.pipeline.audit import audit, summarize
from analyzer.store.db import connect, migrate
from tests.helpers import bars

TODAY = date(2026, 10, 9)


def add_quarters(conn, cik, concept, tag, ends, accession="a1"):
    for end in ends:
        e = pd.Timestamp(end)
        s = (e - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        conn.execute("INSERT INTO facts VALUES (?,?,?, 'USD', ?, ?, 100, ?, '10-Q', ?, 'companyfacts')",
                     (cik, concept, tag, s, end, end, accession))


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.executemany("INSERT INTO tickers (ticker, cik, sector_etf, updated_at) VALUES (?,?,?,?)", [
        ("GOOD", 1, "XLK", "x"), ("NOCIK", None, "XLK", "x"), ("MERGE", 2, "XLE", "x"),
        ("LAG", 3, None, "x"),
    ])
    replace_prices(c, "GOOD", bars(["2026-10-08", "2026-10-09"], [1.0, 1.0]), "yfinance")
    replace_prices(c, "MERGE", bars(["2009-01-02", "2026-10-09"], [1.0, 1.0]), "yfinance")
    replace_prices(c, "LAG", bars(["2026-09-01"], [1.0]), "yfinance")
    recent = ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    for concept, tag in [("revenue", "us-gaap:Revenues"),
                         ("operating_cash_flow", "us-gaap:NetCashProvidedByUsedInOperatingActivities"),
                         ("capex", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment")]:
        add_quarters(c, 1, concept, tag, recent)
    add_quarters(c, 2, "revenue", "us-gaap:Revenues", ["2024-03-31", "2024-06-30"])
    c.execute("INSERT INTO filings VALUES ('lag-acc', 3, '10-Q', '2026-08-01', NULL, NULL, 'x.htm')")
    return c


def test_audit_flags(conn):
    result = {a.ticker: a for a in audit(conn, TODAY)}
    assert result["GOOD"].flags == []
    assert result["GOOD"].revenue_quarters == 4 and result["GOOD"].has_fcf
    assert "no CIK" in result["NOCIK"].flags and "no prices" in result["NOCIK"].flags
    assert "check CIK merge" in result["MERGE"].flags
    lag = result["LAG"].flags
    assert {"stale prices", "no fundamentals", "companyfacts behind latest 10-Q/10-K",
            "no sector ETF"} <= set(lag)


def test_instance_fallback_facts_count_as_present(conn):
    conn.execute("INSERT INTO facts VALUES (3, 'revenue', 'us-gaap:Revenues', 'USD', "
                 "'2026-04-01', '2026-06-30', 1, '2026-08-01', '10-Q', 'lag-acc', 'instance')")
    lag = {a.ticker: a for a in audit(conn, TODAY)}["LAG"]
    assert "companyfacts behind latest 10-Q/10-K" not in lag.flags


def test_audit_subset_and_summary(conn):
    audits = audit(conn, TODAY, tickers={"GOOD", "LAG"})
    assert [a.ticker for a in audits] == ["GOOD", "LAG"]
    s = summarize(audits)
    assert s["tickers"] == 2 and s["with prices"] == 2 and s["with 8+ revenue quarters"] == 0
    assert s["flagged"] == 1


def _cli_env(monkeypatch, tmp_path, conn, loader):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "make_clients", lambda settings: type("C", (), {"sec": None})())
    monkeypatch.setattr(cli, "load_ticker_map", loader)
    monkeypatch.setattr(cli, "load_cik_merges", lambda tm: {})

    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(cli, "date", FixedDate)
    conn.commit()


def test_cli_audit_flagged_only_and_all(conn, monkeypatch, tmp_path):
    conn.commit()
    conn.close()
    from analyzer.config import load_settings
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    db = load_settings().db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.db").replace(db)
    _cli_env(monkeypatch, tmp_path, connect(db), lambda sec: pd.DataFrame())
    out = CliRunner().invoke(cli.app, ["audit"])
    assert out.exit_code == 0, out.output
    assert "LAG" in out.output and "GOOD " not in out.output.split("\n\n", 1)[1]
    out = CliRunner().invoke(cli.app, ["audit", "--all", "--tickers", "good"])
    assert "GOOD" in out.output


def test_cli_audit_survives_offline_ticker_map(conn, monkeypatch, tmp_path):
    conn.commit()
    conn.close()
    from analyzer.config import load_settings
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    db = load_settings().db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.db").replace(db)

    def boom(sec):
        raise OSError("offline")

    _cli_env(monkeypatch, tmp_path, connect(db), boom)
    result = CliRunner().invoke(cli.app, ["audit"])
    assert result.exit_code == 0, result.output
    assert "warning" in result.output.lower() and "tickers" in result.output
