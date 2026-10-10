# Phase 1: Data Layer and Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `uv run analyzer update` fills a local SQLite database and disk cache with point-in-time data for the full universe: index membership (current and past), young listings, daily prices with a fallback source, EDGAR fundamentals with the known quirks handled, filings, insider transactions and FRED macro series. `uv run analyzer audit` reports coverage and data problems.

**Architecture:** One Python package (`src/analyzer/`). Network access goes through a single `HttpClient` (disk cache, rate limit, retry). Providers parse source data into plain rows. Sync functions write those rows to SQLite. `pipeline/update.py` runs the steps in order and isolates failures. Pure transforms (`build_membership`, `discrete_quarters`, parsers) are kept free of I/O so they can be unit-tested without the network.

**Tech Stack:** Python 3.12, uv, pandas, requests, yfinance, lxml, Typer, python-dotenv, stdlib `sqlite3`, pytest, ruff.

**Spec:** [`docs/specs/2026-10-10-stock-analyzer-design.md`](../specs/2026-10-10-stock-analyzer-design.md), sections 2, 3, 4, 11 and 12. Phase 1 is section 13, item 1.

## Global Constraints

- Python `>=3.12`; `.python-version` pins `3.12`.
- Free data only. Primary sources and structured data only; no HTML scraping of news sites (Wikipedia index tables are allowed by the spec).
- SEC: at most 10 requests per second (the client uses a 0.125 s minimum interval) and every request sends `SEC_USER_AGENT`, which must contain a contact email.
- A fundamental value becomes known on its EDGAR `filed` date, never its period-end date.
- Default tests never touch the network. Live tests are marked `@pytest.mark.live` and run only with `uv run pytest -m live`.
- Thresholds live in `rules.toml`, which has a `version` field that must be bumped when any value changes.
- `data/` and `reports/` are gitignored. `.env` holds `SEC_USER_AGENT` and `FRED_API_KEY`.
- Canonical ticker form: uppercase, `.` replaced by `-` (`BRK.B` → `BRK-B`).
- Dates are stored in SQLite as ISO `YYYY-MM-DD` strings.
- The FRED API key must never appear in cache file names, log lines or error messages.
- If more than 5% of the items in a step fail, `analyzer update` exits with code 1.

## Decisions made while planning (deviations from the spec, to confirm in review)

Probes run on 2026-10-10 changed four details of spec section 3. Each is noted again in the task that implements it.

1. **Price fallback = Nasdaq public endpoint** (`api.nasdaq.com/api/quote/{SYMBOL}/historical`). It returned split-adjusted OHLCV for stocks and ETFs, needs no key, and goes back about 10 years. Class shares use a dot (`BRK.B`). Tiingo was not probed because it needs a key, and Nasdaq is enough for a one-bar-per-day fallback.
2. **Past index membership from monthly Wikipedia revisions.** The S&P 500 article no longer has a changes table. Old revisions of both articles have parseable constituents tables: S&P 500 from 2010, S&P 400 from 2013. Membership is rebuilt from one snapshot per month plus today's page, so start and end dates are accurate to about one month. The current page's "Date added" column refines start dates where available.
3. **Sector and industry come from GICS first, SIC second.** The Wikipedia tables carry the GICS sector and sub-industry, which map exactly to the SPDR sector ETFs and to the industry ETFs. The SIC code is used only for stocks without GICS (young listings, delisted names).
4. **Bulk SEC datasets and the daily index.** Insider history comes from SEC's quarterly Form 3/4/5 datasets, with per-filing Form 4 XML only for filings after the latest published quarter. Daily incremental updates use EDGAR's daily master index, so only companies that filed something are re-fetched.
5. **EDGAR quirk tests use synthetic fixtures modeled on the real shapes,** plus live tests against the real tickers (NVDA, XOM, COST, JPM). Recording real companyfacts files would add megabytes of fixtures for little gain.

## Review Focus

- A vandalized or half-edited Wikipedia revision (a constituents table with 10 rows, or none) must not erase index members. Expect: that revision is skipped and the next older sane revision is used. Test: Task 6, `test_snapshot_skips_vandalized_revision`.
- Running `analyzer update` during US market hours stores a partial bar for today. Expect: the next run overwrites it and does **not** treat the changed close as a dividend adjustment that forces a full re-download of every symbol. Test: Task 5, `test_partial_last_bar_does_not_trigger_refetch`.
- The 07:00 Madrid run may happen before EDGAR publishes yesterday's daily index, and the laptop may be off for days. Expect: an unpublished recent weekday is retried on the next run rather than skipped, and old gaps (holidays) don't block progress. Test: Task 11, `test_pointer_waits_for_unpublished_recent_day`.
- A 10-K restates last year's revenue. Expect: a view as of a date before the 10-K was filed still sees the original value. Test: Task 8, `test_restatement_is_point_in_time`.
- A FRED request fails. Expect: the error message and the cache directory never contain the API key. Tests: Task 3, `test_error_message_omits_query_params`, and Task 13, `test_fred_key_is_never_cached`.

## File Structure

```
pyproject.toml                         # Task 1
.python-version                        # Task 1
.env.example                           # Task 1
rules.toml                             # Task 1
src/analyzer/__init__.py               # Task 1
src/analyzer/cli.py                    # Task 1 (skeleton), 15 (update), 16 (audit)
src/analyzer/config.py                 # Task 1: Settings, Rules
src/analyzer/store/__init__.py         # Task 2
src/analyzer/store/db.py               # Task 2: connect, migrate, upsert_rows, sync state
src/analyzer/store/migrations/001_initial.sql   # Task 2
src/analyzer/data/__init__.py          # Task 3
src/analyzer/data/http.py              # Task 3: HttpClient
src/analyzer/data/clients.py           # Task 3: one client per source
src/analyzer/data/prices.py            # Task 4: providers and fallback chain
src/analyzer/data/price_sync.py        # Task 5: price storage and incremental sync
src/analyzer/universe/__init__.py      # Task 6
src/analyzer/universe/wikipedia.py     # Task 6: revisions, parsing, snapshots
src/analyzer/universe/membership.py    # Task 6: intervals, members_on
src/analyzer/universe/industry.py      # Task 7: GICS/SIC → ETFs
src/analyzer/universe/registry.py      # Task 7: tickers table
src/analyzer/data/edgar/__init__.py    # Task 7
src/analyzer/data/edgar/companies.py   # Task 7: ticker map, submissions, filings
src/analyzer/data/edgar/tags.py        # Task 8: concept → XBRL tags
src/analyzer/data/edgar/companyfacts.py# Task 8: extraction and storage
src/analyzer/data/edgar/facts_read.py  # Task 8: point-in-time reads
src/analyzer/data/edgar/merges.py      # Task 8: CIK merges
src/analyzer/data/edgar/cik_merges.toml# Task 8
src/analyzer/data/edgar/quarters.py    # Task 9: discrete quarters
src/analyzer/data/edgar/instance.py    # Task 10: XBRL instance fallback
src/analyzer/data/edgar/daily_index.py # Task 11: daily master index
src/analyzer/data/edgar/form4.py       # Task 11: Form 4 XML
src/analyzer/data/edgar/insider_bulk.py# Task 12: quarterly insider datasets
src/analyzer/data/edgar/sync.py        # Task 12: refresh_company
src/analyzer/data/fred.py              # Task 13
src/analyzer/universe/young.py         # Task 14
src/analyzer/universe/members.py       # Task 14: universe_on, price_symbols
src/analyzer/pipeline/__init__.py      # Task 15
src/analyzer/pipeline/update.py        # Task 15
src/analyzer/pipeline/audit.py         # Task 16
tests/__init__.py, tests/helpers.py    # Tasks 1, 3, 5
tests/...                              # one test module per source module
tests/live/test_sources.py             # Task 17
```

---

### Task 1: Project scaffold, settings and rules

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`, `rules.toml`, `src/analyzer/__init__.py`, `src/analyzer/config.py`, `src/analyzer/cli.py`, `tests/__init__.py`, `tests/test_config.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `analyzer.config.PROJECT_ROOT: Path`, `ConfigError(Exception)`, `Settings(sec_user_agent: str, fred_api_key: str | None, data_dir: Path)` with properties `db_path` and `cache_dir`, `load_settings(root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> Settings`, `UniverseRules(min_dollar_volume: float, dollar_volume_days: int, young_max_years: int, young_min_market_cap: float)`, `Rules(version: str, universe: UniverseRules)`, `load_rules(path: Path = PROJECT_ROOT / "rules.toml") -> Rules`, and the Typer `analyzer.cli.app`.

- [ ] **Step 1: Write project files**

`pyproject.toml`:

```toml
[project]
name = "stock-analyzer"
version = "0.1.0"
description = "Personal daily scanner for dislocated, undervalued US stocks"
requires-python = ">=3.12"
dependencies = [
    "lxml>=5.3",
    "numpy>=2.0",
    "pandas>=2.2",
    "python-dotenv>=1.0",
    "requests>=2.32",
    "typer>=0.12",
    "yfinance>=1.7",
]

[project.scripts]
analyzer = "analyzer.cli:app"

[dependency-groups]
dev = ["pytest>=8.3", "ruff>=0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/analyzer"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits real external APIs (opt-in with: uv run pytest -m live)"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = ["E501", "B008"]  # long SQL strings; Typer options as defaults
```

`.python-version`:

```
3.12
```

`.env.example`:

```bash
# SEC requires a contact email in the User-Agent of every request.
SEC_USER_AGENT="StockAnalyzer you@example.com"
# Free key: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY=""
# Optional: where the SQLite database and raw cache live (default: ./data)
# ANALYZER_DATA_DIR=/path/to/data
```

`rules.toml`:

```toml
# Every threshold and weight used by the engine. Bump `version` whenever any value changes.
version = "2026.10.10-1"

[universe]
min_dollar_volume = 25_000_000      # average close × volume over `dollar_volume_days`, USD
dollar_volume_days = 20
young_max_years = 5                 # a young listing first traded less than this many years ago
young_min_market_cap = 2_000_000_000
```

Append to `.gitignore`:

```
data/
reports/
.pytest_cache/
.ruff_cache/
```

`src/analyzer/__init__.py`:

```python
"""Stock Analyzer engine."""
```

`tests/__init__.py`: empty file.

- [ ] **Step 2: Install the environment**

Run: `uv sync`
Expected: creates `.venv` with Python 3.12 and installs the dependencies (uv downloads 3.12 if needed).

- [ ] **Step 3: Write the failing tests**

`tests/test_config.py`:

```python
import pytest
from typer.testing import CliRunner

from analyzer.cli import app
from analyzer.config import PROJECT_ROOT, ConfigError, load_rules, load_settings


def test_settings_read_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=StockAnalyzer me@example.com\nFRED_API_KEY=abc\n")
    s = load_settings(root=tmp_path, environ={})
    assert s.sec_user_agent == "StockAnalyzer me@example.com"
    assert s.fred_api_key == "abc"
    assert s.db_path == tmp_path / "data" / "analyzer.db"
    assert s.cache_dir == tmp_path / "data" / "cache"


def test_environment_overrides_env_file(tmp_path):
    (tmp_path / ".env").write_text("SEC_USER_AGENT=old old@example.com\n")
    s = load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "new new@example.com"})
    assert s.sec_user_agent == "new new@example.com"
    assert s.fred_api_key is None


def test_data_dir_override(tmp_path):
    s = load_settings(
        root=tmp_path,
        environ={"SEC_USER_AGENT": "a a@example.com", "ANALYZER_DATA_DIR": str(tmp_path / "x")},
    )
    assert s.data_dir == tmp_path / "x"


def test_missing_contact_email_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="contact email"):
        load_settings(root=tmp_path, environ={"SEC_USER_AGENT": "StockAnalyzer"})


def test_rules_file_loads():
    rules = load_rules(PROJECT_ROOT / "rules.toml")
    assert rules.version
    assert rules.universe.min_dollar_volume == 25_000_000
    assert rules.universe.dollar_volume_days == 20
    assert rules.universe.young_max_years == 5
    assert rules.universe.young_min_market_cap == 2_000_000_000


def test_cli_help():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Stock Analyzer" in result.output
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.cli'`.

- [ ] **Step 5: Implement config and CLI skeleton**

`src/analyzer/config.py`:

```python
"""Settings from the environment (.env) and rules from rules.toml."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    sec_user_agent: str
    fred_api_key: str | None
    data_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "analyzer.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


def load_settings(root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None) -> Settings:
    """Read `.env` under `root`, then let the process environment override it."""
    env = {**dotenv_values(root / ".env"), **(os.environ if environ is None else environ)}
    user_agent = (env.get("SEC_USER_AGENT") or "").strip()
    if "@" not in user_agent:
        raise ConfigError(
            "SEC_USER_AGENT must be set and include a contact email, "
            "e.g. 'StockAnalyzer you@example.com' (SEC requirement). See .env.example."
        )
    data_dir = Path(env.get("ANALYZER_DATA_DIR") or root / "data")
    return Settings(
        sec_user_agent=user_agent,
        fred_api_key=(env.get("FRED_API_KEY") or "").strip() or None,
        data_dir=data_dir,
    )


@dataclass(frozen=True)
class UniverseRules:
    min_dollar_volume: float
    dollar_volume_days: int
    young_max_years: int
    young_min_market_cap: float


@dataclass(frozen=True)
class Rules:
    version: str
    universe: UniverseRules


def load_rules(path: Path = PROJECT_ROOT / "rules.toml") -> Rules:
    data = tomllib.loads(path.read_text())
    u = data["universe"]
    return Rules(
        version=str(data["version"]),
        universe=UniverseRules(
            min_dollar_volume=float(u["min_dollar_volume"]),
            dollar_volume_days=int(u["dollar_volume_days"]),
            young_max_years=int(u["young_max_years"]),
            young_min_market_cap=float(u["young_min_market_cap"]),
        ),
    )
```

`src/analyzer/cli.py`:

```python
"""Command-line entry point: `analyzer ...`."""

import logging

import typer

app = typer.Typer(help="Stock Analyzer engine.", no_args_is_help=True)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    """Stock Analyzer engine."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check .`
Expected: 6 passed; ruff reports no errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .env.example rules.toml .gitignore src tests
git commit -m "feat: scaffold analyzer package with settings, rules and CLI"
```

---

### Task 2: SQLite store and migrations

**Files:**
- Create: `src/analyzer/store/__init__.py` (empty), `src/analyzer/store/db.py`, `src/analyzer/store/migrations/001_initial.sql`, `tests/store/__init__.py` (empty), `tests/store/test_db.py`

**Interfaces:**
- Produces: `connect(path: Path | str) -> sqlite3.Connection` (WAL, foreign keys, default tuple rows), `migrate(conn) -> int` (number of migrations applied), `upsert_rows(conn, table: str, columns: Sequence[str], rows: Iterable[Sequence]) -> None` (`INSERT OR REPLACE` in one transaction), `get_state(conn, key: str) -> str | None`, `set_state(conn, key: str, value: str) -> None`, and the tables below.

- [ ] **Step 1: Write the failing tests**

`tests/store/test_db.py`:

```python
from analyzer.store.db import connect, get_state, migrate, set_state, upsert_rows

TABLES = {
    "companies", "tickers", "index_snapshots", "index_membership", "young_listings", "prices",
    "facts", "filings", "insider_transactions", "macro", "provider_status", "fetch_failures",
    "sync_state",
}


def test_migrate_creates_tables_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert migrate(conn) == 1
    assert migrate(conn) == 0
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names


