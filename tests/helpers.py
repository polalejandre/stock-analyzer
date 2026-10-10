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
