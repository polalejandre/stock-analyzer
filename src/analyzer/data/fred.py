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
