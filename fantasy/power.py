"""League-level analytics: all-play records, luck, form, power ratings, playoff odds.

All-play
    Every completed week, score each team against *every* other team, not just its
    scheduled opponent. The all-play win% is the record a team "deserved" with a
    random schedule; the gap to its real record is schedule luck. This is the single
    most informative fix over the original "sum of category ranks" leaderboard, which
    rewards being decent everywhere even though H2H only pays for beating one
    opponent in 5 of 9.

Scoring types
    Yahoo H2H has two formats: ``head`` (each category is a W/L/T in the standings,
    e.g. 6-3 is six wins) and ``headone`` (the matchup is one W/L/T). Luck, standings
    simulation and playoff odds all follow the league's actual format.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from .categories import CATEGORIES, CATEGORY_NAMES, category_value, compare
from .matchup_model import TeamModel, perturbed, simulate_matchup


def _week_values(week_df: pd.DataFrame) -> pd.DataFrame:
    vals = {c.name: week_df.apply(lambda r, c=c: category_value(r, c), axis=1) for c in CATEGORIES}
    return pd.DataFrame(vals).set_axis(list(week_df["team_key"]), axis=0)


def head_to_head(v: pd.DataFrame, a: str, b: str) -> dict[str, float]:
    return {c.name: compare(v.at[a, c.name], v.at[b, c.name], c) for c in CATEGORIES}


def all_play(team_weeks: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """All-play and actual results from completed team-weeks.

    Returns ``summary`` (one row per team), ``category_rates`` (team x category all-play
    win rate) and ``weekly`` (team x week all-play matchup win%).
    """
    done = team_weeks[team_weeks["status"] == "postevent"]
    summ: dict[str, dict] = {}
    cat_hits: dict[str, dict[str, list]] = {}
    weekly = []
    for week, wk in done.groupby("week"):
        v = _week_values(wk)
        teams = list(v.index)
        opp = dict(zip(wk["team_key"], wk["opponent_key"]))
        for t in teams:
            s = summ.setdefault(t, dict(ap_w=0, ap_l=0, ap_t=0, ap_cw=0.0, ap_cn=0,
                                        w=0, l=0, t=0, cw=0.0, cl=0.0, ct=0.0, weeks=0))
            ch = cat_hits.setdefault(t, {c: [] for c in CATEGORY_NAMES})
            wk_w = wk_n = 0.0
            for o in teams:
                if o == t:
                    continue
                r = head_to_head(v, t, o)
                won = sum(x == 1 for x in r.values())
                lost = sum(x == 0 for x in r.values())
                res = 1.0 if won > lost else 0.5 if won == lost else 0.0
                s["ap_w"] += res == 1.0
                s["ap_l"] += res == 0.0
                s["ap_t"] += res == 0.5
                s["ap_cw"] += sum(r.values())
                s["ap_cn"] += 9
                wk_w += res
                wk_n += 1
                for c, x in r.items():
                    ch[c].append(x)
                if o == opp.get(t):
                    s["w"] += res == 1.0
                    s["l"] += res == 0.0
                    s["t"] += res == 0.5
                    s["cw"] += won
                    s["cl"] += lost
                    s["ct"] += 9 - won - lost
            s["weeks"] += 1
            weekly.append({"team_key": t, "week": week, "ap_pct": wk_w / wk_n if wk_n else np.nan})

    rows = []
    for t, s in summ.items():
        ap_n = s["ap_w"] + s["ap_l"] + s["ap_t"]
        n = s["w"] + s["l"] + s["t"]
        cn = s["cw"] + s["cl"] + s["ct"]
        rows.append({
            "team_key": t, "weeks": s["weeks"],
            "ap_record": f"{s['ap_w']}-{s['ap_l']}-{s['ap_t']}",
            "ap_pct": (s["ap_w"] + 0.5 * s["ap_t"]) / ap_n if ap_n else np.nan,
            "ap_cat_pct": s["ap_cw"] / s["ap_cn"] if s["ap_cn"] else np.nan,
            "h2h_record": f"{s['w']}-{s['l']}-{s['t']}",
            "h2h_pct": (s["w"] + 0.5 * s["t"]) / n if n else np.nan,
            "cat_record": f"{int(s['cw'])}-{int(s['cl'])}-{int(s['ct'])}",
            "cat_pct": (s["cw"] + 0.5 * s["ct"]) / cn if cn else np.nan,
        })
    summary = pd.DataFrame(rows)
    if not summary.empty:
        # Expected wins vs. actual: positive = the schedule has been kind.
        summary["luck_matchup"] = summary["h2h_pct"] - summary["ap_pct"]
        summary["luck_category"] = summary["cat_pct"] - summary["ap_cat_pct"]
    cat_rates = pd.DataFrame({t: {c: np.mean(v) for c, v in d.items()} for t, d in cat_hits.items()}).T
    return {"summary": summary, "category_rates": cat_rates.reindex(columns=CATEGORY_NAMES),
            "weekly": pd.DataFrame(weekly)}


def recent_form(weekly: pd.DataFrame, half_life: float = 3.0) -> pd.Series:
    """Exponentially weighted all-play win% (recent weeks count more)."""
    if weekly.empty:
        return pd.Series(dtype=float)
    last = weekly["week"].max()
    w = 0.5 ** ((last - weekly["week"]) / half_life)
    tmp = weekly.assign(w=w, wx=w * weekly["ap_pct"])
    g = tmp.groupby("team_key")[["wx", "w"]].sum()
    return g["wx"] / g["w"]


def power_ratings(model: TeamModel, n_sims: int = 1500) -> pd.Series:
    """P(beat a randomly drawn league opponent in a typical full week), ties = half.

    Built on the matchup model, so it is schedule-neutral, games-neutral and regresses
    early-season noise toward the league mean via the model's shrinkage.
    """
    teams = model.teams()
    score = {t: [] for t in teams}
    for a, b in combinations(teams, 2):
        g = float(np.median(model.games_per_week))  # equal games: rate the roster, not the schedule
        sim = simulate_matchup(model, a, b, games_a=g, games_b=g, n_sims=n_sims,
                               seed=(teams.index(a) * 131 + teams.index(b)))
        p = sim.p_win + 0.5 * sim.p_tie
        score[a].append(p)
        score[b].append(1 - p)
    return pd.Series({t: float(np.mean(v)) if v else np.nan for t, v in score.items()})


def playoff_odds(
    model: TeamModel,
    team_weeks: pd.DataFrame,
    future: pd.DataFrame,
    num_playoff_teams: int,
    scoring_type: str = "head",
    playoff_start_week: int | None = None,
    n_sims: int = 2000,
    seed: int = 7,
) -> pd.DataFrame:
    """Monte Carlo the rest of the regular season.

    ``team_weeks`` supplies the banked record (completed regular-season weeks) and the
    in-progress week's live totals; ``future`` is scoreboard rows for weeks not yet
    completed (pairings from Yahoo). Each simulated season reuses one draw per matchup,
    ranks by win% with a random tiebreak (Yahoo's real tiebreakers are not public
    for every format, so we treat exact ties as coin flips).
    """
    rng = np.random.default_rng(seed)
    teams = model.teams()
    idx = {t: i for i, t in enumerate(teams)}
    W = np.zeros((n_sims, len(teams)))
    L = np.zeros_like(W)
    T = np.zeros_like(W)

    reg = team_weeks
    if playoff_start_week:
        reg = reg[reg["week"] < playoff_start_week]
    ap = all_play(reg)["summary"].set_index("team_key") if not reg.empty else pd.DataFrame()
    for t in teams:
        if t in ap.index:
            rec = ap.at[t, "cat_record" if scoring_type == "head" else "h2h_record"]
            w, l, tt = (float(x) for x in rec.split("-"))
            W[:, idx[t]] += w
            L[:, idx[t]] += l
            T[:, idx[t]] += tt

    fut = future[future["status"] != "postevent"]
    if playoff_start_week:
        fut = fut[fut["week"] < playoff_start_week]
    # Strength uncertainty is shared across a team's whole simulated season, so we draw it
    # once per block of seasons (``n_draws`` posterior draws x ``n_sims / n_draws`` seasons)
    # instead of independently per week, which would wash it out and overstate certainty.
    n_draws = 25
    per = int(np.ceil(n_sims / n_draws))
    draws = [perturbed(model, rng) for _ in range(n_draws)]
    for _, grp in fut.groupby("matchup_id", sort=False):
        if len(grp) != 2:
            continue
        a, b = grp.iloc[0], grp.iloc[1]
        if a["team_key"] not in idx or b["team_key"] not in idx:
            continue
        live = a["status"] == "midevent"
        rem_a = a["remaining_games"] if live and pd.notna(a["remaining_games"]) else None
        rem_b = b["remaining_games"] if live and pd.notna(b["remaining_games"]) else None
        parts = [simulate_matchup(m_, a["team_key"], b["team_key"],
                                  current_a=a.to_dict() if live else None,
                                  current_b=b.to_dict() if live else None,
                                  games_a=rem_a, games_b=rem_b, n_sims=per,
                                  seed=int(rng.integers(0, 2 ** 31))) for m_ in draws]
        sim = type(parts[0])(parts[0].team_a, parts[0].team_b, parts[0].cat_prob, parts[0].proj_a,
                             parts[0].proj_b, 0, 0, 0, 0,
                             np.concatenate([p.cats_a for p in parts])[:n_sims],
                             np.concatenate([p.cats_b for p in parts])[:n_sims],
                             np.concatenate([p.ties for p in parts])[:n_sims])
        ia, ib = idx[a["team_key"]], idx[b["team_key"]]
        if scoring_type == "head":
            W[:, ia] += sim.cats_a; L[:, ia] += sim.cats_b; T[:, ia] += sim.ties
            W[:, ib] += sim.cats_b; L[:, ib] += sim.cats_a; T[:, ib] += sim.ties
        else:
            aw, bw = sim.cats_a > sim.cats_b, sim.cats_b > sim.cats_a
            tie = ~(aw | bw)
            W[:, ia] += aw; L[:, ia] += bw; T[:, ia] += tie
            W[:, ib] += bw; L[:, ib] += aw; T[:, ib] += tie

    games = W + L + T
    pct = np.where(games > 0, (W + 0.5 * T) / np.where(games > 0, games, 1), 0.5)
    noisy = pct + rng.uniform(0, 1e-6, pct.shape)
    rank = (-noisy).argsort(1).argsort(1) + 1
    return pd.DataFrame({
        "team_key": teams,
        "playoff_odds": (rank <= num_playoff_teams).mean(0),
        "top_seed_odds": (rank == 1).mean(0),
        "exp_win_pct": pct.mean(0),
        "exp_wins": W.mean(0),
        "wins_p10": np.percentile(W, 10, axis=0),
        "wins_p90": np.percentile(W, 90, axis=0),
        "exp_rank": rank.mean(0),
    }).sort_values("playoff_odds", ascending=False).reset_index(drop=True)
