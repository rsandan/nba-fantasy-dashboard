"""Category leaderboards: who leads each of the nine categories, this week and on average,
and how every team has trended week by week.

Counting stats are compared per week. Percentages come from summed makes and attempts
(a 10-for-20 week weighs more than a 2-for-3 week), which is how Yahoo scores them.
Turnovers rank lowest-first.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .categories import by_name, cats, cat_names, category_value, per_week_value

ADDITIVE = ["FGM", "FGA", "FTM", "FTA", "FG3M", "FG3A", "PTS", "REB", "AST", "STL", "BLK", "TOV"]


def _values(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per team -> team x category values."""
    out = {c.name: [category_value(r, c) for _, r in rows.iterrows()] for c in cats()}
    return pd.DataFrame(out, index=rows["team_key"].values)


def week_table(tw: pd.DataFrame, week: int) -> pd.DataFrame:
    rows = tw[(tw["week"] == week) & (tw["status"] != "preevent")]
    return _values(rows) if not rows.empty else pd.DataFrame(columns=cat_names())


def season_table(tw: pd.DataFrame) -> pd.DataFrame:
    """Per-week averages over completed weeks; percentages from season makes/attempts."""
    done = tw[tw["status"] == "postevent"]
    if done.empty:
        return pd.DataFrame(columns=cat_names())
    g = done.groupby("team_key")
    tot = g[[c for c in ADDITIVE if c in done]].sum()
    weeks = g["week"].nunique()
    out = pd.DataFrame(index=tot.index)
    for c in cats():
        out[c.name] = per_week_value(tot, c, weeks)
    return out


def ranks(values: pd.DataFrame) -> pd.DataFrame:
    """1 = best. Turnovers: fewest is best. Missing values rank last."""
    out = pd.DataFrame(index=values.index)
    for c in cats():
        if c.name not in values:
            continue
        out[c.name] = values[c.name].rank(ascending=not c.higher_is_better, method="min", na_option="bottom")
    return out.astype("Int64")


def previous_ranks(tw: pd.DataFrame, week: int) -> pd.DataFrame:
    prev = week_table(tw, week - 1)
    return ranks(prev) if not prev.empty else pd.DataFrame()


def trend(tw: pd.DataFrame, cat: str, include_live: bool = False) -> pd.DataFrame:
    """Long table: week, team_key, value. The in-progress week is left out by default
    because a half-finished week's counting stats would read as a slump."""
    keep = {"postevent", "midevent"} if include_live else {"postevent"}
    rows = tw[tw["status"].isin(keep)]
    c = by_name()[cat]
    vals = [category_value(r, c) for _, r in rows.iterrows()]
    return pd.DataFrame({"week": rows["week"].values, "team_key": rows["team_key"].values, "value": vals})


def league_average(values: pd.DataFrame) -> pd.Series:
    return values.mean(numeric_only=True)


def bar_share(values: pd.Series, cat: str) -> pd.Series:
    """0-1 bar length relative to the best team (inverted for turnovers)."""
    v = values.astype(float)
    if by_name()[cat].higher_is_better:
        top = v.max()
        return (v / top).clip(0, 1) if top and top == top else v * 0
    low = v.min()
    return (low / v).clip(0, 1) if low and low == low else v * 0


def vs_average(value: float, avg: float, cat: str) -> str:
    if value != value or avg != avg or avg == 0:
        return "", None
    c = by_name()[cat]
    if c.is_shooting:
        d = (value - avg) * 1000
        better = None if abs(d) < 0.5 else d > 0
        txt = f"{'+' if d >= 0 else '−'}{abs(d):.0f} pts vs avg"
    else:
        d = (value - avg) / avg
        better = None if abs(d) < 0.005 else (d > 0 if c.higher_is_better else d < 0)
        txt = f"{'+' if d >= 0 else '−'}{abs(d):.0%} vs avg"
    return txt, better
