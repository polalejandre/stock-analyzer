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
