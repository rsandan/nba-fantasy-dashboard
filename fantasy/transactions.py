"""Free-agency / transaction analytics (the page the original app listed but never built).

Questions it answers
--------------------
* Who works the wire, and does activity actually correlate with winning?
* When do managers make moves (day of week / hour, league timezone)?
* Which pickups stuck vs. were pure streams? ``hold_days`` pairs every add with the
  same team's next drop of that player; the median hold time and the share dropped
  within 7 days separate streamers from genuine pickups.
* Which players churned through the most rosters?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def pair_adds_drops(tx: pd.DataFrame) -> pd.DataFrame:
    """One row per add with the matching later drop by the same team (if any).

    Censored adds (still rostered) keep ``hold_days`` = time until now and ``dropped`` = False,
    so medians aren't biased toward short holds.
    """
    if tx.empty:
        return pd.DataFrame()
    adds = tx[tx["action"] == "add"].sort_values("timestamp")
    drops = tx[tx["action"] == "drop"].sort_values("timestamp")
    # Censoring point for still-rostered adds: now, but never before the last recorded move.
    now = max(pd.Timestamp.now(tz=tx["timestamp"].dt.tz), tx["timestamp"].max())
    rows = []
    for _, a in adds.iterrows():
        later = drops[(drops["team_key"] == a["team_key"]) & (drops["player_id"] == a["player_id"])
                      & (drops["timestamp"] > a["timestamp"])]
        end = later["timestamp"].iloc[0] if not later.empty else now
        rows.append({"team_key": a["team_key"], "team_name": a["team_name"],
                     "player_id": a["player_id"], "player_name": a["player_name"],
                     "added": a["timestamp"], "source_type": a.get("source_type"),
                     "dropped": not later.empty,
                     "hold_days": (end - a["timestamp"]).total_seconds() / 86400})
    return pd.DataFrame(rows)


def team_activity(tx: pd.DataFrame, holds: pd.DataFrame) -> pd.DataFrame:
    if tx.empty:
        return pd.DataFrame()
    counts = tx.pivot_table(index=["team_key", "team_name"], columns="action",
                            values="player_id", aggfunc="count", fill_value=0).reset_index()
    for c in ("add", "drop", "trade"):
        if c not in counts:
            counts[c] = 0
    if not holds.empty:
        h = holds.groupby("team_key").agg(
            median_hold_days=("hold_days", "median"),
            stream_rate=("hold_days", lambda s: float(((s <= 7) & holds.loc[s.index, "dropped"]).mean())),
        ).reset_index()
        counts = counts.merge(h, on="team_key", how="left")
    counts["trade_players"] = counts.pop("trade")
    return counts.sort_values("add", ascending=False).reset_index(drop=True)


def timing_heatmap(tx: pd.DataFrame) -> pd.DataFrame:
    """Adds by day-of-week x hour (league-local time)."""
    adds = tx[tx["action"] == "add"]
    if adds.empty:
        return pd.DataFrame(0, index=DAYS, columns=range(24))
    t = adds["timestamp"]
    grid = pd.crosstab(t.dt.dayofweek.map(dict(enumerate(DAYS))), t.dt.hour)
    return grid.reindex(index=DAYS, columns=range(24), fill_value=0)


def hot_players(tx: pd.DataFrame, holds: pd.DataFrame, top: int = 15) -> pd.DataFrame:
    adds = tx[tx["action"] == "add"]
    if adds.empty:
        return pd.DataFrame()
    g = adds.groupby(["player_id", "player_name"]).agg(
        times_added=("team_key", "size"), distinct_teams=("team_key", "nunique"),
        last_added=("timestamp", "max")).reset_index()
    if not holds.empty:
        g = g.merge(holds.groupby("player_id")["hold_days"].median().rename("median_hold_days"),
                    on="player_id", how="left")
    return g.sort_values(["times_added", "last_added"], ascending=False).head(top)


def activity_vs_performance(activity: pd.DataFrame, ap_summary: pd.DataFrame) -> dict:
    """Spearman rank correlation between wire activity and all-play win%.

    With 10 teams the correlation is noisy (|rho| < ~0.63 is not significant at 5%), so
    the app reports it with that caveat rather than as a finding.
    """
    if activity.empty or ap_summary.empty:
        return {"rho": np.nan, "n": 0, "frame": pd.DataFrame()}
    m = activity.merge(ap_summary[["team_key", "ap_pct"]], on="team_key")
    rho = m["add"].rank().corr(m["ap_pct"].rank()) if len(m) > 2 else np.nan
    return {"rho": float(rho) if rho == rho else np.nan, "n": len(m), "frame": m}
