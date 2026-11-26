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
* **Head-to-head weighting** (default): the overall value weights each category by how
  much it actually decides weekly matchups (``h2h_factors``). Per-category z columns stay
  unweighted so they still read as "standard deviations above the pool".
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
    h2h: bool = True,
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
    def weights(mask) -> pd.Series:
        if not h2h:
            return pd.Series(1.0, index=Z_COLS)
        f = h2h_factors(df.assign(in_pool=mask.values))
        return pd.Series(f.values, index=[f"z_{c}" for c in f.index])

    for _ in range(max_iter):
        z = _zscores(df, pool_mask)
        w = weights(pool_mask)
        total = (z[active] * w[active]).sum(axis=1)
        new_mask = total.rank(ascending=False, method="first") <= pool_size
        if (new_mask == pool_mask).all():
            break
        pool_mask = new_mask

    z = _zscores(df, pool_mask)
    w = weights(pool_mask)
    df = pd.concat([df, z], axis=1)
    df["value"] = (df[active] * w[active]).sum(axis=1)
    df["value_all9"] = (df[Z_COLS] * w).sum(axis=1)
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


# ============================================================ trade (market) value
H2H_GAMES = 3.5   # games per player per H2H week, for weekly-noise estimates


def h2h_factors(table: pd.DataFrame) -> pd.Series:
    """Per-category shrink factors for head-to-head weekly noise ("G-score" idea).

    Plain z-scores assume a player's week is known exactly. In H2H it isn't: a category
    whose weekly noise is large next to the spread between players (3PT%, steals, the
    shooting percentages) decides fewer matchups than its z-score implies. Factor =
    1 / sqrt(1 + (within-player weekly SD / between-player SD)^2), measured on the
    replacement pool: Poisson noise for counting stats, binomial for makes.
    Reference: Rosenof, "Improving Algorithms for Fantasy Basketball" (arXiv:2307.02188).
    """
    pool = table[table["in_pool"]] if "in_pool" in table and table["in_pool"].any() else table
    out = {}
    for c in CATEGORIES:
        if c.kind == "pct":
            a = pool[c.attempts]
            p = (pool[c.makes] / a.where(a > 0)).fillna(0)
            pbar = pool[c.makes].sum() / max(a.sum(), 1e-9)
            between = float(((p - pbar) * a).std())
            within = float(np.sqrt((a * p * (1 - p)).mean() / H2H_GAMES))
        else:
            between = float(pool[c.stat].std())
            within = float(np.sqrt(max(pool[c.stat].mean(), 0) / H2H_GAMES))
        out[c.name] = 1.0 / np.sqrt(1.0 + (within / between) ** 2) if between > 0 else 1.0
    return pd.Series(out)


STAR_EXP = 1.5        # convexity: one star is worth more than two good players
BUILD_HAIRCUT = 0.95  # build-around value counts slightly less (fits some rosters, not all)


def trade_market(table: pd.DataFrame) -> pd.Series:
    """What a player fetches in a trade, indexed by PLAYER_ID.

    1. H2H-adjusted z (``h2h_factors``) so volatile one-category specialists aren't overrated.
    2. Build-around value: the better of the 9-cat total and the best single-category punt
       (rescaled to 9 categories). Giannis in a punt-FT% build is a top-5 player, and that's
       the price his owner will ask.
    3. Value above replacement (the pool's last rostered player), raised to ``STAR_EXP``:
       stars are scarce, and consolidating frees a roster spot for a waiver pickup.
    """
    if table is None or table.empty:
        return pd.Series(dtype=float)
    t = table.drop_duplicates("PLAYER_ID")
    f = h2h_factors(t)
    gz = pd.DataFrame({c: t[f"z_{c}"] * f[c] for c in CATEGORY_NAMES}, index=t.index)
    total = gz.sum(axis=1)
    punt_best = pd.concat([(total - gz[c]) * len(CATEGORY_NAMES) / (len(CATEGORY_NAMES) - 1)
                           for c in CATEGORY_NAMES], axis=1).max(axis=1)
    base = pd.concat([total, BUILD_HAIRCUT * punt_best], axis=1).max(axis=1)
    n_pool = int(t["in_pool"].sum()) if "in_pool" in t else len(t)
    ordered = base.sort_values(ascending=False)
    repl = float(ordered.iloc[min(n_pool, len(ordered) - 1)])
    market = (base - repl).clip(lower=0) ** STAR_EXP
    return pd.Series(market.values, index=t["PLAYER_ID"].values)
