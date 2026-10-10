"""Daily OHLCV providers. yfinance is primary; the Nasdaq public endpoint is the fallback."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

import pandas as pd

from analyzer.data.http import HttpClient

log = logging.getLogger(__name__)

PRICE_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

# {symbol: [(split date, ratio), ...]}; ratio 10.0 is a 10-for-1 split, 0.1 a 1-for-10 reverse.
# A symbol that is present with an empty list had no split in the window; an absent symbol means
# the provider said nothing about its splits.
Splits = dict[str, list[tuple[date, float]]]


class PriceProvider(Protocol):
    """`fetch` returns split-adjusted bars. A provider that also knows split events offers
    `fetch_with_splits(symbols, start, end) -> (frames, Splits)` (see `fetch_frames_and_splits`)."""

    name: str

    def fetch(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, pd.DataFrame]: ...


def fetch_frames_and_splits(
    provider: PriceProvider, symbols: Sequence[str], start: date, end: date
) -> tuple[dict[str, pd.DataFrame], Splits]:
    """Bars, plus split events for the returned symbols when the provider reports them."""
    with_splits = getattr(provider, "fetch_with_splits", None)
    if with_splits is None:
        return provider.fetch(symbols, start, end), {}
    frames, splits = with_splits(symbols, start, end)
    return frames, {s: splits[s] for s in frames if s in splits}


def _yf_download(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    return yf.download(
        symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
        auto_adjust=False,
        actions=True,  # split events ride on the same request; adds Dividends/Stock Splits columns
        group_by="ticker",
        threads=True,
        progress=False,
    )


def _normalize_dates(values) -> pd.Series:
    d = pd.to_datetime(pd.Series(values))
    if d.dt.tz is not None:
        d = d.dt.tz_localize(None)  # keep the exchange-local wall date
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
        f["volume"] = f["volume"].astype("float64")
        out[sym] = f[PRICE_COLUMNS].sort_values("date").reset_index(drop=True)
    return out


def _yf_splits(raw: pd.DataFrame, symbols: Sequence[str]) -> Splits:
    """Non-zero "Stock Splits" values per symbol (yfinance reports 0 on days without a split).
    Symbols without that column are left out: nothing is known about their splits."""
    out: Splits = {}
    if raw is None or raw.empty or not isinstance(raw.columns, pd.MultiIndex):
        return out
    present = set(raw.columns.get_level_values(0))
    for sym in symbols:
        if sym not in present or "Stock Splits" not in raw[sym].columns:
            continue
        ratios = pd.to_numeric(raw[sym]["Stock Splits"], errors="coerce")
        ratios = ratios[ratios.notna() & (ratios > 0)]
        days = _normalize_dates(ratios.index)
        out[sym] = sorted((d.date(), float(r)) for d, r in zip(days, ratios, strict=True))
    return out


class YFinancePrices:
    name = "yfinance"

    def __init__(self, download: Callable | None = None, batch_size: int = 100):
        self._download = download or _yf_download
        self.batch_size = batch_size

    def fetch(self, symbols, start, end):
        return self.fetch_with_splits(symbols, start, end)[0]

    def fetch_with_splits(self, symbols, start, end) -> tuple[dict[str, pd.DataFrame], Splits]:
        out: dict[str, pd.DataFrame] = {}
        splits: Splits = {}
        symbols = list(symbols)
        for i in range(0, len(symbols), self.batch_size):
            batch = symbols[i : i + self.batch_size]
            try:
                raw = self._download(batch, start, end)
                frames = _split_yf_frame(raw, batch)
                found = _yf_splits(raw, list(frames))
            except Exception as exc:  # yfinance raises many unrelated exception types
                log.warning("yfinance batch of %d failed: %s", len(batch), exc)
                continue
            out.update(frames)
            splits.update(found)
        return out, splits


NASDAQ_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"


def _num(text) -> float:
    if text is None:
        return float("nan")
    t = str(text).replace("$", "").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:  # "", "N/A", "--" and other non-numbers
        return float("nan")


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
    f["volume"] = f["volume"].astype("float64")
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
            except Exception as exc:  # one bad symbol must not abort the chain
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
    splits: Splits = field(default_factory=dict)  # only for symbols whose provider reports them


def fetch_with_fallback(
    providers: Sequence[PriceProvider], symbols: Sequence[str], start: date, end: date
) -> FetchResult:
    result = FetchResult()
    remaining = list(symbols)
    for provider in providers:
        if not remaining:
            break
        try:
            got, splits = fetch_frames_and_splits(provider, remaining, start, end)
        except Exception as exc:  # a broken provider must not stop the chain
            log.warning("price provider %s failed: %s", provider.name, exc)
            continue
        for sym, frame in got.items():
            result.frames[sym] = frame
            result.sources[sym] = provider.name
        result.splits.update(splits)
        remaining = [s for s in remaining if s not in got]
    result.missing = remaining
    return result
