"""Game plans and storylines: the "so what" layer on top of the model.

Game plan
---------
For one team's live matchup, rank concrete moves by how much they change the odds:

* **Add / drop** pairs are scored by re-running the matchup simulation with the
  pickup's expected stats over his remaining games added and the dropped player's
  removed (same random seed, so the difference is the move, not noise). That turns
  "this guy is good" into "19% -> 26%".
* **Category targets**: the closest category you're projected to lose, with the
  per-game gap your remaining games have to close.
* **Protect / concede**: leads worth guarding, and lost causes not worth chasing.
* **Volume and lineup hygiene**: games-left deficits, injured players in active slots.

Storylines
----------
Template-driven headlines for every matchup (blowout, coin flip, upset watch,
comeback) plus a wire report from the transaction log (trades and who won them,
scooped drops, steals, regrets, grinders, quiet managers) and league streaks.
Everything is computed from the same model the rest of the app uses, so a headline
never contradicts the numbers below it.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import matchup_model as mm
from .categories import by_name, cats, cat_names, category_value, compare, fmt_value
from .nba_data import normalize_name
from .trade import QUESTIONABLE, context_weights, is_unavailable, streaming_board

ADDITIVE = ["FGM", "FGA", "FTM", "FTA", "FG3M", "FG3A", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
INJURED = {"INJ", "O", "OUT", "IL", "IL+", "SUSP", "NA", "NR"}
BENCH_SLOTS = {"BN", "IL", "IL+", "NA"}
CAT_PLAYERS = {"FG%": "efficient bigs", "FT%": "good free-throw shooters", "3PT%": "efficient shooters",
               "PTS": "scorers", "REB": "bigs", "AST": "playmakers", "STL": "wings and guards who gamble",
               "BLK": "rim protectors", "TO": "low-usage role players", "3PTM": "volume three-point shooters",
               "FGM": "high-volume scorers", "FTM": "players who get to the line", "A/T": "careful playmakers",
               "FGA": "high-volume scorers", "FTA": "players who get to the line", "3PTA": "volume shooters",
               "FPTS": "players with the most games left"}


# ============================================================================ helpers
def _pick(options: list[str], key: str) -> str:
    """Deterministic variety: the same story keeps its wording across reruns."""
    return options[zlib.crc32(key.encode()) % len(options)]


def _fmt(cat: str, v: float) -> str:
    if v != v:
        return "–"
    return fmt_value(v, by_name()[cat])


def _banked(a: pd.Series, b: pd.Series) -> tuple[int, int, int]:
    res = [compare(category_value(a, c), category_value(b, c), c) for c in cats()]
    return sum(r == 1 for r in res), sum(r == 0 for r in res), sum(r == 0.5 for r in res)


def _current(row: pd.Series, started: bool) -> dict:
    return {k: float(row.get(k, 0) or 0) for k in ADDITIVE} if started else {}


def _rem(row: pd.Series) -> float | None:
    v = row.get("remaining_games")
    return float(v) if v == v and v is not None else None


def player_index(values: pd.DataFrame) -> dict:
    """name_key -> (rank, value, best category, NBA id) for linking Yahoo names to values."""
    if values is None or values.empty:
        return {}
    z = [f"z_{c}" for c in cat_names()]
    best = values[z].idxmax(axis=1).str.replace("z_", "", regex=False)
    return {k: (int(r), float(v), b, pid) for k, r, v, b, pid in
            zip(values["name_key"], values["rank"], values["value"], best, values["PLAYER_ID"])}


def ago(ts, now) -> str:
    s = (now - ts).total_seconds()
    if s < 3600:
        return f"{max(1, int(s // 60))}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d}d ago"


# ========================================================================== game plan
@dataclass
class Action:
    kind: str           # move | chase | protect | concede | volume | lineup
    title: str
    detail: str
    impact: str = ""    # short chip, e.g. "19% → 26%"
    score: float = 0.0
    player_ids: list = field(default_factory=list)


@dataclass
class GamePlan:
    me: str
    opp: str
    p_win: float
    p_tie: float
    headline: str
    actions: list[Action]


def _player_weighted(df: pd.DataFrame, weights: pd.Series, repl: pd.Series,
                     factors: pd.Series | None = None) -> pd.Series:
    f = factors if factors is not None else pd.Series(1.0, index=cat_names())
    return sum((df[f"z_{c}"] - repl.get(c, 0.0)) * weights.get(c, 1.0) * f.get(c, 1.0) for c in cat_names())


def game_plan(me: str, live: pd.DataFrame, model: mm.TeamModel, values: pd.DataFrame | None,
              replacement: pd.Series | None, rosters: pd.DataFrame, free_agents: pd.DataFrame,
              games_left: pd.Series, names: dict, n_sims: int = 3000, top: int = 3) -> GamePlan | None:
    row = live[live["team_key"] == me]
    if row.empty:
        return None
    r_me = row.iloc[0]
    opp_rows = live[live["team_key"] == r_me["opponent_key"]]
    if opp_rows.empty:
        return None
    r_op = opp_rows.iloc[0]
    opp = r_op["team_key"]
    started = r_me["status"] != "preevent"
    cur_me, cur_op = _current(r_me, started), _current(r_op, started)
    rem_me, rem_op = _rem(r_me), _rem(r_op)
    seed = zlib.crc32(f"plan|{me}|{opp}|{int(r_me['week'])}".encode())

    def sim(delta: dict | None = None) -> mm.MatchupSim:
        cur = dict(cur_me)
        for k, v in (delta or {}).items():
            cur[k] = cur.get(k, 0.0) + v
        return mm.simulate_matchup(model, me, opp, cur, cur_op, rem_me, rem_op, n_sims=n_sims, seed=seed)

    base = sim()
    p = base.cat_prob
    win = lambda s: s.p_win + 0.5 * s.p_tie
    actions: list[Action] = []
    opp_name, my_name = names.get(opp, "your opponent"), names.get(me, "You")

    # ---- 1. add / drop, scored by re-simulation
    weights = context_weights(p, floor=0.1)
    if values is not None and not values.empty and replacement is not None and not free_agents.empty:
        board = streaming_board(free_agents, values, games_left, weights, replacement)
        board = board[board["games_left"] > 0].head(6)
        mine = rosters[(rosters["team_key"] == me) & rosters["PLAYER_ID"].notna()] if not rosters.empty else rosters
        if not board.empty and not mine.empty:
            m = mine.merge(values[["PLAYER_ID", "TEAM", "rank", *ADDITIVE, *[f"z_{c}" for c in cat_names()]]],
                           on="PLAYER_ID", how="inner", suffixes=("_y", ""))
            if "selected_position" in m:
                m = m[~m["selected_position"].isin({"IL", "IL+"})]
            m = m[m["rank"] > 60]                                   # never suggest cutting a real asset
            if not m.empty:
                m["games_left"] = m["TEAM"].map(games_left).fillna(0)
                if "status" in m:   # an injured player's remaining games are worth nothing
                    m.loc[m["status"].map(is_unavailable), "games_left"] = 0
                from .valuation import h2h_factors
                m["score"] = _player_weighted(m, weights, replacement, h2h_factors(values)) * m["games_left"]
                drops = m.sort_values("score").head(3)
                stats = values.set_index("PLAYER_ID")
                best = None
                for _, a in board.iterrows():
                    for _, d in drops.iterrows():
                        if a["stream_score"] <= d["score"]:
                            continue
                        sa, sd = stats.loc[a["PLAYER_ID"]], stats.loc[d["PLAYER_ID"]]
                        if isinstance(sa, pd.DataFrame):
                            sa = sa.iloc[0]
                        if isinstance(sd, pd.DataFrame):
                            sd = sd.iloc[0]
                        delta = {k: float(sa[k]) * a["games_left"] - float(sd[k]) * d["games_left"] for k in ADDITIVE}
                        s = sim(delta)
                        gain = win(s) - win(base)
                        if best is None or gain > best[0]:
                            best = (gain, a, d, s)
                if best and best[0] >= 0.01:
                    gain, a, d, s = best
                    helps = [c for c in cat_names() if s.cat_prob[c] - p[c] >= 0.04]
                    helps_txt = f", and it moves {', '.join(helps[:3])}" if helps else ""
                    d_hurt = is_unavailable(d.get("status"))
                    d_txt = (f"{d['name']} is listed as out" if d_hurt
                             else f"{d['name']}'s {int(d['games_left'])}")
                    gtd = (f" {a['name']} is a game-time decision, so check his status first."
                           if str(a.get("status", "")).upper() in QUESTIONABLE else "")
                    actions.append(Action(
                        "move", f"Add {a['name']}, drop {d['name']}",
                        f"{a['name']} has {int(a['games_left'])} game{'s' if a['games_left'] != 1 else ''} left "
                        f"this week{' while ' if d_hurt else ' to '}{d_txt}{helps_txt}.{gtd}",
                        f"{win(base):.0%} → {win(s):.0%}", 4 + 100 * gain, [a["PLAYER_ID"], d["PLAYER_ID"]]))

    # ---- 2. chase the most winnable category you're behind in
    proj_me, proj_op = base.proj_a, base.proj_b
    behind = [c for c in cat_names() if 0.12 <= p[c] < 0.5]
    if behind:
        c = max(behind, key=lambda k: p[k])
        cat = by_name()[c]
        proj = f"You're projected at {_fmt(c, proj_me[c])} to their {_fmt(c, proj_op[c])}."
        if cat.is_shooting:
            title = f"Swing {c}"
            detail = f"{proj} Lean on {CAT_PLAYERS.get(c, 'good shooters')} and sit low-percentage volume shooters."
        elif cat.kind == "pct":
            title = f"Swing {c}"
            detail = f"{proj} Lean on {CAT_PLAYERS.get(c, 'steady players')}."
        elif not cat.higher_is_better:
            gap = proj_me[c] - proj_op[c]
            per = f" That's {gap / rem_me:.1f} fewer per game over your {int(rem_me)} games." \
                if rem_me and rem_me > 0 and gap > 0 else ""
            title = "Cut the turnovers"
            detail = f"{proj}{per} Favor {CAT_PLAYERS.get(c, 'low-usage role players')} over high-usage creators."
        else:
            gap = proj_op[c] - proj_me[c]
            per = f" That's about {gap / rem_me:.1f} more per game over your {int(rem_me)} games." \
                if rem_me and rem_me > 0 and gap > 0 else ""
            title = "Get more games in" if cat.kind == "points" else f"Go get {c}"
            detail = f"{proj}{per} Stream {CAT_PLAYERS.get(c, 'the best available players')}."
        actions.append(Action("chase", title, detail, f"{p[c]:.0%} now", 3 + 8 * p[c]))

    # ---- 3. protect a lead that's still in play
    ahead = [c for c in cat_names() if 0.5 <= p[c] <= 0.8]
    if ahead:
        c = min(ahead, key=lambda k: p[k])
        tip = ("Don't stream low-percentage shooters." if by_name()[c].is_shooting
               else f"Keep your {CAT_PLAYERS.get(c, 'best players')} in the lineup every day.")
        actions.append(Action("protect", f"Protect {c}",
                              f"You lead it {p[c]:.0%} of the time, projected {_fmt(c, proj_me[c])} to "
                              f"{_fmt(c, proj_op[c])}. {tip}", f"{p[c]:.0%}", 3 + 6 * (0.8 - p[c])))

    # (Injured players in active slots are handled by the Lineup Check card.)

    # ---- 5. volume
    if rem_me is not None and rem_op is not None:
        diff = rem_me - rem_op
        if diff <= -3:
            actions.append(Action("volume", f"You're down {int(-diff)} games",
                                  f"{opp_name} has {int(rem_op)} games left to your {int(rem_me)}. Use every "
                                  "open slot and stream players with games on light nights.",
                                  f"−{int(-diff)} games", 4 + abs(diff)))
        elif diff >= 3:
            actions.append(Action("volume", f"Use your {int(diff)}-game edge",
                                  f"You have {int(rem_me)} games left to {opp_name}'s {int(rem_op)}. Set your "
                                  "lineup daily so none of them sit on the bench.", f"+{int(diff)} games", 3 + diff / 2))

    # ---- 6. stop chasing lost causes / don't waste moves on locks
    lost = [c for c in cat_names() if p[c] < 0.08]
    locked = [c for c in cat_names() if p[c] > 0.95]
    if lost:
        actions.append(Action("concede", f"Let {' and '.join(lost[:2])} go",
                              f"{'They are' if len(lost) > 1 else 'It is'} under 10% to flip. Point your adds at "
                              "the categories that can still swing.", f"{max(p[c] for c in lost):.0%}", 3))
    elif locked:
        actions.append(Action("concede", f"{' and '.join(locked[:2])} {'are' if len(locked) > 1 else 'is'} locked",
                              "Stop spending moves there. Every add should target a category still in play.",
                              f"{min(p[c] for c in locked):.0%}", 2))

    actions.sort(key=lambda a: a.score, reverse=True)
    w = win(base)
    if w < 0.35:
        head = _pick(["Underdog this week. Here's how to flip it.", "You're behind. Three moves to fight back.",
                      "Down but not out. Start here."], me + opp)
    elif w < 0.65:
        head = _pick(["This one's up for grabs. Make it yours.", "Coin flip week. These moves tip it.",
                      "Too close to coast. Here's the edge."], me + opp)
    else:
        head = _pick(["You're in control. Don't let it slip.", "Favored this week. Close it out.",
                      "Ahead for now. Here's how to stay there."], me + opp)
    return GamePlan(me, opp, base.p_win, base.p_tie, head, actions[:top])


# ========================================================================= storylines
@dataclass
class Story:
    kind: str                # matchup | wire | league
    tag: str
    headline: str
    dek: str
    teams: list = field(default_factory=list)
    when: str = ""
    score: float = 0.0
    p: tuple | None = None   # (team_a, p_a, team_b, p_b) for matchup cards


def _ranks(ap_summary: pd.DataFrame, head: bool) -> dict:
    if ap_summary is None or ap_summary.empty:
        return {}
    col = "cat_pct" if head and "cat_pct" in ap_summary else "h2h_pct" if "h2h_pct" in ap_summary else "ap_pct"
    order = ap_summary.sort_values(col, ascending=False)["team_key"].tolist()
    return {k: i + 1 for i, k in enumerate(order)}


def matchup_stories(sims: list, live: pd.DataFrame, names: dict, ranks: dict,
                    recent_adds: pd.DataFrame | None = None, pidx: dict | None = None) -> list[Story]:
    out = []
    for s in sims:
        a = live[live["team_key"] == s.team_a].iloc[0]
        b = live[live["team_key"] == s.team_b].iloc[0]
        started = a["status"] != "preevent"
        pa, pb = s.p_win + s.p_tie / 2, s.p_loss + s.p_tie / 2
        fav, dog = (s.team_a, s.team_b) if pa >= pb else (s.team_b, s.team_a)
        fp = max(pa, pb)
        nf, nd = names.get(fav, fav), names.get(dog, dog)
        cat_fav = s.cat_prob if fav == s.team_a else 1 - s.cat_prob
        swing = sorted(cat_names(), key=lambda c: abs(cat_fav[c] - 0.5))[:2]
        swing = [c for c in swing if 0.25 <= cat_fav[c] <= 0.75]
        dog_best = min(cat_names(), key=lambda c: cat_fav[c])
        rf, rd = ranks.get(fav), ranks.get(dog)
        w, l, t = (_banked(a, b) if fav == s.team_a else _banked(b, a)) if started else (0, 0, 0)
        ra, rb = _rem(a), _rem(b)
        key = f"{s.team_a}{s.team_b}{int(a['week'])}"

        games = ""
        if ra is not None and rb is not None and abs(ra - rb) >= 3:
            more, less = (s.team_a, s.team_b) if ra > rb else (s.team_b, s.team_a)
            hi, lo = int(max(ra, rb)), int(min(ra, rb))
            games = (f" {names.get(more)} has {hi} games left to {names.get(less)}'s {lo}, "
                     f"a {hi - lo}-game edge.")
        swing_txt = (f" It comes down to {' and '.join(swing)}." if swing else "")
        record = f"leads {w}-{l}-{t} and " if started and w > l else ""

        if started and w < l and fp >= 0.55:
            tag, score = "COMEBACK", 3.5
            headline = _pick([f"{nf} trails {w}-{l}, but the math says comeback",
                              f"Don't trust the scoreboard: {nf} is favored despite trailing {w}-{l}"], key)
            dek = f"{nf} wins {fp:.0%} of simulations from here.{games}{swing_txt}"
        elif rf and rd and rf - rd >= 3 and fp >= 0.6:
            tag, score = "UPSET WATCH", 3.2
            headline = _pick([f"Upset watch: #{rf} {nf} is {fp:.0%} to take down #{rd} {nd}",
                              f"#{rf} {nf} has #{rd} {nd} in trouble"], key)
            dek = f"{nf} {record}is favored in {fp:.0%} of simulations.{swing_txt}{games}"
        elif fp >= 0.93:
            tag, score = "BLOWOUT", 1.0
            headline = _pick([f"{nf} is running away with it", f"{nf} has this one wrapped up",
                              f"No drama here: {nf} cruises"], key)
            dek = (f"{nf} {record}wins {fp:.0%} of simulations. {nd}'s best shot is {dog_best} "
                   f"({1 - cat_fav[dog_best]:.0%}).{games}")
        elif fp >= 0.72:
            tag, score = "IN CONTROL", 1.5
            headline = _pick([f"{nf} has {nd} on the ropes", f"{nf} in the driver's seat against {nd}",
                              f"Advantage {nf}"], key)
            dek = f"{nf} {record}is {fp:.0%} to win.{swing_txt}{games}"
        elif fp >= 0.58:
            tag, score = "TIGHT", 2.2
            headline = _pick([f"{nf} holds the edge, but {swing[0] if swing else 'it'} is up for grabs",
                              f"{nf} leans ahead of {nd} in a tight one"], key)
            dek = f"{nf} is {fp:.0%} to win, {nd} {1 - fp:.0%}.{swing_txt}{games}"
        else:
            tag, score = "COIN FLIP", 3.0
            headline = _pick([f"{names.get(s.team_a)} vs {names.get(s.team_b)} is a coin flip",
                              f"Too close to call: {names.get(s.team_a)} vs {names.get(s.team_b)}",
                              f"Nobody's safe in {names.get(s.team_a)} vs {names.get(s.team_b)}"], key)
            dek = f"{pa:.0%} to {pb:.0%}.{swing_txt}{games}"
        score += 0.15 * (16 - (rf or 8) - (rd or 8)) / 8          # bigger stage, bigger story

        # Transaction tie-in: did either side add help in a swing category this week?
        if recent_adds is not None and not recent_adds.empty and pidx and swing:
            for team in (s.team_a, s.team_b):
                ta = recent_adds[recent_adds["team_key"] == team]
                hits = [(n, pidx[normalize_name(n)][2]) for n in ta["player_name"]
                        if normalize_name(n) in pidx and pidx[normalize_name(n)][2] in swing]
                if hits:
                    n, c = hits[-1]
                    dek += f" {names.get(team)} grabbed {n} this week for {c}, exactly where this one is decided."
                    score += 0.3
                    break
        out.append(Story("matchup", tag, headline, dek.strip(), [s.team_a, s.team_b], score=score,
                         p=(s.team_a, pa, s.team_b, pb)))
    return out


def streak_stories(tw: pd.DataFrame, names: dict) -> list[Story]:
    """Current W/L streaks from completed weeks (3+ is a story)."""
    done = tw[tw["status"] == "postevent"]
    if done.empty:
        return []
    res = {}
    for (wk, _), g in done.groupby(["week", "matchup_id"]):
        if len(g) != 2:
            continue
        a, b = g.iloc[0], g.iloc[1]
        w, l, _t = _banked(a, b)
        res.setdefault(a["team_key"], []).append((wk, "W" if w > l else "L" if l > w else "T"))
        res.setdefault(b["team_key"], []).append((wk, "W" if l > w else "L" if w > l else "T"))
    out = []
    for team, seq in res.items():
        seq = [r for _, r in sorted(seq)]
        last, n = seq[-1], 0
        for r in reversed(seq):
            if r != last:
                break
            n += 1
        if n >= 3 and last in "WL":
            nm = names.get(team, team)
            if last == "W":
                out.append(Story("league", "STREAK", f"{nm} has won {n} straight",
                                 "The hottest team in the league. Someone has to stop it.", [team], score=1.5 + n / 4))
            else:
                out.append(Story("league", "SKID", f"{nm} has lost {n} in a row",
                                 "Time to shake something up on the wire.", [team], score=1.2 + n / 4))
    return out


def wire_stories(tx: pd.DataFrame, names: dict, pidx: dict, now: pd.Timestamp,
                 win_odds: dict | None = None, days: int = 14) -> list[Story]:
    """Storylines from adds, drops and trades in the last ``days`` days."""
    if tx is None or tx.empty:
        return []
    tx = tx[tx["timestamp"] <= now]
    recent = tx[tx["timestamp"] >= now - pd.Timedelta(days=days)]
    out: list[Story] = []
    info = lambda n: pidx.get(normalize_name(n)) if isinstance(n, str) else None

    # Trades: who got the better per-game value?
    trades = recent[recent["action"] == "trade"]
    for tid, g in trades.groupby("transaction_id"):
        sides = {k: grp["player_name"].tolist() for k, grp in g.groupby("team_key")}
        if len(sides) != 2:
            continue
        (ta, pa), (tb, pb) = sides.items()
        va = sum(info(n)[1] for n in pa if info(n)) if pa else 0.0
        vb = sum(info(n)[1] for n in pb if info(n)) if pb else 0.0
        winner, edge = (ta, va - vb) if va >= vb else (tb, vb - va)
        verdict = ("Even on paper." if edge < 0.4 else
                   f"By per-game value, {names.get(winner)} comes out ahead (+{edge:.1f}).")
        out.append(Story("wire", "TRADE", f"{names.get(ta)} lands {', '.join(pa)} from {names.get(tb)}",
                         f"{names.get(tb)} gets {', '.join(pb)} back. {verdict}", [ta, tb],
                         ago(g["timestamp"].max(), now), 4.0))

    adds = recent[recent["action"] == "add"].sort_values("timestamp")
    drops = recent[recent["action"] == "drop"].sort_values("timestamp")

    # Scooped: dropped by one manager, grabbed by another within three days.
    for _, d in drops.iterrows():
        later = adds[(adds["player_name"] == d["player_name"]) & (adds["team_key"] != d["team_key"])
                     & (adds["timestamp"] > d["timestamp"])
                     & (adds["timestamp"] <= d["timestamp"] + pd.Timedelta(days=3))]
        if later.empty:
            continue
        a = later.iloc[0]
        hrs = (a["timestamp"] - d["timestamp"]).total_seconds() / 3600
        gap = f"{int(hrs)} hours" if hrs < 48 else f"{int(hrs // 24)} days"
        i = info(d["player_name"])
        rank = f" He's the #{i[0]} player by per-game value." if i and i[0] <= 200 else ""
        out.append(Story("wire", "SCOOPED", f"{names.get(a['team_key'])} pounced on {d['player_name']}",
                         f"{names.get(d['team_key'])} let him go and he was gone {gap} later.{rank}",
                         [a["team_key"], d["team_key"]], ago(a["timestamp"], now),
                         2.6 + (0.8 if i and i[0] <= 120 else 0)))

    # Steal: best pickup still on the roster.
    if not adds.empty:
        dropped_after = {(r["team_key"], r["player_name"]) for _, r in drops.iterrows()}
        held = adds[[(t, n) not in dropped_after for t, n in zip(adds["team_key"], adds["player_name"])]]
        ranked = [(info(n)[0], n, t, ts) for n, t, ts in zip(held["player_name"], held["team_key"], held["timestamp"])
                  if info(n)]
        if ranked:
            rk, n, t, ts = min(ranked)
            if rk <= 150:
                out.append(Story("wire", "STEAL", f"{names.get(t)}'s sneaky pickup: {n}",
                                 f"Grabbed off waivers, now the #{rk} player in the league by per-game value.",
                                 [t], ago(ts, now), 3.0 + (150 - rk) / 150))

    # Regret: a top-120 player dropped recently.
    for _, d in drops.iterrows():
        i = info(d["player_name"])
        if i and i[0] <= 120:
            taken = adds[(adds["player_name"] == d["player_name"]) & (adds["timestamp"] > d["timestamp"])]
            if not taken.empty:
                continue  # already told as SCOOPED
            out.append(Story("wire", "REGRET?", f"Did {names.get(d['team_key'])} give up on {d['player_name']} too soon?",
                             f"He's the #{i[0]} player by per-game value and he's sitting on waivers.",
                             [d["team_key"]], ago(d["timestamp"], now), 2.4))

    # Grinder: most adds in the last week.
    week = adds[adds["timestamp"] >= now - pd.Timedelta(days=7)]
    if not week.empty:
        cnt = week.groupby("team_key").size().sort_values(ascending=False)
        t, n = cnt.index[0], int(cnt.iloc[0])
        if n >= 3:
            odds = f" It has them at {win_odds[t]:.0%} this week." if win_odds and t in win_odds else ""
            out.append(Story("wire", "GRINDER", f"{names.get(t)} is living on the waiver wire",
                             f"{n} adds in the last 7 days, the most in the league.{odds}", [t], "this week", 1.8))

    # Hot name: added by 2+ different teams.
    if not adds.empty:
        multi = adds.groupby("player_name")["team_key"].nunique()
        multi = multi[multi >= 2].sort_values(ascending=False)
        if not multi.empty:
            n = multi.index[0]
            out.append(Story("wire", "HOT", f"{n} is the wire's hottest name",
                             f"Added by {int(multi.iloc[0])} different teams in the last two weeks.", [],
                             "last 2 weeks", 1.6))

    # Quiet: no moves in two weeks while losing this week.
    if win_odds:
        active = set(recent["team_key"])
        for t, w in sorted(win_odds.items(), key=lambda kv: kv[1]):
            if t not in active and w < 0.4:
                out.append(Story("wire", "QUIET", f"Is anyone home at {names.get(t)}?",
                                 f"No moves in two weeks, and {w:.0%} to win this week.", [t], "2 weeks", 1.4))
                break
    return out


def league_feed(sims, live, tw, tx, names, ap_summary, head: bool, values: pd.DataFrame | None,
                now: pd.Timestamp, cat_rates: pd.DataFrame | None = None,
                rosters: pd.DataFrame | None = None) -> dict:
    """Everything the home page shows: top story, matchup stories, wire and league notes."""
    pidx = player_index(values)
    ranks = _ranks(ap_summary, head)
    recent_adds = pd.DataFrame()
    if tx is not None and not tx.empty:
        wk_start = pd.Timestamp(live["week_start"].iloc[0]).tz_localize(now.tz) if len(live) else now
        recent_adds = tx[(tx["action"] == "add") & (tx["timestamp"] >= wk_start) & (tx["timestamp"] <= now)]
    m = matchup_stories(sims, live, names, ranks, recent_adds, pidx)
    win_odds = {}
    for s in m:
        a, pa, b, pb = s.p
        win_odds[a], win_odds[b] = pa, pb
    wire = wire_stories(tx, names, pidx, now, win_odds)
    league = streak_stories(tw, names)
    seen, uniq = set(), []
    for s in wire:                      # same player can be dropped/added twice in a window
        if s.headline not in seen:
            seen.add(s.headline)
            uniq.append(s)
    wire = uniq
    pool = m + wire + league
    top = max(pool, key=lambda s: s.score) if pool else None
    report = team_report(names, tx, pidx, now, win_odds, tw, cat_rates, rosters)
    order = {k: i for i, k in enumerate(sorted(ranks, key=ranks.get))} if ranks else {}
    report.sort(key=lambda n: order.get(n.team_key, 99))
    return dict(top=top, matchups=sorted(m, key=lambda s: -s.score),
                wire=sorted([s for s in wire + league if s is not top], key=lambda s: -s.score),
                report=report, ranks=ranks)


# ======================================================================= team report
@dataclass
class TeamNote:
    team_key: str
    good: str
    bad: str


def _streaks(tw: pd.DataFrame) -> dict:
    """team -> (result, length) for the current run of completed-week results."""
    done = tw[tw["status"] == "postevent"]
    res = {}
    for (wk, _), g in done.groupby(["week", "matchup_id"]):
        if len(g) != 2:
            continue
        a, b = g.iloc[0], g.iloc[1]
        w, l, _t = _banked(a, b)
        res.setdefault(a["team_key"], []).append((wk, "W" if w > l else "L" if l > w else "T"))
        res.setdefault(b["team_key"], []).append((wk, "W" if l > w else "L" if w > l else "T"))
    out = {}
    for team, seq in res.items():
        seq = [r for _, r in sorted(seq)]
        n = 0
        for r in reversed(seq):
            if r != seq[-1]:
                break
            n += 1
        out[team] = (seq[-1], n)
    return out


def _fmt_cat(cat: str, v: float) -> str:
    c = by_name().get(cat)
    if c is not None and c.kind == "pct":
        return fmt_value(v, c)
    return f"{v:.0f}/wk"


def _rank_text(cat: str, r: int, v: float, n: int, tied: bool = False) -> str:
    """'#2 in the league in FT% (.812)'. Turnovers read as fewest/most so #1 is never ambiguous."""
    pos = f"T-#{r}" if tied else f"#{r}"
    val = _fmt_cat(cat, v)
    if cat == "TO":
        if r == 1:
            return f"{'Tied for fewest' if tied else 'Fewest'} turnovers in the league ({val})"
        if r == n:
            return f"{'Tied for most' if tied else 'Most'} turnovers in the league ({val})"
        return f"{pos} fewest turnovers in the league ({val})" if r <= n // 2 else f"{pos} of {n} in turnovers ({val}, fewer is better)"
    if r <= n // 2:
        return f"{pos} in the league in {cat} ({val})"
    return f"{pos} of {n} in {cat} ({val})"


