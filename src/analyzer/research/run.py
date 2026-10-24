"""`analyzer research run`: one research run, its files, and the out-of-sample log."""

from __future__ import annotations

import csv
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from analyzer.config import PROJECT_ROOT, ResearchRules, Rules, parse_rules
from analyzer.research.coverage import coverage
from analyzer.research.events import (
    GROUP_DISLOCATED,
    GROUP_DISLOCATED_ALL,
    GROUP_SETUP,
    assign_period,
    select_events,
)
from analyzer.research.outcomes import forward_returns, simulate_plans
from analyzer.research.stats import breakdowns, quintiles, summarize, verdict
from analyzer.signals.evaluate import evaluate, load_panels
from analyzer.signals.reasons import reasons

log = logging.getLogger(__name__)

PERIODS = {"in-sample": "in_sample", "oos": "oos"}
FROZEN_PATHS = ("rules.toml", "docs/specs/2026-10-24-phase-2-design-addendum.md")
OOS_LOG = PROJECT_ROOT / "docs" / "research" / "oos-runs.csv"
OOS_FIELDS = ["run_date", "run_id", "rules_version", "commit", "n", "median_xs_spy", "win_rate",
              "dislocated_median_xs_spy", "verdict", "dirty_tree"]
BREAKDOWN_COLUMNS = ["shock", "correction", "sector", "flow", "ref_etf", "regime", "variant",
                     "tier", "annual", "fallback", "has_missing"]
# evaluate's rows call the flow code "industry" (the industry ETF's flow); events name it
FLOW_LABELS = {1.0: "hot", 0.0: "neutral", -1.0: "cold"}


