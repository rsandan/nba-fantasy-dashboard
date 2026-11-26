"""Team profiles, trade matchmaking and lineup checks.

Strengths & weaknesses
    Two views per category: how the team has actually done (all-play category win rate,
    ranked in the league) and what the current roster projects to (sum of per-game z,
    ranked). Results can lag a roster that just changed; the roster view can't see
    streaming. Showing both is more honest than blending them.

Trade matchmaking
    Partner fit is how well two teams' needs interlock: you're strong where they're
    weak and vice versa (negative correlation of category profiles). Suggestions search
    every 1-for-1 and 2-for-1 between your top players and each partner's, score both
    sides with their own context weights (categories near 50/50 matter most, as in
    ``trade.context_weights``) and keep only deals where *both* teams improve. A deal
    that only helps you gets declined, so it isn't worth suggesting.

Lineup check
    Today's lineup against today's NBA schedule: injured players in active slots,
    starters with no game while an eligible bench player has one, and IL candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from .categories import CATEGORY_NAMES
from .trade import DEPTH, context_weights, is_unavailable

Z = [f"z_{c}" for c in CATEGORY_NAMES]
ACTIVE_EXCLUDE = {"BN", "IL", "IL+", "NA"}
FLEX = {"Util": None, "G": {"PG", "SG"}, "F": {"SF", "PF"}}
STATUS_TEXT = {"O": "out", "OUT": "out", "INJ": "injured", "IL": "on the injured list", "IL+": "on the injured list",
               "SUSP": "suspended", "NA": "not active", "NR": "not on a roster", "GTD": "a game-time decision",
               "DTD": "day-to-day"}


# ======================================================================= team profile
@dataclass
class TeamProfile:
    team_key: str
    table: pd.DataFrame        # category -> results rank, roster rank, rate, roster z, label
    strengths: list[str]
    weaknesses: list[str]
    advice: str


def roster_z(rosters: pd.DataFrame, values: pd.DataFrame) -> pd.DataFrame:
    """Team x category sum of per-game z over healthy rostered players."""
    if rosters is None or rosters.empty or values is None or values.empty:
        return pd.DataFrame(columns=CATEGORY_NAMES)
    r = rosters.dropna(subset=["PLAYER_ID"])
    if "status" in r:
        r = r[~r["status"].map(is_unavailable)]
    m = r[["team_key", "PLAYER_ID"]].merge(values[["PLAYER_ID", *Z]], on="PLAYER_ID")
    out = m.groupby("team_key")[Z].sum()
    out.columns = CATEGORY_NAMES
    return out


def team_profile(team: str, cat_rates: pd.DataFrame, rz: pd.DataFrame) -> TeamProfile | None:
    if team not in cat_rates.index and team not in rz.index:
        return None
    n = max(len(cat_rates), len(rz))
    res_rank = cat_rates.rank(ascending=False, method="min") if not cat_rates.empty else pd.DataFrame()
    ros_rank = rz.rank(ascending=False, method="min") if not rz.empty else pd.DataFrame()
    rows = []
    for c in CATEGORY_NAMES:
        rr = int(res_rank.at[team, c]) if team in res_rank.index else None
        ro = int(ros_rank.at[team, c]) if team in ros_rank.index else None
        ranks = [x for x in (rr, ro) if x is not None]
        avg = sum(ranks) / len(ranks) if ranks else n / 2
        label = "strength" if avg <= max(2, n * 0.25) else "weakness" if avg > n * 0.75 else "middle"
        rows.append(dict(cat=c, results_rank=rr, roster_rank=ro, avg_rank=avg,
                         rate=float(cat_rates.at[team, c]) if team in cat_rates.index else np.nan,
                         z=float(rz.at[team, c]) if team in rz.index else np.nan, label=label))
    t = pd.DataFrame(rows).set_index("cat")
    strengths = t[t["label"] == "strength"].sort_values("avg_rank").index.tolist()
    weaknesses = t[t["label"] == "weakness"].sort_values("avg_rank", ascending=False).index.tolist()
    deep = t[t["avg_rank"] >= n - 0.5].index.tolist()
    if len(deep) >= 1 and len(weaknesses) >= 2:
        advice = (f"You're bottom of the league in {' and '.join(deep[:2])}. Consider punting "
                  f"{'it' if len(deep[:2]) == 1 else 'them'} and trading that value for "
                  f"{', '.join([c for c in weaknesses if c not in deep][:2]) or 'your middle categories'}.")
    elif weaknesses:
        mid = t[t["label"] == "middle"].sort_values("avg_rank").index.tolist()
        advice = (f"Shore up {' and '.join(weaknesses[:2])}. "
                  + (f"You have surplus in {strengths[0]} to deal from." if strengths else ""))
        if not strengths and mid:
            advice += f" {mid[0]} is your closest thing to a strength."
    else:
        advice = "A balanced build with no glaring hole. Win the matchups on volume."
    return TeamProfile(team, t, strengths, weaknesses, advice.strip())


# ======================================================================= trade hub
@dataclass
class Partner:
    team_key: str
    fit: float                 # 0-100
    they_need: list[str]       # your strengths that cover their weaknesses
    they_have: list[str]       # their strengths that cover your weaknesses


@dataclass
class TradeIdea:
    partner: str
    give: list = field(default_factory=list)       # PLAYER_IDs
    get: list = field(default_factory=list)
    give_names: list = field(default_factory=list)
    get_names: list = field(default_factory=list)
    mine: float = 0.0
    theirs: float = 0.0
    pitch: str = ""


def _profile_vec(team, cat_rates, rz) -> pd.Series:
    """Blend of results and roster strength, centered: + is a surplus, - a need."""
    parts = []
    if team in cat_rates.index:
        parts.append(cat_rates.loc[team].reindex(CATEGORY_NAMES).rank(pct=True))
    if team in rz.index:
        parts.append(rz.loc[team].reindex(CATEGORY_NAMES).rank(pct=True))
    if not parts:
        return pd.Series(0.0, index=CATEGORY_NAMES)
    return sum(parts) / len(parts) - 0.5


def trade_partners(me: str, teams: list, cat_rates: pd.DataFrame, rz: pd.DataFrame) -> list[Partner]:
    mine = _profile_vec(me, cat_rates, rz)
    out = []
    for t in teams:
        if t == me:
            continue
        th = _profile_vec(t, cat_rates, rz)
        corr = float(np.corrcoef(mine.values, th.values)[0, 1]) if mine.std() > 0 and th.std() > 0 else 0.0
        fit = round((1 - corr) / 2 * 100)
        they_need = [c for c in CATEGORY_NAMES if mine[c] > 0.1 and th[c] < -0.1]
        they_have = [c for c in CATEGORY_NAMES if th[c] > 0.1 and mine[c] < -0.1]
        they_need.sort(key=lambda c: th[c])
        they_have.sort(key=lambda c: mine[c])
        out.append(Partner(t, fit, they_need, they_have))
    return sorted(out, key=lambda p: -p.fit)


def suggest_trades(me: str, rosters: pd.DataFrame, values: pd.DataFrame, replacement: pd.Series,
                   cat_rates: pd.DataFrame, partners: list[Partner], names: dict,
                   per_side: int = 9, top: int = 5, max_per_partner: int = 2) -> list[TradeIdea]:
    """Search 1-for-1 and 2-for-1 deals where both sides gain on their own weighted value."""
    if rosters is None or rosters.empty or values is None or values.empty:
        return []
    v = values.drop_duplicates("PLAYER_ID").set_index("PLAYER_ID")
    r = rosters.dropna(subset=["PLAYER_ID"])
    if "status" in r:
        r = r[~r["status"].map(is_unavailable)]
    r = r[r["PLAYER_ID"].isin(v.index)]
    repl = replacement.reindex(CATEGORY_NAMES).fillna(0).values

    def side(team):
        ids = r[r["team_key"] == team]["PLAYER_ID"].tolist()
        ids = sorted(ids, key=lambda i: -float(v.at[i, "value"]))[:per_side]
        return ids, (v.loc[ids, Z].values if ids else np.zeros((0, len(Z))))

    def weights(team):
        rates = cat_rates.loc[team] if team in cat_rates.index else None
        return context_weights(rates).reindex(CATEGORY_NAMES).values

    my_ids, my_z = side(me)
    w_me = weights(me)
    ideas = []

    def pkg(vals_sorted):
        """Two-player packages: the second body counts 60%. Starting slots are limited,
        so depth is worth less than concentrated value (why consolidation trades win)."""
        v = sorted(vals_sorted, reverse=True)
        return v[0] + (DEPTH * v[1] if len(v) > 1 else 0.0)

    for p in partners:
        th_ids, th_z = side(p.team_key)
        if not my_ids or not th_ids:
            continue
        w_th = weights(p.team_key)
        # Value above replacement, weighted by each team's own needs.
        mine_for_me = (my_z - repl) @ w_me
        mine_for_th = (my_z - repl) @ w_th
        theirs_for_me = (th_z - repl) @ w_me
        theirs_for_th = (th_z - repl) @ w_th
        found = []
        give_sets = [(i,) for i in range(len(my_ids))] + list(combinations(range(len(my_ids)), 2))
        get_sets = [(j,) for j in range(len(th_ids))] + list(combinations(range(len(th_ids)), 2))
        for gs in give_sets:
            for ts in get_sets:
                if len(gs) == 2 and len(ts) == 2:
                    continue                                   # keep it to 1-for-1 and 2-for-1
                d_me = pkg([theirs_for_me[j] for j in ts]) - pkg([mine_for_me[i] for i in gs])
                d_th = pkg([mine_for_th[i] for i in gs]) - pkg([theirs_for_th[j] for j in ts])
                if d_me > 0.3 and d_th > 0.3:          # both sides must feel the upgrade
                    found.append((d_me + 0.6 * d_th, gs, ts, d_me, d_th))
        found.sort(key=lambda x: -x[0])
        used = set()
        for _, gs, ts, d_me, d_th in found:
            gkey, tkey = tuple(my_ids[i] for i in gs), tuple(th_ids[j] for j in ts)
            if gkey in used or tkey in used:               # vary the players across ideas
                continue
            used.update({gkey, tkey})
            give = [my_ids[i] for i in gs]
            get = [th_ids[j] for j in ts]
            gn = [v.at[i, "PLAYER_NAME"] for i in give]
            tn = [v.at[j, "PLAYER_NAME"] for j in get]
            sell = (f"help in {' and '.join(p.they_need[:2])}" if p.they_need
                    else "a consolidation upgrade" if len(gs) > len(ts) else "added depth")
            pitch = (f"Pitch it as {sell}. They come out {d_th:+.1f} on their own needs, "
                     f"you come out {d_me:+.1f}.")
            ideas.append(TradeIdea(p.team_key, give, get, gn, tn, d_me, d_th, pitch))
            if len([i for i in ideas if i.partner == p.team_key]) >= max_per_partner:
                break
    ideas.sort(key=lambda i: -(i.mine + 0.6 * i.theirs))
    return ideas[:top]


# ===================================================================== lineup check
@dataclass
class LineupIssue:
    kind: str          # injured | idle | il
    title: str
    detail: str


def _positions(x) -> set:
    if isinstance(x, (list, tuple, set)):
        return {str(p) for p in x}
    if isinstance(x, str):
        return {p.strip() for p in x.replace("/", ",").split(",") if p.strip()}
    return set()


def _fits(slot: str, eligible: set) -> bool:
    if slot == "Util":
        return True
    if slot in eligible:
        return True
    return slot in FLEX and bool(FLEX[slot] and FLEX[slot] & eligible)


def lineup_check(team: str, rosters: pd.DataFrame, playing_today: set) -> tuple[list[LineupIssue], bool]:
    """Issues with today's lineup. Second value is False when lineup data isn't available."""
    if rosters is None or rosters.empty or "selected_position" not in rosters:
        return [], False
    r = rosters[rosters["team_key"] == team].copy()
    if r.empty:
        return [], False
    nba_team = r["TEAM"] if "TEAM" in r else pd.Series(np.nan, index=r.index)
    if "nba_team" in r:
        nba_team = nba_team.fillna(r["nba_team"])
    r["plays"] = nba_team.isin(playing_today)
    r["hurt"] = r["status"].map(is_unavailable) if "status" in r else False
    r["elig"] = r["eligible_positions"].map(_positions)
    active = r[~r["selected_position"].isin(ACTIVE_EXCLUDE)]
    bench = r[r["selected_position"] == "BN"]
    issues: list[LineupIssue] = []

    for _, p in active[active["hurt"]].iterrows():
        issues.append(LineupIssue("injured", f"{p['name']} is out but starting at {p['selected_position']}",
                                  f"Listed as {STATUS_TEXT.get(str(p['status']).upper(), p['status'])}. "
                                  "Swap him to the bench or IL."))

    open_bench = bench[bench["plays"] & ~bench["hurt"]].copy()
    idle = active[~active["plays"] & ~active["hurt"]]
    used = set()
    for _, s in idle.iterrows():
        cands = open_bench[[(_fits(s["selected_position"], e) and n not in used)
                            for e, n in zip(open_bench["elig"], open_bench["name"])]]
        if cands.empty:
            continue
        b = cands.iloc[0]
        used.add(b["name"])
        issues.append(LineupIssue("idle", f"Start {b['name']} over {s['name']}",
                                  f"{s['name']} has no game today and {b['name']} does. "
                                  f"{b['name']} can play {s['selected_position']}."))

    il_open = "IL" in set(r["selected_position"]) or "IL+" in set(r["selected_position"])
    for _, p in bench[bench["hurt"]].iterrows():
        if str(p.get("status", "")).upper() in {"INJ", "O", "OUT", "IL"}:
            issues.append(LineupIssue("il", f"Move {p['name']} to IL",
                                      "He's injured and taking a bench spot. If you have an open IL slot, "
                                      "that frees a spot for a streamer." if not il_open else
                                      "He's injured and taking a bench spot. Check whether your IL slot is free."))
            break
    return issues, True
