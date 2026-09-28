"""Team-strength model and Monte Carlo matchup simulator for H2H categories.

Model
-----
Each team-week is summarised as a vector of **per player-game rates** over 11 additive
components::

    FGA, FG%, FTA, FT%, 3PTM, PTS, REB, AST, STL, BLK, TO     (per game started)

Working per game (Yahoo's ``completed_games``) instead of per week matters: weekly
totals swing mostly with how many games a team gets (schedule, injuries, streaming),
and games are *known in advance* from the schedule. Rates isolate roster quality;
games are plugged back in at projection time.

Team mean rates
    Recency-weighted average of the team's weekly rates (half-life ``half_life`` weeks)
    shrunk toward a prior with strength ``prior_weeks`` (empirical-Bayes /
    credibility weighting). The prior is the league average, or a roster-based
    projection if supplied, which is what makes week-1 predictions sensible.

Week-to-week noise
    Residuals from each team's own mean, pooled across the league, give an 11x11
    covariance. Categories are correlated (a big-volume week lifts PTS, 3PTM, FGA and
    TO together; a hot shooting week lifts FG% and PTS), and ignoring that
    overstates how often a team sweeps or gets swept. The covariance is estimated on
    rates rescaled by ``sqrt(games / G_ref)`` because the variance of a per-game average
    shrinks with the number of games; at projection time it is scaled back by
    ``G_ref / games_remaining``. Off-diagonals are shrunk 30% toward zero (a simple
    Ledoit-Wolf-style regulariser) because 10 teams x a few weeks is a small sample.

Simulation
    For the remaining games of each team, draw rates from N(mu, Sigma * G_ref / n),
    convert to totals (makes = attempts x pct, so percentages aggregate correctly),
    add what is already banked this week, round to integers / Yahoo's 3-decimal
    display precision (ties are real in STL/BLK), and score categories.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import zlib

from .categories import CATEGORIES, CATEGORY_NAMES, compare

COMPONENTS = ["FGA", "FGP", "FTA", "FTP", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
PCT_COMPONENTS = {"FGP": ("FGM", "FGA"), "FTP": ("FTM", "FTA")}
_IDX = {c: i for i, c in enumerate(COMPONENTS)}
# Weakly-informative fallback noise (coefficient of variation per component) used only
# when there are too few completed weeks to estimate a covariance (preseason / week 1).
_FALLBACK_CV = {"FGA": 0.08, "FGP": 0.05, "FTA": 0.15, "FTP": 0.07, "FG3M": 0.15, "PTS": 0.08,
                "REB": 0.08, "AST": 0.10, "STL": 0.18, "BLK": 0.25, "TOV": 0.12}


def team_week_rates(tw: pd.DataFrame) -> pd.DataFrame:
    """Per-game component rates for team-weeks with at least one game played."""
    df = tw[tw["games"].fillna(0) > 0].copy()
    g = df["games"]
    out = pd.DataFrame({"team_key": df["team_key"], "week": df["week"], "games": g})
    for c in ("FGA", "FTA", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"):
        out[c] = df[c] / g
    out["FGP"] = np.where(df["FGA"] > 0, df["FGM"] / df["FGA"].where(df["FGA"] > 0, 1), np.nan)
    out["FTP"] = np.where(df["FTA"] > 0, df["FTM"] / df["FTA"].where(df["FTA"] > 0, 1), np.nan)
    out[["FGP", "FTP"]] = out[["FGP", "FTP"]].fillna(out[["FGP", "FTP"]].mean())
    return out.reset_index(drop=True)


@dataclass
class TeamModel:
    mu: pd.DataFrame                       # team_key x COMPONENTS
    cov: np.ndarray                        # K x K covariance at G_ref games
    g_ref: float                           # reference games per week
    games_per_week: pd.Series              # typical games per week, by team
    league_mean: pd.Series
    n_weeks: int = 0
    diagnostics: dict = field(default_factory=dict)
    n_eff: pd.Series | None = None         # recency-weighted weeks of evidence, by team
    prior_weeks: float = 3.0

    def mean_uncertainty_scale(self, team: str) -> float:
        """Posterior variance of a team's mean rate, as a multiple of the weekly covariance
        (at G_ref games): ``1 / (evidence weeks + prior weeks)``."""
        n = float(self.n_eff.get(team, 0.0)) if self.n_eff is not None else 0.0
        return 1.0 / (n + self.prior_weeks)

    def teams(self) -> list[str]:
        return list(self.mu.index)


def fit_team_model(
    history: pd.DataFrame,
    teams: list[str] | None = None,
    half_life: float = 6.0,
    prior_weeks: float = 3.0,
    cov_shrink: float = 0.3,
    priors: pd.DataFrame | None = None,
) -> TeamModel:
    """Fit on completed team-weeks (``status == 'postevent'``)."""
    hist = history[history["status"] == "postevent"] if "status" in history else history
    rates = team_week_rates(hist)
    teams = list(teams) if teams is not None else sorted(history["team_key"].dropna().unique())

    if rates.empty:
        # Preseason: no league history. Fall back to priors or a neutral generic team.
        base = (priors.mean() if priors is not None and not priors.empty
                else pd.Series({"FGA": 8.4, "FGP": 0.475, "FTA": 2.4, "FTP": 0.785, "FG3M": 1.4,
                                "PTS": 11.8, "REB": 4.6, "AST": 2.8, "STL": 0.85, "BLK": 0.55,
                                "TOV": 1.45}))
        mu = pd.DataFrame([priors.loc[t] if priors is not None and t in priors.index else base
                           for t in teams], index=teams)[COMPONENTS]
        sd = np.array([base[c] * _FALLBACK_CV[c] for c in COMPONENTS])
        return TeamModel(mu, np.diag(sd ** 2), 32.0, pd.Series(32.0, index=teams), base[COMPONENTS],
                         0, {"cov_source": "fallback"}, pd.Series(0.0, index=teams), prior_weeks)

    last_week = rates["week"].max()
    rates["w"] = 0.5 ** ((last_week - rates["week"]) / half_life)
    g_ref = float(rates["games"].median())
    league_mean = pd.Series(np.average(rates[COMPONENTS], axis=0, weights=rates["w"]), index=COMPONENTS)
    if priors is not None and not priors.empty:
        # Roster projections are per *rostered* player-game; Yahoo rates are per *started*
        # player-game (benches and rest days differ). Rescale so the priors' league mean
        # matches the observed league mean and only the between-team differences carry over.
        priors = priors[COMPONENTS] * (league_mean / priors[COMPONENTS].mean())

    mu_rows, resid, n_eff = {}, [], {}
    for t in teams:
        r = rates[rates["team_key"] == t]
        prior = priors.loc[t][COMPONENTS] if priors is not None and t in priors.index else league_mean
        if r.empty:
            mu_rows[t] = prior
            continue
        wsum = r["w"].sum()
        n_eff[t] = float(wsum)
        xbar = pd.Series(np.average(r[COMPONENTS], axis=0, weights=r["w"]), index=COMPONENTS)
        mu_rows[t] = (wsum * xbar + prior_weeks * prior) / (wsum + prior_weeks)
        if len(r) >= 2:
            raw_mean = r[COMPONENTS].mean()
            scale = np.sqrt(r["games"].values / g_ref)[:, None]
            e = (r[COMPONENTS].values - raw_mean.values) * scale
            resid.append(e * np.sqrt(len(r) / (len(r) - 1)))  # small-sample dof correction
    mu = pd.DataFrame(mu_rows).T[COMPONENTS]

    K = len(COMPONENTS)
    n_resid = sum(len(e) for e in resid)
    if n_resid >= 2 * K:
        E = np.vstack(resid)
        cov = E.T @ E / n_resid
        cov = (1 - cov_shrink) * cov + cov_shrink * np.diag(np.diag(cov))
        source = f"pooled residuals (n={n_resid})"
    else:
        cov = np.diag([(league_mean[c] * _FALLBACK_CV[c]) ** 2 for c in COMPONENTS])
        source = "fallback (too few weeks for covariance)"
    cov = cov + np.eye(K) * 1e-9  # guarantee positive-definite for Cholesky

    gpw = hist.groupby("team_key")["games"].median().reindex(teams).fillna(g_ref)
    return TeamModel(mu, cov, g_ref, gpw, league_mean, int(rates["week"].nunique()),
                     {"cov_source": source, "half_life": half_life, "prior_weeks": prior_weeks},
                     pd.Series(n_eff).reindex(teams).fillna(0.0), prior_weeks)


def perturbed(model: TeamModel, rng: np.random.Generator) -> TeamModel:
    """One posterior draw of every team's mean rates (for season-long simulations, where
    a team's true strength must stay fixed across all of its simulated weeks)."""
    L = np.linalg.cholesky(model.cov)
    mu = model.mu.copy()
    for t in mu.index:
        shift = L @ rng.standard_normal(len(COMPONENTS)) * np.sqrt(model.mean_uncertainty_scale(t))
        mu.loc[t] = mu.loc[t].values + shift
    mu[["FGP", "FTP"]] = mu[["FGP", "FTP"]].clip(0, 1)
    out = TeamModel(mu, model.cov, model.g_ref, model.games_per_week, model.league_mean,
                    model.n_weeks, model.diagnostics, model.n_eff, model.prior_weeks)
    out.n_eff = pd.Series(1e9, index=mu.index)  # strength already drawn: no extra mean noise
    return out


