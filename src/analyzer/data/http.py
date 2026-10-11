"""The single way the engine talks to the network: disk cache, rate limit, retry."""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import zlib
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


def _safe(url: str) -> str:
    """The URL without its query string, safe to put in messages and logs."""
    return url.split("?", 1)[0]


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
        validate: Callable[[bytes], Any] | None = None,
    ) -> bytes:
        """Return the body. If `validate` raises ValueError, the body is rejected.

        A rejected cached body is dropped and refetched. A rejected fresh body is
        never cached and raises FetchError.
        """
        if cache_key is not None:
            cached = self._read_cache(cache_key, max_age)
            if cached is not None:
                try:
                    if validate is not None:
                        validate(cached)
                    return cached
                except ValueError:
                    log.warning("dropping invalid cache entry %s", cache_key)
                    self.cache_path(cache_key).unlink(missing_ok=True)
        body = self._fetch(url, params)
        if validate is not None:
            try:
                validate(body)
            except ValueError as exc:
                raise FetchError(f"invalid response body for {_safe(url)}") from exc
        if cache_key is not None:
            self._write_cache(cache_key, body)
        return body

    def get_text(self, url: str, **kwargs) -> str:
        return self.get_bytes(url, **kwargs).decode("utf-8", errors="replace")

    def get_json(self, url: str, **kwargs) -> Any:
        return json.loads(self.get_bytes(url, validate=json.loads, **kwargs))

    def _read_cache(self, key: str, max_age: timedelta | None) -> bytes | None:
        path = self.cache_path(key)
        if not path.exists():
            return None
        if max_age is not None and self.now() - path.stat().st_mtime >= max_age.total_seconds():
            return None
        try:
            return gzip.decompress(path.read_bytes())
        except (OSError, EOFError, zlib.error):
            log.warning("corrupt cache file %s, treating as a miss", key)
            path.unlink(missing_ok=True)
            return None

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
                    raise NotFound(_safe(url))
                if resp.status_code < 400:
                    return resp.content
                error = f"HTTP {resp.status_code}"
                if resp.status_code not in RETRY_STATUSES:
                    break
            if attempt < self.max_retries:
                log.debug("retrying %s after %s", _safe(url), error)
                self.sleep(2**attempt)
        raise FetchError(f"{error} for {_safe(url)}")
