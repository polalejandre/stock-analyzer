from datetime import date

import pytest

from analyzer.config import ConfigError
from analyzer.data.fred import MACRO_SERIES, sync_macro
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client


def obs(*pairs):
    return {"observations": [{"date": d, "value": v} for d, v in pairs]}


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_sync_stores_values_and_skips_missing_markers(conn, tmp_path):
    client, session = fake_client(
        tmp_path, handler=lambda url, p: FakeResponse(json_data=obs(("2026-10-02", "-0.52"),
                                                                    ("2026-10-09", "."))))
    result = sync_macro(conn, client, "KEY", date(2026, 10, 10))
    assert result.failed == []
    assert len(session.calls) == len(MACRO_SERIES)
    assert session.calls[0]["params"]["observation_start"] == "2006-01-01"
    assert conn.execute("SELECT COUNT(*) FROM macro").fetchone()[0] == len(MACRO_SERIES)
    assert result.latest["NFCI"] == "2026-10-02"


def test_incremental_refetches_revision_window(conn, tmp_path):
    conn.execute("INSERT INTO macro VALUES ('NFCI', '2026-10-02', -0.5)")
    client, session = fake_client(tmp_path, handler=lambda url, p: FakeResponse(json_data=obs()))
    sync_macro(conn, client, "KEY", date(2026, 10, 10))
    nfci = next(c for c in session.calls if c["params"]["series_id"] == "NFCI")
    assert nfci["params"]["observation_start"] == "2026-07-04"


def test_missing_key_is_a_config_error(conn, tmp_path):
    client, _ = fake_client(tmp_path)
    with pytest.raises(ConfigError, match="FRED_API_KEY"):
        sync_macro(conn, client, None, date(2026, 10, 10))


def test_fred_key_is_never_cached(conn, tmp_path):
    client, _ = fake_client(tmp_path, handler=lambda url, p: FakeResponse(400))
    result = sync_macro(conn, client, "SECRETKEY", date(2026, 10, 10))
    assert len(result.failed) == len(MACRO_SERIES)
    assert all("SECRETKEY" not in err for _, err in result.failed)
    cache = tmp_path / "cache"
    assert not cache.exists() or not any("SECRETKEY" in str(p) for p in cache.rglob("*"))