class GitError(RuntimeError):
    """git is missing, or the project is not a git checkout."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    except OSError as exc:
        raise GitError(f"cannot run git: {exc}") from exc


def git_state(root: Path = PROJECT_ROOT, paths=FROZEN_PATHS) -> tuple[str, bool, bool]:
    """(HEAD commit, a frozen path is untracked or has uncommitted changes, anything under src/ is
    uncommitted). Raises GitError when git cannot answer."""
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise GitError(f"git rev-parse HEAD failed: {head.stderr.strip()}")
    status = _git(root, "status", "--porcelain", "--", *paths)
    if status.returncode != 0:
        raise GitError(f"git status failed: {status.stderr.strip()}")
    dirty = bool(status.stdout.strip())
    for path in paths:
        if _git(root, "ls-files", "--error-unmatch", "--", path).returncode != 0:
            dirty = True  # untracked (or unknown to git): not reproducible from a commit
    tree = _git(root, "status", "--porcelain", "--", "src")
    if tree.returncode != 0:
        raise GitError(f"git status failed: {tree.stderr.strip()}")
    return head.stdout.strip(), dirty, bool(tree.stdout.strip())


def committed_research_rules(root: Path = PROJECT_ROOT) -> ResearchRules:
    """The [research] section of rules.toml as committed at HEAD. Raises GitError when git cannot
    answer."""
    shown = _git(root, "show", "HEAD:rules.toml")
    if shown.returncode != 0:
        raise GitError(f"git show HEAD:rules.toml failed: {shown.stderr.strip()}")
    return parse_rules(shown.stdout).research


@dataclass
class RunResult:
    run_id: str
    out_dir: Path
    summary: dict


def run_id_for(now: pd.Timestamp, period: str) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{PERIODS[period]}"


def run_research(conn, rules: Rules, period: str, out_root: Path, *, panels=None,
                 now: pd.Timestamp | None = None) -> RunResult:
    key = PERIODS[period]
    now = now or pd.Timestamp.now()
    if key == "in_sample":
        # No out-of-sample price is read while tuning (the purge ends in-sample windows first).
        last_day = (pd.Timestamp(rules.research.oos_start) - pd.Timedelta(days=1)).date()
        panels = panels.upto(last_day) if panels is not None else load_panels(conn, rules, end=last_day)
    elif panels is None:
        panels = load_panels(conn, rules)
    log.info("evaluating %d dates", len(panels.dates))
    ev = evaluate(panels, rules)
    events = select_events(ev, rules.research.cooldown_days)
    events["period"] = assign_period(events, panels.dates, rules)
    if key == "in_sample":
        assert not events["period"].isin(["oos", "oos_incomplete"]).any(), \
            "an in-sample run must not contain out-of-sample events"
    ref_of = panels.entities["ref_etf"].to_dict()
    events = events.join(forward_returns(events, panels.stocks, panels.etfs, ref_of,
                                         rules.research.horizons, rules.research.go_horizon))
    is_setup = events["group"] == GROUP_SETUP
    events = events.join(simulate_plans(events[is_setup], panels.stocks, ev.trap_fails, rules.plans))
    events["reasons"] = ""
    events.loc[is_setup, "reasons"] = [" | ".join(reasons(r, rules))
                                       for r in events[is_setup].to_dict("records")]
    events["has_missing"] = events.get("missing", pd.Series(0, index=events.index)).fillna(0) > 0
    events["flow"] = (events["industry"].map(FLOW_LABELS) if "industry" in events
                      else pd.Series(index=events.index, dtype=object)).fillna("unknown")
    # every group (dislocated_all rows carry no regime of their own): the regime of the event day
    events["regime"] = ev.regime.reindex(pd.DatetimeIndex(events["date"])).fillna("unknown").to_numpy()

    chosen = events[events["period"] == key]
    groups = {g: chosen[chosen["group"] == g] for g in (GROUP_SETUP, GROUP_DISLOCATED,
                                                         GROUP_DISLOCATED_ALL)}
    summaries = {g: summarize(e, rules) for g, e in groups.items()}
    q = quintiles(groups[GROUP_SETUP], rules)
    v = verdict(summaries[GROUP_SETUP], summaries[GROUP_DISLOCATED], q, rules)
    if key == "in_sample":
        v["note"] = "in-sample: for tuning only; the go/no-go is judged out of sample"

    run_id = run_id_for(now, period)
    out_dir = Path(out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events.csv", index=False)
    pd.concat([breakdowns(e, BREAKDOWN_COLUMNS, rules).assign(group=g) for g, e in groups.items()],
              ignore_index=True).to_csv(out_dir / "breakdowns.csv", index=False)
    q.to_csv(out_dir / "quintiles.csv", index=False)
    summary = {
        "run_id": run_id, "period": key, "rules_version": rules.version,
        "guidance_tone_enabled": rules.gates.guidance_tone_enabled,
        "win_basis": rules.research.win_basis,
        "dates": [str(panels.dates[0].date()), str(panels.dates[-1].date())],
        "groups": summaries, "verdict": v,
        "plan_outcomes": {p: groups[GROUP_SETUP][f"{p}_outcome"].value_counts().to_dict()
                          for p in ("swing", "position") if f"{p}_outcome" in groups[GROUP_SETUP]},
        "coverage": coverage(conn, panels, ev),
        "regime_known_share": {g: _known_share(e) for g, e in groups.items()},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=_json))
    return RunResult(run_id, out_dir, summary)


def _known_share(events: pd.DataFrame) -> float:
    """Share of the period's event days whose liquidity regime is known."""
    return float((events["regime"] != "unknown").mean()) if len(events) else float("nan")


def _json(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return str(value)


def append_oos_started(run_id: str, rules_version: str, commit: str, path: Path | None = None,
                       dirty_tree: bool = False) -> None:
    """The row written before an out-of-sample run starts (verdict "started", no metrics), so a
    run that crashes after reading out-of-sample data is still on record."""
    _append_oos_row({"run_date": run_id[:8], "run_id": run_id, "rules_version": rules_version,
                     "commit": commit, "n": "", "median_xs_spy": "", "win_rate": "",
                     "dislocated_median_xs_spy": "", "verdict": "started",
                     "dirty_tree": dirty_tree}, path)


def append_oos_log(result: RunResult, commit: str, path: Path | None = None,
                   dirty_tree: bool = False) -> None:
    s = result.summary
    setup, dislocated = s["groups"][GROUP_SETUP], s["groups"][GROUP_DISLOCATED]
    _append_oos_row({"run_date": result.run_id[:8], "run_id": result.run_id,
                     "rules_version": s["rules_version"], "commit": commit, "n": setup["n"],
                     "median_xs_spy": setup["median_xs_spy"], "win_rate": setup["win_rate"],
                     "dislocated_median_xs_spy": dislocated["median_xs_spy"],
                     "verdict": "pass" if s["verdict"]["passed"] else "fail",
                     "dirty_tree": dirty_tree}, path)


def _append_oos_row(row: dict, path: Path | None) -> None:
    path = path or OOS_LOG
    new = not Path(path).exists() or Path(path).stat().st_size == 0
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OOS_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)
