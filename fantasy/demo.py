"""Synthetic league generator.

Used for (1) the test suite, (2) ``FANTASY_DEMO=1`` so the dashboard runs without
Yahoo credentials (useful for portfolio visitors when tokens expire), and (3)
sanity-checking the model: we *know* each synthetic team's true strength, so the
backtest should beat the baselines and be calibrated.

The generator is deliberately generative rather than i.i.d. noise: each team has
latent per-game rates, games per week vary with the schedule, counting stats are
Poisson given games, and makes are Binomial given attempts. That reproduces the
real structure the model has to deal with (volume-driven correlation, ties in
low-count categories, percentage noise that shrinks with attempts).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .yahoo_client import TEAM_WEEK_COLUMNS, LeagueMeta

TEAM_NAMES = ["Sam's Swag Team", "Doomenshmirtz Evil Inc.", "Chunch's Challengers", "Han Da Dons",
              "Tyshiii", "Neal's Fascinating Team", "Driton's Dazzling Team", "ariel's Wonderful Team",
              "THE REAL MIAMI HEAT", "darius"]
NBA_TEAMS = ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC",
             "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC",
             "SAS", "TOR", "UTA", "WAS"]
YAHOO_ABBR = {"GSW": "GS", "NOP": "NO", "NYK": "NY", "SAS": "SA", "PHX": "PHO"}
FIRST = ["Jalen", "Tyrese", "Anthony", "Devin", "Jaylen", "Scottie", "Paolo", "Evan", "Cade", "Darius",
         "Franz", "Alperen", "Victor", "Chet", "Amen", "Jamal", "Tyler", "Mikal", "Dejounte", "Nikola",
         "Luka", "Jayson", "Donovan", "Trae", "Zion", "Brandon", "De'Aaron", "Kawhi", "Jimmy", "Domantas"]
LAST = ["Brunson", "Maxey", "Edwards", "Booker", "Brown", "Barnes", "Banchero", "Mobley", "Cunningham",
        "Garland", "Wagner", "Sengun", "Wembanyama", "Holmgren", "Thompson", "Murray", "Herro", "Bridges",
        "Jokic", "Doncic", "Tatum", "Mitchell", "Young", "Williamson", "Miller", "Fox", "Leonard", "Butler",
        "Sabonis", "Ingram"]

LEAGUE_PREFIX = "466.l.99999"


def _team_keys(n=10):
    return [f"{LEAGUE_PREFIX}.t.{i}" for i in range(1, n + 1)]


def _latent_rates(rng, n):
    """Per player-game latent rates with archetypes (e.g. big-man builds punt FT%)."""
    base = dict(FGA=8.4, FGP=0.475, FTA=2.4, FTP=0.785, FG3M=1.35, PTS=11.8, REB=4.6, AST=2.8,
                STL=0.85, BLK=0.55, TOV=1.45)
    rows = []
    for i in range(n):
        big = rng.uniform(-1, 1)          # + = bigs (REB/BLK/FG%), - = guards (AST/3PTM/FT%)
        vol = rng.normal(0, 0.06)         # overall quality
        r = {k: v * (1 + vol) for k, v in base.items()}
        r["REB"] *= 1 + 0.14 * big
        r["BLK"] *= 1 + 0.30 * big
        r["FGP"] = base["FGP"] + 0.018 * big + rng.normal(0, 0.006)
        r["FTP"] = base["FTP"] - 0.035 * big + rng.normal(0, 0.01)
        r["AST"] *= 1 - 0.18 * big
        r["FG3M"] *= 1 - 0.22 * big
        r["STL"] *= 1 + rng.normal(0, 0.06)
        r["TOV"] *= 1 - 0.08 * big + rng.normal(0, 0.05)
        r["PTS"] = r["FGA"] * r["FGP"] * 2 + r["FG3M"] + r["FTA"] * r["FTP"]
        rows.append(r)
    return pd.DataFrame(rows)


def _latent_from_rosters(rng, rosters, players, keys):
    """Team per-game rates implied by the roster, rescaled to Yahoo's started-game level."""
    from .matchup_model import COMPONENTS, roster_priors
    base = pd.Series(dict(FGA=8.4, FGP=0.475, FTA=2.4, FTP=0.785, FG3M=1.35, PTS=11.8, REB=4.6,
                          AST=2.8, STL=0.85, BLK=0.55, TOV=1.45))
    from .nba_data import normalize_name
    ros = rosters.assign(name_key=rosters["name"].map(normalize_name)).merge(
        players[["name_key", "PLAYER_ID"]], on="name_key", how="left")
    pri = roster_priors(ros, players.assign(availability=players["GP"] / 82))
    if pri.empty:
        latent = _latent_rates(rng, len(keys))
        latent.index = keys
        return latent
    latent = pri.reindex(keys).fillna(pri.mean())[COMPONENTS] * (base / pri.mean())[COMPONENTS]
    latent = latent * rng.normal(1, 0.02, latent.shape)       # coaching/streaming skill not in the roster
    latent["PTS"] = 2 * latent["FGA"] * latent["FGP"] + latent["FG3M"] + latent["FTA"] * latent["FTP"]
    return latent


