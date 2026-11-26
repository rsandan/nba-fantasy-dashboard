"""Pre-built NBA data snapshots, so a page load never waits on stats.nba.com.

Why this exists
---------------
stats.nba.com throttles or silently drops requests from cloud hosts (Render included).
With retries and 45 s timeouts, one blocked call held the page on "Loading player
stats…" for minutes, then came back empty. So the app no longer calls it at all.

Instead a GitHub Action (``.github/workflows/refresh-data.yml``) runs
``scripts/refresh_data.py`` every few hours and commits small CSVs to the ``data``
branch. The app reads them from raw.githubusercontent.com (a CDN, ~1 s), falling back
to a local ``data/`` folder if one exists. Committing to ``data`` instead of ``main``
means a refresh never triggers a Render redeploy.

The refresh script tries stats.nba.com first and, if that's blocked, rebuilds the same
per-game table from cdn.nba.com box scores (public static JSON, not rate limited). The
parsers for that path live here and are pure so they're unit-tested offline.
"""
from __future__ import annotations

import io
import logging
import os
import re
import urllib.request
from pathlib import Path

import pandas as pd

from .nba_data import STAT_COLS, standardize_player_frame

log = logging.getLogger(__name__)

RAW_BASE = os.getenv(
    "NBA_SNAPSHOT_URL",
    "https://raw.githubusercontent.com/rsandan/nba-fantasy-dashboard/data/",
)
LOCAL_DIR = Path(os.getenv("NBA_SNAPSHOT_DIR", Path(__file__).resolve().parent.parent / "data"))
FETCH_TIMEOUT = float(os.getenv("NBA_SNAPSHOT_TIMEOUT", "10"))

CDN_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
CDN_BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
CDN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}
GAME_COLUMNS = ["game_id", "game_date", "PLAYER_ID", "PLAYER_NAME", "TEAM", *STAT_COLS]


# --------------------------------------------------------------------- file names
def players_file(season: str, last_n: int = 0) -> str:
    return f"players_{season}_last{int(last_n)}.csv"


def schedule_file(season: str) -> str:
    return f"schedule_{season}.csv"


def games_file(season: str) -> str:
    return f"games_{season}.csv.gz"