def test_wal_mode(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_connect_creates_parent_directory(tmp_path):
    connect(tmp_path / "nested" / "dir" / "a.db")
    assert (tmp_path / "nested" / "dir" / "a.db").exists()


def test_upsert_rows_replaces_on_primary_key(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    cols = ["series_id", "date", "value"]
    upsert_rows(conn, "macro", cols, [("NFCI", "2026-01-02", -0.5)])
    upsert_rows(conn, "macro", cols, [("NFCI", "2026-01-02", -0.4), ("NFCI", "2026-01-09", -0.3)])
    assert conn.execute("SELECT date, value FROM macro ORDER BY date").fetchall() == [
        ("2026-01-02", -0.4),
        ("2026-01-09", -0.3),
    ]


def test_sync_state_roundtrip(tmp_path):
    conn = connect(tmp_path / "a.db")
    migrate(conn)
    assert get_state(conn, "k") is None
    set_state(conn, "k", "v1")
    set_state(conn, "k", "v2")
    assert get_state(conn, "k") == "v2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.store'`.

- [ ] **Step 3: Write the schema**

`src/analyzer/store/migrations/001_initial.sql`:

```sql
CREATE TABLE companies (
    cik             INTEGER PRIMARY KEY,
    name            TEXT,
    sic             TEXT,
    sic_description TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE tickers (
    ticker            TEXT PRIMARY KEY,
    cik               INTEGER,
    name              TEXT,
    exchange          TEXT,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    sector_etf        TEXT,
    industry_etf      TEXT,
    updated_at        TEXT NOT NULL
);

CREATE TABLE index_snapshots (
    index_name        TEXT NOT NULL,      -- 'sp500' | 'sp400'
    snapshot_date     TEXT NOT NULL,      -- the date the revision represents
    revision_id       INTEGER NOT NULL,
    ticker            TEXT NOT NULL,
    security          TEXT,
    gics_sector       TEXT,
    gics_sub_industry TEXT,
    date_added        TEXT,
    PRIMARY KEY (index_name, snapshot_date, ticker)
);

CREATE TABLE index_membership (
    index_name TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT,                      -- exclusive; NULL means still a member
    PRIMARY KEY (index_name, ticker, start_date)
);

CREATE TABLE young_listings (
    ticker           TEXT PRIMARY KEY,
    cik              INTEGER,
    first_trade_date TEXT NOT NULL,
    screened_on      TEXT NOT NULL
);

CREATE TABLE prices (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL NOT NULL,
    adj_close REAL,
    volume    REAL,
    source    TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
) WITHOUT ROWID;

CREATE TABLE facts (
    cik          INTEGER NOT NULL,
    concept      TEXT NOT NULL,
    tag          TEXT NOT NULL,           -- 'us-gaap:Revenues', 'dei:EntityCommonStockSharesOutstanding'
    unit         TEXT NOT NULL,
    period_start TEXT NOT NULL DEFAULT '',-- '' for instant facts
    period_end   TEXT NOT NULL,
    value        REAL NOT NULL,
    filed        TEXT NOT NULL,
    form         TEXT,
    accession    TEXT NOT NULL,
    source       TEXT NOT NULL,           -- 'companyfacts' | 'instance'
    PRIMARY KEY (cik, concept, tag, period_start, period_end, accession)
) WITHOUT ROWID;

CREATE TABLE filings (
    accession        TEXT PRIMARY KEY,
    cik              INTEGER NOT NULL,
    form             TEXT NOT NULL,
    filing_date      TEXT NOT NULL,
    report_date      TEXT,
    items            TEXT,
    primary_document TEXT
);
CREATE INDEX filings_by_cik ON filings (cik, form, filing_date);

CREATE TABLE insider_transactions (
    accession         TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    issuer_cik        INTEGER NOT NULL,
    filing_date       TEXT NOT NULL,
    owner_names       TEXT,
    relationship      TEXT,               -- e.g. 'Director,Officer'
    officer_title     TEXT,
    trans_date        TEXT,
    trans_code        TEXT,               -- 'P' = open-market purchase
    shares            REAL,
    price             REAL,
    acquired_disposed TEXT,
    source            TEXT NOT NULL,      -- 'bulk' | 'xml'
    PRIMARY KEY (accession, seq)
);
CREATE INDEX insider_by_issuer ON insider_transactions (issuer_cik, trans_code, trans_date);

CREATE TABLE macro (
    series_id TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (series_id, date)
) WITHOUT ROWID;

CREATE TABLE provider_status (
    provider         TEXT PRIMARY KEY,
    last_success     TEXT NOT NULL,
    latest_data_date TEXT,
    detail           TEXT
);

CREATE TABLE fetch_failures (
    run_id TEXT NOT NULL,
    step   TEXT NOT NULL,
    item   TEXT NOT NULL,
    error  TEXT NOT NULL,
    at     TEXT NOT NULL
);

CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

- [ ] **Step 4: Implement the store module**

`src/analyzer/store/db.py`:

```python
"""SQLite connection, numbered SQL migrations and small write helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migration_files() -> list[tuple[int, str, str]]:
    folder = resources.files("analyzer.store") / "migrations"
    found = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.name, entry.read_text()))
    return sorted(found)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply migrations not yet recorded in schema_migrations. Each runs in one transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    applied = 0
    for version, name, sql in _migration_files():
        if version in done:
            continue
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        conn.executescript(
            f"BEGIN;\n{sql}\n"
            f"INSERT INTO schema_migrations VALUES ({version}, '{name}', '{stamp}');\nCOMMIT;"
        )
        applied += 1
    return applied


def upsert_rows(
    conn: sqlite3.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence]
) -> None:
    placeholders = ", ".join("?" * len(columns))
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with conn:
        conn.executemany(sql, rows)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    upsert_rows(conn, "sync_state", ["key", "value"], [(key, value)])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/store -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/store tests/store
git commit -m "feat: add SQLite store with WAL and numbered migrations"
```

---

### Task 3: HTTP client with disk cache, rate limit and retry

**Files:**
- Create: `src/analyzer/data/__init__.py` (empty), `src/analyzer/data/http.py`, `src/analyzer/data/clients.py`, `tests/helpers.py`, `tests/data/__init__.py` (empty), `tests/data/test_http.py`

**Interfaces:**
- Consumes: `Settings` (Task 1).
- Produces:
  - `NotFound(Exception)`, `FetchError(Exception)`.
  - `HttpClient(cache_dir: Path, user_agent: str, *, min_interval: float = 0.0, max_retries: int = 4, timeout: float = 30.0, session=None, sleep=time.sleep, clock=time.monotonic, now=time.time, extra_headers: dict[str, str] | None = None)` with `get_bytes(url, *, params=None, cache_key: str | None = None, max_age: timedelta | None = None) -> bytes`, `get_text(...) -> str`, `get_json(...) -> Any`, `cache_path(key) -> Path`. `cache_key=None` means no caching. `max_age=None` means the cached copy never expires.
  - `Clients(sec, wiki, nasdaq, fred)` and `make_clients(settings: Settings) -> Clients`.
  - Test helpers in `tests/helpers.py`: `FakeResponse`, `FakeSession`, `fake_client(tmp_path, *, responses=None, handler=None, **kwargs) -> tuple[HttpClient, FakeSession]`.

- [ ] **Step 1: Write the test helpers**

`tests/helpers.py`:

```python
"""Shared test doubles. No test may touch the network."""

from __future__ import annotations

import json

from analyzer.data.http import HttpClient


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes | str = b"", json_data=None):
        if json_data is not None:
            content = json.dumps(json_data)
        self.status_code = status_code
        self.content = content.encode() if isinstance(content, str) else content


class FakeSession:
    """Stands in for requests.Session.

    Give either `responses` (served in order; an Exception instance is raised) or
    `handler(url, params) -> FakeResponse` to route by URL.
    """

    def __init__(self, responses=None, handler=None):
        self.responses = list(responses or [])
        self.handler = handler
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append({"url": url, "params": params, "headers": dict(headers or {})})
        r = self.handler(url, params) if self.handler else self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(tmp_path, *, responses=None, handler=None, **kwargs):
    session = FakeSession(responses, handler)
    client = HttpClient(
        tmp_path / "cache", "Test test@example.com", session=session, sleep=lambda s: None, **kwargs
    )
    return client, session
```

- [ ] **Step 2: Write the failing tests**

`tests/data/test_http.py`:

```python
import os
from datetime import timedelta

import pytest
import requests

from analyzer.data.http import FetchError, HttpClient, NotFound
from tests.helpers import FakeResponse, FakeSession, fake_client


def test_sends_user_agent_and_returns_body(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(200, b"hello")])
    assert client.get_bytes("https://x.test/a") == b"hello"
    assert session.calls[0]["headers"]["User-Agent"] == "Test test@example.com"


def test_cache_without_max_age_never_expires(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(200, b"v1")])
    assert client.get_text("https://x.test/a", cache_key="k/a") == "v1"
    assert client.get_text("https://x.test/a", cache_key="k/a") == "v1"
    assert len(session.calls) == 1


def test_cache_expires_after_max_age(tmp_path):
    client, session = fake_client(
        tmp_path, responses=[FakeResponse(200, b"v1"), FakeResponse(200, b"v2")]
    )
    client.get_text("https://x.test/a", cache_key="k/a", max_age=timedelta(hours=1))
    old = client.cache_path("k/a").stat().st_mtime - 7200
    os.utime(client.cache_path("k/a"), (old, old))
    assert client.get_text("https://x.test/a", cache_key="k/a", max_age=timedelta(hours=1)) == "v2"


def test_get_json(tmp_path):
    client, _ = fake_client(tmp_path, responses=[FakeResponse(200, json_data={"a": 1})])
    assert client.get_json("https://x.test/a") == {"a": 1}


def test_retries_server_errors_with_backoff(tmp_path):
    sleeps = []
    session = FakeSession([FakeResponse(503), requests.ConnectionError("x"), FakeResponse(200, b"ok")])
    client = HttpClient(tmp_path, "UA a@example.com", session=session, sleep=sleeps.append)
    assert client.get_bytes("https://x.test/a") == b"ok"
    assert sleeps == [1, 2]


def test_404_raises_not_found_without_retry(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(404)])
    with pytest.raises(NotFound):
        client.get_bytes("https://x.test/missing")
    assert len(session.calls) == 1


def test_gives_up_after_max_retries(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(500)] * 3, max_retries=2)
    with pytest.raises(FetchError, match="HTTP 500"):
        client.get_bytes("https://x.test/a")
    assert len(session.calls) == 3


def test_error_message_omits_query_params(tmp_path):
    client, _ = fake_client(tmp_path, responses=[FakeResponse(400)])
    with pytest.raises(FetchError) as exc:
        client.get_bytes("https://x.test/a", params={"api_key": "SECRET123"})
    assert "SECRET123" not in str(exc.value)


def test_throttles_to_min_interval(tmp_path):
    sleeps = []
    session = FakeSession([FakeResponse(200, b"1"), FakeResponse(200, b"2")])
    client = HttpClient(
        tmp_path, "UA a@example.com", session=session, sleep=sleeps.append,
        clock=lambda: 100.0, min_interval=0.125,
    )
    client.get_bytes("https://x.test/1")
    client.get_bytes("https://x.test/2")
    assert sleeps == [0.125]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.data'`.

- [ ] **Step 4: Implement the client**

`src/analyzer/data/http.py`:

```python
"""The single way the engine talks to the network: disk cache, rate limit, retry."""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class NotFound(Exception):
    """The server answered 404."""


class FetchError(Exception):
    """The request failed after all retries. The message never includes query parameters."""


class HttpClient:
    def __init__(
        self,
        cache_dir: Path,
        user_agent: str,
        *,
        min_interval: float = 0.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], float] = time.time,
        extra_headers: dict[str, str] | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.headers = {"User-Agent": user_agent, **(extra_headers or {})}
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.now = now
        self._last_request: float | None = None

    def cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.gz"

    def get_bytes(
        self,
        url: str,
        *,
        params: dict | None = None,
        cache_key: str | None = None,
        max_age: timedelta | None = None,
    ) -> bytes:
        if cache_key is not None:
            cached = self._read_cache(cache_key, max_age)
            if cached is not None:
                return cached
        body = self._fetch(url, params)
        if cache_key is not None:
            self._write_cache(cache_key, body)
        return body

    def get_text(self, url: str, **kwargs) -> str:
        return self.get_bytes(url, **kwargs).decode("utf-8", errors="replace")

    def get_json(self, url: str, **kwargs) -> Any:
        return json.loads(self.get_bytes(url, **kwargs))

    def _read_cache(self, key: str, max_age: timedelta | None) -> bytes | None:
        path = self.cache_path(key)
        if not path.exists():
            return None
        if max_age is not None and self.now() - path.stat().st_mtime >= max_age.total_seconds():
            return None
        return gzip.decompress(path.read_bytes())

    def _write_cache(self, key: str, body: bytes) -> None:
        path = self.cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(gzip.compress(body))
        os.replace(tmp, path)

    def _throttle(self) -> None:
        if self._last_request is not None and self.min_interval > 0:
            wait = self.min_interval - (self.clock() - self._last_request)
            if wait > 0:
                self.sleep(wait)
        self._last_request = self.clock()

    def _fetch(self, url: str, params: dict | None) -> bytes:
        error = "no attempt made"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(
                    url, params=params, headers=self.headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                error = type(exc).__name__
            else:
                if resp.status_code == 404:
                    raise NotFound(url)
                if resp.status_code < 400:
                    return resp.content
                error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUSES:
                    break
            if attempt < self.max_retries:
                log.debug("retrying %s after %s", url, error)
                self.sleep(2**attempt)
        raise FetchError(f"{error} for {url}")
```

`src/analyzer/data/clients.py`:

```python
"""One HttpClient per source, each with its own rate limit and headers."""

from __future__ import annotations

from dataclasses import dataclass

from analyzer.config import Settings
from analyzer.data.http import HttpClient

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
PLAIN_UA = "stock-analyzer/0.1 (personal non-commercial research)"


@dataclass(frozen=True)
class Clients:
    sec: HttpClient
    wiki: HttpClient
    nasdaq: HttpClient
    fred: HttpClient


def make_clients(settings: Settings) -> Clients:
    cache = settings.cache_dir
    return Clients(
        sec=HttpClient(cache, settings.sec_user_agent, min_interval=0.125),  # SEC: <= 10 req/s
        wiki=HttpClient(cache, PLAIN_UA, min_interval=0.2),
        nasdaq=HttpClient(
            cache, BROWSER_UA, min_interval=0.3, extra_headers={"Accept": "application/json"}
        ),
        fred=HttpClient(cache, PLAIN_UA, min_interval=0.6),  # FRED: 120 req/min
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_http.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data tests/helpers.py tests/data
git commit -m "feat: add cached, rate-limited HTTP client with retry"
```

---

### Task 4: Price providers and the fallback chain

**Files:**
- Create: `src/analyzer/data/prices.py`, `tests/data/test_prices.py`
- Modify: `docs/specs/2026-10-10-stock-analyzer-design.md` (section 3 table: daily prices fallback = Nasdaq public endpoint)

**Interfaces:**
- Consumes: `HttpClient`, `FetchError`, `NotFound` (Task 3).
- Produces:
  - `PRICE_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]`. `date` is a tz-naive `Timestamp` at midnight; rows are sorted ascending and have a non-NaN `close`; `adj_close` may be NaN (Nasdaq).
  - `PriceProvider` protocol: attribute `name: str`, method `fetch(symbols: Sequence[str], start: date, end: date) -> dict[str, pd.DataFrame]`. Symbols with no data are left out of the dict. `end` is inclusive.
  - `YFinancePrices(download=None, batch_size=100)`, name `"yfinance"`.
  - `NasdaqPrices(client: HttpClient)`, name `"nasdaq"`.
  - `FetchResult(frames: dict[str, DataFrame], sources: dict[str, str], missing: list[str])` and `fetch_with_fallback(providers, symbols, start, end) -> FetchResult`.

- [ ] **Step 1: Write the failing tests**

`tests/data/test_prices.py`:

```python
from datetime import date

import pandas as pd

from analyzer.data.prices import (
    PRICE_COLUMNS,
    NasdaqPrices,
    YFinancePrices,
    fetch_with_fallback,
)
from tests.helpers import FakeResponse, fake_client

FIELDS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def _yf_frame():
    idx = pd.DatetimeIndex(["2026-10-08", "2026-10-09"], name="Date")
    cols = pd.MultiIndex.from_product([["AAPL", "DEAD"], FIELDS])
    nan = float("nan")
    rows = [
        [1.0, 2.0, 0.5, 1.5, 1.4, 100, nan, nan, nan, nan, nan, nan],
        [2.0, 3.0, 1.5, 2.5, 2.4, 200, nan, nan, nan, nan, nan, nan],
    ]
    return pd.DataFrame(rows, index=idx, columns=cols)


def test_yfinance_splits_batch_and_drops_missing_symbols():
    calls = []

    def download(symbols, start, end):
        calls.append(list(symbols))
        return _yf_frame()

    out = YFinancePrices(download=download).fetch(["AAPL", "DEAD"], date(2026, 10, 1), date(2026, 10, 9))
    assert list(out) == ["AAPL"]
    f = out["AAPL"]
    assert list(f.columns) == PRICE_COLUMNS
    assert f["date"].tolist() == [pd.Timestamp("2026-10-08"), pd.Timestamp("2026-10-09")]
    assert f["adj_close"].tolist() == [1.4, 2.4]
    assert calls == [["AAPL", "DEAD"]]


def test_yfinance_batches_symbols():
    calls = []

    def download(symbols, start, end):
        calls.append(list(symbols))
        return pd.DataFrame()

    YFinancePrices(download=download, batch_size=2).fetch(["A", "B", "C"], date(2026, 1, 1), date(2026, 1, 2))
    assert calls == [["A", "B"], ["C"]]


def test_yfinance_batch_error_is_skipped():
    def boom(*args):
        raise RuntimeError("rate limited")

    assert YFinancePrices(download=boom).fetch(["AAPL"], date(2026, 1, 1), date(2026, 1, 2)) == {}


NASDAQ_ROWS = {
    "data": {
        "symbol": "BRK/B",
        "tradesTable": {
            "rows": [
                {"date": "10/09/2026", "close": "$1,515.62", "volume": "4,306,143",
                 "open": "$1,500.00", "high": "$1,520.10", "low": "$1,499.50"},
                {"date": "10/08/2026", "close": "$1,510.00", "volume": "N/A",
                 "open": "$1,505.00", "high": "$1,512.00", "low": "$1,501.00"},
            ]
        },
    }
}
NOT_FOUND = {"data": None, "status": {"rCode": 400}}


def test_nasdaq_parses_rows_and_maps_class_shares(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(json_data=NASDAQ_ROWS)])
    out = NasdaqPrices(client).fetch(["BRK-B"], date(2026, 10, 1), date(2026, 10, 9))
    f = out["BRK-B"]
    assert session.calls[0]["url"].endswith("/BRK.B/historical")
    assert session.calls[0]["params"]["assetclass"] == "stocks"
    assert f["date"].tolist() == [pd.Timestamp("2026-10-08"), pd.Timestamp("2026-10-09")]
    assert f["close"].tolist() == [1510.0, 1515.62]
    assert f["volume"].isna().tolist() == [True, False]
    assert f["adj_close"].isna().all()


def test_nasdaq_falls_back_to_etf_asset_class(tmp_path):
    client, session = fake_client(
        tmp_path, responses=[FakeResponse(json_data=NOT_FOUND), FakeResponse(json_data=NASDAQ_ROWS)]
    )
    out = NasdaqPrices(client).fetch(["SMH"], date(2026, 10, 1), date(2026, 10, 9))
    assert "SMH" in out
    assert [c["params"]["assetclass"] for c in session.calls] == ["stocks", "etf"]


def test_nasdaq_unknown_symbol_is_left_out(tmp_path):
    client, _ = fake_client(
        tmp_path, responses=[FakeResponse(json_data=NOT_FOUND), FakeResponse(json_data=NOT_FOUND)]
    )
    assert NasdaqPrices(client).fetch(["ZZZZQ"], date(2026, 10, 1), date(2026, 10, 9)) == {}


class StubProvider:
    def __init__(self, name, symbols):
        self.name = name
        self.symbols = set(symbols)
        self.asked = []

    def fetch(self, symbols, start, end):
        self.asked.append(list(symbols))
        frame = pd.DataFrame({c: [1.0] for c in PRICE_COLUMNS})
        frame["date"] = pd.Timestamp("2026-10-09")
        return {s: frame for s in symbols if s in self.symbols}


def test_fallback_chain_asks_next_provider_only_for_missing_symbols():
    a, b = StubProvider("a", ["X"]), StubProvider("b", ["Y"])
    res = fetch_with_fallback([a, b], ["X", "Y", "Z"], date(2026, 10, 1), date(2026, 10, 9))
    assert res.sources == {"X": "a", "Y": "b"}
    assert res.missing == ["Z"]
    assert b.asked == [["Y", "Z"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_prices.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.data.prices'`.

- [ ] **Step 3: Implement the providers**

`src/analyzer/data/prices.py`:

```python
"""Daily OHLCV providers. yfinance is primary; the Nasdaq public endpoint is the fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from analyzer.data.http import FetchError, HttpClient, NotFound

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


class PriceProvider(Protocol):
    name: str

    def fetch(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]: ...


def _yf_download(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
        auto_adjust=False,
        actions=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )


def _normalize_dates(values) -> pd.Series:
    d = pd.to_datetime(pd.Series(values))
    if d.dt.tz is not None:
        d = d.dt.tz_convert(None)
    return d.dt.normalize().astype("datetime64[ns]")


def _split_yf_frame(raw: pd.DataFrame, symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty or not isinstance(raw.columns, pd.MultiIndex):
        return out
    present = set(raw.columns.get_level_values(0))
    for sym in symbols:
        if sym not in present:
            continue
        f = raw[sym].rename(
            columns={"Open": "open", "High": "high", "Low": "low", "Close": "close",
                     "Adj Close": "adj_close", "Volume": "volume"}
        )
        if "adj_close" not in f:
            f["adj_close"] = f["close"]
        f = f.dropna(subset=["close"])
        if f.empty:
            continue
        f = f.reset_index()
        f["date"] = _normalize_dates(f.iloc[:, 0])
        out[sym] = f[PRICE_COLUMNS].sort_values("date").reset_index(drop=True)
    return out


class YFinancePrices:
    name = "yfinance"

    def __init__(self, download: Callable | None = None, batch_size: int = 100):
        self._download = download or _yf_download
        self.batch_size = batch_size

    def fetch(self, symbols, start, end):
        out: dict[str, pd.DataFrame] = {}
        symbols = list(symbols)
        for i in range(0, len(symbols), self.batch_size):
            batch = symbols[i : i + self.batch_size]
            try:
                raw = self._download(batch, start, end)
            except Exception as exc:  # yfinance raises many unrelated exception types
                log.warning("yfinance batch of %d failed: %s", len(batch), exc)
                continue
            out.update(_split_yf_frame(raw, batch))
        return out


NASDAQ_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"


def _num(text) -> float:
    t = str(text).replace("$", "").replace(",", "").strip()
    return float(t) if t and t.upper() != "N/A" else float("nan")


def _parse_nasdaq_rows(rows: list[dict]) -> pd.DataFrame:
    f = pd.DataFrame(
        {
            "date": _normalize_dates(pd.to_datetime([r["date"] for r in rows], format="%m/%d/%Y")),
            "open": [_num(r.get("open")) for r in rows],
            "high": [_num(r.get("high")) for r in rows],
            "low": [_num(r.get("low")) for r in rows],
            "close": [_num(r.get("close")) for r in rows],
            "adj_close": float("nan"),  # Nasdaq is split-adjusted only
            "volume": [_num(r.get("volume")) for r in rows],
        }
    )
    return f.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)[PRICE_COLUMNS]


class NasdaqPrices:
    name = "nasdaq"

    def __init__(self, client: HttpClient):
        self.client = client

    def fetch(self, symbols, start, end):
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                frame = self._fetch_one(sym, start, end)
            except (FetchError, NotFound, ValueError, KeyError, TypeError) as exc:
                log.warning("nasdaq %s failed: %s", sym, exc)
                continue
            if frame is not None and not frame.empty:
                out[sym] = frame
        return out

    def _fetch_one(self, sym: str, start: date, end: date) -> pd.DataFrame | None:
        url = NASDAQ_URL.format(symbol=sym.replace("-", "."))  # BRK-B -> BRK.B
        for asset_class in ("stocks", "etf"):
            body = self.client.get_json(
                url,
                params={"assetclass": asset_class, "fromdate": start.isoformat(),
                        "todate": end.isoformat(), "limit": 9999},
            )
            rows = ((body.get("data") or {}).get("tradesTable") or {}).get("rows") or []
            if rows:
                return _parse_nasdaq_rows(rows)
        return None


@dataclass
class FetchResult:
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def fetch_with_fallback(
    providers: Sequence[PriceProvider], symbols: Sequence[str], start: date, end: date
) -> FetchResult:
    result = FetchResult()
    remaining = list(symbols)
    for provider in providers:
        if not remaining:
            break
        got = provider.fetch(remaining, start, end)
        for sym, frame in got.items():
            result.frames[sym] = frame
            result.sources[sym] = provider.name
        remaining = [s for s in remaining if s not in got]
    result.missing = remaining
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_prices.py -v`
Expected: 7 passed.

- [ ] **Step 5: Record the fallback decision in the spec**

In `docs/specs/2026-10-10-stock-analyzer-design.md`, section 3, replace the row

```
| Daily prices fallback | To be chosen by the phase 1 probe | Candidates: Tiingo free tier, Nasdaq public endpoint. |
```

with

```
| Daily prices fallback | Nasdaq public endpoint (`api.nasdaq.com/api/quote/{SYMBOL}/historical`) | Chosen by the 2026-10-10 probe: no key, stocks and ETFs, split-adjusted OHLCV, about 10 years. Class shares use a dot (`BRK.B`). |
```

and in section 14 replace "Which price fallback to use: settled by the phase 1 probe." with "Price fallback: Nasdaq public endpoint (decided 2026-10-10)."

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data/prices.py tests/data/test_prices.py docs/specs
git commit -m "feat: add yfinance and Nasdaq price providers with fallback chain"
```

---

### Task 5: Price storage and incremental sync

**Files:**
- Create: `src/analyzer/data/price_sync.py`, `tests/data/test_price_sync.py`
- Modify: `tests/helpers.py` (add `bars`, `FakeProvider`)

**Interfaces:**
- Consumes: `PriceProvider`, `fetch_with_fallback`, `PRICE_COLUMNS` (Task 4); `upsert_rows` (Task 2).
- Produces:
  - `HISTORY_START = date(2009, 1, 1)`, `OVERLAP_DAYS = 14`, `ADJ_TOLERANCE = 1e-4`.
  - `read_prices(conn, symbol, start: date | None = None, end: date | None = None) -> DataFrame` with `PRICE_COLUMNS + ["source"]`.
  - `write_prices(conn, symbol, frame, source) -> None` (upsert), `replace_prices(conn, symbol, frame, source) -> None` (delete all rows of the symbol, then insert, in one transaction).
  - `last_dates(conn, symbols) -> dict[str, date]`, `latest_market_date(conn) -> date | None` (latest SPY bar).
  - `PriceSyncReport(updated, refetched, via_fallback: dict[str, str], missing, historical_missing)`.
  - `sync_prices(conn, providers, active: Sequence[str], historical: Sequence[str], today: date, history_start: date = HISTORY_START) -> PriceSyncReport`. **Active** symbols are refreshed every run. **Historical** symbols (past index members) are fetched once when first seen and never refreshed; their misses are reported separately and don't count as failures.

Behavior:
1. Symbols never stored (or active symbols whose last bar is older than 365 days) get full history from `history_start` through the fallback chain.
2. Active symbols already stored are fetched from `min(last date) − OVERLAP_DAYS` through the chain.
3. For primary-source data, overlapping days (excluding the most recent stored day, which may be a partial intraday bar) are compared with what is stored. A relative difference above `ADJ_TOLERANCE` in `close` or `adj_close` means a split or dividend changed the adjusted history, so that symbol's full history is re-downloaded from the primary provider and replaces the stored rows.
4. Rows from a fallback provider without `adj_close` get `adj_close = close × (adj_close / close of the latest stored row)`, or `close` when nothing is stored.

- [ ] **Step 1: Add test helpers**

Add these imports to the import block at the top of `tests/helpers.py` (keep imports at the top, or ruff reports E402):

```python
import pandas as pd

from analyzer.data.prices import PRICE_COLUMNS
```

Then append:

```python
def bars(dates, closes, adj=None, volume=1_000_000.0):
    """A price frame in provider format. `adj` defaults to `closes`."""
    adj = closes if adj is None else adj
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": adj,
            "volume": volume,
        }
    )[PRICE_COLUMNS]


class FakeProvider:
    """PriceProvider over in-memory frames, filtered to the requested window."""

    def __init__(self, name, data):
        self.name = name
        self.data = dict(data)
        self.calls = []

    def fetch(self, symbols, start, end):
        self.calls.append((tuple(symbols), start, end))
        out = {}
        for s in symbols:
            f = self.data.get(s)
            if f is None:
                continue
            f = f[(f["date"] >= pd.Timestamp(start)) & (f["date"] <= pd.Timestamp(end))]
            if not f.empty:
                out[s] = f.reset_index(drop=True)
        return out
```

- [ ] **Step 2: Write the failing tests**

`tests/data/test_price_sync.py`:

```python
from datetime import date

import pytest

from analyzer.data.price_sync import (
    last_dates,
    latest_market_date,
    read_prices,
    replace_prices,
    sync_prices,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeProvider, bars

TODAY = date(2026, 10, 9)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_new_symbols_get_full_history_with_fallback(conn):
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-08", "2026-10-09"], [10, 11])})
    backup = FakeProvider("nasdaq", {"SMH": bars(["2026-10-09"], [600], adj=[float("nan")])})
    rep = sync_prices(conn, [primary, backup], ["AAPL", "SMH", "GONE"], ["OLD"], TODAY)
    assert sorted(rep.updated) == ["AAPL", "SMH"]
    assert rep.via_fallback == {"SMH": "nasdaq"}
    assert rep.missing == ["GONE"]
    assert rep.historical_missing == ["OLD"]
    smh = read_prices(conn, "SMH")
    assert smh["adj_close"].tolist() == [600.0]  # nothing stored before: adj = close
    assert smh["source"].tolist() == ["nasdaq"]


def test_incremental_appends_new_days(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-01", "2026-10-02", "2026-10-05"], [10, 11, 12])})
    rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert rep.updated == ["AAPL"] and rep.refetched == []
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11, 12]
    assert primary.calls[0][1] == date(2026, 9, 18)  # last date − 14 days


def test_dividend_adjustment_triggers_full_refetch(conn):
    replace_prices(conn, "KO", bars(["2026-09-30", "2026-10-01", "2026-10-02"], [60, 61, 62]), "yfinance")
    new_history = bars(["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05"], [60, 61, 62, 63],
                       adj=[59.4, 60.4, 61.4, 63])
    primary = FakeProvider("yfinance", {"KO": new_history})
    rep = sync_prices(conn, [primary], ["KO"], [], TODAY)
    assert rep.refetched == ["KO"]
    assert read_prices(conn, "KO")["adj_close"].tolist() == [59.4, 60.4, 61.4, 63]


def test_partial_last_bar_does_not_trigger_refetch(conn):
    # Stored 10-02 bar was captured intraday at 11.0; the final close is 11.5.
    replace_prices(conn, "AAPL", bars(["2026-10-01", "2026-10-02"], [10, 11]), "yfinance")
    primary = FakeProvider("yfinance", {"AAPL": bars(["2026-10-01", "2026-10-02"], [10, 11.5])})
    rep = sync_prices(conn, [primary], ["AAPL"], [], TODAY)
    assert rep.refetched == []
    assert read_prices(conn, "AAPL")["close"].tolist() == [10, 11.5]


def test_fallback_rows_reuse_latest_adjustment_ratio(conn):
    replace_prices(conn, "AAPL", bars(["2026-10-01"], [100], adj=[99]), "yfinance")
    primary = FakeProvider("yfinance", {})
    backup = FakeProvider("nasdaq", {"AAPL": bars(["2026-10-02"], [110], adj=[float("nan")])})
    rep = sync_prices(conn, [primary, backup], ["AAPL"], [], TODAY)
    assert rep.via_fallback == {"AAPL": "nasdaq"}
    assert read_prices(conn, "AAPL")["adj_close"].round(4).tolist() == [99.0, 108.9]


def test_historical_symbols_are_not_refreshed(conn):
    replace_prices(conn, "OLD", bars(["2015-01-02"], [5]), "yfinance")
    primary = FakeProvider("yfinance", {"OLD": bars(["2015-01-02", "2026-10-01"], [5, 6])})
    sync_prices(conn, [primary], [], ["OLD"], TODAY)
    assert primary.calls == []


def test_last_and_market_dates(conn):
    replace_prices(conn, "SPY", bars(["2026-10-08", "2026-10-09"], [600, 601]), "yfinance")
    assert last_dates(conn, ["SPY", "X"]) == {"SPY": date(2026, 10, 9)}
    assert latest_market_date(conn) == date(2026, 10, 9)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_price_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.data.price_sync'`.

- [ ] **Step 4: Implement storage and sync**

`src/analyzer/data/price_sync.py`:

```python
"""Store daily prices and keep them current with the fewest downloads."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.prices import PRICE_COLUMNS, PriceProvider, fetch_with_fallback
from analyzer.store.db import upsert_rows

HISTORY_START = date(2009, 1, 1)
OVERLAP_DAYS = 14
ADJ_TOLERANCE = 1e-4
STALE_AFTER_DAYS = 365

_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]


def _iso(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


def _clean(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def _rows(symbol: str, frame: pd.DataFrame, source: str):
    for r in frame.itertuples(index=False):
        yield (symbol, _iso(r.date), _clean(r.open), _clean(r.high), _clean(r.low),
               float(r.close), _clean(r.adj_close), _clean(r.volume), source)


def read_prices(conn: sqlite3.Connection, symbol: str, start=None, end=None) -> pd.DataFrame:
    sql = ("SELECT date, open, high, low, close, adj_close, volume, source "
           "FROM prices WHERE symbol = ?")
    params: list = [symbol]
    if start is not None:
        sql += " AND date >= ?"
        params.append(_iso(start))
    if end is not None:
        sql += " AND date <= ?"
        params.append(_iso(end))
    df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
    df["date"] = pd.to_datetime(df["date"])
    return df


def write_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    upsert_rows(conn, "prices", _COLUMNS, _rows(symbol, frame, source))


def replace_prices(conn, symbol: str, frame: pd.DataFrame, source: str) -> None:
    with conn:
        conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))
        conn.executemany(
            f"INSERT INTO prices ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
            list(_rows(symbol, frame, source)),
        )


def last_dates(conn, symbols: Sequence[str]) -> dict[str, date]:
    wanted = set(symbols)
    rows = conn.execute("SELECT symbol, MAX(date) FROM prices GROUP BY symbol").fetchall()
    return {s: date.fromisoformat(d) for s, d in rows if s in wanted}


def latest_market_date(conn) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM prices WHERE symbol = 'SPY'").fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _with_adj(conn, symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    if not frame["adj_close"].isna().any():
        return frame
    row = conn.execute(
        "SELECT close, adj_close FROM prices WHERE symbol = ? AND adj_close IS NOT NULL "
        "AND close > 0 AND date < ? ORDER BY date DESC LIMIT 1",
        (symbol, _iso(frame["date"].min())),
    ).fetchone()
    ratio = row[1] / row[0] if row else 1.0
    frame = frame.copy()
    frame["adj_close"] = frame["adj_close"].fillna(frame["close"] * ratio)
    return frame


def _adjustment_changed(stored: pd.DataFrame, fresh: pd.DataFrame, primary: str) -> bool:
    stored = stored[stored["source"] == primary]
    if stored.empty:
        return False
    stored = stored[stored["date"] < stored["date"].max()]  # last bar may be partial
    merged = stored.merge(fresh[PRICE_COLUMNS], on="date", suffixes=("_old", "_new"))
    if merged.empty:
        return False
    drift = max(
        (merged["close_new"] / merged["close_old"] - 1).abs().max(),
        (merged["adj_close_new"] / merged["adj_close_old"] - 1).abs().max(),
    )
    return bool(drift > ADJ_TOLERANCE)


@dataclass
class PriceSyncReport:
    updated: list[str] = field(default_factory=list)
    refetched: list[str] = field(default_factory=list)
    via_fallback: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    historical_missing: list[str] = field(default_factory=list)


def sync_prices(
    conn,
    providers: Sequence[PriceProvider],
    active: Sequence[str],
    historical: Sequence[str],
    today: date,
    history_start: date = HISTORY_START,
) -> PriceSyncReport:
    primary = providers[0]
    report = PriceSyncReport()
    active_set = set(active)
    everything = list(dict.fromkeys([*active, *historical]))
    known = last_dates(conn, everything)
    stale_cutoff = today - timedelta(days=STALE_AFTER_DAYS)

    full = [s for s in everything
            if s not in known or (s in active_set and known[s] < stale_cutoff)]
    incremental = [s for s in active if s in known and s not in full]

    if full:
        res = fetch_with_fallback(providers, full, history_start, today)
        for sym, frame in res.frames.items():
            replace_prices(conn, sym, _with_adj(conn, sym, frame), res.sources[sym])
            report.updated.append(sym)
            if res.sources[sym] != primary.name:
                report.via_fallback[sym] = res.sources[sym]
        report.missing += [s for s in res.missing if s in active_set]
        report.historical_missing += [s for s in res.missing if s not in active_set]

    if incremental:
        start = min(known[s] for s in incremental) - timedelta(days=OVERLAP_DAYS)
        res = fetch_with_fallback(providers, incremental, start, today)
        stale: list[str] = []
        for sym, frame in res.frames.items():
            source = res.sources[sym]
            if source == primary.name and _adjustment_changed(
                read_prices(conn, sym, start=frame["date"].min()), frame, primary.name
            ):
                stale.append(sym)
                continue
            write_prices(conn, sym, _with_adj(conn, sym, frame), source)
            report.updated.append(sym)
            if source != primary.name:
                report.via_fallback[sym] = source
        report.missing += res.missing
        if stale:
            refreshed = primary.fetch(stale, history_start, today)
            for sym in stale:
                if sym in refreshed:
                    replace_prices(conn, sym, refreshed[sym], primary.name)
                    report.refetched.append(sym)
                else:
                    report.missing.append(sym)
    return report
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data -v`
Expected: all tests in `tests/data` pass (7 new).

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data/price_sync.py tests/data/test_price_sync.py tests/helpers.py
git commit -m "feat: add incremental price sync with adjustment detection and fallback"
```

---

### Task 6: Index membership from Wikipedia revisions

**Files:**
- Create: `src/analyzer/universe/__init__.py` (empty), `src/analyzer/universe/wikipedia.py`, `src/analyzer/universe/membership.py`, `tests/universe/__init__.py` (empty), `tests/universe/test_wikipedia.py`, `tests/universe/test_membership.py`

**Interfaces:**
- Consumes: `HttpClient` (Task 3); `upsert_rows` (Task 2).
- Produces:
  - `membership.INDEXES = ("sp500", "sp400")`, `Interval(ticker: str, start: date, end: date | None)` (end exclusive), `build_membership(snapshots: Sequence[tuple[date, set[str]]], date_added: Mapping[str, date] | None = None) -> list[Interval]`, `rebuild_membership(conn, index_name) -> int`, `members_on(conn, index_name, d: date) -> set[str]`.
  - `wikipedia.PAGES`, `EXPECTED_SIZE`, `SNAPSHOT_START`, `Revision(revid: int, timestamp: str)`, `normalize_ticker(raw) -> str | None`, `parse_constituents(html: str) -> DataFrame[ticker, security, gics_sector, gics_sub_industry, date_added]`, `revisions_before(client, title, when: date, today: date, limit: int = 5) -> list[Revision]`, `revision_html(client, revid) -> str`, `snapshot_for(client, index_name, when, today) -> tuple[Revision, DataFrame]`, `month_starts(start, end) -> list[date]`, `SnapshotSync(attempted: int, failed: list[tuple[str, str]])`, `sync_index_snapshots(conn, client, index_name, today, start: date | None = None) -> SnapshotSync`.

Notes for the implementer:
- Planning probe (2026-10-10): the current S&P 500 article has only a constituents table (`Symbol, Security, GICS Sector, GICS Sub-Industry, Headquarters Location, Date added, CIK, Founded`). Old revisions use other headers: `Ticker symbol`/`Ticker Symbol`, `Company`, `GICS Sub Industry`. Changes tables have two header rows (a pandas `MultiIndex`) and must be ignored.
- One snapshot per month start, plus one for today. Snapshots that are neither a month start nor today are deleted, so daily runs don't pile up rows.
- `pd.read_html` needs `io.StringIO(html)`, not a raw string.

- [ ] **Step 1: Write the failing membership tests**

`tests/universe/test_membership.py`:

```python
from datetime import date

from analyzer.store.db import connect, migrate
from analyzer.universe.membership import Interval, build_membership, members_on

D = date


def test_additions_and_removals():
    snaps = [
        (D(2020, 1, 1), {"A", "B"}),
        (D(2020, 2, 1), {"A", "C"}),
        (D(2020, 3, 1), {"A", "C"}),
    ]
    assert build_membership(snaps) == [
        Interval("A", D(2020, 1, 1), None),
        Interval("B", D(2020, 1, 1), D(2020, 2, 1)),
        Interval("C", D(2020, 2, 1), None),
    ]


def test_date_added_refines_start_within_gap():
    snaps = [(D(2020, 1, 1), {"A"}), (D(2020, 2, 1), {"A", "C"})]
    out = build_membership(snaps, {"C": D(2020, 1, 15), "A": D(1990, 1, 1)})
    assert Interval("C", D(2020, 1, 15), None) in out
    assert Interval("A", D(2020, 1, 1), None) in out  # outside the gap: ignored


def test_readded_ticker_gets_two_intervals():
    snaps = [(D(2020, 1, 1), {"X"}), (D(2020, 2, 1), set()), (D(2020, 3, 1), {"X"})]
    assert build_membership(snaps) == [
        Interval("X", D(2020, 1, 1), D(2020, 2, 1)),
        Interval("X", D(2020, 3, 1), None),
    ]


def test_members_on(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO index_membership VALUES (?,?,?,?)",
        [("sp500", "A", "2020-01-01", None), ("sp500", "B", "2020-01-01", "2020-02-01")],
    )
    assert members_on(conn, "sp500", D(2020, 1, 31)) == {"A", "B"}
    assert members_on(conn, "sp500", D(2020, 2, 1)) == {"A"}
    assert members_on(conn, "sp400", D(2020, 2, 1)) == set()
```

- [ ] **Step 2: Write the failing Wikipedia tests**

`tests/universe/test_wikipedia.py`:

```python
from datetime import date

import pytest

from analyzer.store.db import connect, migrate
from analyzer.universe.membership import members_on
from analyzer.universe.wikipedia import (
    API_URL,
    month_starts,
    normalize_ticker,
    parse_constituents,
    snapshot_for,
    sync_index_snapshots,
)
from tests.helpers import FakeResponse, fake_client


def table(headers, rows):
    th = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{th}</tr>{body}</table>"


def page(tickers, old_headers=False):
    if old_headers:
        headers = ["Ticker symbol", "Company", "GICS Sector", "GICS Sub Industry"]
        rows = [(t, f"{t} Corp", "Information Technology", "Semiconductors") for t in tickers]
    else:
        headers = ["Symbol", "Security", "GICS Sector", "GICS Sub-Industry", "Date added"]
        rows = [(t, f"{t} Corp", "Health Care", "Biotechnology", "2001-08-07") for t in tickers]
    navbox = table(["x"], [("y",)])
    return f"<html><body>{table(headers, rows)}{navbox}</body></html>"


def names(n, offset=0):
    return [f"T{i}" for i in range(offset, offset + n)]


@pytest.mark.parametrize(
    "raw, expected",
    [("brk.b", "BRK-B"), ("GOOGL[3]", "GOOGL"), (" MMM ", "MMM"), ("", None), ("N/A", None)],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


def test_parse_current_layout():
    df = parse_constituents(page(names(60) + ["BRK.B"]))
    assert len(df) == 61
    row = df[df.ticker == "BRK-B"].iloc[0]
    assert row.gics_sector == "Health Care"
    assert row.gics_sub_industry == "Biotechnology"
    assert row.date_added == "2001-08-07"


def test_parse_old_layout():
    df = parse_constituents(page(names(60), old_headers=True))
    assert len(df) == 60
    assert df.iloc[0].gics_sub_industry == "Semiconductors"
    assert df.iloc[0].date_added is None


def test_parse_without_table_raises():
    with pytest.raises(ValueError):
        parse_constituents("<html><body><p>vandalized</p></body></html>")


def test_month_starts():
    assert month_starts(date(2026, 7, 15), date(2026, 10, 10)) == [
        date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)
    ]


def revisions_json(*revids):
    return {"query": {"pages": [{"revisions": [
        {"revid": r, "timestamp": "2026-01-01T00:00:00Z"} for r in revids]}]}}


def test_snapshot_skips_vandalized_revision(tmp_path):
    def handler(url, params):
        if url == API_URL:
            return FakeResponse(json_data=revisions_json(2, 1))
        html = page(names(10)) if params["oldid"] == 2 else page(names(490))
        return FakeResponse(content=html)

    client, _ = fake_client(tmp_path, handler=handler)
    rev, df = snapshot_for(client, "sp500", date(2026, 9, 1), today=date(2026, 10, 10))
    assert rev.revid == 1
    assert len(df) == 490


def test_sync_builds_membership_from_snapshots(tmp_path):
    def handler(url, params):
        if url == API_URL:
            revid = int(params["rvstart"][:10].replace("-", ""))
            return FakeResponse(json_data=revisions_json(revid))
        tickers = names(490) if params["oldid"] < 20260901 else names(490, offset=1)
        return FakeResponse(content=page(tickers))

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    result = sync_index_snapshots(conn, client, "sp500", date(2026, 10, 10), start=date(2026, 8, 1))
    assert result.failed == []
    assert result.attempted == 4  # 08-01, 09-01, 10-01 and today
    assert "T0" in members_on(conn, "sp500", date(2026, 8, 15))
    assert "T0" not in members_on(conn, "sp500", date(2026, 9, 1))
    assert "T490" in members_on(conn, "sp500", date(2026, 9, 1))
    assert len(members_on(conn, "sp500", date(2026, 10, 5))) == 490

    # A later day's run replaces the previous "today" snapshot instead of adding one.
    sync_index_snapshots(conn, client, "sp500", date(2026, 10, 11), start=date(2026, 8, 1))
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM index_snapshots ORDER BY 1")]
    assert dates == ["2026-08-01", "2026-09-01", "2026-10-01", "2026-10-11"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/universe -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.universe'`.

- [ ] **Step 4: Implement membership**

`src/analyzer/universe/membership.py`:

```python
"""Index membership intervals rebuilt from periodic constituent snapshots."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

INDEXES = ("sp500", "sp400")


@dataclass(frozen=True)
class Interval:
    ticker: str
    start: date
    end: date | None  # exclusive; None = still a member


def build_membership(
    snapshots: Sequence[tuple[date, set[str]]], date_added: Mapping[str, date] | None = None
) -> list[Interval]:
    """A ticker is a member from the first snapshot that lists it until the first that doesn't.

    `date_added` (from the current page) moves a start date earlier when it falls between the
    previous snapshot and the one where the ticker first appears.
    """
    date_added = date_added or {}
    open_since: dict[str, date] = {}
    out: list[Interval] = []
    prev: date | None = None
    for d, members in sorted(snapshots, key=lambda s: s[0]):
        for ticker in members - open_since.keys():
            start = d
            added = date_added.get(ticker)
            if added and prev and prev < added <= d:
                start = added
            open_since[ticker] = start
        for ticker in open_since.keys() - members:
            out.append(Interval(ticker, open_since.pop(ticker), d))
        prev = d
    out += [Interval(t, s, None) for t, s in open_since.items()]
    return sorted(out, key=lambda i: (i.ticker, i.start))


def rebuild_membership(conn, index_name: str) -> int:
    snaps: dict[date, set[str]] = defaultdict(set)
    added: dict[str, date] = {}
    rows = conn.execute(
        "SELECT snapshot_date, ticker, date_added FROM index_snapshots WHERE index_name = ? "
        "ORDER BY snapshot_date",
        (index_name,),
    ).fetchall()
    for snap_date, ticker, date_added in rows:
        snaps[date.fromisoformat(snap_date)].add(ticker)
        if date_added:
            added[ticker] = date.fromisoformat(date_added)
    intervals = build_membership(list(snaps.items()), added)
    with conn:
        conn.execute("DELETE FROM index_membership WHERE index_name = ?", (index_name,))
        conn.executemany(
            "INSERT INTO index_membership VALUES (?, ?, ?, ?)",
            [(index_name, i.ticker, i.start.isoformat(), i.end.isoformat() if i.end else None)
             for i in intervals],
        )
    return len(intervals)


def members_on(conn, index_name: str, d: date) -> set[str]:
    rows = conn.execute(
        "SELECT ticker FROM index_membership WHERE index_name = ? AND start_date <= ? "
        "AND (end_date IS NULL OR end_date > ?)",
        (index_name, d.isoformat(), d.isoformat()),
    )
    return {r[0] for r in rows}
```

- [ ] **Step 5: Implement the Wikipedia source**

`src/analyzer/universe/wikipedia.py`:

```python
"""S&P 500/400 constituents from the current and past revisions of the Wikipedia lists."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.http import HttpClient
from analyzer.universe.membership import rebuild_membership

API_URL = "https://en.wikipedia.org/w/api.php"
INDEX_URL = "https://en.wikipedia.org/w/index.php"
PAGES = {"sp500": "List_of_S&P_500_companies", "sp400": "List_of_S&P_400_companies"}
EXPECTED_SIZE = {"sp500": (480, 520), "sp400": (380, 420)}  # sanity bounds per snapshot
SNAPSHOT_START = {"sp500": date(2010, 1, 1), "sp400": date(2013, 1, 1)}


@dataclass(frozen=True)
class Revision:
    revid: int
    timestamp: str


def normalize_ticker(raw) -> str | None:
    t = re.sub(r"\[.*?\]", "", str(raw)).strip().upper().replace(".", "-").replace(" ", "")
    return t if re.fullmatch(r"[A-Z][A-Z0-9-]{0,9}", t) else None


def _find(cols: dict[str, object], *needles: str):
    return next((c for k, c in cols.items() if any(n in k for n in needles)), None)


def _text_column(table: pd.DataFrame, column):
    if column is None:
        return None
    return table[column].astype(str).where(table[column].notna(), None)


def parse_constituents(html: str) -> pd.DataFrame:
    """The first table with >= 50 rows and a symbol/ticker column, in a fixed shape."""
    for t in pd.read_html(io.StringIO(html)):
        if len(t) < 50 or isinstance(t.columns, pd.MultiIndex):
            continue
        cols = {str(c).lower(): c for c in t.columns}
        sym = _find(cols, "symbol", "ticker")
        if sym is None:
            continue
        sec = _find(cols, "security", "company")
        sector = _find(cols, "gics sector")
        sub = _find(cols, "gics sub")
        added = _find(cols, "date added", "date first added")
        out = pd.DataFrame(
            {
                "ticker": t[sym].map(normalize_ticker),
                "security": _text_column(t, sec),
                "gics_sector": _text_column(t, sector),
                "gics_sub_industry": _text_column(t, sub),
                "date_added": (
                    pd.to_datetime(t[added], errors="coerce").dt.strftime("%Y-%m-%d")
                    if added is not None else None
                ),
            }
        )
        out["date_added"] = out["date_added"].where(out["date_added"].notna(), None)
        return out.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)
    raise ValueError("no constituents table found")


def revisions_before(
    client: HttpClient, title: str, when: date, today: date, limit: int = 5
) -> list[Revision]:
    final = when <= today - timedelta(days=2)  # older answers can no longer change
    data = client.get_json(
        API_URL,
        params={"action": "query", "prop": "revisions", "titles": title, "rvlimit": limit,
                "rvstart": f"{when.isoformat()}T00:00:00Z", "rvdir": "older",
                "rvprop": "ids|timestamp", "format": "json", "formatversion": 2},
        cache_key=f"wiki/revisions/{title.replace('&', 'and')}/{when.isoformat()}",
        max_age=None if final else timedelta(hours=12),
    )
    revs = data["query"]["pages"][0].get("revisions", [])
    return [Revision(int(r["revid"]), r["timestamp"]) for r in revs]


def revision_html(client: HttpClient, revid: int) -> str:
    return client.get_text(INDEX_URL, params={"oldid": revid}, cache_key=f"wiki/html/{revid}.html")


def snapshot_for(client, index_name: str, when: date, today: date) -> tuple[Revision, pd.DataFrame]:
    lo, hi = EXPECTED_SIZE[index_name]
    for rev in revisions_before(client, PAGES[index_name], when, today):
        try:
            df = parse_constituents(revision_html(client, rev.revid))
        except ValueError:
            continue
        if lo <= len(df) <= hi:
            return rev, df
    raise ValueError(f"no sane {index_name} revision before {when}")


def month_starts(start: date, end: date) -> list[date]:
    d = start if start.day == 1 else (start.replace(day=1) + timedelta(days=32)).replace(day=1)
    out = []
    while d <= end:
        out.append(d)
        d = (d + timedelta(days=32)).replace(day=1)
    return out


@dataclass
class SnapshotSync:
    attempted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)


def sync_index_snapshots(
    conn, client: HttpClient, index_name: str, today: date, start: date | None = None
) -> SnapshotSync:
    start = start or SNAPSHOT_START[index_name]
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot_date FROM index_snapshots WHERE index_name = ?", (index_name,))}
    targets = [d for d in month_starts(start, today) if d.isoformat() not in have]
    if today.day != 1:
        targets.append(today)
    result = SnapshotSync()
    for d in targets:
        result.attempted += 1
        when = d + timedelta(days=1) if d == today else d  # today's snapshot = latest revision
        try:
            rev, df = snapshot_for(client, index_name, when, today)
        except Exception as exc:  # one bad month must not stop the rest
            result.failed.append((f"{index_name} {d}", f"{type(exc).__name__}: {exc}"))
            continue
        rows = [(index_name, d.isoformat(), rev.revid, r.ticker, r.security, r.gics_sector,
                 r.gics_sub_industry, r.date_added) for r in df.itertuples()]
        with conn:
            conn.execute(
                "DELETE FROM index_snapshots WHERE index_name = ? AND snapshot_date = ?",
                (index_name, d.isoformat()),
            )
            conn.executemany("INSERT INTO index_snapshots VALUES (?,?,?,?,?,?,?,?)", rows)
    with conn:
        conn.execute(
            "DELETE FROM index_snapshots WHERE index_name = ? AND substr(snapshot_date, 9, 2) <> '01' "
            "AND snapshot_date <> ?",
            (index_name, today.isoformat()),
        )
    rebuild_membership(conn, index_name)
    return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/universe -v`
Expected: all pass (4 membership + 10 Wikipedia).

- [ ] **Step 7: Commit**

```bash
git add src/analyzer/universe tests/universe
git commit -m "feat: rebuild S&P 500/400 membership from Wikipedia revision snapshots"
```

---

### Task 7: SEC companies, filings and reference ETFs

**Files:**
- Create: `src/analyzer/data/edgar/__init__.py` (empty), `src/analyzer/data/edgar/companies.py`, `src/analyzer/universe/industry.py`, `src/analyzer/universe/registry.py`, `tests/data/edgar/__init__.py` (empty), `tests/data/edgar/test_companies.py`, `tests/universe/test_industry.py`, `tests/universe/test_registry.py`

**Interfaces:**
- Consumes: `HttpClient`, `NotFound` (Task 3); `upsert_rows` (Task 2); `index_snapshots` rows (Task 6).
- Produces:
  - `companies.TICKERS_URL`, `SUBMISSIONS_URL`, `FILING_FORMS`, `load_ticker_map(sec) -> DataFrame[cik:int, name, ticker, exchange]` (tickers normalized like `normalize_ticker`), `Submissions(cik, name, sic, sic_description, filings: DataFrame[accession, form, filing_date, report_date, items, primary_document])`, `fetch_submissions(sec, cik, *, since=date(2013, 1, 1), max_age=timedelta(hours=20)) -> Submissions`, `store_submissions(conn, subs: Submissions, now: datetime) -> None`.
  - `industry.ReferenceEtfs(sector_etf: str | None, industry_etf: str | None)`, `reference_etfs(gics_sector, gics_sub_industry, sic) -> ReferenceEtfs`, `SECTOR_ETFS`, `SUB_INDUSTRY_ETFS`, `SIC_RULES`, `ALL_REFERENCE_ETFS: list[str]` (includes `SPY`).
  - `registry.refresh_tickers(conn, ticker_map: DataFrame, today: date) -> int` (rows written to `tickers`).

GICS is preferred because it maps exactly to the SPDR sector ETFs. SIC is the fallback for stocks without GICS (young listings, delisted names).

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_companies.py`:

```python
from datetime import date, datetime

from analyzer.data.edgar.companies import (
    SUBMISSIONS_URL,
    fetch_submissions,
    load_ticker_map,
    store_submissions,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

TICKERS = {"fields": ["cik", "name", "ticker", "exchange"],
           "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"],
                    [1067983, "Berkshire Hathaway", "brk.b", "NYSE"],
                    [1, "Dup", "AAPL", "OTC"]]}


def filings_block(rows):
    keys = ["accessionNumber", "form", "filingDate", "reportDate", "items", "primaryDocument"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


SUBMISSIONS = {
    "cik": "320193", "name": "Apple Inc.", "sic": "3571", "sicDescription": "Electronic Computers",
    "filings": {
        "recent": filings_block([
            ("0000320193-26-000100", "10-Q", "2026-08-01", "2026-06-27", "", "aapl-20260627.htm"),
            ("0000320193-26-000090", "8-K", "2026-07-31", "2026-07-31", "2.02,9.01", "a8k.htm"),
            ("0000320193-26-000080", "S-8", "2026-07-01", "", "", "s8.htm"),
        ]),
        "files": [
            {"name": "CIK0000320193-submissions-001.json", "filingFrom": "2012-01-01", "filingTo": "2016-12-31"},
            {"name": "CIK0000320193-submissions-002.json", "filingFrom": "1994-01-01", "filingTo": "2011-12-31"},
        ],
    },
}
OLD_PAGE = filings_block([
    ("0000320193-15-000001", "10-K", "2015-10-28", "2015-09-26", "", "aapl10k.htm"),
    ("0000320193-12-000001", "10-K", "2012-10-31", "2012-09-29", "", "old.htm"),
])


def test_load_ticker_map_normalizes_and_dedupes(tmp_path):
    client, _ = fake_client(tmp_path, responses=[FakeResponse(json_data=TICKERS)])
    df = load_ticker_map(client)
    assert df.ticker.tolist() == ["AAPL", "BRK-B"]
    assert df.set_index("ticker").at["AAPL", "cik"] == 320193


def test_fetch_submissions_reads_recent_and_needed_older_pages(tmp_path):
    def handler(url, params):
        if url == SUBMISSIONS_URL.format(cik=320193):
            return FakeResponse(json_data=SUBMISSIONS)
        assert url.endswith("CIK0000320193-submissions-001.json")  # -002 ends before `since`
        return FakeResponse(json_data=OLD_PAGE)

    client, _ = fake_client(tmp_path, handler=handler)
    subs = fetch_submissions(client, 320193, since=date(2013, 1, 1))
    assert subs.sic == "3571"
    assert subs.filings.accession.tolist() == [
        "0000320193-26-000100", "0000320193-26-000090", "0000320193-15-000001"]
    assert subs.filings.set_index("form").at["8-K", "items"] == "2.02,9.01"


def test_store_submissions(tmp_path):
    def handler(url, params):
        return FakeResponse(json_data=SUBMISSIONS if "CIK0000320193.json" in url else OLD_PAGE)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    store_submissions(conn, fetch_submissions(client, 320193), datetime(2026, 10, 10))
    assert conn.execute("SELECT name, sic FROM companies").fetchall() == [("Apple Inc.", "3571")]
    assert conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0] == 3
```

`tests/universe/test_industry.py`:

```python
import pytest

from analyzer.universe.industry import ALL_REFERENCE_ETFS, ReferenceEtfs, reference_etfs


@pytest.mark.parametrize(
    "sector, sub, sic, expected",
    [
        ("Information Technology", "Semiconductors", None, ReferenceEtfs("XLK", "SMH")),
        ("Information Technology", "Technology Hardware, Storage & Peripherals", "3571",
         ReferenceEtfs("XLK", None)),
        ("Telecommunication Services", None, None, ReferenceEtfs("XLC", None)),
        ("Financials", "Regional Banks", "6022", ReferenceEtfs("XLF", "KRE")),
        (None, None, "3674", ReferenceEtfs("XLK", "SMH")),
        (None, None, "5412", ReferenceEtfs("XLP", None)),
        (None, None, "5651", ReferenceEtfs("XLY", "XRT")),
        (None, None, "6770", ReferenceEtfs(None, None)),
        (None, None, None, ReferenceEtfs(None, None)),
    ],
)
def test_reference_etfs(sector, sub, sic, expected):
    assert reference_etfs(sector, sub, sic) == expected


def test_all_reference_etfs_include_spy_and_sectors():
    assert {"SPY", "XLK", "SMH", "KRE"} <= set(ALL_REFERENCE_ETFS)
```

`tests/universe/test_registry.py`:

```python
from datetime import date

import pandas as pd

from analyzer.store.db import connect, migrate
from analyzer.universe.registry import refresh_tickers


def test_refresh_tickers_combines_wikipedia_sec_and_sic(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany(
        "INSERT INTO index_snapshots VALUES (?,?,?,?,?,?,?,?)",
        [("sp500", "2026-09-01", 1, "NVDA", "Nvidia", "Information Technology", "Semiconductors", None),
         ("sp500", "2026-10-01", 2, "NVDA", "Nvidia", "Information Technology", "Semiconductors", None)],
    )
    conn.execute("INSERT INTO young_listings VALUES ('SNDK', 2023207, '2025-02-24', '2026-10-10')")
    conn.execute("INSERT INTO companies VALUES (2023207, 'Sandisk Corp', '3572', 'Computer Storage', 'x')")
    ticker_map = pd.DataFrame(
        {"cik": [1045810, 2023207], "name": ["NVIDIA CORP", "Sandisk Corp"],
         "ticker": ["NVDA", "SNDK"], "exchange": ["Nasdaq", "Nasdaq"]})
    assert refresh_tickers(conn, ticker_map, date(2026, 10, 10)) == 2
    rows = dict((r[0], r[1:]) for r in conn.execute(
        "SELECT ticker, cik, gics_sector, sector_etf, industry_etf FROM tickers"))
    assert rows["NVDA"] == (1045810, "Information Technology", "XLK", "SMH")
    assert rows["SNDK"] == (2023207, None, "XLK", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar tests/universe/test_industry.py tests/universe/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement SEC companies and filings**

`src/analyzer/data/edgar/companies.py`:

```python
"""SEC ticker map and per-company submissions (name, SIC, filing list)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd

from analyzer.data.http import HttpClient
from analyzer.store.db import upsert_rows

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
FILING_FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A", "10-KT", "4", "4/A"}


def load_ticker_map(sec: HttpClient) -> pd.DataFrame:
    d = sec.get_json(TICKERS_URL, cache_key="sec/company_tickers_exchange.json",
                     max_age=timedelta(days=1))
    df = pd.DataFrame(d["data"], columns=d["fields"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
    df["exchange"] = df["exchange"].fillna("")
    df["cik"] = df["cik"].astype(int)
    return df.drop_duplicates("ticker").reset_index(drop=True)[["cik", "name", "ticker", "exchange"]]


@dataclass
class Submissions:
    cik: int
    name: str | None
    sic: str | None
    sic_description: str | None
    filings: pd.DataFrame


def _filings_frame(block: dict) -> pd.DataFrame:
    n = len(block.get("accessionNumber", []))
    return pd.DataFrame(
        {
            "accession": block.get("accessionNumber", []),
            "form": block.get("form", []),
            "filing_date": block.get("filingDate", []),
            "report_date": block.get("reportDate", [None] * n),
            "items": block.get("items", [""] * n),
            "primary_document": block.get("primaryDocument", [None] * n),
        }
    )


def fetch_submissions(
    sec: HttpClient, cik: int, *, since: date = date(2013, 1, 1),
    max_age: timedelta | None = timedelta(hours=20),
) -> Submissions:
    d = sec.get_json(SUBMISSIONS_URL.format(cik=cik), cache_key=f"sec/submissions/{cik}.json",
                     max_age=max_age)
    frames = [_filings_frame(d["filings"]["recent"])]
    for page in d["filings"].get("files", []):
        if page.get("filingTo", "9999-12-31") < since.isoformat():
            continue
        older = sec.get_json(SUBMISSIONS_PAGE_URL.format(name=page["name"]),
                             cache_key=f"sec/submissions/pages/{page['name']}")
        frames.append(_filings_frame(older))
    f = pd.concat(frames, ignore_index=True)
    f = f[f["form"].isin(FILING_FORMS) & (f["filing_date"] >= since.isoformat())]
    f = f.drop_duplicates("accession").reset_index(drop=True)
    return Submissions(cik, d.get("name"), d.get("sic") or None, d.get("sicDescription") or None, f)


def store_submissions(conn, subs: Submissions, now: datetime) -> None:
    upsert_rows(conn, "companies", ["cik", "name", "sic", "sic_description", "updated_at"],
                [(subs.cik, subs.name, subs.sic, subs.sic_description, now.isoformat())])
    upsert_rows(
        conn, "filings",
        ["accession", "cik", "form", "filing_date", "report_date", "items", "primary_document"],
        [(r.accession, subs.cik, r.form, r.filing_date, r.report_date or None, r.items or None,
          r.primary_document) for r in subs.filings.itertuples()],
    )
```

- [ ] **Step 4: Implement the ETF mapping**

`src/analyzer/universe/industry.py`:

```python
"""Map a stock to its sector ETF and, when one exists, its industry ETF."""

from __future__ import annotations

from dataclasses import dataclass

SECTOR_ETFS = {
    "Information Technology": "XLK",
    "Communication Services": "XLC",
    "Telecommunication Services": "XLC",   # GICS name before 2018
    "Telecommunications Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

SUB_INDUSTRY_ETFS = {
    "Semiconductors": "SMH",
    "Semiconductor Materials & Equipment": "SMH",
    "Semiconductor Equipment": "SMH",
    "Application Software": "IGV",
    "Systems Software": "IGV",
    "Biotechnology": "XBI",
    "Pharmaceuticals": "XPH",
    "Regional Banks": "KRE",
    "Diversified Banks": "KBE",
    "Homebuilding": "ITB",
    "Oil & Gas Exploration & Production": "XOP",
    "Oil & Gas Equipment & Services": "OIH",
    "Aerospace & Defense": "ITA",
    "Health Care Equipment": "IHI",
    "Health Care Supplies": "IHI",
    "Apparel Retail": "XRT",
    "Broadline Retail": "XRT",
    "Home Improvement Retail": "XRT",
    "Other Specialty Retail": "XRT",
    "Computer & Electronics Retail": "XRT",
    "Automotive Retail": "XRT",
    "Steel": "XME",
    "Diversified Metals & Mining": "XME",
    "Copper": "XME",
    "Aluminum": "XME",
    "Gold": "GDX",
    "Passenger Airlines": "JETS",
    "Property & Casualty Insurance": "KIE",
    "Life & Health Insurance": "KIE",
    "Multi-line Insurance": "KIE",
    "Reinsurance": "KIE",
    "Insurance Brokers": "KIE",
}

# (first SIC, last SIC, GICS-style sector, industry ETF). First match wins: specific rules first.
SIC_RULES: list[tuple[int, int, str | None, str | None]] = [
    (6770, 6770, None, None),                      # blank checks (SPACs)
    (3674, 3674, "Information Technology", "SMH"),
    (7372, 7372, "Information Technology", "IGV"),
    (2836, 2836, "Health Care", "XBI"),
    (2834, 2834, "Health Care", "XPH"),
    (3841, 3845, "Health Care", "IHI"),
    (6021, 6022, "Financials", "KRE"),
    (6311, 6411, "Financials", "KIE"),
    (6798, 6798, "Real Estate", None),
    (1311, 1311, "Energy", "XOP"),
    (1381, 1389, "Energy", "OIH"),
    (1040, 1040, "Materials", "GDX"),
    (1000, 1099, "Materials", "XME"),
    (3310, 3399, "Materials", "XME"),
    (1531, 1531, "Consumer Discretionary", "ITB"),
    (3720, 3729, "Industrials", "ITA"),
    (3760, 3769, "Industrials", "ITA"),
    (4512, 4512, "Industrials", "JETS"),
    (3570, 3579, "Information Technology", None),
    (3660, 3699, "Information Technology", None),
    (7370, 7379, "Information Technology", None),
    (1300, 1399, "Energy", None),
    (2900, 2999, "Energy", None),
    (4900, 4999, "Utilities", None),
    (4800, 4899, "Communication Services", None),
    (2000, 2199, "Consumer Staples", None),
    (5400, 5499, "Consumer Staples", None),
    (5912, 5912, "Consumer Staples", None),
    (5200, 5999, "Consumer Discretionary", "XRT"),
    (3711, 3711, "Consumer Discretionary", None),
    (2800, 2899, "Materials", None),
    (8000, 8099, "Health Care", None),
    (6000, 6799, "Financials", None),
    (4000, 4799, "Industrials", None),
    (3400, 3599, "Industrials", None),
    (1500, 1799, "Industrials", None),
]

ALL_REFERENCE_ETFS = sorted(
    set(SECTOR_ETFS.values()) | set(SUB_INDUSTRY_ETFS.values())
    | {etf for *_, etf in SIC_RULES if etf} | {"SPY"}
)


@dataclass(frozen=True)
class ReferenceEtfs:
    sector_etf: str | None
    industry_etf: str | None


def _from_sic(sic: str | None) -> tuple[str | None, str | None]:
    if not sic or not str(sic).isdigit():
        return None, None
    code = int(sic)
    for lo, hi, sector, etf in SIC_RULES:
        if lo <= code <= hi:
            return sector, etf
    return None, None


def reference_etfs(gics_sector: str | None, gics_sub_industry: str | None,
                   sic: str | None) -> ReferenceEtfs:
    if gics_sector:
        return ReferenceEtfs(SECTOR_ETFS.get(gics_sector),
                             SUB_INDUSTRY_ETFS.get(gics_sub_industry or ""))
    sector, etf = _from_sic(sic)
    return ReferenceEtfs(SECTOR_ETFS.get(sector) if sector else None, etf)
```

Note the SNDK expectation in `test_registry.py`: SIC 3572 falls in `(3570, 3579, "Information Technology", None)`, so the sector ETF is `XLK` and there is no industry ETF.

- [ ] **Step 5: Implement the tickers registry**

`src/analyzer/universe/registry.py`:

```python
"""The `tickers` table: every stock the engine tracks, with CIK and reference ETFs."""

from __future__ import annotations

from datetime import date

import pandas as pd

from analyzer.store.db import upsert_rows
from analyzer.universe.industry import reference_etfs

COLUMNS = ["ticker", "cik", "name", "exchange", "gics_sector", "gics_sub_industry",
           "sector_etf", "industry_etf", "updated_at"]


def refresh_tickers(conn, ticker_map: pd.DataFrame, today: date) -> int:
    latest = pd.read_sql_query(
        "SELECT s.ticker, s.security, s.gics_sector, s.gics_sub_industry FROM index_snapshots s "
        "JOIN (SELECT ticker, MAX(snapshot_date) AS d FROM index_snapshots GROUP BY ticker) m "
        "ON m.ticker = s.ticker AND m.d = s.snapshot_date",
        conn,
    ).drop_duplicates("ticker").set_index("ticker")
    young = {r[0] for r in conn.execute("SELECT ticker FROM young_listings")}
    sec = ticker_map.set_index("ticker")
    sic = dict(conn.execute("SELECT cik, sic FROM companies").fetchall())

    rows = []
    for ticker in sorted(set(latest.index) | young):
        cik = int(sec.at[ticker, "cik"]) if ticker in sec.index else None
        name = sec.at[ticker, "name"] if ticker in sec.index else None
        exchange = sec.at[ticker, "exchange"] if ticker in sec.index else None
        sector = latest.at[ticker, "gics_sector"] if ticker in latest.index else None
        sub = latest.at[ticker, "gics_sub_industry"] if ticker in latest.index else None
        if name is None and ticker in latest.index:
            name = latest.at[ticker, "security"]
        ref = reference_etfs(sector, sub, sic.get(cik))
        rows.append((ticker, cik, name, exchange, sector, sub, ref.sector_etf, ref.industry_etf,
                     today.isoformat()))
    upsert_rows(conn, "tickers", COLUMNS, rows)
    return len(rows)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar tests/universe -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/analyzer/data/edgar src/analyzer/universe tests/data/edgar tests/universe
git commit -m "feat: add SEC submissions, filings and GICS/SIC reference ETF mapping"
```

---

### Task 8: Companyfacts extraction, tag map, CIK merges and point-in-time reads

**Files:**
- Create: `src/analyzer/data/edgar/tags.py`, `src/analyzer/data/edgar/companyfacts.py`, `src/analyzer/data/edgar/facts_read.py`, `src/analyzer/data/edgar/merges.py`, `src/analyzer/data/edgar/cik_merges.toml`, `tests/data/edgar/test_companyfacts.py`

**Interfaces:**
- Consumes: `HttpClient`, `NotFound` (Task 3); `upsert_rows` (Task 2); `load_ticker_map` output (Task 7).
- Produces:
  - `tags.Concept(name, kind: "duration" | "instant", unit, tags: tuple[str, ...])`, `CONCEPTS`, `CONCEPT_BY_NAME: dict[str, Concept]`, `PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A"}`.
  - `companyfacts.FACT_COLUMNS`, `fetch_companyfacts(sec, cik, max_age=timedelta(hours=20)) -> dict | None`, `extract_facts(cf: dict, cik: int) -> list[tuple]` (rows in `FACT_COLUMNS` order), `store_facts(conn, rows) -> None`.
  - `facts_read.load_concept(conn, cik, concept, as_of: date | None = None, merges: Mapping[int, Sequence[int]] | None = None) -> DataFrame[cik, tag, period_start (Timestamp | NaT), period_end, value, filed, accession]`: one row per period, sorted by `period_end`. For each period: the current CIK beats predecessors, then the highest-priority tag, then the latest filing on or before `as_of`.
  - `facts_read.latest_shares(conn, cik, as_of, merges=None) -> float | None`.
  - `merges.load_cik_merges(ticker_map, path=DEFAULT_PATH) -> dict[int, list[int]]` (current CIK → predecessor CIKs).

Concept list (the tag order is the priority order):

| concept | kind | unit | tags |
|---|---|---|---|
| revenue | duration | USD | us-gaap: Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet, RevenuesNetOfInterestExpense, RegulatedAndUnregulatedOperatingRevenue, RevenueFromContractWithCustomerIncludingAssessedTax |
| cost_of_revenue | duration | USD | CostOfRevenue, CostOfGoodsAndServicesSold, CostOfGoodsSold |
| gross_profit | duration | USD | GrossProfit |
| operating_income | duration | USD | OperatingIncomeLoss |
| net_income | duration | USD | NetIncomeLoss, ProfitLoss |
| eps_diluted | duration | USD/shares | EarningsPerShareDiluted, EarningsPerShareBasicAndDiluted |
| operating_cash_flow | duration | USD | NetCashProvidedByUsedInOperatingActivities, NetCashProvidedByUsedInOperatingActivitiesContinuingOperations |
| capex | duration | USD | PaymentsToAcquirePropertyPlantAndEquipment, PaymentsToAcquireProductiveAssets |
| depreciation_amortization | duration | USD | DepreciationDepletionAndAmortization, DepreciationAndAmortization, DepreciationAmortizationAndAccretionNet, Depreciation |
| diluted_shares | duration | shares | WeightedAverageNumberOfDilutedSharesOutstanding |
| cash | instant | USD | CashAndCashEquivalentsAtCarryingValue, CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents |
| long_term_debt | instant | USD | LongTermDebtNoncurrent, LongTermDebt |
| short_term_debt | instant | USD | LongTermDebtCurrent, DebtCurrent, ShortTermBorrowings, CommercialPaper |
| stockholders_equity | instant | USD | StockholdersEquity, StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest |
| total_assets | instant | USD | Assets |
| shares_outstanding | instant | shares | dei:EntityCommonStockSharesOutstanding, us-gaap:CommonStockSharesOutstanding |

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_companyfacts.py`:

```python
from datetime import date

import pandas as pd
import pytest

from analyzer.data.edgar.companyfacts import extract_facts, store_facts
from analyzer.data.edgar.facts_read import latest_shares, load_concept
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.store.db import connect, migrate


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar/test_companyfacts.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the tag map**

`src/analyzer/data/edgar/tags.py`:

```python
"""Engine concepts and the XBRL tags that can carry them, in priority order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-KT/A"}


@dataclass(frozen=True)
class Concept:
    name: str
    kind: Literal["duration", "instant"]
    unit: str
    tags: tuple[str, ...]


def _g(*names: str) -> tuple[str, ...]:
    return tuple(n if ":" in n else f"us-gaap:{n}" for n in names)


CONCEPTS: tuple[Concept, ...] = (
    Concept("revenue", "duration", "USD", _g(
        "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
        "RevenuesNetOfInterestExpense", "RegulatedAndUnregulatedOperatingRevenue",
        "RevenueFromContractWithCustomerIncludingAssessedTax")),
    Concept("cost_of_revenue", "duration", "USD", _g(
        "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")),
    Concept("gross_profit", "duration", "USD", _g("GrossProfit")),
    Concept("operating_income", "duration", "USD", _g("OperatingIncomeLoss")),
    Concept("net_income", "duration", "USD", _g("NetIncomeLoss", "ProfitLoss")),
    Concept("eps_diluted", "duration", "USD/shares", _g(
        "EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted")),
    Concept("operating_cash_flow", "duration", "USD", _g(
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations")),
    Concept("capex", "duration", "USD", _g(
        "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets")),
    Concept("depreciation_amortization", "duration", "USD", _g(
        "DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet", "Depreciation")),
    Concept("diluted_shares", "duration", "shares", _g(
        "WeightedAverageNumberOfDilutedSharesOutstanding")),
    Concept("cash", "instant", "USD", _g(
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
    Concept("long_term_debt", "instant", "USD", _g("LongTermDebtNoncurrent", "LongTermDebt")),
    Concept("short_term_debt", "instant", "USD", _g(
        "LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "CommercialPaper")),
    Concept("stockholders_equity", "instant", "USD", _g(
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")),
    Concept("total_assets", "instant", "USD", _g("Assets")),
    Concept("shares_outstanding", "instant", "shares", _g(
        "dei:EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding")),
)

CONCEPT_BY_NAME = {c.name: c for c in CONCEPTS}
CONCEPT_BY_TAG = {tag: c for c in CONCEPTS for tag in c.tags}
```

- [ ] **Step 4: Implement extraction, storage, merges and reads**

`src/analyzer/data/edgar/companyfacts.py`:

```python
"""Pull the engine's concepts out of SEC companyfacts JSON."""

from __future__ import annotations

from datetime import timedelta

from analyzer.data.edgar.tags import CONCEPTS, PERIODIC_FORMS
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
FACT_COLUMNS = ["cik", "concept", "tag", "unit", "period_start", "period_end", "value", "filed",
                "form", "accession", "source"]


def fetch_companyfacts(sec: HttpClient, cik: int,
                       max_age: timedelta | None = timedelta(hours=20)) -> dict | None:
    try:
        return sec.get_json(COMPANYFACTS_URL.format(cik=cik),
                            cache_key=f"sec/companyfacts/{cik}.json", max_age=max_age)
    except NotFound:
        return None  # no XBRL facts for this company


def extract_facts(cf: dict, cik: int) -> list[tuple]:
    rows = []
    facts = cf.get("facts", {})
    for concept in CONCEPTS:
        for qualified in concept.tags:
            namespace, tag = qualified.split(":")
            entry = facts.get(namespace, {}).get(tag)
            if not entry:
                continue
            for f in entry.get("units", {}).get(concept.unit, []):
                if f.get("form") not in PERIODIC_FORMS:
                    continue
                if (concept.kind == "duration") != ("start" in f):
                    continue
                rows.append((cik, concept.name, qualified, concept.unit, f.get("start", ""),
                             f["end"], float(f["val"]), f["filed"], f["form"], f["accn"],
                             "companyfacts"))
    return rows


def store_facts(conn, rows: list[tuple]) -> None:
    upsert_rows(conn, "facts", FACT_COLUMNS, rows)
```

`src/analyzer/data/edgar/cik_merges.toml`:

```toml
# Companies whose XBRL history continues under a predecessor CIK, for example after a
# holding-company reorganization. `ticker` is resolved to its current CIK through SEC's
# ticker map; a predecessor equal to the current CIK is ignored.
# Verified by the live test in Task 17 and by `analyzer audit` (flag "check CIK merge").

[[merge]]
ticker = "XOM"
predecessor_ciks = [34088]
```

`src/analyzer/data/edgar/merges.py`:

```python
"""Predecessor CIKs whose history belongs to a current company."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path(__file__).with_name("cik_merges.toml")


def load_cik_merges(ticker_map: pd.DataFrame, path: Path = DEFAULT_PATH) -> dict[int, list[int]]:
    config = tomllib.loads(Path(path).read_text())
    cik_by_ticker = dict(zip(ticker_map["ticker"], ticker_map["cik"].astype(int), strict=True))
    out: dict[int, list[int]] = {}
    for entry in config.get("merge", []):
        current = cik_by_ticker.get(entry["ticker"])
        if current is None:
            continue
        preds = [int(p) for p in entry["predecessor_ciks"] if int(p) != current]
        if preds:
            out[current] = preds
    return out
```

`src/analyzer/data/edgar/facts_read.py`:

```python
"""Point-in-time reads of stored facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pandas as pd

from analyzer.data.edgar.tags import CONCEPT_BY_NAME

_OUT = ["cik", "tag", "period_start", "period_end", "value", "filed", "accession"]


def load_concept(
    conn,
    cik: int,
    concept: str,
    as_of: date | None = None,
    merges: Mapping[int, Sequence[int]] | None = None,
) -> pd.DataFrame:
    """One value per period, as it was known on `as_of` (default: everything filed)."""
    ciks = [cik, *(merges or {}).get(cik, [])]
    sql = (f"SELECT {', '.join(_OUT)} FROM facts WHERE concept = ? "
           f"AND cik IN ({', '.join('?' * len(ciks))})")
    params: list = [concept, *ciks]
    if as_of is not None:
        sql += " AND filed <= ?"
        params.append(as_of.isoformat())
    df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=_OUT)
    priority = {t: i for i, t in enumerate(CONCEPT_BY_NAME[concept].tags)}
    df["prio"] = df["tag"].map(priority).fillna(len(priority))
    df["foreign"] = (df["cik"] != cik).astype(int)
    df = df.sort_values(["period_start", "period_end", "foreign", "prio", "filed"],
                        ascending=[True, True, True, True, False])
    df = df.drop_duplicates(["period_start", "period_end"], keep="first")
    df["period_start"] = pd.to_datetime(df["period_start"].mask(df["period_start"] == ""))
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["filed"] = pd.to_datetime(df["filed"])
    return df.sort_values(["period_end", "period_start"]).reset_index(drop=True)[_OUT]


def latest_shares(conn, cik: int, as_of: date,
                  merges: Mapping[int, Sequence[int]] | None = None) -> float | None:
    """Shares outstanding from the latest filing known on `as_of`."""
    for concept in ("shares_outstanding", "diluted_shares"):
        df = load_concept(conn, cik, concept, as_of=as_of, merges=merges)
        if not df.empty:
            return float(df.sort_values(["period_end", "filed"]).iloc[-1]["value"])
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar/test_companyfacts.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data/edgar tests/data/edgar/test_companyfacts.py
git commit -m "feat: extract EDGAR companyfacts with tag priority, CIK merges and PIT reads"
```

---

### Task 9: Discrete quarters from quarterly and year-to-date facts

**Files:**
- Create: `src/analyzer/data/edgar/quarters.py`, `tests/data/edgar/test_quarters.py`

**Interfaces:**
- Consumes: the `load_concept` frame shape (Task 8).
- Produces: `QUARTER_DAYS = (77, 120)`, `discrete_quarters(df: DataFrame, additive: bool = True) -> DataFrame[period_start, period_end, value, filed, days, derived]`, one row per quarter end, sorted by `period_end`.

Rules:
- A fact whose duration is 77–120 days is a quarter. This covers 12-week quarters (84 days), 16-week quarters (112 days, COST and PEP) and 14- or 17-week quarters in 53-week years (98 or 119 days).
- Year-to-date facts that share a start date form a chain (3, 6, 9, 12 months). The difference between consecutive links whose end dates are 77–120 days apart is a derived quarter, unless a direct quarter for that end date exists.
- A derived quarter's `filed` date is the later of its two components.
- `additive=False` (EPS, weighted shares) uses direct quarters only, because per-share YTD values can't be subtracted.

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_quarters.py`:

```python
import pandas as pd

from analyzer.data.edgar.quarters import discrete_quarters


def frame(rows):
    """rows: (start, end, value, filed)"""
    df = pd.DataFrame(rows, columns=["period_start", "period_end", "value", "filed"])
    for c in ("period_start", "period_end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


def test_calendar_year_from_ytd_chain():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
        ("2025-01-01", "2025-09-30", 420, "2025-11-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),
    ]))
    assert q["value"].tolist() == [100, 150, 170, 180]
    assert q["derived"].tolist() == [False, True, True, True]
    assert q["filed"].iloc[3] == pd.Timestamp("2026-02-15")


def test_direct_quarter_wins_over_derived():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-04-01", "2025-06-30", 155, "2025-08-01"),
        ("2025-01-01", "2025-06-30", 250, "2025-08-01"),
    ]))
    assert q["value"].tolist() == [100, 155]
    assert q["derived"].tolist() == [False, False]