def _week_totals(rng, rates: pd.Series, games: int) -> dict:
    fga = rng.poisson(rates["FGA"] * games)
    fta = rng.poisson(rates["FTA"] * games)
    fgm = rng.binomial(fga, np.clip(rates["FGP"], 0, 1))
    ftm = rng.binomial(fta, np.clip(rates["FTP"], 0, 1))
    fg3m = min(rng.poisson(rates["FG3M"] * games), fgm)
    return dict(FGM=float(fgm), FGA=float(fga), FTM=float(ftm), FTA=float(fta), FG3M=float(fg3m),
                PTS=float(2 * fgm + fg3m + ftm), REB=float(rng.poisson(rates["REB"] * games)),
                AST=float(rng.poisson(rates["AST"] * games)), STL=float(rng.poisson(rates["STL"] * games)),
                BLK=float(rng.poisson(rates["BLK"] * games)), TOV=float(rng.poisson(rates["TOV"] * games)))


def _round_robin(teams: list[str], weeks: int) -> dict[int, list[tuple[str, str]]]:
    t = list(teams)
    out = {}
    for w in range(1, weeks + 1):
        out[w] = [(t[i], t[-1 - i]) for i in range(len(t) // 2)]
        t = [t[0]] + [t[-1]] + t[1:-1]
    return out


def make_league(seed: int = 42, n_teams: int = 10, completed_weeks: int = 9, total_weeks: int = 20,
                current_progress: float = 0.55):
    """Return a dict with everything the app needs, in the same schemas as the live path."""
    rng = np.random.default_rng(seed)
    keys = _team_keys(n_teams)
    names = dict(zip(keys, TEAM_NAMES[:n_teams]))
    # Rosters first, then derive each team's true strength from its roster so every page
    # (roster priors, valuations, results) tells one consistent story.
    players_cur, players_prior = make_players(rng)
    rosters, free_agents = _rosters(rng, players_cur, keys)
    latent = _latent_from_rosters(rng, rosters, players_prior, keys)
    schedule = _round_robin(keys, total_weeks)
    season_start = date(2026, 10, 19)

    rows = []
    for w, pairs in schedule.items():
        ws, we = season_start + timedelta(days=7 * (w - 1)), season_start + timedelta(days=7 * w - 1)
        status = ("postevent" if w <= completed_weeks else
                  "midevent" if w == completed_weeks + 1 else "preevent")
        for mi, (a, b) in enumerate(pairs):
            for team, opp in ((a, b), (b, a)):
                sched_games = int(rng.integers(27, 38))
                if status == "postevent":
                    played, remaining = sched_games, 0
                elif status == "midevent":
                    played = int(round(sched_games * current_progress))
                    remaining = sched_games - played
                else:
                    played, remaining = 0, sched_games
                totals = _week_totals(rng, latent.loc[team], played) if played else dict.fromkeys(
                    ["FGM", "FGA", "FTM", "FTA", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"], 0.0)
                rows.append(dict(week=w, matchup_id=f"{w}-{mi}", team_key=team, team_name=names[team],
                                 opponent_key=opp, status=status, is_playoffs=False, is_consolation=False,
                                 week_start=ws.isoformat(), week_end=we.isoformat(), games=float(played),
                                 live_games=0.0, remaining_games=float(remaining), **totals,
                                 **{"FG%": totals["FGM"] / totals["FGA"] if totals["FGA"] else np.nan,
                                    "FT%": totals["FTM"] / totals["FTA"] if totals["FTA"] else np.nan}))
    team_weeks = pd.DataFrame(rows)[TEAM_WEEK_COLUMNS]

    current_week = completed_weeks + 1
    meta = LeagueMeta(league_key=LEAGUE_PREFIX, name="Season 3 of Love Island (NBA) [demo]",
                      season="2026", scoring_type="head", num_teams=n_teams, current_week=current_week,
                      start_week=1, end_week=total_weeks + 3, playoff_start_week=total_weeks + 1,
                      num_playoff_teams=6, roster_size=13)
    teams = pd.DataFrame({"team_key": keys, "team_name": [names[k] for k in keys], "logo_url": None,
                          "manager": [n.split("'")[0] for n in names.values()], "moves": 0, "trades": 0})

    tx = make_transactions(rng, keys, names, players_cur, season_start, completed_weeks)
    nba_sched = make_nba_schedule(rng, season_start, total_weeks)
    return dict(meta=meta, teams=teams, team_weeks=team_weeks, latent=latent, players=players_cur,
                players_prior=players_prior, rosters=rosters, free_agents=free_agents,
                transactions=tx, schedule=nba_sched)


def make_players(rng, n=420):
    """Current (partial) and prior-season per-game lines for a synthetic player universe."""
    names = {f"{FIRST[i % len(FIRST)]} {LAST[(i * 7 + i // len(FIRST)) % len(LAST)]}"
             + ("" if i < 300 else " Jr.") for i in range(n)}
    names = sorted(names)[:n]
    q = np.sort(rng.gamma(2.2, 1.0, len(names)))[::-1]          # talent: few stars, long tail
    q = q / q.max()
    big = rng.uniform(-1, 1, len(names))
    minutes = np.clip(10 + 26 * q + rng.normal(0, 3, len(names)), 6, 38)

    def line(minutes, noise):
        m = minutes / 36
        fga = np.clip(m * (9 + 12 * q) * (1 + rng.normal(0, noise, len(q))), 0.5, None)
        fgp = np.clip(0.455 + 0.05 * big + rng.normal(0, 0.02 + noise / 4, len(q)), 0.35, 0.68)
        fta = np.clip(m * (1.5 + 5.5 * q) * (1 + rng.normal(0, noise, len(q))), 0.1, None)
        ftp = np.clip(0.78 - 0.08 * big + rng.normal(0, 0.04 + noise / 3, len(q)), 0.45, 0.94)
        fg3m = np.clip(m * (1.2 + 2 * q) * (1 - 0.6 * big) * (1 + rng.normal(0, noise, len(q))), 0, None)
        fgm, ftm = fga * fgp, fta * ftp
        return pd.DataFrame({
            "MIN": minutes, "FGM": fgm, "FGA": fga, "FTM": ftm, "FTA": fta, "FG3M": np.minimum(fg3m, fgm),
            "PTS": 2 * fgm + np.minimum(fg3m, fgm) + ftm,
            "REB": np.clip(m * (4 + 4 * q) * (1 + 0.55 * big) * (1 + rng.normal(0, noise, len(q))), 0, None),
            "AST": np.clip(m * (2 + 5 * q) * (1 - 0.5 * big) * (1 + rng.normal(0, noise, len(q))), 0, None),
            "STL": np.clip(m * (0.7 + 0.6 * q) * (1 + rng.normal(0, noise * 1.5, len(q))), 0, None),
            "BLK": np.clip(m * (0.3 + 0.6 * q) * (1 + 1.1 * big) * (1 + rng.normal(0, noise * 1.5, len(q))), 0, None),
            "TOV": np.clip(m * (1 + 2 * q) * (1 + rng.normal(0, noise, len(q))), 0.1, None),
        })

    ids = np.arange(1_630_000, 1_630_000 + len(names))
    team = rng.choice(NBA_TEAMS, len(names))
    prior = line(minutes, 0.08)
    prior.insert(0, "GP", rng.integers(45, 83, len(names)).astype(float))
    cur = line(minutes * rng.normal(1, 0.05, len(names)), 0.2)   # small samples are noisier
    cur.insert(0, "GP", rng.integers(0, 12, len(names)).astype(float))
    from .nba_data import normalize_name
    frames = []
    for df in (cur, prior):
        df.insert(0, "TEAM", team)
        df.insert(0, "PLAYER_NAME", names)
        df.insert(0, "PLAYER_ID", ids)
        df["name_key"] = df["PLAYER_NAME"].map(normalize_name)
        frames.append(df)
    cur = frames[0][frames[0]["GP"] > 0].reset_index(drop=True)
    return cur, frames[1]


def _rosters(rng, players, keys, size=13):
    order = players.sort_values("MIN", ascending=False)
    taken = order.head(len(keys) * size).sample(frac=1, random_state=int(rng.integers(1e9)))
    rows = []
    for i, (_, p) in enumerate(taken.iterrows()):
        rows.append({"player_id": int(p["PLAYER_ID"]) % 100000, "name": p["PLAYER_NAME"],
                     "editorial_team_abbr": YAHOO_ABBR.get(p["TEAM"], p["TEAM"]),
                     "eligible_positions": ["PG", "SG", "SF", "PF", "C"][i % 5], "status": "",
                     "team_key": keys[i % len(keys)]})
    rosters = pd.DataFrame(rows)
    fa = order.iloc[len(keys) * size: len(keys) * size + 150]
    free = pd.DataFrame({"player_id": fa["PLAYER_ID"].astype(int) % 100000, "name": fa["PLAYER_NAME"],
                         "editorial_team_abbr": fa["TEAM"].map(lambda t: YAHOO_ABBR.get(t, t)),
                         "eligible_positions": "G,F", "status": "",
                         "percent_owned": rng.integers(0, 40, len(fa))})
    return rosters, free


def make_transactions(rng, keys, names, players, season_start, weeks):
    tz = "America/New_York"
    activity = rng.gamma(1.5, 1.0, len(keys))                       # some managers grind the wire
    pool = players.sort_values("MIN", ascending=False).iloc[100:260]
    rows, tid = [], 1
    for ti, key in enumerate(keys):
        n = int(activity[ti] * weeks * 1.4)
        for _ in range(n):
            p = pool.sample(1, random_state=int(rng.integers(1e9))).iloc[0]
            day = season_start + timedelta(days=int(rng.integers(0, weeks * 7)))
            hour = int(rng.choice([9, 10, 12, 18, 19, 20, 21, 22, 23], p=[.08, .08, .12, .1, .14, .16, .14, .1, .08]))
            ts = pd.Timestamp(datetime(day.year, day.month, day.day, hour), tz=tz)
            base = dict(transaction_id=str(tid), transaction_type="add/drop", status="successful",
                        team_key=key, team_name=names[key], source_type="freeagents")
            rows.append({**base, "timestamp": ts, "action": "add", "player_id": int(p["PLAYER_ID"]) % 100000,
                         "player_name": p["PLAYER_NAME"], "nba_team": p["TEAM"]})
            if rng.random() < 0.7:                                    # most adds are streams
                hold = float(rng.exponential(5))
                rows.append({**base, "timestamp": ts + pd.Timedelta(days=hold), "action": "drop",
                             "player_id": int(p["PLAYER_ID"]) % 100000, "player_name": p["PLAYER_NAME"],
                             "nba_team": p["TEAM"], "transaction_id": str(tid + 100000)})
            tid += 1
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz)
    return df.sort_values("timestamp").reset_index(drop=True)


def make_nba_schedule(rng, season_start, weeks):
    rows = []
    for d in range(weeks * 7):
        day = season_start + timedelta(days=d)
        teams = list(rng.permutation(NBA_TEAMS))
        n_games = int(rng.integers(3, 12))
        for g in range(n_games):
            rows.append({"game_date": day, "home": teams[2 * g], "away": teams[2 * g + 1]})
    return pd.DataFrame(rows)


def to_yahoo_scoreboard(week_rows: pd.DataFrame) -> dict:
    """Serialise team-week rows back into Yahoo's scoreboard JSON shape (test fixture)."""
    stat = lambda sid, v: {"stat": {"coverage_type": "week", "stat_id": sid, "value": v}}
    matchups = {}
    for i, (mid, grp) in enumerate(week_rows.groupby("matchup_id", sort=False)):
        teams = {}
        for j, (_, r) in enumerate(grp.iterrows()):
            pct = lambda m, a: "" if a == 0 else f"{m / a:.3f}".lstrip("0")
            teams[str(j)] = {"team": [
                [{"team_key": r["team_key"]}, {"team_id": r["team_key"].split(".")[-1]}, {"name": r["team_name"]}],
                {"team_stats": {"coverage_type": "week", "week": str(r["week"]), "stats": [
                    stat("9004003", f"{int(r['FGM'])}/{int(r['FGA'])}"), stat("5", pct(r["FGM"], r["FGA"])),
                    stat("9007006", f"{int(r['FTM'])}/{int(r['FTA'])}"), stat("8", pct(r["FTM"], r["FTA"])),
                    stat("10", str(int(r["FG3M"]))), stat("12", str(int(r["PTS"]))), stat("15", str(int(r["REB"]))),
                    stat("16", str(int(r["AST"]))), stat("17", str(int(r["STL"]))), stat("18", str(int(r["BLK"]))),
                    stat("19", str(int(r["TOV"])))]}},
                {"team_remaining_games": {"coverage_type": "week", "week": str(r["week"]), "total": {
                    "remaining_games": int(r["remaining_games"]), "live_games": int(r["live_games"]),
                    "completed_games": int(r["games"])}}},
            ]}
        teams["count"] = len(grp)
        first = grp.iloc[0]
        matchups[str(i)] = {"matchup": {
            "week": str(first["week"]), "week_start": first["week_start"], "week_end": first["week_end"],
            "status": first["status"], "is_playoffs": "0", "is_consolation": "0",
            "0": {"teams": teams}}}
    matchups["count"] = len(matchups)
    return {"fantasy_content": {"league": [{"league_key": LEAGUE_PREFIX},
                                           {"scoreboard": {"week": str(week_rows["week"].iloc[0]),
                                                           "0": {"matchups": matchups}}}]}}
