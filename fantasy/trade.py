"""Trade and waiver decision tools built on the z-score valuation.

Context weighting
-----------------
Raw z-score totals treat every category as equally valuable, but in H2H a category
you already win 90% of the time (or lose 90%) barely moves your matchup odds. The
marginal value of a category is highest where your win rate is near 50%. We weight
each category by ``floor + (1 - floor) * 4 p (1 - p)`` where ``p`` is the team's
all-play win rate in that category (``4p(1-p)`` peaks at 1 when p = 0.5 and is the
derivative-shaped "swing" weight). Punted categories get weight 0.

Roster-spot asymmetry
---------------------
In a 2-for-1 the side receiving one player opens a roster spot they can fill from
waivers; the side receiving two must cut someone. We price that at replacement
level: ``delta += (players_given - players_received) * replacement_z``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .categories import CATEGORY_NAMES
from .nba_data import YAHOO_TO_NBA_TEAM, normalize_name
from .valuation import Z_COLS, category_profile


def context_weights(cat_rates: pd.Series | None, punts=(), floor: float = 0.25) -> pd.Series:
    if cat_rates is None or cat_rates.empty:
        w = pd.Series(1.0, index=CATEGORY_NAMES)
    else:
        p = cat_rates.reindex(CATEGORY_NAMES).fillna(0.5).clip(0, 1)
        w = floor + (1 - floor) * 4 * p * (1 - p)
    for c in punts:
        if c in w.index:
            w[c] = 0.0
    return w


@dataclass
class TradeEval:
    table: pd.DataFrame
    raw_delta: float
    weighted_delta: float
    verdict: str


def evaluate_trade(values: pd.DataFrame, give_ids: list, get_ids: list,
                   replacement: pd.Series, weights: pd.Series | None = None) -> TradeEval:
    give = category_profile(values, give_ids)
    get = category_profile(values, get_ids)
    spot_adj = (len(give_ids) - len(get_ids)) * replacement.reindex(CATEGORY_NAMES).fillna(0)
    delta = get - give + spot_adj
    w = weights if weights is not None else pd.Series(1.0, index=CATEGORY_NAMES)
    table = pd.DataFrame({"give_z": give, "get_z": get, "roster_spot_adj": spot_adj,
                          "delta": delta, "weight": w, "weighted_delta": delta * w})
    active = w > 0
    raw = float(delta[active].sum())
    weighted = float((delta * w)[active].sum())
    # Thresholds in z units: ~0.5 z is roughly the gap between adjacent draft rounds.
    if weighted > 0.75:
        verdict = "Accept: clear win for your build"
    elif weighted > 0.25:
        verdict = "Lean accept: modest gain"
    elif weighted > -0.25:
        verdict = "Neutral: value is roughly even; decide on fit and schedule"
    elif weighted > -0.75:
        verdict = "Lean decline: modest loss"
    else:
        verdict = "Decline: you give up meaningful value"
    return TradeEval(table, raw, weighted, verdict)


def match_players(yahoo: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Attach NBA valuation rows to Yahoo players by normalised name (+ team as tiebreak)."""
    if yahoo.empty or values.empty:
        return yahoo.assign(PLAYER_ID=np.nan)
    y = yahoo.copy()
    y["name_key"] = y["name"].map(normalize_name)
    y["nba_team"] = y.get("editorial_team_abbr", pd.Series("", index=y.index)).fillna("").str.upper()
    y["nba_team"] = y["nba_team"].map(lambda t: YAHOO_TO_NBA_TEAM.get(t, t))
    v = values[["PLAYER_ID", "name_key", "TEAM"]]
    merged = y.merge(v, on="name_key", how="left")
    # Two NBA players can share a normalised name; keep the one on the matching team.
    merged["_team_match"] = (merged["TEAM"] == merged["nba_team"]).astype(int)
    merged = merged.sort_values("_team_match", ascending=False).drop_duplicates(
        subset=[c for c in ("player_id", "team_key") if c in merged.columns] or ["name_key"])
    return merged.drop(columns="_team_match")


def streaming_board(free_agents: pd.DataFrame, values: pd.DataFrame, games_left: pd.Series,
                    weights: pd.Series, replacement: pd.Series | None = None) -> pd.DataFrame:
    """Rank available players for the rest of the current scoring week.

    ``stream_score = games_left * sum_c w_c * (z_c - replacement_c)``: per-game value
    *above the roster spot you'd be vacating* (proxied by replacement level) in the
    categories still in play this week, times how many chances you get. Measuring
    above replacement matters: raw z is negative for nearly every free agent, so
    ``z * games`` would perversely rank players with *zero* games first. A mediocre
    player with 4 games often beats a better one with 2; this puts that on one scale.
    """
    fa = match_players(free_agents, values)
    fa = fa.merge(values[["PLAYER_ID", "GP", "MIN", *Z_COLS, "value"]], on="PLAYER_ID", how="inner")
    team = fa["TEAM"].fillna(fa["nba_team"])
    fa["games_left"] = team.map(games_left).fillna(0).astype(int)
    repl = replacement if replacement is not None else pd.Series(0.0, index=CATEGORY_NAMES)
    wz = sum((fa[f"z_{c}"] - repl.get(c, 0.0)) * weights.get(c, 1.0) for c in CATEGORY_NAMES)
    fa["weighted_per_game"] = wz
    fa["stream_score"] = wz * fa["games_left"]
    cols = ["PLAYER_ID", "name", "TEAM", "eligible_positions", "percent_owned", "status", "games_left",
            "GP", "MIN", "value", "weighted_per_game", "stream_score", *Z_COLS]
    cols = [c for c in cols if c in fa.columns]
    return fa[cols].sort_values("stream_score", ascending=False).reset_index(drop=True)
