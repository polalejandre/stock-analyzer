import socket

import pytest
import requests

from tests.conftest import NETWORK_ERROR


def test_requests_are_refused():
    with pytest.raises(RuntimeError, match=NETWORK_ERROR):
        requests.get("https://example.org/")
    with pytest.raises(RuntimeError, match=NETWORK_ERROR):
        requests.Session().get("https://example.org/")


def test_raw_sockets_are_refused():
    with pytest.raises(RuntimeError, match=NETWORK_ERROR):
        socket.create_connection(("example.org", 443), timeout=1)
    with socket.socket() as s, pytest.raises(RuntimeError, match=NETWORK_ERROR):
        s.connect(("127.0.0.1", 9))


def test_yfinance_transport_is_refused():
    from curl_cffi import requests as curl_requests

    with pytest.raises(RuntimeError, match=NETWORK_ERROR):
        curl_requests.Session().get("https://query2.finance.yahoo.com/")



@pytest.mark.live
def test_live_tests_are_not_guarded():
    from tests import conftest

    assert requests.Session.request is not conftest._refuse
    assert socket.create_connection is not conftest._refuse
