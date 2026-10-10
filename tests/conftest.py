"""Default tests never touch the network: every outbound path raises unless marked `live`."""

from __future__ import annotations

import socket

import pytest
import requests

NETWORK_ERROR = "network access in a default test"


def _refuse(*args, **kwargs):
    raise RuntimeError(NETWORK_ERROR)


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if request.node.get_closest_marker("live") is not None:
        return
    monkeypatch.setattr(requests.Session, "request", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    try:  # yfinance talks through curl_cffi, which bypasses requests and Python sockets
        from curl_cffi import requests as curl_requests
    except ImportError:  # pragma: no cover - curl_cffi ships with yfinance
        return
    monkeypatch.setattr(curl_requests.Session, "request", _refuse)