def test_costco_style_16_week_fourth_quarter():
    q = discrete_quarters(frame([
        ("2025-09-01", "2025-11-23", 60, "2025-12-15"),    # 12 weeks
        ("2025-09-01", "2026-02-15", 125, "2026-03-10"),   # 24 weeks YTD
        ("2025-09-01", "2026-05-10", 190, "2026-06-01"),   # 36 weeks YTD
        ("2025-09-01", "2026-08-30", 280, "2026-10-08"),   # 52 weeks
    ]))
    assert q["value"].tolist() == [60, 65, 65, 90]
    assert q["days"].tolist()[-1] == 112


def test_53_week_year_fourth_quarter_is_kept():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-09-27", 300, "2025-11-01"),   # 39 weeks YTD
        ("2025-01-01", "2026-01-24", 450, "2026-03-01"),   # 17 weeks after the 39-week YTD
    ]))
    assert q["value"].tolist() == [150]
    assert q["days"].tolist() == [119]


def test_gap_that_is_not_a_quarter_is_ignored():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        ("2025-01-01", "2025-12-31", 600, "2026-02-15"),   # 9-month gap: not derivable
    ]))
    assert q["value"].tolist() == [100]


def test_non_additive_uses_direct_quarters_only():
    q = discrete_quarters(frame([
        ("2025-01-01", "2025-03-31", 1.0, "2025-05-01"),
        ("2025-01-01", "2025-06-30", 2.1, "2025-08-01"),
    ]), additive=False)
    assert q["value"].tolist() == [1.0]