# ------------------------------------------------------------------------ reading
def _read_remote(name: str) -> pd.DataFrame | None:
    if not RAW_BASE:
        return None
    try:
        req = urllib.request.Request(RAW_BASE + name, headers={"User-Agent": "love-island-dashboard"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return pd.read_csv(io.BytesIO(resp.read()))
    except Exception as exc:  # 404 before the first refresh, network blips, etc.
        log.info("snapshot %s not available remotely: %s", name, exc)
        return None


def _read_local(name: str) -> pd.DataFrame | None:
    path = LOCAL_DIR / name
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except Exception as exc:
        log.warning("couldn't read local snapshot %s: %s", path, exc)
        return None


def read(name: str) -> pd.DataFrame:
    """Remote snapshot, else local copy, else an empty frame. Never raises, never hangs."""
    for reader in (_read_remote, _read_local):
        df = reader(name)
        if df is not None and not df.empty:
            return df
    return pd.DataFrame()


def load_players(season: str, last_n: int = 0) -> pd.DataFrame:
    df = read(players_file(season, last_n))
    return standardize_player_frame(df) if not df.empty else df


def load_schedule(season: str) -> pd.DataFrame:
    df = read(schedule_file(season))
    if df.empty:
        return pd.DataFrame(columns=["game_date", "home", "away"])
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date
    return df[["game_date", "home", "away"]].dropna().reset_index(drop=True)


# ------------------------------------------------------------- cdn.nba.com parsing
def season_code(season: str) -> str:
    """``'2026-27' -> '26'``: the two digits NBA game ids use for a season."""
    return season[2:4]


def regular_season_game_id(season: str, n: int) -> str:
    """Regular-season game ids are ``002`` + season yy + 5-digit sequence."""
    return f"002{season_code(season)}{n:05d}"


def parse_cdn_schedule(raw: dict) -> pd.DataFrame:
    """scheduleLeagueV2.json -> one row per regular-season game."""
    rows = []
    sched = raw.get("leagueSchedule", {})
    for day in sched.get("gameDates", []):
        for g in day.get("games", []):
            gid = str(g.get("gameId", ""))
            if not gid.startswith("002"):          # preseason 001, All-Star 003, playoffs 004
                continue
            home = (g.get("homeTeam") or {}).get("teamTricode")
            away = (g.get("awayTeam") or {}).get("teamTricode")
            when = g.get("gameDateEst") or g.get("gameDateTimeEst") or day.get("gameDate")
            rows.append({"game_id": gid, "game_date": pd.to_datetime(when, errors="coerce"),
                         "home": home, "away": away, "status": int(g.get("gameStatus") or 0)})
    df = pd.DataFrame(rows, columns=["game_id", "game_date", "home", "away", "status"])
    if df.empty:
        return df
    df["game_date"] = df["game_date"].dt.date
    df = df[(df["home"].str.len() == 3) & (df["away"].str.len() == 3)]
    return df.reset_index(drop=True)


_MIN_RE = re.compile(r"PT(?:(\d+)M)?(?:([\d.]+)S)?")


def parse_minutes(value) -> float:
    """``'PT34M12.00S' -> 34.2``; also accepts ``'34:12'`` and plain numbers."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    m = _MIN_RE.fullmatch(s)
    if m:
        return float(m.group(1) or 0) + float(m.group(2) or 0) / 60
    if ":" in s:
        mm, ss = s.split(":", 1)
        try:
            return float(mm) + float(ss) / 60
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


_BOX_FIELDS = {
    "FGM": "fieldGoalsMade", "FGA": "fieldGoalsAttempted",
    "FTM": "freeThrowsMade", "FTA": "freeThrowsAttempted",
    "FG3M": "threePointersMade", "FG3A": "threePointersAttempted", "PTS": "points", "REB": "reboundsTotal",
    "AST": "assists", "STL": "steals", "BLK": "blocks", "TOV": "turnovers",
}


def parse_cdn_boxscore(raw: dict) -> pd.DataFrame:
    """One live-data box score -> one row per player who actually played."""
    game = raw.get("game", {})
    gid = str(game.get("gameId", ""))
    when = pd.to_datetime(game.get("gameEt") or game.get("gameTimeUTC"), errors="coerce")
    gdate = when.date() if pd.notna(when) else None
    rows = []
    for side in ("homeTeam", "awayTeam"):
        team = game.get(side) or {}
        tri = team.get("teamTricode")
        for p in team.get("players", []) or []:
            st = p.get("statistics") or {}
            minutes = parse_minutes(st.get("minutes") or st.get("minutesCalculated"))
            if str(p.get("played", "1")) != "1" or minutes <= 0:
                continue
            row = {"game_id": gid, "game_date": gdate, "PLAYER_ID": int(p["personId"]),
                   "PLAYER_NAME": p.get("name") or f"{p.get('firstName', '')} {p.get('familyName', '')}".strip(),
                   "TEAM": tri, "MIN": round(minutes, 2)}
            for col, field in _BOX_FIELDS.items():
                row[col] = float(st.get(field) or 0)
            rows.append(row)
    return pd.DataFrame(rows, columns=GAME_COLUMNS)


def aggregate_per_game(games: pd.DataFrame, last_n: int = 0) -> pd.DataFrame:
    """Player-game rows -> the same per-game table LeagueDashPlayerStats returns.

    ``last_n`` keeps each player's most recent ``n`` games. Team is the player's team in
    his latest game, so traded players show their current club.
    """
    if games is None or games.empty:
        return pd.DataFrame()
    g = games.copy()
    g["game_date"] = pd.to_datetime(g["game_date"], errors="coerce")
    g = g.sort_values(["PLAYER_ID", "game_date", "game_id"])
    if last_n:
        g = g.groupby("PLAYER_ID", group_keys=False).tail(int(last_n))
    agg = g.groupby("PLAYER_ID").agg(
        PLAYER_NAME=("PLAYER_NAME", "last"), TEAM=("TEAM", "last"), GP=("game_id", "nunique"),
        **{c: (c, "mean") for c in STAT_COLS})
    out = agg.reset_index()
    out[STAT_COLS] = out[STAT_COLS].round(2)
    return out


# ------------------------------------------------------- Basketball-Reference
# Cloud-friendly per-game table (one page per season). NBA blocks GitHub's runners on
# both stats.nba.com and cdn.nba.com; Basketball-Reference and ESPN don't.
BBREF_PER_GAME = "https://www.basketball-reference.com/leagues/NBA_{end_year}_per_game.html"
ESPN_SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
                   "?dates={yyyymmdd}&limit=40")
_BBREF_COLS = {"G": "GP", "MP": "MIN", "FG": "FGM", "FGA": "FGA", "FT": "FTM", "FTA": "FTA",
               "3P": "FG3M", "3PA": "FG3A", "PTS": "PTS", "TRB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK",
               "TOV": "TOV"}


def bbref_end_year(season: str) -> int:
    """``'2026-27' -> 2027``: Basketball-Reference names seasons by the year they end."""
    return int(season[:4]) + 1


def nba_team(abbr) -> str | None:
    from .nba_data import YAHOO_TO_NBA_TEAM
    if not isinstance(abbr, str) or not abbr:
        return None
    return YAHOO_TO_NBA_TEAM.get(abbr.upper(), abbr.upper())


def parse_bbref_per_game(html: str) -> pd.DataFrame:
    """Basketball-Reference per-game page -> PLAYER_NAME, TEAM, GP + per-game STAT_COLS.

    Traded players appear once per team plus a combined row (``TOT`` / ``2TM``); we keep
    the combined line and label it with the player's most recent team.
    """
    tables = pd.read_html(io.StringIO(html), attrs={"id": "per_game_stats"})
    df = tables[0]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)
    team_col = "Team" if "Team" in df.columns else "Tm"
    df = df[df["Player"].notna() & (df["Player"] != "Player")]
    df = df[~df["Player"].astype(str).str.contains("League Average", na=False)].copy()
    df["Player"] = df["Player"].astype(str).str.replace("*", "", regex=False).str.strip()
    df["_combined"] = df[team_col].astype(str).str.fullmatch(r"TOT|\dTM")
    df["_order"] = range(len(df))
    rows = []
    for _, grp in df.groupby(["Player", "Age"] if "Age" in df.columns else ["Player"], sort=False):
        combined = grp[grp["_combined"]]
        line = (combined if not combined.empty else grp).iloc[0]
        teams = grp[~grp["_combined"]].sort_values("_order")
        latest = teams.iloc[-1][team_col] if not teams.empty else line[team_col]
        rec = {"PLAYER_NAME": line["Player"], "TEAM": nba_team(latest)}
        for src, dst in _BBREF_COLS.items():
            rec[dst] = pd.to_numeric(line.get(src), errors="coerce")
        rows.append(rec)
    out = pd.DataFrame(rows)
    for c in ["GP", *STAT_COLS]:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    return out[out["GP"] > 0].reset_index(drop=True)


def attach_nba_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Add NBA person ids by name, using the player list bundled with nba_api (offline).

    The ids drive headshot photos. Unmatched players get a stable negative id so joins
    and de-duplication still work; the UI shows initials for those.
    """
    from nba_api.stats.static import players as static_players

    from .nba_data import normalize_name

    roster = pd.DataFrame(static_players.get_players())
    roster["name_key"] = roster["full_name"].map(normalize_name)
    roster = roster.sort_values("is_active", ascending=False).drop_duplicates("name_key")
    ids = dict(zip(roster["name_key"], roster["id"]))
    keys = df["PLAYER_NAME"].map(normalize_name)
    fallback = keys.map(lambda k: -(int.from_bytes(k.encode()[:6].ljust(6, b"\0"), "big") % 10**9) - 1)
    out = df.copy()
    out["PLAYER_ID"] = keys.map(ids).fillna(fallback).astype("int64")
    return out.drop_duplicates("PLAYER_ID")


def parse_espn_scoreboard(raw: dict, game_date) -> pd.DataFrame:
    """ESPN scoreboard for one day -> game_date, home, away (NBA tricodes)."""
    rows = []
    for ev in raw.get("events", []) or []:
        season_type = ((ev.get("season") or {}).get("type"))
        if season_type not in (None, 2):        # 1 preseason, 2 regular, 3 playoffs
            continue
        comp = (ev.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): nba_team((c.get("team") or {}).get("abbreviation"))
                 for c in comp.get("competitors", [])}
        if sides.get("home") and sides.get("away"):
            rows.append({"game_date": game_date, "home": sides["home"], "away": sides["away"]})
    return pd.DataFrame(rows, columns=["game_date", "home", "away"])


# ---------------------------------------------------------- "last N days" windows
TOTAL_COLS = ["GP", *STAT_COLS]


def to_totals(per_game: pd.DataFrame, as_of) -> pd.DataFrame:
    """Per-game table -> season-to-date totals stamped with a date (one history row)."""
    t = per_game[["PLAYER_ID", "PLAYER_NAME", "TEAM", "GP", *STAT_COLS]].copy()
    for c in STAT_COLS:
        t[c] = (t[c] * t["GP"]).round(1)
    t.insert(0, "as_of", pd.Timestamp(as_of).date().isoformat())
    return t


def window_from_history(history: pd.DataFrame, as_of, days: int) -> pd.DataFrame:
    """Per-game lines over roughly the last ``days`` days from cumulative snapshots.

    Subtracts each player's totals as of ``days`` ago from today's totals. Empty until
    the history reaches back far enough.
    """
    if history is None or history.empty:
        return pd.DataFrame()
    h = history.copy()
    h["as_of"] = pd.to_datetime(h["as_of"]).dt.date
    today = pd.Timestamp(as_of).date()
    cutoff = (pd.Timestamp(as_of) - pd.Timedelta(days=days)).date()
    cur = h[h["as_of"] == h.loc[h["as_of"] <= today, "as_of"].max()]
    past = h[h["as_of"] <= cutoff]
    if cur.empty or past.empty:
        return pd.DataFrame()
    base = past.sort_values("as_of").groupby("PLAYER_ID").tail(1).set_index("PLAYER_ID")
    cur = cur.set_index("PLAYER_ID")
    base = base.reindex(cur.index)
    base[TOTAL_COLS] = base[TOTAL_COLS].fillna(0.0)
    d = cur[TOTAL_COLS] - base[TOTAL_COLS]
    d = d[d["GP"] > 0]
    out = d[STAT_COLS].div(d["GP"], axis=0).round(2)
    out["GP"] = d["GP"]
    out["PLAYER_NAME"] = cur.loc[out.index, "PLAYER_NAME"]
    out["TEAM"] = cur.loc[out.index, "TEAM"]
    return out.reset_index()
