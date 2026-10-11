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


def wiki_user_agent(contact: str | None) -> str:
    """Wikimedia's User-Agent policy asks for a contact; without one, 429s are more likely."""
    if contact is None:
        return PLAIN_UA
    return f"stock-analyzer/0.1 (personal non-commercial research; {contact})"


def make_clients(settings: Settings) -> Clients:
    cache = settings.cache_dir
    return Clients(
        sec=HttpClient(cache, settings.sec_user_agent, min_interval=0.125),  # SEC: <= 10 req/s
        wiki=HttpClient(cache, wiki_user_agent(settings.wikipedia_contact), min_interval=0.2),
        nasdaq=HttpClient(
            cache, BROWSER_UA, min_interval=0.3, extra_headers={"Accept": "application/json"}
        ),
        fred=HttpClient(cache, PLAIN_UA, min_interval=0.6),  # FRED: 120 req/min
    )