def test_empty_input():
    assert discrete_quarters(frame([])).empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar/test_quarters.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/analyzer/data/edgar/quarters.py`:

```python
"""Discrete fiscal quarters from direct quarterly facts and year-to-date differences."""

from __future__ import annotations

import pandas as pd

QUARTER_DAYS = (77, 120)  # 11–17 weeks: 12/13/14/16/17-week quarters
_OUT = ["period_start", "period_end", "value", "filed", "days", "derived"]


def _is_quarter(days: int) -> bool:
    return QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]


def discrete_quarters(df: pd.DataFrame, additive: bool = True) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=_OUT)
    d = df.dropna(subset=["period_start"]).copy()
    d["days"] = (d["period_end"] - d["period_start"]).dt.days
    quarters: dict[pd.Timestamp, tuple] = {}
    for r in d[d["days"].map(_is_quarter)].itertuples():
        quarters[r.period_end] = (r.period_start, r.value, r.filed, False)
    if additive:
        for _start, chain in d[d["days"] >= QUARTER_DAYS[0]].groupby("period_start"):
            prev = None
            for r in chain.sort_values("period_end").itertuples():
                if prev is not None and r.period_end not in quarters:
                    if _is_quarter((r.period_end - prev.period_end).days):
                        quarters[r.period_end] = (
                            prev.period_end + pd.Timedelta(days=1),
                            r.value - prev.value,
                            max(r.filed, prev.filed),
                            True,
                        )
                prev = r
    out = pd.DataFrame(
        [(s, e, v, f, (e - s).days + (1 if derived else 0), derived)
         for e, (s, v, f, derived) in quarters.items()],
        columns=_OUT,
    )
    return out.sort_values("period_end").reset_index(drop=True)