def team_report(names: dict, tx: pd.DataFrame, pidx: dict, now: pd.Timestamp, win_odds: dict,
                tw: pd.DataFrame, cat_rates: pd.DataFrame | None, rosters: pd.DataFrame | None,
                days: int = 14) -> list[TeamNote]:
    """One good and one bad thing for every team, most notable first.

    Candidates come from the last two weeks of transactions, streaks, this week's odds
    and injuries; each carries a score so the strongest signal wins. Every team falls
    back to its best and worst category, so no cell is ever empty.
    """
    good = {k: [] for k in names}
    bad = {k: [] for k in names}
    info = lambda n: pidx.get(normalize_name(n)) if isinstance(n, str) else None

    # Streaks
    for t, (r, n) in _streaks(tw).items():
        if t not in names:
            continue
        if r == "W" and n >= 2:
            good[t].append((2 + n / 2, f"Won {n} straight weeks"))
        elif r == "L" and n >= 2:
            bad[t].append((2 + n / 2, f"Lost {n} straight weeks"))

    # This week's odds
    for t, p in (win_odds or {}).items():
        if t not in names:
            continue
        if p >= 0.7:
            good[t].append((1.5 + 2 * (p - 0.7), f"{p:.0%} to win this week"))
        elif p <= 0.3:
            bad[t].append((1.5 + 2 * (0.3 - p), f"Only {p:.0%} to win this week"))

    if tx is not None and not tx.empty:
        tx = tx[tx["timestamp"] <= now]
        recent = tx[tx["timestamp"] >= now - pd.Timedelta(days=days)]
        adds = recent[recent["action"] == "add"].sort_values("timestamp")
        drops = recent[recent["action"] == "drop"].sort_values("timestamp")

        # Trades: winner and loser by per-game value
        for _, g in recent[recent["action"] == "trade"].groupby("transaction_id"):
            sides = {k: grp["player_name"].tolist() for k, grp in g.groupby("team_key")}
            if len(sides) != 2:
                continue
            (ta, pa), (tb, pb) = sides.items()
            va = sum(info(n)[1] for n in pa if info(n))
            vb = sum(info(n)[1] for n in pb if info(n))
            win, lose, got, edge = (ta, tb, pa, va - vb) if va >= vb else (tb, ta, pb, vb - va)
            if edge >= 0.4:
                good[win].append((3.5, f"Won the {got[0]} trade (+{edge:.1f} value)"))
                bad[lose].append((3.5, f"Lost the {got[0]} trade (−{edge:.1f} value)"))
            else:
                for t, p in ((ta, pa), (tb, pb)):
                    good[t].append((1.2, f"Fair trade for {p[0]}"))

        # Best pickup still held
        dropped_after = set(zip(drops["team_key"], drops["player_name"]))
        for t, grp in adds.groupby("team_key"):
            held = [(info(n)[0], n) for n in grp["player_name"]
                    if (t, n) not in dropped_after and info(n)]
            if held:
                rk, n = min(held)
                if rk <= 150:
                    good[t].append((2.5 + (150 - rk) / 100, f"Pickup {n} is now #{rk}"))

        # Scooped someone else's drop
        for _, d in drops.iterrows():
            later = adds[(adds["player_name"] == d["player_name"]) & (adds["team_key"] != d["team_key"])
                         & (adds["timestamp"] > d["timestamp"])
                         & (adds["timestamp"] <= d["timestamp"] + pd.Timedelta(days=3))]
            if not later.empty and later.iloc[0]["team_key"] in good:
                i = info(d["player_name"])
                bonus = 0.8 if i and i[0] <= 120 else 0
                good[later.iloc[0]["team_key"]].append((2.2 + bonus, f"Scooped {d['player_name']} off waivers"))

        # Regret: cut a top-120 player
        for _, d in drops.iterrows():
            i = info(d["player_name"])
            if i and i[0] <= 120 and d["team_key"] in bad:
                taken = adds[(adds["player_name"] == d["player_name"]) & (adds["timestamp"] > d["timestamp"])]
                where = "now on another team" if not taken.empty else "still on waivers"
                bad[d["team_key"]].append((2.4 + (120 - i[0]) / 100, f"Cut {d['player_name']} (#{i[0]}), {where}"))

        # Activity
        week = adds[adds["timestamp"] >= now - pd.Timedelta(days=7)]
        cnt = week.groupby("team_key").size()
        if not cnt.empty and cnt.max() >= 3:
            t = cnt.idxmax()
            if t in good:
                good[t].append((1.4, f"{int(cnt.max())} adds this week, most in the league"))
        active = set(recent["team_key"])
        for t in names:
            if t not in active:
                bad[t].append((1.3 + (0.6 if win_odds and win_odds.get(t, 1) < 0.4 else 0), "No moves in two weeks"))

    # Injuries on the roster
    if rosters is not None and not rosters.empty and "status" in rosters:
        from .trade import is_unavailable
        hurt = rosters[rosters["status"].map(is_unavailable)]
        if "selected_position" in hurt:
            hurt = hurt[~hurt["selected_position"].isin({"IL", "IL+"})]
        for t, grp in hurt.groupby("team_key"):
            if t in bad and len(grp) >= 1:
                n = len(grp)
                who = grp["name"].iloc[0]
                bad[t].append((1.6 + 0.4 * n, f"{who} out, not on IL" if n == 1 else f"{n} injured players taking roster spots"))

    # Fallbacks: best and worst category, ranked on actual season-to-date stats
    from .power import season_ranks
    values, rk = season_ranks(tw)
    if not rk.empty:
        n = len(rk)
        for t in names:
            if t not in rk.index or rk.loc[t].isna().all():
                continue
            row = rk.loc[t]
            best, worst = row.idxmin(), row.idxmax()
            good[t].append((0.5, _rank_text(best, int(row[best]), values.at[t, best], n, tied=(rk[best] == row[best]).sum() > 1)))
            bad[t].append((0.5, _rank_text(worst, int(row[worst]), values.at[t, worst], n, tied=(rk[worst] == row[worst]).sum() > 1)))

    out = []
    for t in names:
        g = max(good[t], key=lambda x: x[0])[1] if good[t] else "Holding steady"
        b = max(bad[t], key=lambda x: x[0])[1] if bad[t] else "Nothing glaring"
        out.append(TeamNote(t, g, b))
    return out
