"""Player valuation for 9-cat: replacement-aware z-scores with punt support.

Method
------
For each category we measure a player's per-game contribution relative to the pool of
players who are actually rostered in *this* league (n_teams x roster slots), in units
of that pool's standard deviation.

* Counting stats: ``z = (x - mean_pool) / sd_pool``; turnovers are sign-flipped.
* Percentages use **impact**, not raw percentage: ``(pct - pct_pool) * attempts``.
  A 60% shooter on 3 FGA barely moves a weekly FG%, a 52% shooter on 20 FGA moves it a
  lot. ``pct_pool`` is volume-weighted (sum makes / sum attempts).
* The pool is chosen iteratively: score everyone, keep the top N, recompute the pool's
  means/SDs, repeat until the membership stabilises (3-4 passes). Using all ~550 NBA
  players as the baseline inflates everyone's z because the denominator is full of
  end-of-bench players nobody rosters.
* **Punting**: punted categories are dropped from the total *and* from pool selection,
  so the pool (and therefore every other category's baseline) reflects the build.
* Replacement level per category is the mean z of the first ~N/10 players just
  outside the pool: roughly what you could pick up on waivers today. The trade
  analyzer uses it to price roster-spot asymmetry in 2-for-1 deals.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .categories import CATEGORIES, CATEGORY_NAMES

Z_COLS = [f"z_{c}" for c in CATEGORY_NAMES]


@dataclass
class ValuationResult:
    table: pd.DataFrame            # one row per player, z per category + totals, ranked
    replacement: pd.Series         # replacement-level z per category
    pool_size: int
    punts: tuple[str, ...]


def _zscores(df: pd.DataFrame, pool_mask: pd.Series) -> pd.DataFrame:
    pool = df[pool_mask]
    z = pd.DataFrame(index=df.index)
    for cat in CATEGORIES:
        if cat.kind == "pct":
            pool_pct = pool[cat.makes].sum() / max(pool[cat.attempts].sum(), 1e-9)
            player_pct = np.where(df[cat.attempts] > 0, df[cat.makes] / df[cat.attempts].where(df[cat.attempts] > 0, 1), pool_pct)
            impact = (player_pct - pool_pct) * df[cat.attempts]
            pool_impact = impact[pool_mask]
            sd = pool_impact.std(ddof=0)
            z[f"z_{cat.name}"] = (impact - pool_impact.mean()) / (sd if sd > 0 else 1.0)
        else:
            mu, sd = pool[cat.stat].mean(), pool[cat.stat].std(ddof=0)
            val = (df[cat.stat] - mu) / (sd if sd > 0 else 1.0)
            z[f"z_{cat.name}"] = val if cat.higher_is_better else -val
    return z


def value_players(
    players: pd.DataFrame,
    n_teams: int = 10,
    roster_size: int = 13,
    punts: tuple[str, ...] | list[str] = (),
    min_games: int = 5,
    max_iter: int = 6,
) -> ValuationResult:
    """Score players (per-game columns FGM, FGA, FTM, FTA, FG3M, PTS, REB, AST, STL, BLK, TOV)."""
    punts = tuple(p for p in punts if p in CATEGORY_NAMES)
    evidence = players["GP_TOT"] if "GP_TOT" in players else players["GP"]
    df = players[evidence >= min_games].copy().reset_index(drop=True)
    if df.empty:
        return ValuationResult(df, pd.Series(0.0, index=CATEGORY_NAMES), 0, punts)
    pool_size = min(n_teams * roster_size, len(df))
    active = [f"z_{c}" for c in CATEGORY_NAMES if c not in punts]

    # Seed the pool with minutes played: a stable, category-neutral first guess.
    pool_mask = df["MIN"].rank(ascending=False, method="first") <= pool_size
    for _ in range(max_iter):
        z = _zscores(df, pool_mask)
        total = z[active].sum(axis=1)
        new_mask = total.rank(ascending=False, method="first") <= pool_size
        if (new_mask == pool_mask).all():
            break
        pool_mask = new_mask

    z = _zscores(df, pool_mask)
    df = pd.concat([df, z], axis=1)
    df["value"] = df[active].sum(axis=1)
    df["value_all9"] = df[Z_COLS].sum(axis=1)
    # Availability: a player who plays 55 of 82 games delivers ~2/3 of his per-game value
    # over a season. Show both; per-game value drives weekly decisions, adjusted drives drafts.
    if "availability" not in df:
        team_gp = df.groupby("TEAM")["GP"].transform("max").clip(lower=1)
        df["availability"] = (df["GP"] / team_gp).clip(0, 1)
    df["value_adj"] = df["value"] * df["availability"]
    df["in_pool"] = pool_mask.values
    df = df.sort_values("value", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)

    band = df.iloc[pool_size: pool_size + max(5, pool_size // 10)]
    if band.empty:
        band = df.tail(max(5, pool_size // 10))
    replacement = band[Z_COLS].mean()
    replacement.index = CATEGORY_NAMES
    return ValuationResult(df, replacement, pool_size, punts)


def category_profile(values: pd.DataFrame, player_ids: list) -> pd.Series:
    """Sum of per-category z for a set of players (a roster, or one side of a trade)."""
    sub = values[values["PLAYER_ID"].isin(player_ids)]
    s = sub[Z_COLS].sum()
    s.index = CATEGORY_NAMES
    return s