# -------------------------------------------------------------------- simulation
def _simulate_totals(model: TeamModel, team: str, current: dict, n_rem: float,
                     n_sims: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Simulated end-of-week additive totals for one team."""
    cur = {k: float(current.get(k, 0.0) or 0.0) for k in
           ("FGM", "FGA", "FTM", "FTA", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV")}
    out = {k: np.full(n_sims, v) for k, v in cur.items()}
    if n_rem and n_rem > 0:
        # Two noise sources, same covariance shape: week-to-week variation of a per-game
        # average over n_rem games, plus uncertainty about the team's true mean. Omitting
        # the second makes early-season probabilities overconfident.
        scale = model.g_ref / n_rem + model.mean_uncertainty_scale(team)
        L = np.linalg.cholesky(model.cov * scale)
        draws = rng.standard_normal((n_sims, len(COMPONENTS))) @ L.T + model.mu.loc[team].values
        d = {c: draws[:, _IDX[c]] for c in COMPONENTS}
        fga = np.rint(np.clip(d["FGA"], 0, None) * n_rem)
        fta = np.rint(np.clip(d["FTA"], 0, None) * n_rem)
        out["FGA"] += fga
        out["FTA"] += fta
        out["FGM"] += np.rint(fga * np.clip(d["FGP"], 0, 1))
        out["FTM"] += np.rint(fta * np.clip(d["FTP"], 0, 1))
        for c in ("FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"):
            out[c] += np.rint(np.clip(d[c], 0, None) * n_rem)
    return out


def _category_arrays(t: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    vals = {}
    for cat in CATEGORIES:
        if cat.kind == "pct":
            att = t[cat.attempts]
            with np.errstate(invalid="ignore", divide="ignore"):
                vals[cat.name] = np.round(np.where(att > 0, t[cat.makes] / att, np.nan), cat.decimals)
        else:
            vals[cat.name] = t[cat.stat]
    return vals


def _compare_arrays(a: np.ndarray, b: np.ndarray, higher_is_better: bool) -> np.ndarray:
    an, bn = np.isnan(a), np.isnan(b)
    a0, b0 = np.nan_to_num(a), np.nan_to_num(b)
    win = (a0 > b0) if higher_is_better else (a0 < b0)
    res = np.where(a0 == b0, 0.5, win.astype(float))
    res = np.where(an & ~bn, 0.0, res)
    res = np.where(bn & ~an, 1.0, res)
    return np.where(an & bn, 0.5, res)


@dataclass
class MatchupSim:
    team_a: str
    team_b: str
    cat_prob: pd.Series            # P(team A wins category), ties count half
    proj_a: pd.Series              # median projected category values
    proj_b: pd.Series
    p_win: float
    p_tie: float
    p_loss: float
    exp_cats_a: float
    cats_a: np.ndarray             # per-simulation categories won by A (ties excluded)
    cats_b: np.ndarray
    ties: np.ndarray


def simulate_matchup(model: TeamModel, team_a: str, team_b: str,
                     current_a: dict | None = None, current_b: dict | None = None,
                     games_a: float | None = None, games_b: float | None = None,
                     n_sims: int = 4000, seed: int | None = 0) -> MatchupSim:
    """Distribution of a (possibly in-progress) H2H category matchup.

    ``games_*`` are games *remaining*; default is each team's typical games per week
    (a full, not-yet-started week). ``current_*`` are totals already banked.
    """
    rng = np.random.default_rng(seed)
    ga = model.games_per_week.get(team_a, model.g_ref) if games_a is None else games_a
    gb = model.games_per_week.get(team_b, model.g_ref) if games_b is None else games_b
    ta = _category_arrays(_simulate_totals(model, team_a, current_a or {}, ga, n_sims, rng))
    tb = _category_arrays(_simulate_totals(model, team_b, current_b or {}, gb, n_sims, rng))
    res = {c.name: _compare_arrays(ta[c.name], tb[c.name], c.higher_is_better) for c in CATEGORIES}
    R = np.column_stack([res[c] for c in CATEGORY_NAMES])
    cats_a = (R == 1).sum(1)
    cats_b = (R == 0).sum(1)
    ties = (R == 0.5).sum(1)
    return MatchupSim(
        team_a, team_b,
        pd.Series({c: res[c].mean() for c in CATEGORY_NAMES}),
        pd.Series({c: np.nanmedian(ta[c]) for c in CATEGORY_NAMES}),
        pd.Series({c: np.nanmedian(tb[c]) for c in CATEGORY_NAMES}),
        float((cats_a > cats_b).mean()), float((cats_a == cats_b).mean()),
        float((cats_a < cats_b).mean()), float((cats_a + 0.5 * ties).mean()),
        cats_a, cats_b, ties,
    )


def live_matchups(model: TeamModel, week_rows: pd.DataFrame, n_sims: int = 4000) -> list[MatchupSim]:
    """Simulate every matchup in a scoreboard week using banked totals + games remaining."""
    sims = []
    for _, grp in week_rows.groupby("matchup_id", sort=False):
        if len(grp) != 2:
            continue
        a, b = grp.iloc[0], grp.iloc[1]
        started = a["status"] != "preevent"
        # Yahoo's remaining_games is the schedule-aware count of roster games left, which
        # is exactly the exposure we want (valid pre-week too). Fall back to typical games.
        rem_a = a["remaining_games"] if pd.notna(a["remaining_games"]) else None
        rem_b = b["remaining_games"] if pd.notna(b["remaining_games"]) else None
        key = f"{a['team_key']}|{b['team_key']}|{int(a['week'])}".encode()
        sims.append(simulate_matchup(
            model, a["team_key"], b["team_key"],
            current_a=a.to_dict() if started else None, current_b=b.to_dict() if started else None,
            games_a=rem_a, games_b=rem_b, n_sims=n_sims, seed=zlib.crc32(key)))
    return sims


# ---------------------------------------------------------------------- backtest
def _actual_category_results(a: pd.Series, b: pd.Series) -> dict[str, float]:
    from .categories import category_value
    return {c.name: compare(category_value(a, c), category_value(b, c), c) for c in CATEGORIES}


def _empirical_baseline(hist: pd.DataFrame, ta: str, tb: str) -> dict[str, float]:
    """Nonparametric baseline: how often A's past weekly value beat B's past weekly value,
    over every pair of past weeks. No modelling, no schedule adjustment."""
    from .categories import category_value
    ra, rb = hist[hist["team_key"] == ta], hist[hist["team_key"] == tb]
    if ra.empty or rb.empty:
        return {c: 0.5 for c in CATEGORY_NAMES}
    out = {}
    for c in CATEGORIES:
        va = [category_value(r, c) for _, r in ra.iterrows()]
        vb = [category_value(r, c) for _, r in rb.iterrows()]
        out[c.name] = float(np.mean([compare(x, y, c) for x in va for y in vb]))
    return out


def backtest(team_weeks: pd.DataFrame, min_train_weeks: int = 3, n_sims: int = 2000,
             **fit_kwargs) -> dict:
    """Walk-forward evaluation: for each completed week w, fit on weeks < w only and
    predict every matchup in week w before it starts.

    Games are set to the games each team actually got. That is information available
    ex-ante in principle (the NBA schedule is public and Yahoo shows games remaining),
    though it does leak which players ended up injured, so treat results as a mild
    upper bound on pre-week skill.
    """
    done = team_weeks[team_weeks["status"] == "postevent"]
    weeks = sorted(done["week"].unique())
    cat_rows, match_rows = [], []
    for w in weeks[min_train_weeks:]:
        hist = done[done["week"] < w]
        model = fit_team_model(hist, teams=sorted(done["team_key"].unique()), **fit_kwargs)
        for mid, grp in done[done["week"] == w].groupby("matchup_id"):
            if len(grp) != 2:
                continue
            a, b = grp.iloc[0], grp.iloc[1]
            sim = simulate_matchup(model, a["team_key"], b["team_key"],
                                   games_a=a["games"], games_b=b["games"], n_sims=n_sims,
                                   seed=int(w) * 1000 + len(match_rows))
            actual = _actual_category_results(a, b)
            base = _empirical_baseline(hist, a["team_key"], b["team_key"])
            for c in CATEGORY_NAMES:
                for flip in (False, True):  # both perspectives -> symmetric calibration
                    p, pb, y = sim.cat_prob[c], base[c], actual[c]
                    if flip:
                        p, pb, y = 1 - p, 1 - pb, 1 - y
                    cat_rows.append({"week": w, "matchup_id": mid, "category": c,
                                     "p_model": p, "p_baseline": pb, "y": y})
            won = sum(v == 1 for v in actual.values())
            lost = sum(v == 0 for v in actual.values())
            y_m = 1.0 if won > lost else (0.5 if won == lost else 0.0)
            match_rows.append({"week": w, "matchup_id": mid, "team_a": a["team_key"],
                               "team_b": b["team_key"], "p_win": sim.p_win + 0.5 * sim.p_tie,
                               "y": y_m, "exp_cats": sim.exp_cats_a,
                               "actual_cats": won + 0.5 * (9 - won - lost)})
    cats, matches = pd.DataFrame(cat_rows), pd.DataFrame(match_rows)
    return {"categories": cats, "matchups": matches, "metrics": _metrics(cats, matches),
            "calibration": calibration_table(cats) if not cats.empty else pd.DataFrame()}


def _brier(p, y):
    return float(np.mean((np.asarray(p) - np.asarray(y)) ** 2))


def _logloss(p, y, eps=1e-3):
    p = np.clip(np.asarray(p), eps, 1 - eps)
    y = np.asarray(y)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _metrics(cats: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    if cats.empty:
        return pd.DataFrame()
    rows = []
    for label, col in (("Model", "p_model"), ("Empirical all-play baseline", "p_baseline")):
        rows.append({"model": label, "category_brier": _brier(cats[col], cats["y"]),
                     "category_logloss": _logloss(cats[col], cats["y"])})
    rows.append({"model": "Coin flip (0.5)", "category_brier": _brier(np.full(len(cats), .5), cats["y"]),
                 "category_logloss": _logloss(np.full(len(cats), .5), cats["y"])})
    out = pd.DataFrame(rows)
    if not matches.empty:
        out.loc[0, "matchup_brier"] = _brier(matches["p_win"], matches["y"])
        out.loc[2, "matchup_brier"] = _brier(np.full(len(matches), .5), matches["y"])
        out.loc[0, "cats_MAE"] = float(np.mean(np.abs(matches["exp_cats"] - matches["actual_cats"])))
    # Brier skill score vs coin flip: 1 = perfect, 0 = no better than 50/50.
    out["category_BSS"] = 1 - out["category_brier"] / out.loc[2, "category_brier"]
    return out


def calibration_table(cats: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    b = pd.cut(cats["p_model"], edges, include_lowest=True)
    return (cats.groupby(b, observed=True)
            .agg(mean_pred=("p_model", "mean"), observed=("y", "mean"), n=("y", "size"))
            .reset_index(drop=True))


def roster_priors(rosters: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Per player-game component rates implied by each team's current roster.

    ``rosters`` needs ``team_key`` and ``PLAYER_ID`` (after name matching); ``players`` is a
    per-game table (blended season). Each player is weighted by availability x minutes,
    a proxy for how many of the team's started games he'll fill.
    """
    if rosters is None or rosters.empty or "PLAYER_ID" not in rosters:
        return pd.DataFrame(columns=COMPONENTS)
    avail = players["availability"] if "availability" in players else 1.0
    p = players.assign(_w=avail * players["MIN"].clip(lower=1))
    m = rosters.dropna(subset=["PLAYER_ID"]).merge(p, on="PLAYER_ID", how="inner")
    if m.empty:
        return pd.DataFrame(columns=COMPONENTS)
    rows = {}
    for team, g in m.groupby("team_key"):
        w = g["_w"] / g["_w"].sum()
        tot = {c: float((g[c] * w).sum()) for c in ("FGM", "FGA", "FTM", "FTA", "FG3M", "PTS", "REB",
                                                    "AST", "STL", "BLK", "TOV")}
        rows[team] = {"FGA": tot["FGA"], "FGP": tot["FGM"] / max(tot["FGA"], 1e-9), "FTA": tot["FTA"],
                      "FTP": tot["FTM"] / max(tot["FTA"], 1e-9), "FG3M": tot["FG3M"], "PTS": tot["PTS"],
                      "REB": tot["REB"], "AST": tot["AST"], "STL": tot["STL"], "BLK": tot["BLK"],
                      "TOV": tot["TOV"]}
    return pd.DataFrame(rows).T[COMPONENTS]