```

`days` is the period length: for direct facts it is `end − start` (as in the input), for derived quarters it is `end − (previous end + 1 day) + 1`, i.e. `end − previous end`, so both conventions match the gap the tests check (112 and 119).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar/test_quarters.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/data/edgar/quarters.py tests/data/edgar/test_quarters.py
git commit -m "feat: derive discrete quarters incl. 16-week and 53-week fiscal calendars"
```

---

### Task 10: XBRL instance-document fallback for companyfacts lag

**Files:**
- Create: `src/analyzer/data/edgar/instance.py`, `tests/data/edgar/test_instance.py`

**Interfaces:**
- Consumes: `CONCEPT_BY_TAG` (Task 8), `store_facts` (Task 8), `filings` rows (Task 7), `HttpClient` (Task 3).
- Produces: `FILING_INDEX_URL`, `FILE_URL`, `find_instance_name(index_json: dict) -> str | None`, `parse_instance(xml: bytes, *, cik: int, accession: str, form: str, filed: str) -> list[tuple]` (rows in `FACT_COLUMNS` order, `source="instance"`), `fetch_instance_facts(sec, cik, accession, form, filed) -> list[tuple]`, `apply_instance_fallback(conn, sec, cik, today, lookback_days=120) -> list[str]` (accessions filled).

The fallback runs for the latest two 10-Q/10-K filings from the last 120 days that have no facts stored. Contexts with a `segment` or `scenario` (dimensional facts, e.g. per-segment revenue) are skipped, because companyfacts only carries non-dimensional facts.

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_instance.py`:

```python
from datetime import date

from analyzer.data.edgar.instance import (
    FILE_URL,
    FILING_INDEX_URL,
    apply_instance_fallback,
    find_instance_name,
    parse_instance,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

XML = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
  xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:dei="http://xbrl.sec.gov/dei/2025"
  xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <xbrli:context id="Q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="SEG"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">1</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">x:A</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  <xbrli:unit id="usdPerShare"><xbrli:divide>
    <xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>
    <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
  </xbrli:divide></xbrli:unit>
  <us-gaap:Revenues contextRef="Q" unitRef="usd" decimals="-6">5000000000</us-gaap:Revenues>
  <us-gaap:Revenues contextRef="SEG" unitRef="usd" decimals="-6">1000</us-gaap:Revenues>
  <us-gaap:EarningsPerShareDiluted contextRef="Q" unitRef="usdPerShare" decimals="2">1.25</us-gaap:EarningsPerShareDiluted>
  <us-gaap:Assets contextRef="I" unitRef="usd">90000</us-gaap:Assets>
  <us-gaap:Assets contextRef="Q" unitRef="usd">1</us-gaap:Assets>
  <dei:EntityCommonStockSharesOutstanding contextRef="I" unitRef="shares">1000000</dei:EntityCommonStockSharesOutstanding>
  <us-gaap:GrossProfit contextRef="Q" unitRef="usd" xsi:nil="true"/>
  <us-gaap:SomethingElse contextRef="Q" unitRef="usd">7</us-gaap:SomethingElse>
</xbrli:xbrl>"""

INDEX = {"directory": {"item": [{"name": "FilingSummary.xml"}, {"name": "aapl-20260630_htm.xml"},
                                {"name": "aapl-20260630_cal.xml"}, {"name": "aapl-20260630.htm"}]}}


def test_find_instance_name():
    assert find_instance_name(INDEX) == "aapl-20260630_htm.xml"
    legacy = {"directory": {"item": [{"name": "x_lab.xml"}, {"name": "aapl-20120630.xml"}]}}
    assert find_instance_name(legacy) == "aapl-20120630.xml"
    assert find_instance_name({"directory": {"item": [{"name": "a.htm"}]}}) is None


def test_parse_instance_keeps_plain_facts_only():
    rows = parse_instance(XML, cik=1, accession="0000000001-26-000001", form="10-Q",
                          filed="2026-08-01")
    got = {(r[1], r[4], r[5], r[6]) for r in rows}
    assert got == {
        ("revenue", "2026-04-01", "2026-06-30", 5e9),
        ("eps_diluted", "2026-04-01", "2026-06-30", 1.25),
        ("total_assets", "", "2026-06-30", 90000.0),
        ("shares_outstanding", "", "2026-06-30", 1e6),
    }
    assert all(r[10] == "instance" for r in rows)


def test_apply_fallback_fills_missing_latest_filing(tmp_path):
    acc, cik = "0000000001-26-000001", 1
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.execute("INSERT INTO filings VALUES (?, ?, '10-Q', '2026-08-01', '2026-06-30', NULL, 'a.htm')",
                 (acc, cik))

    def handler(url, params):
        if url == FILING_INDEX_URL.format(cik=cik, acc=acc.replace("-", "")):
            return FakeResponse(json_data=INDEX)
        assert url == FILE_URL.format(cik=cik, acc=acc.replace("-", ""), name="aapl-20260630_htm.xml")
        return FakeResponse(content=XML)

    client, session = fake_client(tmp_path, handler=handler)
    assert apply_instance_fallback(conn, client, cik, date(2026, 8, 5)) == [acc]
    assert conn.execute("SELECT COUNT(*) FROM facts WHERE source = 'instance'").fetchone()[0] == 4
    assert apply_instance_fallback(conn, client, cik, date(2026, 8, 5)) == []
    assert len(session.calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar/test_instance.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/analyzer/data/edgar/instance.py`:

```python
"""Read facts straight from a filing's XBRL instance when companyfacts lags behind."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from analyzer.data.edgar.companyfacts import store_facts
from analyzer.data.edgar.tags import CONCEPT_BY_TAG
from analyzer.data.http import HttpClient

FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
FILE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{name}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_LINKBASE = re.compile(r"(_cal|_def|_lab|_pre)\.xml$|FilingSummary\.xml$", re.IGNORECASE)


def find_instance_name(index_json: dict) -> str | None:
    names = [item["name"] for item in index_json["directory"]["item"]]
    for n in names:
        if n.endswith("_htm.xml"):  # instance extracted from inline XBRL
            return n
    for n in names:
        if n.endswith(".xml") and not _LINKBASE.search(n):
            return n
    return None


def _namespace_prefix(uri: str) -> str | None:
    if "fasb.org/us-gaap" in uri:
        return "us-gaap"
    if "xbrl.sec.gov/dei" in uri:
        return "dei"
    return None


def parse_instance(xml: bytes, *, cik: int, accession: str, form: str, filed: str) -> list[tuple]:
    root = ET.fromstring(xml)
    contexts: dict[str, tuple[str, str]] = {}
    for ctx in root.iter(f"{XBRLI}context"):
        if ctx.find(f"{XBRLI}entity/{XBRLI}segment") is not None or ctx.find(f"{XBRLI}scenario") is not None:
            continue
        period = ctx.find(f"{XBRLI}period")
        instant = period.findtext(f"{XBRLI}instant")
        if instant:
            contexts[ctx.get("id")] = ("", instant.strip())
        else:
            contexts[ctx.get("id")] = (period.findtext(f"{XBRLI}startDate").strip(),
                                       period.findtext(f"{XBRLI}endDate").strip())
    units: dict[str, str] = {}
    for unit in root.iter(f"{XBRLI}unit"):
        measures = [m.text.strip().split(":")[-1] for m in unit.iter(f"{XBRLI}measure")]
        units[unit.get("id")] = "/".join(measures)  # USD, shares, or USD/shares

    rows, seen = [], set()
    for el in root:
        if not el.tag.startswith("{"):
            continue
        uri, local = el.tag[1:].split("}")
        prefix = _namespace_prefix(uri)
        concept = CONCEPT_BY_TAG.get(f"{prefix}:{local}") if prefix else None
        ctx = contexts.get(el.get("contextRef"))
        if concept is None or ctx is None or units.get(el.get("unitRef")) != concept.unit:
            continue
        start, end = ctx
        text = (el.text or "").strip()
        if (concept.kind == "duration") != bool(start) or el.get(XSI_NIL) == "true" or not text:
            continue
        key = (concept.name, f"{prefix}:{local}", start, end)
        if key in seen:
            continue
        seen.add(key)
        rows.append((cik, concept.name, f"{prefix}:{local}", concept.unit, start, end, float(text),
                     filed, form, accession, "instance"))
    return rows


def fetch_instance_facts(sec: HttpClient, cik: int, accession: str, form: str,
                         filed: str) -> list[tuple]:
    acc = accession.replace("-", "")
    index = sec.get_json(FILING_INDEX_URL.format(cik=cik, acc=acc),
                         cache_key=f"sec/filings/{acc}/index.json")
    name = find_instance_name(index)
    if name is None:
        return []
    xml = sec.get_bytes(FILE_URL.format(cik=cik, acc=acc, name=name),
                        cache_key=f"sec/filings/{acc}/{name}")
    return parse_instance(xml, cik=cik, accession=accession, form=form, filed=filed)


def apply_instance_fallback(conn, sec: HttpClient, cik: int, today: date,
                            lookback_days: int = 120) -> list[str]:
    pending = conn.execute(
        "SELECT accession, form, filing_date FROM filings f WHERE cik = ? "
        "AND form IN ('10-Q', '10-K') AND filing_date >= ? "
        "AND NOT EXISTS (SELECT 1 FROM facts x WHERE x.cik = f.cik AND x.accession = f.accession) "
        "ORDER BY filing_date DESC LIMIT 2",
        (cik, (today - timedelta(days=lookback_days)).isoformat()),
    ).fetchall()
    filled = []
    for accession, form, filed in pending:
        rows = fetch_instance_facts(sec, cik, accession, form, filed)
        if rows:
            store_facts(conn, rows)
            filled.append(accession)
    return filled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar/test_instance.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/data/edgar/instance.py tests/data/edgar/test_instance.py
git commit -m "feat: fall back to XBRL instance documents when companyfacts lags"
```

---

### Task 11: EDGAR daily index and Form 4 parsing

**Files:**
- Create: `src/analyzer/data/edgar/daily_index.py`, `src/analyzer/data/edgar/form4.py`, `tests/data/edgar/test_daily_index.py`, `tests/data/edgar/test_form4.py`

**Interfaces:**
- Consumes: `HttpClient`, `NotFound` (Task 3); `get_state`, `set_state`, `upsert_rows` (Task 2).
- Produces:
  - `daily_index.MASTER_URL`, `POINTER_KEY = "edgar_daily_index_done"`, `IndexEntry(cik: int, company: str, form: str, date_filed: date, accession: str)`, `parse_master_index(text) -> list[IndexEntry]`, `fetch_master_index(sec, d: date) -> list[IndexEntry] | None` (`None` = not published / 404), `next_pointer(last_done: date, published: Mapping[date, bool], today: date) -> date`, `scan_daily_indexes(conn, sec, ciks: set[int], today: date) -> dict[int, set[str]]` (CIK → forms filed since the pointer).
  - `form4.INSIDER_COLUMNS`, `form4_document_url(cik, accession, primary_document) -> str`, `parse_form4(xml: bytes, accession: str, filing_date: str) -> list[tuple]` (rows in `INSIDER_COLUMNS` order, `source="xml"`), `sync_form4_filings(conn, sec, ciks: set[int], since: date) -> int` (filings parsed).

Pointer rule: the daily index for a day is retried until it is published. The pointer moves past a day only if its index was found, it is a weekend, or it is more than 3 days old (a market holiday). On the very first run there is no pointer; it is set to yesterday, because the backfill already fetched everything.

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_daily_index.py`:

```python
from datetime import date

import pytest

from analyzer.data.edgar.daily_index import (
    POINTER_KEY,
    next_pointer,
    parse_master_index,
    scan_daily_indexes,
)
from analyzer.store.db import connect, get_state, migrate, set_state
from tests.helpers import FakeResponse, fake_client

MASTER = """Description:           Daily Index of EDGAR Dissemination Feed by Company Name
Last Data Received:    October 6, 2026

CIK|Company Name|Form Type|Date Filed|Filename
--------------------------------------------------------------------------------
320193|Apple Inc.|10-Q|20261006|edgar/data/320193/0000320193-26-000100.txt
1065280|NETFLIX INC|4|20261006|edgar/data/1065280/0001065280-26-000200.txt
999|OTHER CO|8-K|20261006|edgar/data/999/0000000999-26-000001.txt
"""


def test_parse_master_index():
    entries = parse_master_index(MASTER)
    assert [(e.cik, e.form, e.accession) for e in entries] == [
        (320193, "10-Q", "0000320193-26-000100"),
        (1065280, "4", "0001065280-26-000200"),
        (999, "8-K", "0000000999-26-000001"),
    ]
    assert entries[0].date_filed == date(2026, 10, 6)


def test_pointer_waits_for_unpublished_recent_day():
    today = date(2026, 10, 10)  # Saturday
    published = {date(2026, 10, 6): True, date(2026, 10, 7): True, date(2026, 10, 8): False,
                 date(2026, 10, 9): True}
    assert next_pointer(date(2026, 10, 5), published, today) == date(2026, 10, 7)


def test_pointer_skips_old_holidays_and_weekends():
    today = date(2026, 12, 30)
    published = {date(2026, 12, 25): False,   # holiday, 5 days old
                 date(2026, 12, 26): False,   # Saturday
                 date(2026, 12, 27): False,   # Sunday
                 date(2026, 12, 28): True}
    assert next_pointer(date(2026, 12, 24), published, today) == date(2026, 12, 28)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_first_scan_only_sets_pointer(conn, tmp_path):
    client, session = fake_client(tmp_path)
    assert scan_daily_indexes(conn, client, {320193}, date(2026, 10, 10)) == {}
    assert get_state(conn, POINTER_KEY) == "2026-10-09"
    assert session.calls == []


def test_scan_collects_forms_for_tracked_ciks(conn, tmp_path):
    set_state(conn, POINTER_KEY, "2026-10-05")

    def handler(url, params):
        if url.endswith("master.20261006.idx"):
            return FakeResponse(content=MASTER)
        if url.endswith("master.20261009.idx"):
            return FakeResponse(404)  # not published yet
        return FakeResponse(content=MASTER.split("--------")[0])  # published, no rows we track

    client, _ = fake_client(tmp_path, handler=handler)
    changed = scan_daily_indexes(conn, client, {320193, 1065280}, date(2026, 10, 10))
    assert changed == {320193: {"10-Q"}, 1065280: {"4"}}
    assert get_state(conn, POINTER_KEY) == "2026-10-08"
```

`tests/data/edgar/test_form4.py`:

```python
from datetime import date

from analyzer.data.edgar.form4 import form4_document_url, parse_form4, sync_form4_filings
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>NKE</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer>
      <officerTitle>President and CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Roe Rick</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>true</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-15</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>10000</value></transactionShares>
        <transactionPricePerShare><value>61.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-09-16-04:00</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_document_url_strips_xsl_rendering_prefix():
    url = form4_document_url(320193, "0000320193-26-000001", "xslF345X05/wk-form4_1.xml")
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/wk-form4_1.xml"


def test_parse_form4():
    rows = parse_form4(FORM4, "0000320193-26-000001", "2026-09-17")
    assert len(rows) == 2
    acc, seq, issuer, filed, owners, rel, title, tdate, code, shares, price, ad, src = rows[0]
    assert (issuer, owners, rel, title) == (320193, "Doe Jane; Roe Rick", "Director,Officer",
                                             "President and CEO")
    assert (tdate, code, shares, price, ad, src) == ("2026-09-15", "P", 10000.0, 61.5, "A", "xml")
    assert rows[1][7] == "2026-09-16" and rows[1][10] is None


def test_sync_form4_filings_parses_new_filings_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    conn.executemany("INSERT INTO filings VALUES (?, ?, ?, ?, NULL, NULL, ?)", [
        ("0000320193-26-000001", 320193, "4", "2026-09-17", "xslF345X05/f.xml"),
        ("0000320193-26-000000", 320193, "4", "2026-01-02", "xslF345X05/old.xml"),  # before since
        ("0000999999-26-000001", 999999, "4", "2026-09-17", "f.xml"),               # untracked CIK
    ])
    client, session = fake_client(tmp_path, responses=[FakeResponse(content=FORM4)])
    assert sync_form4_filings(conn, client, {320193}, since=date(2026, 6, 30)) == 1
    assert sync_form4_filings(conn, client, {320193}, since=date(2026, 6, 30)) == 0
    assert len(session.calls) == 1
    assert conn.execute("SELECT COUNT(*) FROM insider_transactions").fetchone()[0] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar/test_daily_index.py tests/data/edgar/test_form4.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the daily index**

`src/analyzer/data/edgar/daily_index.py`:

```python
"""EDGAR daily master index: which companies filed what, day by day."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import get_state, set_state

MASTER_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/master.{ymd}.idx"
POINTER_KEY = "edgar_daily_index_done"
HOLIDAY_AFTER_DAYS = 3


@dataclass(frozen=True)
class IndexEntry:
    cik: int
    company: str
    form: str
    date_filed: date
    accession: str


def _parse_date(text: str) -> date:
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {text!r}")


def parse_master_index(text: str) -> list[IndexEntry]:
    lines = text.splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.startswith("-----")), None)
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        cik, company, form, filed, filename = parts
        accession = filename.strip().rsplit("/", 1)[-1].removesuffix(".txt")
        out.append(IndexEntry(int(cik), company.strip(), form.strip(), _parse_date(filed), accession))
    return out


def fetch_master_index(sec: HttpClient, d: date) -> list[IndexEntry] | None:
    url = MASTER_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
    try:
        text = sec.get_text(url, cache_key=f"sec/daily-index/{d:%Y%m%d}.idx")
    except NotFound:
        return None
    return parse_master_index(text)


def next_pointer(last_done: date, published: Mapping[date, bool], today: date) -> date:
    pointer = last_done
    for d in sorted(published):
        if published[d] or d.weekday() >= 5 or (today - d).days > HOLIDAY_AFTER_DAYS:
            pointer = d
        else:
            break
    return pointer


def scan_daily_indexes(conn, sec: HttpClient, ciks: set[int], today: date) -> dict[int, set[str]]:
    last = get_state(conn, POINTER_KEY)
    if last is None:
        set_state(conn, POINTER_KEY, (today - timedelta(days=1)).isoformat())
        return {}
    last_done = date.fromisoformat(last)
    changed: dict[int, set[str]] = {}
    published: dict[date, bool] = {}
    d = last_done + timedelta(days=1)
    while d < today:
        if d.weekday() >= 5:
            published[d] = False
        else:
            entries = fetch_master_index(sec, d)
            published[d] = entries is not None
            for e in entries or []:
                if e.cik in ciks:
                    changed.setdefault(e.cik, set()).add(e.form)
        d += timedelta(days=1)
    set_state(conn, POINTER_KEY, next_pointer(last_done, published, today).isoformat())
    return changed
```

- [ ] **Step 4: Implement Form 4 parsing**

`src/analyzer/data/edgar/form4.py`:

```python
"""Insider transactions from individual Form 4 XML documents."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import upsert_rows

INSIDER_COLUMNS = ["accession", "seq", "issuer_cik", "filing_date", "owner_names", "relationship",
                   "officer_title", "trans_date", "trans_code", "shares", "price",
                   "acquired_disposed", "source"]
_FLAGS = (("isDirector", "Director"), ("isOfficer", "Officer"),
          ("isTenPercentOwner", "TenPercentOwner"), ("isOther", "Other"))


def form4_document_url(cik: int, accession: str, primary_document: str) -> str:
    doc = re.sub(r"^xsl[^/]+/", "", primary_document)  # raw XML, not the XSL-rendered view
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{doc}"


def _text(el, path: str) -> str | None:
    value = (el.findtext(path) or "").strip()
    return value or None


def _float(el, path: str) -> float | None:
    value = _text(el, path)
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def parse_form4(xml: bytes, accession: str, filing_date: str) -> list[tuple]:
    root = ET.fromstring(xml)
    issuer = int(root.findtext("issuer/issuerCik").strip())
    names, relationships, titles = [], set(), []
    for owner in root.findall("reportingOwner"):
        name = _text(owner, "reportingOwnerId/rptOwnerName")
        if name:
            names.append(name)
        rel = owner.find("reportingOwnerRelationship")
        if rel is None:
            continue
        for tag, label in _FLAGS:
            if (rel.findtext(tag) or "").strip().lower() in ("1", "true"):
                relationships.add(label)
        title = _text(rel, "officerTitle")
        if title:
            titles.append(title)
    rows = []
    for seq, t in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        trans_date = _text(t, "transactionDate/value")
        rows.append((
            accession, seq, issuer, filing_date, "; ".join(names) or None,
            ",".join(sorted(relationships)) or None, "; ".join(titles) or None,
            trans_date[:10] if trans_date else None,
            _text(t, "transactionCoding/transactionCode"),
            _float(t, ".//transactionShares/value"),
            _float(t, ".//transactionPricePerShare/value"),
            _text(t, ".//transactionAcquiredDisposedCode/value"),
            "xml",
        ))
    return rows


def sync_form4_filings(conn, sec: HttpClient, ciks: set[int], since: date) -> int:
    pending = conn.execute(
        "SELECT f.accession, f.cik, f.filing_date, f.primary_document FROM filings f "
        "WHERE f.form IN ('4', '4/A') AND f.filing_date > ? "
        "AND NOT EXISTS (SELECT 1 FROM insider_transactions t WHERE t.accession = f.accession)",
        (since.isoformat(),),
    ).fetchall()
    parsed = 0
    for accession, cik, filing_date, document in pending:
        if cik not in ciks or not document:
            continue
        try:
            xml = sec.get_bytes(form4_document_url(cik, accession, document),
                                cache_key=f"sec/form4/{accession}.xml")
        except NotFound:
            continue
        upsert_rows(conn, "insider_transactions", INSIDER_COLUMNS,
                    parse_form4(xml, accession, filing_date))
        parsed += 1
    return parsed
```

A Form 4 with only derivative transactions stores no rows, so it is looked at again on later runs. That costs no network (the XML is cached) and keeps the schema simple.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data/edgar/daily_index.py src/analyzer/data/edgar/form4.py tests/data/edgar
git commit -m "feat: add EDGAR daily index scanning and Form 4 parsing"
```

---

### Task 12: Insider bulk datasets and per-company EDGAR refresh

**Files:**
- Create: `src/analyzer/data/edgar/insider_bulk.py`, `src/analyzer/data/edgar/sync.py`, `tests/data/edgar/test_insider_bulk.py`, `tests/data/edgar/test_sync.py`

**Interfaces:**
- Consumes: `INSIDER_COLUMNS`, `sync_form4_filings` (Task 11); `fetch_submissions`, `store_submissions` (Task 7); `fetch_companyfacts`, `extract_facts`, `store_facts` (Task 8); `get_state`, `set_state` (Task 2).
- Produces:
  - `insider_bulk.BULK_URL`, `STATE_QUARTERS = "insider_bulk_quarters"`, `STATE_CIKS = "insider_bulk_ciks"`, `parse_insider_zip(data: bytes, issuer_ciks: set[int]) -> list[tuple]` (rows in `INSIDER_COLUMNS` order, `source="bulk"`), `quarters_to_load(today: date, start: tuple[int, int] = (2013, 1)) -> list[str]` (labels like `"2024q1"`, through the last completed quarter), `quarter_end(label) -> date`, `InsiderSync(loaded: list[str], backfilled_ciks: int, xml_filings: int)`, `sync_insider(conn, sec, ciks: set[int], today: date) -> InsiderSync`.
  - `sync.refresh_company(conn, sec, cik, *, max_age: timedelta | None, facts: bool = True, now: datetime | None = None) -> None`.

Bulk dataset notes for the implementer:
- URL: `https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{label}_form345.zip`, e.g. `2024q1_form345.zip`.
- Files used: `SUBMISSION.tsv` (`ACCESSION_NUMBER`, `FILING_DATE`, `DOCUMENT_TYPE`, `ISSUERCIK`), `REPORTINGOWNER.tsv` (`ACCESSION_NUMBER`, `RPTOWNERNAME`, `RPTOWNER_RELATIONSHIP`, `RPTOWNER_TITLE`), `NONDERIV_TRANS.tsv` (`ACCESSION_NUMBER`, `TRANS_DATE`, `TRANS_CODE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE`, `TRANS_ACQUIRED_DISP_CD`). Dates look like `05-JAN-2024`.
- These column names come from SEC's dataset documentation and were **not** probed during planning (SEC requests need the owner's `SEC_USER_AGENT`). Task 17's live test checks them against a real file; if a name differs, fix the constants in `insider_bulk.py` and the fixture in this task together.
- The latest quarter is published some weeks after it ends. A 404 stops the loop; Form 4 XML covers the time since the last loaded quarter.
- When a new CIK joins the tracked set (a new young listing), all loaded quarters are re-read from the disk cache for that CIK only. No new downloads.

