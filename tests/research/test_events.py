from types import SimpleNamespace

import pandas as pd

from analyzer.config import load_rules
from analyzer.research.events import (
    GROUP_DISLOCATED,
    GROUP_DISLOCATED_ALL,
    GROUP_SETUP,
    assign_period,
    cooldown_mask,
    select_events,
)

R = load_rules().research


def test_cooldown_keeps_first_day_then_waits():
    rows = pd.DataFrame({"symbol": ["A"] * 50 + ["B"] * 2, "pos": list(range(50)) + [3, 4]})
    keep = cooldown_mask(rows, 42)
    assert rows[keep]["pos"].tolist() == [0, 42, 3]


def test_setups_get_their_own_cooldown():
    rows = pd.DataFrame({"symbol": ["A"] * 20, "pos": range(20), "setup": [False] * 10 + [True] * 10})
    g2 = rows[cooldown_mask(rows, 42)]
    setups = rows[rows["setup"]]
    g3 = setups[cooldown_mask(setups, 42)]
    assert g2["pos"].tolist() == [0] and g3["pos"].tolist() == [10]


def test_periods_and_purge():
    dates = pd.bdate_range("2013-01-01", "2023-06-30")

    def pos(d):
        return dates.searchsorted(pd.Timestamp(d))

    ev = pd.DataFrame({"pos": [pos("2013-06-03"), pos("2015-03-02"), pos("2021-11-01"),
                               pos("2022-03-01"), len(dates) - 10]})
    assert assign_period(ev, dates, load_rules()).tolist() == [
        "before_start", "in_sample", "purged", "oos", "oos_incomplete"]


def test_select_events_three_groups_with_separate_cooldowns():
    rows = pd.DataFrame({
        "symbol": ["A"] * 20,
        "pos": range(20),
        "setup": [False] * 10 + [True] * 10,
    })
    dislocated_all = pd.DataFrame({"symbol": ["A", "A", "B"], "pos": [5, 50, 7]})
    ev = select_events(SimpleNamespace(rows=rows, dislocated_all=dislocated_all), 42)
    got = {g: sub["pos"].tolist() for g, sub in ev.groupby("group")}
    assert got == {GROUP_DISLOCATED: [0], GROUP_SETUP: [10], GROUP_DISLOCATED_ALL: [5, 50, 7]}


def test_period_when_data_ends_before_oos_start():
    dates = pd.bdate_range("2020-01-01", "2020-12-31")
    last = len(dates) - 1
    assert dates[-1] < pd.Timestamp(R.oos_start)
    ev = pd.DataFrame({"pos": [last - R.purge_horizon - 1, last - R.purge_horizon,
                               last - R.purge_horizon + 1, last]})
    assert assign_period(ev, dates, load_rules()).tolist() == [
        "in_sample", "in_sample", "purged", "purged"]
