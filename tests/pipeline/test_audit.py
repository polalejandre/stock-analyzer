from datetime import date, timedelta

import pandas as pd
import pytest
from typer.testing import CliRunner

from analyzer import cli
from analyzer.config import load_settings
from analyzer.data.price_sync import replace_prices
from analyzer.pipeline import audit as audit_mod
from analyzer.pipeline.audit import audit, missing_reference_etfs, summarize
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


def test_inactive_ticker_skips_price_and_cik_flags(conn):
    flagged = {a.ticker: a for a in audit(conn, TODAY, active={"GOOD"})}
    assert not flagged["NOCIK"].active
    assert flagged["NOCIK"].flags == []
    assert "stale prices" not in flagged["LAG"].flags
    assert "companyfacts behind latest 10-Q/10-K" not in flagged["LAG"].flags
    assert "no fundamentals" in flagged["LAG"].flags
    s = summarize(list(flagged.values()))
    assert s["active tickers"] == 1 and s["inactive tickers"] == 3 and s["flagged"] == 0
    assert "no prices" in {a.ticker: a for a in audit(conn, TODAY, active={"NOCIK"})}["NOCIK"].flags


def test_financials_no_fcf_is_expected_not_flagged(conn):
    conn.execute("UPDATE tickers SET gics_sector = 'Financials' WHERE ticker = 'MERGE'")
    conn.execute("UPDATE tickers SET gics_sector = 'Energy' WHERE ticker = 'GOOD'")
    result = {a.ticker: a for a in audit(conn, TODAY)}
    assert "no FCF (financials, expected)" in result["MERGE"].flags
    assert "no FCF" not in result["MERGE"].flags


def test_no_fcf_boundary(conn):
    # the oldest quarter (2025-09-30) is in the 470-day window on day 470 and out on day 471
    assert audit(conn, date(2026, 10, 9), tickers={"GOOD"})[0].has_fcf
    late = date(2025, 9, 30) + timedelta(days=471)
    a = audit(conn, late, tickers={"GOOD"})[0]
    assert not a.has_fcf and "no FCF" in a.flags
    edge = date(2025, 9, 30) + timedelta(days=470)
    assert audit(conn, edge, tickers={"GOOD"})[0].has_fcf


def test_stale_price_boundary(conn):
    assert "stale prices" not in audit(conn, date(2026, 10, 13), tickers={"GOOD"})[0].flags  # 4 days
    assert "stale prices" in audit(conn, date(2026, 10, 14), tickers={"GOOD"})[0].flags  # 5 days


def test_merges_change_result(conn):
    assert "check CIK merge" in audit(conn, TODAY, tickers={"MERGE"})[0].flags
    add_quarters(conn, 20, "revenue", "us-gaap:Revenues", ["2010-03-31", "2010-06-30"])
    merged = audit(conn, TODAY, merges={2: [20]}, tickers={"MERGE"})[0]
    assert "check CIK merge" not in merged.flags and merged.revenue_quarters == 4


def test_per_ticker_error_isolated(conn, monkeypatch):
    real = audit_mod._quarters

    def flaky(c, cik, concept, merges):
        if cik == 1:
            raise ValueError("boom")
        return real(c, cik, concept, merges)

    monkeypatch.setattr(audit_mod, "_quarters", flaky)
    result = {a.ticker: a for a in audit(conn, TODAY)}
    assert result["GOOD"].flags == ["audit error: ValueError: boom"]
    assert "stale prices" in result["LAG"].flags


def test_missing_reference_etfs(conn):
    replace_prices(conn, "XLK", bars(["2026-10-09"], [1.0]), "yfinance")
    assert missing_reference_etfs(conn) == ["XLE"]
    assert summarize(audit(conn, TODAY), ["XLE"])["reference ETFs without prices"] == 1


def _cli_env(monkeypatch, tmp_path, conn, loader):
    conn.commit()
    conn.close()
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    db = load_settings().db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "t.db").replace(db)
    monkeypatch.setattr(cli, "make_clients", lambda settings: type("C", (), {"sec": None})())
    monkeypatch.setattr(cli, "load_ticker_map", loader)
    monkeypatch.setattr(cli, "load_cik_merges", lambda tm: {})
    monkeypatch.setattr(cli, "price_symbols", lambda c, d, r: (["GOOD", "LAG", "MERGE"], ["NOCIK"]))


ARGS = ["audit", "--today", "2026-10-09"]


def test_cli_audit_flagged_only_and_all(conn, monkeypatch, tmp_path):
    _cli_env(monkeypatch, tmp_path, conn, lambda sec: pd.DataFrame())
    out = CliRunner().invoke(cli.app, ARGS)
    assert out.exit_code == 0, out.output
    listing = out.stdout.split("\n\n", 1)[1]
    assert "LAG" in listing and "GOOD " not in listing and "NOCIK" not in listing
    assert "reference ETFs without prices: XLE, XLK" in out.stdout
    out = CliRunner().invoke(cli.app, [*ARGS, "--all", "--tickers", "good"])
    assert out.exit_code == 0 and "GOOD" in out.stdout and "LAG" not in out.stdout
    out = CliRunner().invoke(cli.app, [*ARGS, "--all", "--tickers", "nocik"])
    assert "inactive" in out.stdout


def test_cli_audit_survives_offline_ticker_map(conn, monkeypatch, tmp_path):
    def boom(sec):
        raise OSError("offline")

    _cli_env(monkeypatch, tmp_path, conn, boom)
    result = CliRunner().invoke(cli.app, ARGS)
    assert result.exit_code == 0, result.output
    assert "warning" in result.stderr.lower() and "tickers" in result.stdout


def test_cli_audit_bug_in_ticker_map_surfaces(conn, monkeypatch, tmp_path):
    def bug(sec):
        raise KeyError("data")

    _cli_env(monkeypatch, tmp_path, conn, bug)
    assert CliRunner().invoke(cli.app, ARGS).exit_code != 0


def test_cli_audit_bad_merges_file_exits_2(conn, monkeypatch, tmp_path):
    _cli_env(monkeypatch, tmp_path, conn, lambda sec: pd.DataFrame())

    def bad(tm):
        raise ValueError("malformed")

    monkeypatch.setattr(cli, "load_cik_merges", bad)
    result = CliRunner().invoke(cli.app, ARGS)
    assert result.exit_code == 2 and "malformed" in result.stderr


def test_cli_audit_argument_errors(conn, monkeypatch, tmp_path):
    runner = CliRunner()
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    assert runner.invoke(cli.app, ["audit", "--tickers", " , "]).exit_code == 2
    assert runner.invoke(cli.app, ["audit", "--today", "nope"]).exit_code == 2
    result = runner.invoke(cli.app, ["audit"])  # no database yet
    assert result.exit_code == 2 and "analyzer update" in result.stderr
    assert not (tmp_path / "analyzer.db").exists()


def test_cli_audit_warns_when_no_active_universe(conn, monkeypatch, tmp_path):
    _cli_env(monkeypatch, tmp_path, conn, lambda sec: pd.DataFrame())
    monkeypatch.setattr(cli, "price_symbols", lambda c, d, r: ([], []))
    result = CliRunner().invoke(cli.app, ARGS)
    assert result.exit_code == 0
    assert "warning: no active universe found" in result.stderr
