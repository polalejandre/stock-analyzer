import pytest

from tests.panel_fixtures import assert_truncation_invariant, cut, random_walk

CLOSES = random_walk(["A", "B"], n=120, seed=3)
DATES = [CLOSES.index[60], CLOSES.index[90]]


def test_clean_compute_passes():
    assert_truncation_invariant(lambda end: cut(CLOSES, end).rolling(10).mean(), DATES)


def test_leaky_compute_fails():
    def leaky(end):
        x = cut(CLOSES, end)
        # peeks one row ahead: a ~1e-6 relative leak that exists only when later data does
        return x + 1e-6 * x.shift(-1).fillna(0)

    with pytest.raises(AssertionError):
        assert_truncation_invariant(leaky, DATES)


def test_empty_cut_dates_rejected():
    with pytest.raises(AssertionError):
        assert_truncation_invariant(lambda end: cut(CLOSES, end), [])