- [ ] **Step 1: Write the failing tests**

`tests/data/edgar/test_insider_bulk.py`:

```python
import io
import zipfile
from datetime import date

import pytest

from analyzer.data.edgar.insider_bulk import (
    BULK_URL,
    parse_insider_zip,
    quarter_end,
    quarters_to_load,
    sync_insider,
)
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client


def tsv(header, rows):
    return "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)


def make_zip(issuer=320193, accession="0000320193-24-000001"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", tsv(
            ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK"],
            [(accession, "05-JAN-2024", "4", str(issuer)),
             ("0000000001-24-000009", "05-JAN-2024", "4", "1"),
             ("0000320193-24-000002", "06-JAN-2024", "3", str(issuer))]))
        z.writestr("REPORTINGOWNER.tsv", tsv(
            ["ACCESSION_NUMBER", "RPTOWNERNAME", "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"],
            [(accession, "Doe Jane", "Director,Officer", "CEO"),
             (accession, "Roe Rick", "Director", "")]))
        z.writestr("NONDERIV_TRANS.tsv", tsv(
            ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
             "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD"],
            [(accession, "03-JAN-2024", "P", "1000", "50.5", "A"),
             (accession, "03-JAN-2024", "S", "200", "", "D"),
             ("0000000001-24-000009", "03-JAN-2024", "P", "1", "1", "A")]))
    return buf.getvalue()


def test_parse_insider_zip_filters_and_normalizes():
    rows = parse_insider_zip(make_zip(), {320193})
    assert len(rows) == 2
    first = rows[0]
    assert first[:4] == ("0000320193-24-000001", 0, 320193, "2024-01-05")
    assert first[4:7] == ("Doe Jane; Roe Rick", "Director,Officer", "CEO")
    assert first[7:13] == ("2024-01-03", "P", 1000.0, 50.5, "A", "bulk")
    assert rows[1][1] == 1 and rows[1][10] is None


def test_quarters_to_load():
    assert quarters_to_load(date(2013, 10, 10)) == ["2013q1", "2013q2", "2013q3"]
    assert quarters_to_load(date(2014, 2, 1))[-1] == "2013q4"
    assert quarter_end("2024q1") == date(2024, 3, 31)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    return c


def test_sync_loads_published_quarters_and_replaces_xml_rows(conn, tmp_path):
    conn.execute("INSERT INTO insider_transactions VALUES "
                 "('0000320193-24-000001', 0, 320193, '2024-01-05', NULL, NULL, NULL, "
                 "'2024-01-03', 'P', 1, 1, 'A', 'xml')")

    def handler(url, params):
        if url == BULK_URL.format(label="2013q1"):
            return FakeResponse(content=make_zip())
        return FakeResponse(404)

    client, _ = fake_client(tmp_path, handler=handler)
    result = sync_insider(conn, client, {320193}, date(2013, 10, 10))
    assert result.loaded == ["2013q1"]
    sources = conn.execute("SELECT DISTINCT source FROM insider_transactions").fetchall()
    assert sources == [("bulk",)]


def test_new_cik_is_backfilled_from_cached_zips(conn, tmp_path):
    def handler(url, params):
        if url == BULK_URL.format(label="2013q1"):
            return FakeResponse(content=make_zip(issuer=320193))
        return FakeResponse(404)

    client, session = fake_client(tmp_path, handler=handler)
    sync_insider(conn, client, {1}, date(2013, 10, 10))
    calls_before = len(session.calls)
    result = sync_insider(conn, client, {1, 320193}, date(2013, 10, 10))
    assert result.backfilled_ciks == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM insider_transactions WHERE issuer_cik = 320193").fetchone()[0] == 2
    zip_calls = [c for c in session.calls[calls_before:] if c["url"].endswith("2013q1_form345.zip")]
    assert zip_calls == []
```

`tests/data/edgar/test_sync.py`:

```python
from datetime import datetime, timedelta

from analyzer.data.edgar.companyfacts import COMPANYFACTS_URL
from analyzer.data.edgar.companies import SUBMISSIONS_URL
from analyzer.data.edgar.sync import refresh_company
from analyzer.store.db import connect, migrate
from tests.helpers import FakeResponse, fake_client

SUBS = {"name": "Apple Inc.", "sic": "3571", "sicDescription": "Computers",
        "filings": {"recent": {"accessionNumber": ["a1"], "form": ["10-Q"],
                               "filingDate": ["2026-08-01"], "reportDate": ["2026-06-27"],
                               "items": [""], "primaryDocument": ["x.htm"]}, "files": []}}
CF = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
    {"start": "2026-03-29", "end": "2026-06-27", "val": 9e10, "filed": "2026-08-01",
     "form": "10-Q", "accn": "a1"}]}}}}}


def test_refresh_company_stores_submissions_and_facts(tmp_path):
    def handler(url, params):
        if url == SUBMISSIONS_URL.format(cik=320193):
            return FakeResponse(json_data=SUBS)
        assert url == COMPANYFACTS_URL.format(cik=320193)
        return FakeResponse(json_data=CF)

    client, _ = fake_client(tmp_path, handler=handler)
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=timedelta(0), now=datetime(2026, 10, 10))
    assert conn.execute("SELECT sic FROM companies").fetchone() == ("3571",)
    assert conn.execute("SELECT value FROM facts").fetchone() == (9e10,)


def test_refresh_company_without_facts(tmp_path):
    client, session = fake_client(tmp_path, responses=[FakeResponse(json_data=SUBS)])
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    refresh_company(conn, client, 320193, max_age=None, facts=False)
    assert len(session.calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/edgar/test_insider_bulk.py tests/data/edgar/test_sync.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the bulk loader**

`src/analyzer/data/edgar/insider_bulk.py`:

```python
"""Insider transaction history from SEC's quarterly Form 3/4/5 datasets."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from analyzer.data.edgar.form4 import INSIDER_COLUMNS, sync_form4_filings
from analyzer.data.http import HttpClient, NotFound
from analyzer.store.db import get_state, set_state

BULK_URL = ("https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/"
            "{label}_form345.zip")
STATE_QUARTERS = "insider_bulk_quarters"
STATE_CIKS = "insider_bulk_ciks"


def _iso(text: str) -> str | None:
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _float(text: str) -> float | None:
    try:
        return float(text) if str(text).strip() else None
    except ValueError:
        return None


def _read(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    path = next(n for n in z.namelist() if n.split("/")[-1].upper() == name)
    return pd.read_csv(z.open(path), sep="\t", dtype=str, keep_default_na=False, quoting=3)


def parse_insider_zip(data: bytes, issuer_ciks: set[int]) -> list[tuple]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sub = _read(z, "SUBMISSION.TSV")
        owners = _read(z, "REPORTINGOWNER.TSV")
        trans = _read(z, "NONDERIV_TRANS.TSV")
    sub = sub[sub["DOCUMENT_TYPE"].isin(["4", "4/A"])].copy()
    sub["ISSUERCIK"] = sub["ISSUERCIK"].astype(int)
    sub = sub[sub["ISSUERCIK"].isin(issuer_ciks)]
    if sub.empty:
        return []
    owners = owners[owners["ACCESSION_NUMBER"].isin(sub["ACCESSION_NUMBER"])]
    by_owner = owners.groupby("ACCESSION_NUMBER").agg(
        owner_names=("RPTOWNERNAME", lambda s: "; ".join(x for x in s if x) or None),
        relationship=("RPTOWNER_RELATIONSHIP", lambda s: ",".join(
            sorted({p for x in s for p in x.split(",") if p})) or None),
        officer_title=("RPTOWNER_TITLE", lambda s: "; ".join(x for x in s if x) or None),
    )
    m = trans.merge(sub[["ACCESSION_NUMBER", "FILING_DATE", "ISSUERCIK"]], on="ACCESSION_NUMBER")
    m = m.merge(by_owner, left_on="ACCESSION_NUMBER", right_index=True, how="left")
    m["seq"] = m.groupby("ACCESSION_NUMBER").cumcount()
    return [
        (r.ACCESSION_NUMBER, int(r.seq), int(r.ISSUERCIK), _iso(r.FILING_DATE),
         r.owner_names if isinstance(r.owner_names, str) else None,
         r.relationship if isinstance(r.relationship, str) else None,
         r.officer_title if isinstance(r.officer_title, str) else None,
         _iso(r.TRANS_DATE), r.TRANS_CODE or None, _float(r.TRANS_SHARES),
         _float(r.TRANS_PRICEPERSHARE), r.TRANS_ACQUIRED_DISP_CD or None, "bulk")
        for r in m.itertuples()
    ]


def quarters_to_load(today: date, start: tuple[int, int] = (2013, 1)) -> list[str]:
    current = (today.month - 1) // 3 + 1
    last = (today.year, current - 1) if current > 1 else (today.year - 1, 4)
    y, q = start
    out = []
    while (y, q) <= last:
        out.append(f"{y}q{q}")
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def quarter_end(label: str) -> date:
    y, q = int(label[:4]), int(label[-1])
    first_of_next = date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1)
    return first_of_next - timedelta(days=1)


def _insert(conn, rows: list[tuple]) -> None:
    accessions = sorted({r[0] for r in rows})
    with conn:
        conn.executemany("DELETE FROM insider_transactions WHERE accession = ?",
                         [(a,) for a in accessions])
        conn.executemany(
            f"INSERT INTO insider_transactions ({', '.join(INSIDER_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(INSIDER_COLUMNS))})", rows)


@dataclass
class InsiderSync:
    loaded: list[str] = field(default_factory=list)
    backfilled_ciks: int = 0
    xml_filings: int = 0


def sync_insider(conn, sec: HttpClient, ciks: set[int], today: date) -> InsiderSync:
    result = InsiderSync()
    loaded = set(json.loads(get_state(conn, STATE_QUARTERS) or "[]"))
    covered = set(json.loads(get_state(conn, STATE_CIKS) or "[]"))

    new_ciks = ciks - covered
    if loaded and new_ciks:  # re-read cached quarters for companies added since
        for label in sorted(loaded):
            data = sec.get_bytes(BULK_URL.format(label=label), cache_key=f"sec/insider/{label}.zip")
            rows = parse_insider_zip(data, new_ciks)
            if rows:
                _insert(conn, rows)
        result.backfilled_ciks = len(new_ciks)

    for label in quarters_to_load(today):
        if label in loaded:
            continue
        try:
            data = sec.get_bytes(BULK_URL.format(label=label), cache_key=f"sec/insider/{label}.zip")
        except NotFound:
            break  # not published yet; later quarters aren't either
        rows = parse_insider_zip(data, ciks)
        if rows:
            _insert(conn, rows)
        loaded.add(label)
        result.loaded.append(label)

    set_state(conn, STATE_QUARTERS, json.dumps(sorted(loaded)))
    set_state(conn, STATE_CIKS, json.dumps(sorted(covered | ciks)))
    since = quarter_end(max(loaded)) if loaded else today - timedelta(days=120)
    result.xml_filings = sync_form4_filings(conn, sec, ciks, since)
    return result
```

- [ ] **Step 4: Implement the per-company refresh**

`src/analyzer/data/edgar/sync.py`:

```python
"""Refresh one company's submissions and, optionally, its companyfacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analyzer.data.edgar.companies import fetch_submissions, store_submissions
from analyzer.data.edgar.companyfacts import extract_facts, fetch_companyfacts, store_facts
from analyzer.data.http import HttpClient


