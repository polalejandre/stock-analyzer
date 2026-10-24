import numpy as np
import pandas as pd

from analyzer.features.windows import days_since, reduce_since, since_max
from tests.panel_fixtures import assert_truncation_invariant, random_walk


def test_since_max_counts_back_to_the_latest_high():
    v = np.array([[1.0], [5.0], [3.0], [2.0], [5.0], [4.0]])
    s = since_max(v, window=3)
    assert np.isnan(s[:2, 0]).all()
    assert s[2:, 0].tolist() == [1.0, 2.0, 0.0, 1.0]


def test_since_max_is_nan_over_a_gap():
    v = np.array([[1.0], [np.nan], [3.0], [2.0], [1.0]])
    s = since_max(v, 3)
    assert np.isnan(s[2, 0]) and np.isnan(s[3, 0])     # windows that include the gap
    assert s[4, 0] == 2.0


def test_reduce_since_min_and_sum():
    v = np.array([[4.0], [3.0], [5.0], [1.0]])
    since = np.array([[np.nan], [1.0], [0.0], [2.0]])
    assert reduce_since(v, since, 3, "min")[:, 0].tolist()[1:] == [3.0, 5.0, 1.0]
    assert reduce_since(v, since, 3, "sum")[3, 0] == 9.0


def test_days_since():
    f = pd.DataFrame({"A": [False, True, False, False, True]})
    assert days_since(f)["A"].tolist()[1:] == [0.0, 1.0, 2.0, 0.0]
    assert np.isnan(days_since(f)["A"].iloc[0])


def test_window_helpers_are_truncation_invariant_across_chunks():
    # 70 columns spans more than one column chunk
    closes = random_walk([f"S{i}" for i in range(70)], n=120, seed=7)
    closes.iloc[30:33, 3] = np.nan

    def compute(end):
        c = closes if end is None else closes.loc[:end]
        v = c.to_numpy()
        since = since_max(v, 20)
        idx, cols = c.index, c.columns
        return {
            "since": pd.DataFrame(since, index=idx, columns=cols),
            "min": pd.DataFrame(reduce_since(v, since, 20, "min"), index=idx, columns=cols),
            "sum": pd.DataFrame(reduce_since(v, since, 20, "sum"), index=idx, columns=cols),
            "count": pd.DataFrame(reduce_since(v, since, 20, "count"), index=idx, columns=cols),
            "days": days_since(c.pct_change() <= -0.02),
        }
    assert_truncation_invariant(compute, [closes.index[60], closes.index[100]])
