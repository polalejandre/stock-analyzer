"""Shared test doubles. No test may touch the network."""

from __future__ import annotations

import json

import pandas as pd

from analyzer.data.http import HttpClient
from analyzer.data.prices import PRICE_COLUMNS


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
        if self.handler:
            r = self.handler(url, params)
        else:
            if not self.responses:
                raise AssertionError(f"unexpected extra request to {url}")
            r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(tmp_path, *, responses=None, handler=None, **kwargs):
    session = FakeSession(responses, handler)
    kwargs.setdefault("sleep", lambda s: None)
    client = HttpClient(tmp_path / "cache", "Test test@example.com", session=session, **kwargs)
    return client, session


def bars(dates, closes, adj=None, volume=1_000_000.0):
    """A price frame in provider format. `adj` defaults to `closes`."""
    adj = closes if adj is None else adj
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates).astype("datetime64[ns]"),  # providers emit ns
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