def refresh_company(conn, sec: HttpClient, cik: int, *, max_age: timedelta | None,
                    facts: bool = True, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    store_submissions(conn, fetch_submissions(sec, cik, max_age=max_age), now)
    if facts:
        cf = fetch_companyfacts(sec, cik, max_age=max_age)
        if cf:
            store_facts(conn, extract_facts(cf, cik))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/data/edgar -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/data/edgar tests/data/edgar
git commit -m "feat: load insider history from SEC bulk datasets and refresh companies"
```

---

### Task 13: FRED macro series

**Files:**
- Create: `src/analyzer/data/fred.py`, `tests/data/test_fred.py`

**Interfaces:**
- Consumes: `HttpClient` (Task 3), `ConfigError` (Task 1), `upsert_rows` (Task 2).
- Produces: `FRED_URL`, `MACRO_SERIES = ("WALCL", "RRPONTSYD", "M2SL", "NFCI", "BAMLH0A0HYM2", "DFII10")`, `MACRO_HISTORY_START = date(2006, 1, 1)`, `REVISION_WINDOW = timedelta(days=90)`, `fetch_series(client, api_key, series_id, start) -> list[tuple[str, str, float]]`, `MacroSync(latest: dict[str, str], failed: list[tuple[str, str]])`, `sync_macro(conn, client, api_key: str | None, today: date) -> MacroSync`.

Each run re-fetches the last 90 days of every series, because FRED revises recent values (M2 especially). FRED responses are never cached, so the key cannot end up in a cache file name.

- [ ] **Step 1: Write the failing tests**

`tests/data/test_fred.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/data/test_fred.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/analyzer/data/fred.py`:

```python
"""Macro liquidity series from FRED."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from analyzer.config import ConfigError
from analyzer.data.http import HttpClient
from analyzer.store.db import upsert_rows

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
MACRO_SERIES = ("WALCL", "RRPONTSYD", "M2SL", "NFCI", "BAMLH0A0HYM2", "DFII10")
MACRO_HISTORY_START = date(2006, 1, 1)
REVISION_WINDOW = timedelta(days=90)


def fetch_series(client: HttpClient, api_key: str, series_id: str,
                 start: date) -> list[tuple[str, str, float]]:
    data = client.get_json(FRED_URL, params={"series_id": series_id, "api_key": api_key,
                                             "file_type": "json",
                                             "observation_start": start.isoformat()})
    return [(series_id, o["date"], float(o["value"])) for o in data["observations"]
            if o["value"] not in (".", "")]


@dataclass
class MacroSync:
    latest: dict[str, str] = field(default_factory=dict)
    failed: list[tuple[str, str]] = field(default_factory=list)


def sync_macro(conn, client: HttpClient, api_key: str | None, today: date) -> MacroSync:
    if not api_key:
        raise ConfigError("FRED_API_KEY is not set. Get a free key at "
                          "https://fred.stlouisfed.org/docs/api/api_key.html and add it to .env")
    result = MacroSync()
    for series_id in MACRO_SERIES:
        row = conn.execute("SELECT MAX(date) FROM macro WHERE series_id = ?", (series_id,)).fetchone()
        start = (date.fromisoformat(row[0]) - REVISION_WINDOW) if row[0] else MACRO_HISTORY_START
        try:
            rows = fetch_series(client, api_key, series_id, start)
        except Exception as exc:  # FetchError messages never include the query string
            result.failed.append((series_id, f"{type(exc).__name__}: {exc}"))
            continue
        upsert_rows(conn, "macro", ["series_id", "date", "value"], rows)
        latest = conn.execute("SELECT MAX(date) FROM macro WHERE series_id = ?",
                              (series_id,)).fetchone()[0]
        if latest:
            result.latest[series_id] = latest
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/data/test_fred.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analyzer/data/fred.py tests/data/test_fred.py
git commit -m "feat: sync FRED liquidity series with a revision window"
```

---

### Task 14: Young listings and the point-in-time universe

**Files:**
- Create: `src/analyzer/universe/young.py`, `src/analyzer/universe/members.py`, `tests/universe/test_young.py`, `tests/universe/test_members.py`

**Interfaces:**
- Consumes: `PriceProvider` (Task 4); `replace_prices`, `HISTORY_START` (Task 5); `members_on`, `INDEXES` (Task 6); `latest_shares` (Task 8); `UniverseRules` (Task 1); `upsert_rows` (Task 2).
- Produces:
  - `young.LISTED_EXCHANGES = {"NYSE", "Nasdaq"}`, `young_candidates(ticker_map, exclude: set[str]) -> list[str]`, `average_dollar_volume(frame, days) -> float`, `screen_young_listings(conn, primary, ticker_map, index_tickers: set[str], rules: UniverseRules, today: date, history_start: date = HISTORY_START) -> list[str]`.
  - `members.Member(ticker: str, cik: int | None, source: str)` (`source` is `"sp500"`, `"sp400"` or `"young"`), `young_cutoff(d, rules) -> date`, `universe_on(conn, d: date, rules: UniverseRules, merges=None) -> list[Member]`, `price_symbols(conn, today: date, rules: UniverseRules) -> tuple[list[str], list[str]]` (active, historical).

Screening (weekly):
1. Candidates: NYSE/Nasdaq tickers from SEC's map that are not index members, excluding warrants, units, rights and preferreds (`ABCDW`, `ABCDU`, `ABCDR`, `ABC-PA`, `ABC-WT`, `ABC-U`, ...).
2. Fetch 45 days of prices for all candidates and keep those with enough bars and a 20-day average dollar volume ≥ $25M.
3. Fetch full history for the survivors. Keep those whose first bar is more than 30 days after `history_start` (anything older could have been clipped by the start date, so it is not young inside the research window). Store their prices and a `young_listings` row.

`universe_on(d)`: index members on `d`, plus young listings whose first trade date is in `(d − 5 years, d]`, have SIC ≠ 6770 (blank check) and a market cap on `d` of at least $2B (latest close on or before `d` × shares outstanding known on `d`). Then every member must have at least 20 bars up to `d` and a 20-day average dollar volume ≥ $25M. Index membership wins when a ticker is both.

- [ ] **Step 1: Write the failing tests**

`tests/universe/test_young.py`:

```python
from datetime import date

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import read_prices
from analyzer.store.db import connect, migrate
from analyzer.universe.young import average_dollar_volume, screen_young_listings, young_candidates
from tests.helpers import FakeProvider, bars

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=20, young_max_years=5,
                      young_min_market_cap=2e9)


def ticker_map(rows):
    return pd.DataFrame(rows, columns=["cik", "name", "ticker", "exchange"])


def test_young_candidates_filters_exchange_index_and_derivative_tickers():
    tm = ticker_map([
        (1, "A", "NEWCO", "Nasdaq"), (2, "B", "NEWCW", "Nasdaq"), (3, "C", "ABC-PA", "NYSE"),
        (4, "D", "AAPL", "Nasdaq"), (5, "E", "OTCX", "OTC"), (6, "F", "BRK-B", "NYSE"),
        (7, "G", "SPAC-U", "NYSE"),
    ])
    assert young_candidates(tm, exclude={"AAPL"}) == ["BRK-B", "NEWCO"]


def test_average_dollar_volume():
    f = bars(pd.bdate_range("2026-09-01", periods=25), [10.0] * 25, volume=3_000_000.0)
    assert average_dollar_volume(f, 20) == 30_000_000.0


def days(n, end="2026-10-09"):
    return pd.bdate_range(end=end, periods=n)


def test_screen_keeps_liquid_recent_listings(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    tm = ticker_map([(10, "New", "NEWCO", "Nasdaq"), (11, "Thin", "THIN", "Nasdaq"),
                     (12, "Old", "OLDCO", "NYSE")])
    data = {
        "NEWCO": bars(pd.bdate_range("2023-03-01", "2026-10-09"), 50.0, volume=1_000_000.0),
        "THIN": bars(days(60), 5.0, volume=100_000.0),
        "OLDCO": bars(pd.bdate_range("2009-01-02", "2026-10-09"), 40.0, volume=1_000_000.0),
    }
    kept = screen_young_listings(conn, FakeProvider("yfinance", data), tm, set(), RULES,
                                 date(2026, 10, 9))
    assert kept == ["NEWCO"]
    assert conn.execute("SELECT ticker, cik, first_trade_date FROM young_listings").fetchall() == [
        ("NEWCO", 10, "2023-03-01")]
    assert read_prices(conn, "NEWCO")["date"].min() == pd.Timestamp("2023-03-01")
```

`tests/universe/test_members.py`:

```python
from datetime import date

import pandas as pd
import pytest

from analyzer.config import UniverseRules
from analyzer.data.price_sync import replace_prices
from analyzer.store.db import connect, migrate
from analyzer.universe.members import Member, price_symbols, universe_on
from tests.helpers import bars

RULES = UniverseRules(min_dollar_volume=25e6, dollar_volume_days=20, young_max_years=5,
                      young_min_market_cap=2e9)
D = date(2026, 10, 9)


def liquid(conn, ticker, close=50.0, volume=1_000_000.0, n=30):
    replace_prices(conn, ticker, bars(pd.bdate_range(end=D, periods=n), close, volume=volume),
                   "yfinance")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    migrate(c)
    c.executemany("INSERT INTO index_membership VALUES (?,?,?,?)", [
        ("sp500", "AAPL", "2010-01-01", None),
        ("sp400", "MID", "2015-01-01", None),
        ("sp500", "THINIDX", "2010-01-01", None),
        ("sp500", "GONE", "2010-01-01", "2020-01-01"),
    ])
    c.executemany("INSERT INTO tickers (ticker, cik, updated_at) VALUES (?,?,?)", [
        ("AAPL", 320193, "x"), ("MID", 2, "x"), ("THINIDX", 3, "x")])
    c.executemany("INSERT INTO young_listings VALUES (?,?,?,?)", [
        ("BIGNEW", 100, "2024-01-02", "2026-10-09"),
        ("SMALLNEW", 101, "2024-01-02", "2026-10-09"),
        ("SPACCO", 102, "2024-01-02", "2026-10-09"),
        ("AGED", 103, "2019-06-03", "2026-10-09"),
    ])
    c.execute("INSERT INTO companies VALUES (102, 'Spac', '6770', 'Blank Checks', 'x')")
    shares = [(100, 1e8), (101, 1e7), (102, 1e9), (103, 1e9)]  # × $50 close
    c.executemany(
        "INSERT INTO facts VALUES (?, 'shares_outstanding', 'dei:EntityCommonStockSharesOutstanding',"
        " 'shares', '', '2026-06-30', ?, '2026-08-01', '10-Q', 'acc' || ?, 'companyfacts')",
        [(cik, n, cik) for cik, n in shares])
    for t in ("AAPL", "MID", "BIGNEW", "SMALLNEW", "SPACCO", "AGED"):
        liquid(c, t)
    liquid(c, "THINIDX", volume=1_000.0)
    return c


def test_universe_on_applies_all_filters(conn):
    members = universe_on(conn, D, RULES)
    assert members == [
        Member("AAPL", 320193, "sp500"),
        Member("BIGNEW", 100, "young"),   # $5B cap
        Member("MID", 2, "sp400"),
    ]
    # SMALLNEW: $500M cap. SPACCO: blank check. AGED: listed > 5 years before D.
    # THINIDX: illiquid. GONE: left the index.


def test_universe_on_a_past_date(conn):
    assert [m.ticker for m in universe_on(conn, date(2026, 6, 1), RULES)] == []  # no prices yet


def test_price_symbols_split_active_and_historical(conn):
    active, historical = price_symbols(conn, D, RULES)
    assert active == ["AAPL", "BIGNEW", "MID", "SMALLNEW", "SPACCO", "THINIDX"]
    assert historical == ["AGED", "GONE"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/universe/test_young.py tests/universe/test_members.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the young-listing screen**

`src/analyzer/universe/young.py`:

```python
"""Find liquid stocks that listed recently and are not in the S&P 500/400."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pandas as pd

from analyzer.config import UniverseRules
from analyzer.data.price_sync import HISTORY_START, replace_prices
from analyzer.data.prices import PriceProvider
from analyzer.store.db import upsert_rows

LISTED_EXCHANGES = {"NYSE", "Nasdaq"}
_NASDAQ_DERIVATIVE = re.compile(r"[A-Z]{4}[WUR]")          # warrants, units, rights
_SUFFIX_DERIVATIVE = re.compile(r"-(P[A-Z]?|WS?|WT|U|UN|R|RT)$")  # preferreds, warrants, units
SCREEN_WINDOW_DAYS = 45
CLIPPED_MARGIN = timedelta(days=30)


def young_candidates(ticker_map: pd.DataFrame, exclude: set[str]) -> list[str]:
    tm = ticker_map[ticker_map["exchange"].isin(LISTED_EXCHANGES)]
    keep = [
        t for t in tm["ticker"]
        if t not in exclude
        and not (len(t) == 5 and _NASDAQ_DERIVATIVE.fullmatch(t))
        and not _SUFFIX_DERIVATIVE.search(t)
    ]
    return sorted(set(keep))


def average_dollar_volume(frame: pd.DataFrame, days: int) -> float:
    tail = frame.sort_values("date").tail(days)
    return float((tail["close"] * tail["volume"]).mean())


def screen_young_listings(
    conn,
    primary: PriceProvider,
    ticker_map: pd.DataFrame,
    index_tickers: set[str],
    rules: UniverseRules,
    today: date,
    history_start: date = HISTORY_START,
) -> list[str]:
    candidates = young_candidates(ticker_map, index_tickers)
    recent = primary.fetch(candidates, today - timedelta(days=SCREEN_WINDOW_DAYS), today)
    liquid = [
        t for t, f in recent.items()
        if len(f) >= rules.dollar_volume_days
        and average_dollar_volume(f, rules.dollar_volume_days) >= rules.min_dollar_volume
    ]
    full = primary.fetch(liquid, history_start, today)
    cik_by_ticker = dict(zip(ticker_map["ticker"], ticker_map["cik"], strict=True))
    kept, rows = [], []
    for ticker in sorted(full):
        frame = full[ticker]
        first = frame["date"].min().date()
        if first <= history_start + CLIPPED_MARGIN:
            continue
        replace_prices(conn, ticker, frame, primary.name)
        rows.append((ticker, int(cik_by_ticker[ticker]), first.isoformat(), today.isoformat()))
        kept.append(ticker)
    upsert_rows(conn, "young_listings", ["ticker", "cik", "first_trade_date", "screened_on"], rows)
    return kept
```

- [ ] **Step 4: Implement the universe**

`src/analyzer/universe/members.py`:

```python
"""Which stocks are in the universe on a given date."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from analyzer.config import UniverseRules
from analyzer.data.edgar.facts_read import latest_shares
from analyzer.universe.membership import INDEXES, members_on

BLANK_CHECK_SIC = "6770"


@dataclass(frozen=True)
class Member:
    ticker: str
    cik: int | None
    source: str  # 'sp500' | 'sp400' | 'young'


def young_cutoff(d: date, rules: UniverseRules) -> date:
    return d - timedelta(days=round(365.25 * rules.young_max_years))


def _recent_bars(conn, ticker: str, d: date, n: int) -> list[tuple[float, float]]:
    return conn.execute(
        "SELECT close, COALESCE(volume, 0) FROM prices WHERE symbol = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (ticker, d.isoformat(), n),
    ).fetchall()


def _liquid(conn, ticker: str, d: date, rules: UniverseRules) -> bool:
    rows = _recent_bars(conn, ticker, d, rules.dollar_volume_days)
    if len(rows) < rules.dollar_volume_days:
        return False
    return sum(c * v for c, v in rows) / len(rows) >= rules.min_dollar_volume


def universe_on(conn, d: date, rules: UniverseRules,
                merges: Mapping[int, Sequence[int]] | None = None) -> list[Member]:
    cik_of = dict(conn.execute("SELECT ticker, cik FROM tickers").fetchall())
    picked: dict[str, Member] = {}
    for index_name in INDEXES:
        for ticker in members_on(conn, index_name, d):
            picked.setdefault(ticker, Member(ticker, cik_of.get(ticker), index_name))

    young = conn.execute(
        "SELECT y.ticker, y.cik FROM young_listings y LEFT JOIN companies c ON c.cik = y.cik "
        "WHERE y.first_trade_date > ? AND y.first_trade_date <= ? AND COALESCE(c.sic, '') <> ?",
        (young_cutoff(d, rules).isoformat(), d.isoformat(), BLANK_CHECK_SIC),
    ).fetchall()
    for ticker, cik in young:
        if ticker in picked or cik is None:
            continue
        bars = _recent_bars(conn, ticker, d, 1)
        shares = latest_shares(conn, cik, d, merges)
        if bars and shares and bars[0][0] * shares >= rules.young_min_market_cap:
            picked[ticker] = Member(ticker, cik, "young")

    return sorted((m for m in picked.values() if _liquid(conn, m.ticker, d, rules)),
                  key=lambda m: m.ticker)


def price_symbols(conn, today: date, rules: UniverseRules) -> tuple[list[str], list[str]]:
    """Symbols to refresh daily (active) and past members to fetch once (historical)."""
    active: set[str] = set()
    for index_name in INDEXES:
        active |= members_on(conn, index_name, today)
    active |= {r[0] for r in conn.execute(
        "SELECT ticker FROM young_listings WHERE first_trade_date > ?",
        (young_cutoff(today, rules).isoformat(),))}
    ever = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM index_membership")}
    ever |= {r[0] for r in conn.execute("SELECT ticker FROM young_listings")}
    return sorted(active), sorted(ever - active)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/universe -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/universe tests/universe
git commit -m "feat: screen young listings and compute the point-in-time universe"
```

---

### Task 15: `analyzer update` orchestration

**Files:**
- Create: `src/analyzer/pipeline/__init__.py` (empty), `src/analyzer/pipeline/update.py`, `tests/pipeline/__init__.py` (empty), `tests/pipeline/test_update.py`
- Modify: `src/analyzer/cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–14.
- Produces:
  - `STEPS = ("universe", "prices", "edgar", "macro")`, `FAILURE_LIMIT = 0.05`.
  - `StepResult(name, attempted=0, failed: list[tuple[str, str]], latest_data_date: date | None = None, detail: str = "")` with property `failure_rate`.
  - `UpdateContext(conn, settings, rules, clients, price_providers, today, run_id, tickers: set[str] | None = None)`.
  - `step_universe(ctx)`, `step_prices(ctx)`, `step_edgar(ctx)`, `step_macro(ctx)`, each `-> StepResult`.
  - `run_update(ctx, steps=STEPS, funcs=None) -> list[StepResult]`. A step that raises is recorded as one failed item; the remaining steps still run. `provider_status` is updated only for steps at or under the failure limit. Every failure is written to `fetch_failures`.
  - `build_context(settings, rules, today, tickers) -> UpdateContext`.
  - CLI: `analyzer update [--step NAME ...] [--tickers AAPL,NFLX] [--today YYYY-MM-DD]`. Exit 0 when every step is within the limit, 1 when any step is over it, 2 on a `ConfigError`.

`--tickers` is for quick manual runs: it limits prices and EDGAR to those tickers (plus SPY) and skips the young-listing screen.

The first full run backfills everything: about 1,500 companies' submissions and companyfacts, roughly 50 quarterly insider zips, Form 4 XML for the months since the last published quarter, and ~1,300 price histories. Expect two to four hours on the first run (the Form 4 XML backlog and full price histories dominate). Later runs take minutes.

- [ ] **Step 1: Write the failing tests**

`tests/pipeline/test_update.py`:

```python
from datetime import date

import pytest
from typer.testing import CliRunner

from analyzer import cli
from analyzer.config import load_rules
from analyzer.pipeline.update import StepResult, UpdateContext, run_update, step_prices
from analyzer.store.db import connect, migrate
from tests.helpers import FakeProvider, bars


@pytest.fixture
def ctx(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return UpdateContext(conn=conn, settings=None, rules=load_rules(), clients=None,
                         price_providers=[], today=date(2026, 10, 9), run_id="run1")


def test_failed_step_is_recorded_and_others_still_run(ctx):
    def ok(c):
        return StepResult("prices", attempted=100, failed=[("X", "no data")],
                          latest_data_date=date(2026, 10, 9))

    def boom(c):
        raise RuntimeError("wikipedia down")

    results = run_update(ctx, ["universe", "prices"], funcs={"universe": boom, "prices": ok})
    assert [r.name for r in results] == ["universe", "prices"]
    assert results[0].failure_rate == 1.0
    assert results[1].failure_rate == 0.01
    status = ctx.conn.execute("SELECT provider, latest_data_date FROM provider_status").fetchall()
    assert status == [("prices", "2026-10-09")]
    failures = ctx.conn.execute("SELECT step, item FROM fetch_failures ORDER BY step").fetchall()
    assert failures == [("prices", "X"), ("universe", "*")]


def test_step_prices_refreshes_active_symbols(ctx):
    ctx.conn.execute("INSERT INTO index_membership VALUES ('sp500', 'AAPL', '2010-01-01', NULL)")
    data = {s: bars(["2026-10-08", "2026-10-09"], [1.0, 2.0]) for s in ("AAPL", "SPY", "XLK")}
    ctx.price_providers.append(FakeProvider("yfinance", data))
    result = step_prices(ctx)
    assert result.latest_data_date == date(2026, 10, 9)
    assert ("AAPL", "no price data from any provider") not in result.failed
    assert len(result.failed) == result.attempted - 3  # only AAPL, SPY and XLK have data


def test_step_prices_with_ticker_subset(ctx):
    ctx.tickers = {"AAPL"}
    ctx.conn.execute("INSERT INTO index_membership VALUES ('sp500', 'AAPL', '2010-01-01', NULL)")
    data = {s: bars(["2026-10-09"], [1.0]) for s in ("AAPL", "SPY")}
    ctx.price_providers.append(FakeProvider("yfinance", data))
    result = step_prices(ctx)
    assert result.attempted == 2 and result.failed == []


def test_cli_exit_codes(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    monkeypatch.setenv("ANALYZER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "build_context", lambda *a, **k: None)
    runner = CliRunner()

    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [StepResult("prices", attempted=10)])
    assert runner.invoke(cli.app, ["update"]).exit_code == 0

    bad = StepResult("prices", attempted=10, failed=[("A", "x")])
    monkeypatch.setattr(cli, "run_update", lambda ctx, steps: [bad])
    result = runner.invoke(cli.app, ["update", "--step", "prices"])
    assert result.exit_code == 1
    assert "prices" in result.output


def test_cli_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "no-email")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["update"])
    assert result.exit_code == 2
    assert "contact email" in result.output


def test_unknown_step_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "T t@example.com")
    result = CliRunner().invoke(cli.app, ["update", "--step", "nope"])
    assert result.exit_code == 2
```

Note on `test_cli_config_error`: `load_settings` reads `PROJECT_ROOT/.env`, and the process environment overrides it, so setting `SEC_USER_AGENT` to a value without `@` reliably triggers the error even if the developer has a real `.env`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzer.pipeline'`.

- [ ] **Step 3: Implement the pipeline**

`src/analyzer/pipeline/update.py`:

```python
"""`analyzer update`: run the data steps in order, isolate failures, record freshness."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from analyzer.config import Rules, Settings
from analyzer.data.clients import Clients, make_clients
from analyzer.data.edgar.companies import load_ticker_map
from analyzer.data.edgar.daily_index import scan_daily_indexes
from analyzer.data.edgar.insider_bulk import sync_insider
from analyzer.data.edgar.instance import apply_instance_fallback
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.sync import refresh_company
from analyzer.data.edgar.tags import PERIODIC_FORMS
from analyzer.data.fred import MACRO_SERIES, sync_macro
from analyzer.data.price_sync import latest_market_date, sync_prices
from analyzer.data.prices import NasdaqPrices, PriceProvider, YFinancePrices
from analyzer.store.db import connect, get_state, migrate, set_state, upsert_rows
from analyzer.universe.industry import ALL_REFERENCE_ETFS
from analyzer.universe.members import price_symbols
from analyzer.universe.membership import INDEXES
from analyzer.universe.registry import refresh_tickers
from analyzer.universe.wikipedia import sync_index_snapshots
from analyzer.universe.young import screen_young_listings

log = logging.getLogger(__name__)

STEPS = ("universe", "prices", "edgar", "macro")
FAILURE_LIMIT = 0.05
YOUNG_SCREEN_EVERY_DAYS = 7


@dataclass
class StepResult:
    name: str
    attempted: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    latest_data_date: date | None = None
    detail: str = ""

    @property
    def failure_rate(self) -> float:
        return len(self.failed) / self.attempted if self.attempted else 0.0


@dataclass
class UpdateContext:
    conn: sqlite3.Connection
    settings: Settings | None
    rules: Rules
    clients: Clients | None
    price_providers: list[PriceProvider]
    today: date
    run_id: str
    tickers: set[str] | None = None


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def step_universe(ctx: UpdateContext) -> StepResult:
    res = StepResult("universe")
    for index_name in INDEXES:
        snap = sync_index_snapshots(ctx.conn, ctx.clients.wiki, index_name, ctx.today)
        res.attempted += snap.attempted
        res.failed += snap.failed
    ticker_map = load_ticker_map(ctx.clients.sec)
    last = get_state(ctx.conn, "young_screen_last")
    due = last is None or (ctx.today - date.fromisoformat(last)).days >= YOUNG_SCREEN_EVERY_DAYS
    if due and ctx.tickers is None:
        index_tickers = {r[0] for r in ctx.conn.execute("SELECT DISTINCT ticker FROM index_snapshots")}
        kept = screen_young_listings(ctx.conn, ctx.price_providers[0], ticker_map, index_tickers,
                                     ctx.rules.universe, ctx.today)
        set_state(ctx.conn, "young_screen_last", ctx.today.isoformat())
        res.detail = f"young listings kept={len(kept)}"
    res.attempted += 1
    res.detail = f"{res.detail} tickers={refresh_tickers(ctx.conn, ticker_map, ctx.today)}".strip()
    res.latest_data_date = ctx.today
    return res


def step_prices(ctx: UpdateContext) -> StepResult:
    active, historical = price_symbols(ctx.conn, ctx.today, ctx.rules.universe)
    active = sorted(set(active) | set(ALL_REFERENCE_ETFS))
    if ctx.tickers is not None:
        active = sorted((set(active) & ctx.tickers) | {"SPY"})
        historical = []
    report = sync_prices(ctx.conn, ctx.price_providers, active, historical, ctx.today)
    return StepResult(
        "prices",
        attempted=len(active),
        failed=[(s, "no price data from any provider") for s in report.missing],
        latest_data_date=latest_market_date(ctx.conn),
        detail=(f"updated={len(report.updated)} fallback={len(report.via_fallback)} "
                f"refetched={len(report.refetched)} "
                f"historical_missing={len(report.historical_missing)}"),
    )


def step_edgar(ctx: UpdateContext) -> StepResult:
    res = StepResult("edgar")
    sec = ctx.clients.sec
    ticker_map = load_ticker_map(sec)
    merges = load_cik_merges(ticker_map)
    rows = ctx.conn.execute("SELECT ticker, cik FROM tickers WHERE cik IS NOT NULL").fetchall()
    tracked = {cik for t, cik in rows if ctx.tickers is None or t in ctx.tickers}
    predecessors = {p for c in tracked for p in merges.get(c, [])}
    known = {r[0] for r in ctx.conn.execute("SELECT cik FROM companies")}

    for cik in sorted((tracked | predecessors) - known):  # first time seen: full backfill
        res.attempted += 1
        try:
            refresh_company(ctx.conn, sec, cik, max_age=None if cik in predecessors else timedelta(hours=20))
        except Exception as exc:
            res.failed.append((str(cik), _err(exc)))

    changed = scan_daily_indexes(ctx.conn, sec, tracked & known, ctx.today)
    for cik, forms in sorted(changed.items()):
        res.attempted += 1
        try:
            refresh_company(ctx.conn, sec, cik, max_age=timedelta(0),
                            facts=bool(forms & PERIODIC_FORMS))
        except Exception as exc:
            res.failed.append((str(cik), _err(exc)))

    for cik in sorted(tracked):
        try:
            apply_instance_fallback(ctx.conn, sec, cik, ctx.today)
        except Exception as exc:
            res.attempted += 1
            res.failed.append((f"{cik} instance", _err(exc)))

    insider = sync_insider(ctx.conn, sec, tracked, ctx.today)
    refresh_tickers(ctx.conn, ticker_map, ctx.today)  # SIC-based ETFs for new companies
    latest = ctx.conn.execute("SELECT MAX(filing_date) FROM filings").fetchone()[0]
    res.latest_data_date = date.fromisoformat(latest) if latest else None
    res.attempted = max(res.attempted, len(tracked))
    res.detail = (f"companies={len(tracked)} changed={len(changed)} "
                  f"insider_quarters={len(insider.loaded)} form4_xml={insider.xml_filings}")
    return res


def step_macro(ctx: UpdateContext) -> StepResult:
    result = sync_macro(ctx.conn, ctx.clients.fred, ctx.settings.fred_api_key, ctx.today)
    latest = max(result.latest.values()) if result.latest else None
    return StepResult("macro", attempted=len(MACRO_SERIES), failed=result.failed,
                      latest_data_date=date.fromisoformat(latest) if latest else None)


STEP_FUNCS: dict[str, Callable[[UpdateContext], StepResult]] = {
    "universe": step_universe, "prices": step_prices, "edgar": step_edgar, "macro": step_macro,
}


def _record(ctx: UpdateContext, res: StepResult) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    upsert_rows(ctx.conn, "fetch_failures", ["run_id", "step", "item", "error", "at"],
                [(ctx.run_id, res.name, item, err, now) for item, err in res.failed])
    if res.failure_rate <= FAILURE_LIMIT:
        upsert_rows(ctx.conn, "provider_status",
                    ["provider", "last_success", "latest_data_date", "detail"],
                    [(res.name, now, res.latest_data_date.isoformat() if res.latest_data_date
                      else None, res.detail)])


def run_update(ctx: UpdateContext, steps: Sequence[str] = STEPS,
               funcs: dict[str, Callable[[UpdateContext], StepResult]] | None = None
               ) -> list[StepResult]:
    funcs = funcs or STEP_FUNCS
    results = []
    for name in steps:
        log.info("step %s: start", name)
        try:
            res = funcs[name](ctx)
        except Exception as exc:  # the whole step failed, e.g. a source is down
            log.exception("step %s failed", name)
            res = StepResult(name, attempted=1, failed=[("*", _err(exc))])
        _record(ctx, res)
        log.info("step %s: %d/%d failed", name, len(res.failed), res.attempted)
        results.append(res)
    return results


def build_context(settings: Settings, rules: Rules, today: date,
                  tickers: set[str] | None) -> UpdateContext:
    conn = connect(settings.db_path)
    migrate(conn)
    clients = make_clients(settings)
    return UpdateContext(conn=conn, settings=settings, rules=rules, clients=clients,
                         price_providers=[YFinancePrices(), NasdaqPrices(clients.nasdaq)],
                         today=today, run_id=uuid4().hex[:12], tickers=tickers)
```

`fetch_failures` has no primary key, so `upsert_rows` (`INSERT OR REPLACE`) simply inserts.

- [ ] **Step 4: Add the CLI command**

Replace `src/analyzer/cli.py` with:

```python
"""Command-line entry point: `analyzer ...`."""

import logging
from datetime import date

import typer

from analyzer.config import ConfigError, load_rules, load_settings
from analyzer.pipeline.update import FAILURE_LIMIT, STEPS, build_context, run_update

app = typer.Typer(help="Stock Analyzer engine.", no_args_is_help=True)


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging.")) -> None:
    """Stock Analyzer engine."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _parse_tickers(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {t.strip().upper().replace(".", "-") for t in value.split(",") if t.strip()}


@app.command()
def update(
    step: list[str] = typer.Option(None, "--step", help=f"Run only these steps: {', '.join(STEPS)}"),
    tickers: str = typer.Option(None, help="Comma-separated subset for a quick run."),
    today: str = typer.Option(None, help="Override today's date (YYYY-MM-DD)."),
) -> None:
    """Fetch and store the latest data for the whole universe."""
    steps = step or list(STEPS)
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        typer.echo(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
        raise typer.Exit(2)
    try:
        settings = load_settings()
        rules = load_rules()
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}")
        raise typer.Exit(2) from exc
    run_day = date.fromisoformat(today) if today else date.today()
    ctx = build_context(settings, rules, run_day, _parse_tickers(tickers))
    results = run_update(ctx, steps)
    for r in results:
        latest = r.latest_data_date.isoformat() if r.latest_data_date else "-"
        typer.echo(f"{r.name:9} attempted={r.attempted:<6} failed={len(r.failed):<5} "
                   f"({r.failure_rate:.1%}) latest={latest} {r.detail}")
        for item, err in r.failed[:10]:
            typer.echo(f"  ! {item}: {err}")
        if len(r.failed) > 10:
            typer.echo(f"  ... {len(r.failed) - 10} more in table fetch_failures")
    if any(r.failure_rate > FAILURE_LIMIT for r in results):
        raise typer.Exit(1)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v && uv run ruff check .`
Expected: the whole suite passes; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/pipeline src/analyzer/cli.py tests/pipeline
git commit -m "feat: add analyzer update with failure isolation and freshness tracking"
```

---

### Task 16: `analyzer audit` coverage report

**Files:**
- Create: `src/analyzer/pipeline/audit.py`, `tests/pipeline/test_audit.py`
- Modify: `src/analyzer/cli.py`

**Interfaces:**
- Consumes: `load_concept` (Task 8), `discrete_quarters` (Task 9), `load_ticker_map`, `load_cik_merges` (Tasks 7–8), `make_clients` (Task 3).
- Produces: `TickerAudit(ticker, cik, price_first: date | None, price_last: date | None, revenue_quarters: int, first_revenue_quarter: date | None, has_fcf: bool, facts_behind_filing: bool, sector_etf, industry_etf, flags: list[str])`, `audit(conn, today, merges=None, tickers=None) -> list[TickerAudit]`, `summarize(audits) -> dict[str, int]`. CLI: `analyzer audit [--tickers ...] [--all]` (default prints flagged tickers only).

Flags:
- `no CIK`
- `no prices`
- `stale prices` (last bar more than 4 days before `today`)
- `no fundamentals` (no revenue quarters)
- `check CIK merge` (prices start before 2012 but revenue quarters start 3+ years after prices)
- `companyfacts behind latest 10-Q/10-K` (latest periodic filing's accession has no facts, even after the instance fallback)
- `no FCF` (fewer than 4 recent quarters of both operating cash flow and capex; expected for banks)
- `no sector ETF`

- [ ] **Step 1: Write the failing tests**

`tests/pipeline/test_audit.py`:

```python
from datetime import date

import pandas as pd
import pytest

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


def test_audit_subset_and_summary(conn):
    audits = audit(conn, TODAY, tickers={"GOOD", "LAG"})
    assert [a.ticker for a in audits] == ["GOOD", "LAG"]
    s = summarize(audits)
    assert s["tickers"] == 2 and s["with prices"] == 2 and s["with 8+ revenue quarters"] == 0
    assert s["flagged"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/pipeline/test_audit.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/analyzer/pipeline/audit.py`:

```python
"""`analyzer audit`: per-ticker data coverage and the problems worth a look."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from analyzer.data.edgar.facts_read import load_concept
from analyzer.data.edgar.quarters import discrete_quarters

STALE_PRICE_DAYS = 4
FCF_WINDOW_DAYS = 470  # four quarters plus filing lag


@dataclass
class TickerAudit:
    ticker: str
    cik: int | None
    price_first: date | None = None
    price_last: date | None = None
    revenue_quarters: int = 0
    first_revenue_quarter: date | None = None
    has_fcf: bool = False
    facts_behind_filing: bool = False
    sector_etf: str | None = None
    industry_etf: str | None = None
    flags: list[str] = field(default_factory=list)


def _quarters(conn, cik, concept, merges) -> pd.DataFrame:
    return discrete_quarters(load_concept(conn, cik, concept, merges=merges))


def _recent_count(q: pd.DataFrame, today: date) -> int:
    if q.empty:
        return 0
    cutoff = pd.Timestamp(today - timedelta(days=FCF_WINDOW_DAYS))
    return int((q["period_end"] >= cutoff).sum())


def audit(conn, today: date, merges: Mapping[int, Sequence[int]] | None = None,
          tickers: set[str] | None = None) -> list[TickerAudit]:
    rows = conn.execute(
        "SELECT ticker, cik, sector_etf, industry_etf FROM tickers ORDER BY ticker").fetchall()
    out = []
    for ticker, cik, sector_etf, industry_etf in rows:
        if tickers is not None and ticker not in tickers:
            continue
        a = TickerAudit(ticker, cik, sector_etf=sector_etf, industry_etf=industry_etf)
        first, last = conn.execute(
            "SELECT MIN(date), MAX(date) FROM prices WHERE symbol = ?", (ticker,)).fetchone()
        a.price_first = date.fromisoformat(first) if first else None
        a.price_last = date.fromisoformat(last) if last else None
        if cik is not None:
            revenue = _quarters(conn, cik, "revenue", merges)
            a.revenue_quarters = len(revenue)
            if not revenue.empty:
                a.first_revenue_quarter = revenue["period_end"].min().date()
            ocf = _quarters(conn, cik, "operating_cash_flow", merges)
            capex = _quarters(conn, cik, "capex", merges)
            a.has_fcf = min(_recent_count(ocf, today), _recent_count(capex, today)) >= 4
            latest = conn.execute(
                "SELECT accession FROM filings WHERE cik = ? AND form IN ('10-Q', '10-K') "
                "ORDER BY filing_date DESC LIMIT 1", (cik,)).fetchone()
            if latest:
                has = conn.execute("SELECT 1 FROM facts WHERE cik = ? AND accession = ? LIMIT 1",
                                   (cik, latest[0])).fetchone()
                a.facts_behind_filing = has is None
        a.flags = _flags(a, today)
        out.append(a)
    return out


def _flags(a: TickerAudit, today: date) -> list[str]:
    flags = []
    if a.cik is None:
        flags.append("no CIK")
    if a.price_last is None:
        flags.append("no prices")
    elif (today - a.price_last).days > STALE_PRICE_DAYS:
        flags.append("stale prices")
    if a.cik is not None and a.revenue_quarters == 0:
        flags.append("no fundamentals")
    if (a.price_first and a.price_first < date(2012, 1, 1) and a.first_revenue_quarter
            and a.first_revenue_quarter.year >= a.price_first.year + 3):
        flags.append("check CIK merge")
    if a.facts_behind_filing:
        flags.append("companyfacts behind latest 10-Q/10-K")
    if a.cik is not None and a.revenue_quarters and not a.has_fcf:
        flags.append("no FCF")
    if a.sector_etf is None:
        flags.append("no sector ETF")
    return flags


def summarize(audits: list[TickerAudit]) -> dict[str, int]:
    return {
        "tickers": len(audits),
        "with prices": sum(a.price_last is not None for a in audits),
        "with 8+ revenue quarters": sum(a.revenue_quarters >= 8 for a in audits),
        "with FCF": sum(a.has_fcf for a in audits),
        "companyfacts behind": sum(a.facts_behind_filing for a in audits),
        "flagged": sum(bool(a.flags) for a in audits),
    }
```

Note on `test_audit_flags`: GOOD has exactly 4 recent quarters of OCF and capex, so `has_fcf` is true and it has no flags; MERGE has revenue but no FCF, so it also gets `no FCF` besides `check CIK merge`.

- [ ] **Step 4: Add the CLI command**

Add to `src/analyzer/cli.py`: the imports go in the import block at the top, the command at the bottom.

```python
from analyzer.data.clients import make_clients
from analyzer.data.edgar.companies import load_ticker_map
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.pipeline.audit import audit as run_audit
from analyzer.pipeline.audit import summarize
from analyzer.store.db import connect, migrate


@app.command()
def audit(
    tickers: str = typer.Option(None, help="Comma-separated subset."),
    show_all: bool = typer.Option(False, "--all", help="List every ticker, not only flagged ones."),
) -> None:
    """Report data coverage and problems per ticker."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        typer.echo(f"Configuration error: {exc}")
        raise typer.Exit(2) from exc
    conn = connect(settings.db_path)
    migrate(conn)
    merges = load_cik_merges(load_ticker_map(make_clients(settings).sec))
    audits = run_audit(conn, date.today(), merges, _parse_tickers(tickers))
    for key, value in summarize(audits).items():
        typer.echo(f"{key:28} {value}")
    typer.echo("")
    for a in audits:
        if a.flags or show_all:
            typer.echo(f"{a.ticker:8} cik={a.cik or '-':<9} prices={a.price_first or '-'}..{a.price_last or '-'} "
                       f"rev_q={a.revenue_quarters:<3} etf={a.sector_etf or '-'}/{a.industry_etf or '-'} "
                       f"{'; '.join(a.flags)}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/analyzer/pipeline/audit.py src/analyzer/cli.py tests/pipeline/test_audit.py
git commit -m "feat: add analyzer audit coverage report"
```

---

### Task 17: Live smoke tests, first full run and docs

**Files:**
- Create: `tests/live/__init__.py` (empty), `tests/live/test_sources.py`
- Modify: `README.md`, `docs/plans/2026-09-29-stock-analyzer-design-progress.md` (section 8), and, only if a live test shows a mismatch, `src/analyzer/data/edgar/insider_bulk.py`, `src/analyzer/data/edgar/cik_merges.toml`, `src/analyzer/data/prices.py`

**Interfaces:**
- Consumes: everything above. No new production interfaces.

These tests check the assumptions that unit tests can't: real response shapes, the EDGAR quirks on real companies, and the bulk dataset's column names. They need `.env` with `SEC_USER_AGENT` (and `FRED_API_KEY` for the FRED test).

- [ ] **Step 1: Write the live tests**

`tests/live/test_sources.py`:

```python
"""Opt-in checks against the real sources: uv run pytest -m live -v"""

import io
import zipfile
from datetime import date, timedelta

import pandas as pd
import pytest

from analyzer.config import ConfigError, load_settings
from analyzer.data.clients import make_clients
from analyzer.data.edgar.companies import fetch_submissions, load_ticker_map
from analyzer.data.edgar.companyfacts import extract_facts, fetch_companyfacts, store_facts
from analyzer.data.edgar.daily_index import fetch_master_index
from analyzer.data.edgar.facts_read import load_concept
from analyzer.data.edgar.insider_bulk import BULK_URL, parse_insider_zip
from analyzer.data.edgar.instance import fetch_instance_facts
from analyzer.data.edgar.merges import load_cik_merges
from analyzer.data.edgar.quarters import discrete_quarters
from analyzer.data.fred import fetch_series
from analyzer.data.prices import NasdaqPrices, YFinancePrices
from analyzer.store.db import connect, migrate
from analyzer.universe.wikipedia import snapshot_for

pytestmark = pytest.mark.live
TODAY = date.today()


@pytest.fixture(scope="module")
def settings():
    try:
        return load_settings()
    except ConfigError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def clients(settings):
    return make_clients(settings)


@pytest.fixture(scope="module")
def ticker_map(clients):
    return load_ticker_map(clients.sec)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "live.db")
    migrate(c)
    return c


def cik(ticker_map, ticker):
    return int(ticker_map.set_index("ticker").at[ticker, "cik"])


def facts_for(conn, clients, ticker_map, ticker, merges=None):
    c = cik(ticker_map, ticker)
    for k in [c, *(merges or {}).get(c, [])]:
        store_facts(conn, extract_facts(fetch_companyfacts(clients.sec, k), k))
    return c


def test_yfinance_and_nasdaq(clients):
    start = TODAY - timedelta(days=14)
    assert "AAPL" in YFinancePrices().fetch(["AAPL"], start, TODAY)
    out = NasdaqPrices(clients.nasdaq).fetch(["BRK-B", "SMH"], start, TODAY)
    assert set(out) == {"BRK-B", "SMH"}


def test_wikipedia_current_and_2014(clients):
    for index_name in ("sp500", "sp400"):
        _, now = snapshot_for(clients.wiki, index_name, TODAY + timedelta(days=1), TODAY)
        _, then = snapshot_for(clients.wiki, index_name, date(2014, 1, 1), TODAY)
        assert now["gics_sector"].notna().mean() > 0.95
        assert len(then) > 350


def test_ticker_map_and_submissions(clients, ticker_map):
    subs = fetch_submissions(clients.sec, cik(ticker_map, "AAPL"))
    assert subs.sic == "3571"
    assert (subs.filings.form == "10-Q").sum() >= 30  # older pages were read


def test_nvda_capex_is_continuous(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "NVDA")
    q = discrete_quarters(load_concept(conn, c, "capex"))
    q = q[q.period_end >= "2015-01-01"]
    gaps = q.period_end.diff().dt.days.dropna()
    assert len(q) >= 40 and gaps.max() <= 120


def test_xom_history_survives_cik_change(conn, clients, ticker_map):
    merges = load_cik_merges(ticker_map)
    c = facts_for(conn, clients, ticker_map, "XOM", merges)
    revenue = discrete_quarters(load_concept(conn, c, "revenue", merges=merges))
    # If this fails, look up XOM's predecessor CIK on EDGAR and fix cik_merges.toml.
    assert revenue.period_end.min() <= pd.Timestamp("2012-12-31")


def test_cost_sixteen_week_quarter(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "COST")
    q = discrete_quarters(load_concept(conn, c, "revenue"))
    assert ((q.days >= 105) & (q.days <= 120)).any()


def test_bank_has_net_income_but_no_operating_income(conn, clients, ticker_map):
    c = facts_for(conn, clients, ticker_map, "JPM")
    assert not load_concept(conn, c, "net_income").empty
    recent = load_concept(conn, c, "operating_income")
    assert recent.empty or recent.period_end.max() < pd.Timestamp(TODAY - timedelta(days=365))


def test_instance_document_of_latest_10q(clients, ticker_map):
    c = cik(ticker_map, "AAPL")
    subs = fetch_submissions(clients.sec, c)
    latest = subs.filings[subs.filings.form == "10-Q"].iloc[0]
    rows = fetch_instance_facts(clients.sec, c, latest.accession, "10-Q", latest.filing_date)
    assert any(r[1] == "revenue" for r in rows)


def test_insider_bulk_columns(clients, ticker_map):
    data = clients.sec.get_bytes(BULK_URL.format(label="2024q1"), cache_key="sec/insider/2024q1.zip")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = {n.split("/")[-1].upper() for n in z.namelist()}
    assert {"SUBMISSION.TSV", "REPORTINGOWNER.TSV", "NONDERIV_TRANS.TSV"} <= names
    rows = parse_insider_zip(data, {cik(ticker_map, t) for t in ("NKE", "PFE", "INTC", "JPM")})
    assert rows and any(r[8] == "P" for r in rows)


def test_daily_index_recent_weekday(clients):
    d = TODAY - timedelta(days=7)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    entries = fetch_master_index(clients.sec, d)
    assert entries is None or len(entries) > 1000  # None only on a market holiday


def test_fred(settings, clients):
    if not settings.fred_api_key:
        pytest.skip("FRED_API_KEY not set")
    rows = fetch_series(clients.fred, settings.fred_api_key, "NFCI", TODAY - timedelta(days=60))
    assert rows
```

- [ ] **Step 2: Run the live tests and fix real-world mismatches**

Run: `uv run pytest -m live -v`
Expected: all pass. If one fails:
- `test_insider_bulk_columns`: compare the column names with the real `SUBMISSION.tsv` / `REPORTINGOWNER.tsv` / `NONDERIV_TRANS.tsv` headers. Fix the names in `parse_insider_zip` and in `make_zip` in `tests/data/edgar/test_insider_bulk.py` together, then rerun both suites.
- `test_xom_history_survives_cik_change`: search EDGAR company search for "Exxon Mobil" to find the predecessor CIK, update `cik_merges.toml`, rerun.
- `test_yfinance_and_nasdaq` (Nasdaq part): print the raw response for the failing symbol and adjust `NasdaqPrices._fetch_one`.
Commit any fix with its own message, e.g. `fix: match SEC insider dataset column names`.

- [ ] **Step 3: First full run**

Run: `uv run analyzer update`
Expected: completes in two to four hours on the first run (Form 4 XML backlog and full price histories dominate); each step prints `failed=` at or below 5% and the command exits 0. A run that exits 1 lists the failing items; rerun `uv run analyzer update --step <name>` after fixing or after a transient outage.

Then run: `uv run analyzer audit`
Expected: a summary with ~900+ index tickers plus young listings, most with prices and 8+ revenue quarters. Review the flagged list:
- `check CIK merge` on a well-known company: add an entry to `cik_merges.toml`, rerun `uv run analyzer update --step edgar`.
- `no sector ETF` on index members: a new GICS sub-industry or sector name; add it to `SECTOR_ETFS` / `SUB_INDUSTRY_ETFS`.
- `no FCF` on banks and insurers is expected.

Run it a second time: `uv run analyzer update`
Expected: finishes in a few minutes; prices `updated=` roughly equals the active count, `refetched=` is small (dividends/splits), EDGAR `changed=` is a handful of companies.

- [ ] **Step 4: Update docs**

In `README.md`, replace the "Status" section's first line with "Phase 1 (data layer) is implemented. Scoring, research and the dashboard are next." and add a section after "Status":

````markdown
## Development

```bash
uv sync
cp .env.example .env        # set SEC_USER_AGENT (with your email) and FRED_API_KEY
uv run pytest               # unit tests, no network
uv run pytest -m live       # checks against the real data sources
uv run analyzer update      # first run backfills everything (2–4 hours: Form 4 XML backlog and full price histories dominate)
uv run analyzer audit       # coverage and data problems
```
````

In `docs/plans/2026-09-29-stock-analyzer-design-progress.md`, section 8, replace the price-fallback bullet with "Price fallback: Nasdaq public endpoint, chosen by the 2026-10-10 probe (see the spec)." and add one bullet with the coverage numbers from the first `analyzer audit` summary (tickers, with prices, with 8+ revenue quarters, with FCF, flagged).

- [ ] **Step 5: Commit**

```bash
git add tests/live README.md docs
git commit -m "test: add live source checks; docs: phase 1 run instructions and coverage"
```

---

## Self-review notes

- **Spec coverage (phase 1 scope):** universe incl. past members and young listings (Tasks 6, 14), liquidity filter (Task 14), all section 3 sources (Tasks 4–13), data-layer requirements 3.1 (Tasks 3–5), EDGAR quirks 3.2 (Tasks 8–10), point-in-time rules 3.3 for fundamentals and market cap (Tasks 8, 14), freshness and failure handling 11.2 for `update` (Task 15), storage/config 11.3 (Tasks 1–2), testing 12 for the data layer (all tasks, Task 17 live). Out of phase 1 scope by design: features, signals, research, reports, dashboard, `scan`'s stale-price abort (phase 2/3 read `provider_status`), the systemd timer (phase 3).
- **Known limitations carried into phase 2:** short-term debt picks one tag per period (may undercount); multi-class share counts come from non-dimensional facts only; market cap uses split-adjusted price × last reported shares, so it is off by the split ratio between a split and the next filing; reference ETFs XLC (2018) and XLRE (2015) have no earlier history; delisted tickers often have no yfinance prices (reported by `audit` and by `historical_missing`).
