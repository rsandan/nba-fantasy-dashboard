"""NBA stats + schedule via ``nba_api``.

Changes from the original app
-----------------------------
* **One league-wide call instead of one call per player.** ``LeagueDashPlayerStats``
  returns every player's per-game line in a single request; the old comparison page
  hit ``PlayerGameLog`` once per player per click (and stats.nba.com throttles hard).
* **Season is derived from the date**, not hard-coded to ``"2024-25"``.
* **Shooting percentages are volume-weighted** (sum makes / sum attempts). The old
  comparison averaged per-game FG%, so a 1/1 night counted as much as 10/25.
* **Early-season blending.** Before ~15 games a player's current-season line is mostly
  noise, so ``blend_seasons`` shrinks it toward last season (see docstring).
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import date

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

STAT_COLS = ["MIN", "FGM", "FGA", "FTM", "FTA", "FG3M", "PTS", "REB", "AST", "STL", "BLK", "TOV"]
NBA_HEADERS = {
    "Host": "stats.nba.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Connection": "keep-alive",
}

# Yahoo's editorial team abbreviations -> NBA tricodes (only the ones that differ).
YAHOO_TO_NBA_TEAM = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX",
    "UTAH": "UTA", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA", "NOR": "NOP",
}


def season_for(d: date | None = None) -> str:
    """NBA season label for a date. Training camp opens in late September, so from
    August onward we treat the upcoming season as current (``2026-09-27 -> '2026-27'``)."""
    d = d or date.today()
    start = d.year if d.month >= 8 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def previous_season(season: str) -> str:
    start = int(season[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def normalize_name(name: str) -> str:
    """Canonical key for joining Yahoo and NBA player names.

    Handles accents (Jokić), suffixes (Jr., III), punctuation (O'Neale, P.J.) and
    spacing, which covers essentially every Yahoo/NBA name mismatch in practice.
    """
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def _with_retries(fn, attempts: int = 3, base_delay: float = 1.5):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # stats.nba.com times out / 429s intermittently
            last = exc
            time.sleep(base_delay * (2 ** i))
    raise last  # type: ignore[misc]


def fetch_player_per_game(season: str, last_n_games: int = 0) -> pd.DataFrame:
    """Per-game lines for every player in ``season`` (optionally last N games)."""
    from nba_api.stats.endpoints import leaguedashplayerstats

    def call():
        return leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, per_mode_detailed="PerGame", last_n_games=last_n_games,
            season_type_all_star="Regular Season", headers=NBA_HEADERS, timeout=45,
        ).get_data_frames()[0]

    try:
        raw = _with_retries(call)
    except Exception as exc:
        log.warning("nba_api player stats failed for %s: %s", season, exc)
        return pd.DataFrame()
    if raw.empty:
        return raw
    df = raw.rename(columns={"TEAM_ABBREVIATION": "TEAM"})
    return standardize_player_frame(df)


def standardize_player_frame(df: pd.DataFrame) -> pd.DataFrame:
    keep = ["PLAYER_ID", "PLAYER_NAME", "TEAM", "GP", *STAT_COLS]
    out = df[[c for c in keep if c in df.columns]].copy()
    for c in ["GP", *STAT_COLS]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    out["name_key"] = out["PLAYER_NAME"].map(normalize_name)
    return out.reset_index(drop=True)


def blend_seasons(current: pd.DataFrame, prior: pd.DataFrame, k: float = 15.0) -> pd.DataFrame:
    """Shrink current-season per-game rates toward last season.

    ``rate = w * current + (1 - w) * prior`` with ``w = GP / (GP + k)``. With k=15 a
    player 5 games in is weighted 25% current / 75% prior; at 45 games it is 75/25.
    This is the standard beta-binomial / credibility-weighting argument: k is roughly
    the number of games at which current-season signal equals the prior's. Players
    with no prior (rookies) use current only; players with no current games (injured,
    or preseason) use prior only, so the draft board works before opening night.
    """
    if current is None or current.empty:
        out = prior.copy()
        out["GP_CUR"] = 0.0
        out["GP_TOT"] = out["GP"]
        out["blend_w"] = 0.0
        out["availability"] = (out["GP"] / 82).clip(0, 1)
        return out
    if prior is None or prior.empty:
        out = current.copy()
        out["GP_CUR"] = out["GP"]
        out["GP_TOT"] = out["GP"]
        out["blend_w"] = 1.0
        return out

    cur = current.set_index("PLAYER_ID")
    pri = prior.set_index("PLAYER_ID")
    ids = cur.index.union(pri.index)
    cur, pri = cur.reindex(ids), pri.reindex(ids)
    gp_cur = cur["GP"].fillna(0.0)
    w = gp_cur / (gp_cur + k)
    w = w.where(pri["GP"].fillna(0) > 0, 1.0)          # no prior -> trust current
    w = w.where(gp_cur > 0, 0.0)                         # no current -> prior only
    out = pd.DataFrame(index=ids)
    for c in STAT_COLS:
        out[c] = w * cur[c].fillna(0.0) + (1 - w) * pri[c].fillna(0.0)
    out["PLAYER_NAME"] = cur["PLAYER_NAME"].fillna(pri["PLAYER_NAME"])
    out["TEAM"] = cur["TEAM"].fillna(pri["TEAM"])          # current team after trades/FA
    gp_pri = pri["GP"].fillna(0.0)
    out["GP"] = gp_cur.where(gp_cur > 0, gp_pri)
    out["GP_CUR"] = gp_cur
    out["GP_TOT"] = gp_cur + gp_pri                     # evidence behind the blended line
    out["blend_w"] = w
    # Availability (share of team games played) blends the same way: early-season
    # GP/team-games is dominated by a couple of rest days, last season's 82-game rate isn't.
    team = out["TEAM"]
    cur_team_games = gp_cur.groupby(team).transform("max").clip(lower=1)
    out["availability"] = (w * (gp_cur / cur_team_games) + (1 - w) * (gp_pri / 82)).clip(0, 1)
    out["name_key"] = out["PLAYER_NAME"].map(normalize_name)
    return out.reset_index().rename(columns={"index": "PLAYER_ID"})


def fetch_schedule(season: str) -> pd.DataFrame:
    """Full regular-season schedule: one row per game with date, home and away tricodes."""
    from nba_api.stats.endpoints import scheduleleaguev2

    def call():
        return scheduleleaguev2.ScheduleLeagueV2(
            season=season, headers=NBA_HEADERS, timeout=45).get_data_frames()[0]

    try:
        raw = _with_retries(call)
    except Exception as exc:
        log.warning("nba_api schedule failed for %s: %s", season, exc)
        return pd.DataFrame(columns=["game_date", "home", "away"])
    date_col = "gameDateEst" if "gameDateEst" in raw.columns else "gameDate"
    out = pd.DataFrame({
        "game_date": pd.to_datetime(raw[date_col], errors="coerce").dt.date,
        "home": raw.get("homeTeam_teamTricode"),
        "away": raw.get("awayTeam_teamTricode"),
    }).dropna()
    # Preseason/All-Star rows have non-NBA tricodes or empty teams; keep real matchups only.
    return out[(out["home"].str.len() == 3) & (out["away"].str.len() == 3)].reset_index(drop=True)


def games_by_team(schedule: pd.DataFrame, start: date, end: date) -> pd.Series:
    """Games each NBA team plays in ``[start, end]`` (inclusive)."""
    if schedule is None or schedule.empty:
        return pd.Series(dtype=float)
    window = schedule[(schedule["game_date"] >= start) & (schedule["game_date"] <= end)]
    return pd.concat([window["home"], window["away"]]).value_counts().astype(float)


def fetch_game_logs(player_id: int, season: str) -> pd.DataFrame:
    from nba_api.stats.endpoints import playergamelog

    def call():
        return playergamelog.PlayerGameLog(
            player_id=player_id, season=season, season_type_all_star="Regular Season",
            headers=NBA_HEADERS, timeout=45).get_data_frames()[0]

    try:
        df = _with_retries(call)
    except Exception as exc:
        log.warning("game log failed for %s: %s", player_id, exc)
        return pd.DataFrame()
    if df.empty:
        return df
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], format="mixed", errors="coerce")
    return df


def summarize_logs(df: pd.DataFrame, mode: str = "Average") -> pd.Series:
    """Per-game average or total from game logs, with volume-weighted percentages."""
    cols = ["MIN", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB", "REB",
            "AST", "STL", "BLK", "TOV", "PF", "PTS", "PLUS_MINUS"]
    cols = [c for c in cols if c in df.columns]
    totals = df[cols].apply(pd.to_numeric, errors="coerce").sum()
    out = totals / len(df) if mode == "Average" else totals.copy()
    out = out.round(1)
    out["GP"] = len(df)
    for pct, m, a in (("FG%", "FGM", "FGA"), ("3P%", "FG3M", "FG3A"), ("FT%", "FTM", "FTA")):
        if m in totals and a in totals:
            out[pct] = round(totals[m] / totals[a], 3) if totals[a] > 0 else np.nan
    return out
