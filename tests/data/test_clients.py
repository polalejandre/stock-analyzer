from analyzer.config import Settings
from analyzer.data.clients import PLAIN_UA, make_clients


def _settings(tmp_path, wikipedia_contact):
    return Settings(sec_user_agent="T t@test.invalid", fred_api_key=None, data_dir=tmp_path,
                    wikipedia_contact=wikipedia_contact)


def test_wikipedia_user_agent_carries_the_contact(tmp_path):
    clients = make_clients(_settings(tmp_path, "wiki@test.invalid"))
    agent = clients.wiki.headers["User-Agent"]
    assert agent.startswith("stock-analyzer/")
    assert "wiki@test.invalid" in agent
    assert "wiki@test.invalid" not in clients.fred.headers["User-Agent"]


def test_wikipedia_user_agent_without_contact_is_plain(tmp_path):
    clients = make_clients(_settings(tmp_path, None))
    assert clients.wiki.headers["User-Agent"] == PLAIN_UA
