"""Yahoo Fantasy API access: auth, league discovery and parsing into tidy frames.

Design notes
------------
* **No hard-coded league or team ids.** Yahoo issues a new league key every season
  (``454.l.74601`` -> ``466.l.xxxxx``), so the old hard-coded ``team_ids`` dict broke
  every October. We discover the league at runtime and read team names from the API.
* **Parsing is pure.** ``parse_scoreboard`` / ``parse_transactions`` take raw JSON and
  return DataFrames, so they are unit-tested against fixtures without network access.
* **Makes/attempts are kept.** The old parser dropped ``FGM/A`` and ``FTM/A`` and kept
  only the rounded percentage, which makes it impossible to aggregate or simulate
  shooting categories correctly. We keep the raw components.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from .categories import DEFAULT, SPLIT_STATS, STAT_TO_COL, YAHOO_STAT_IDS, Scoring, scoring_from_settings

log = logging.getLogger(__name__)

SECRET_FILE_CANDIDATES = ("/etc/secrets/keypair.json", "keypair.json", "oauth2.json")
TEAM_WEEK_COLUMNS = [
    "week", "matchup_id", "team_key", "team_name", "opponent_key", "status",
    "is_playoffs", "is_consolation", "week_start", "week_end",
    "games", "live_games", "remaining_games",
    "FGM", "FGA", "FTM", "FTA", "FG3M", "FG3A", "PTS", "REB", "AST", "STL", "BLK", "TOV",
    "FG%", "FT%", "3PT%", "team_points",
]


# --------------------------------------------------------------------------- auth
def _writable_credentials_path() -> str:
    """Materialise credentials somewhere writable.

    ``yahoo_oauth`` rewrites its JSON file whenever it refreshes the access token.
    Render mounts ``/etc/secrets`` read-only, so refreshing in place fails once the
    one-hour token expires. We copy to a temp file and let the library own that copy.
    Sources, in priority order: ``YAHOO_OAUTH_JSON`` / ``KEYPAIR_JSON`` env vars
    (raw JSON), then the secret-file candidates.
    """
    target = os.path.join(tempfile.gettempdir(), "yahoo_oauth_runtime.json")
    if os.path.exists(target):
        return target
    for env_key in ("YAHOO_OAUTH_JSON", "KEYPAIR_JSON"):
        raw = os.getenv(env_key)
        if raw:
            json.loads(raw)  # fail fast on malformed secrets
            with open(target, "w") as fh:
                fh.write(raw)
            return target
    for candidate in SECRET_FILE_CANDIDATES:
        if os.path.exists(candidate):
            shutil.copyfile(candidate, target)
            return target
    raise FileNotFoundError(
        "No Yahoo OAuth credentials found. Set YAHOO_OAUTH_JSON / KEYPAIR_JSON or mount "
        "/etc/secrets/keypair.json (fields: consumer_key, consumer_secret, access_token, "
        "refresh_token, token_time, token_type). Set FANTASY_DEMO=1 to run on demo data."
    )


def authenticate():
    """Return an authenticated ``yahoo_oauth.OAuth2`` session (auto-refreshing)."""
    from yahoo_oauth import OAuth2  # imported lazily so tests/demo need no Yahoo deps

    sc = OAuth2(None, None, from_file=_writable_credentials_path())
    if not sc.token_is_valid():
        sc.refresh_access_token()
    return sc


def _collect(raw: Any, field: str) -> list[str]:
    """Every string value of ``field`` anywhere in a Yahoo JSON document."""
    found: list[str] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k == field and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_collect(v, field))
    elif isinstance(raw, list):
        for v in raw:
            found.extend(_collect(v, field))
    return found


def _team_key_to_league_key(team_key: str) -> str:
    """``'466.l.12345.t.3'`` -> ``'466.l.12345'``."""
    return team_key.split(".t.")[0]


def _my_nba_league_keys(gm) -> list[str]:
    """League keys for every NBA league the logged-in user is in, newest game first.

    Why not ``Game.league_ids()``: yahoo_fantasy_api 2.12 builds that request as
    ``users/games/leagues?use_login=1&...``. Yahoo only understands ``use_login`` as a
    *matrix* parameter (``users;use_login=1``). As a query string it's ignored, the
    request becomes "leagues for all users", and Yahoo answers 401 *"This application
    is not authorized to perform this action"*. That was the opening-night outage.
    """
    keys: list[str] = []
    try:
        keys = _collect(gm.yhandler.get("users;use_login=1/games;game_codes=nba/leagues"), "league_key")
    except Exception as exc:
        log.warning("Yahoo leagues lookup failed (%s); trying the teams endpoint.", exc)
    if not keys:
        # Same information, different route: the user's teams carry their league keys.
        raw = gm.yhandler.get("users;use_login=1/games;game_codes=nba/teams")
        keys = [_team_key_to_league_key(k) for k in _collect(raw, "team_key")]
    return sorted(set(keys), key=lambda k: int(k.split(".")[0]), reverse=True)


def discover_league(sc, league_id: str | None = None, league_key: str | None = None):
    """Find the league to load.

    * ``YAHOO_LEAGUE_KEY`` pins an exact key (``466.l.12345``). No lookup at all.
    * ``YAHOO_LEAGUE_ID`` (the number in your league's URL) is combined with this season's
      NBA game key. Only the public ``game/nba`` endpoint is called, so it keeps working
      even if the user-leagues lookup breaks, and it rolls to the new season on its own
      as long as the league is renewed with the same id.
    * Otherwise: the newest NBA league the logged-in user belongs to.
    """
    import yahoo_fantasy_api as yfa

    league_key = (league_key or os.getenv("YAHOO_LEAGUE_KEY") or "").strip()
    league_id = (league_id or os.getenv("YAHOO_LEAGUE_ID") or "").strip()
    gm = yfa.Game(sc, "nba")
    if league_key:
        return gm.to_league(league_key)
    if league_id:
        game_key = str(gm.game_id())
        if game_key:
            return gm.to_league(f"{game_key}.l.{league_id}")
        log.warning("Couldn't read the current NBA game key; looking the league up instead.")
    keys = _my_nba_league_keys(gm)
    if not keys:
        raise RuntimeError("The authenticated Yahoo user is not in any NBA leagues.")
    if league_id:
        matching = [k for k in keys if k.endswith(f".l.{league_id}")]
        if matching:
            return gm.to_league(matching[0])
    return gm.to_league(keys[0])


# ------------------------------------------------------------------------ parsing
def _to_float(value: Any) -> float:
    try:
        if value in (None, "", "-"):
            return float("nan")
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _split_made_attempted(value: Any) -> tuple[float, float]:
    """``'225/480'`` -> (225.0, 480.0); blanks and ``'-/-'`` -> (0.0, 0.0)."""
    if not isinstance(value, str) or "/" not in value:
        return 0.0, 0.0
    made, att = value.split("/", 1)
    made_f, att_f = _to_float(made), _to_float(att)
    return (0.0 if made_f != made_f else made_f, 0.0 if att_f != att_f else att_f)


def _iter_numbered(obj: dict) -> Iterable[Any]:
    """Yahoo encodes arrays as ``{"0": ..., "1": ..., "count": n}``."""
    for key in sorted((k for k in obj if k.isdigit()), key=int):
        yield obj[key]


def _parse_team_block(team: list) -> dict:
    """Flatten Yahoo's ``team`` list-of-fragments into one record."""
    row: dict[str, Any] = {}
    for fragment in team[0]:
        if isinstance(fragment, dict):
            for key in ("team_key", "team_id", "name"):
                if key in fragment:
                    row[key] = fragment[key]
    for fragment in team[1:]:
        if not isinstance(fragment, dict):
            continue
        if "team_stats" in fragment:
            for stat in fragment["team_stats"].get("stats", []):
                sid = str(stat["stat"]["stat_id"])
                label = YAHOO_STAT_IDS.get(sid)
                value = stat["stat"].get("value")
                if label in SPLIT_STATS:
                    m, a = SPLIT_STATS[label]
                    row[m], row[a] = _split_made_attempted(value)
                elif label in ("FG%", "FT%", "3PT%", "A/T"):
                    row[label] = _to_float(value)
                elif label in STAT_TO_COL:
                    v = _to_float(value)
                    row.setdefault(STAT_TO_COL[label], 0.0 if v != v else v)
        if "team_points" in fragment:
            row["team_points"] = _to_float(fragment["team_points"].get("total"))
        if "team_remaining_games" in fragment:
            total = fragment["team_remaining_games"].get("total", {})
            row["remaining_games"] = _to_float(total.get("remaining_games"))
            row["live_games"] = _to_float(total.get("live_games"))
            row["games"] = _to_float(total.get("completed_games"))
    return row


def parse_scoreboard(raw: dict) -> pd.DataFrame:
    """Raw ``league.matchups(week)`` JSON -> one row per team per week.

    Status is Yahoo's ``preevent`` / ``midevent`` / ``postevent``. Only ``postevent``
    rows are safe to train on; ``midevent`` rows feed the live projection.
    """
    league = raw["fantasy_content"]["league"]
    scoreboard = league[1]["scoreboard"]
    week_default = scoreboard.get("week")
    matchups = scoreboard["0"]["matchups"]
    rows = []
    for idx, wrapper in enumerate(_iter_numbered(matchups)):
        m = wrapper["matchup"]
        teams = [t["team"] for t in _iter_numbered(m["0"]["teams"])]
        parsed = [_parse_team_block(t) for t in teams]
        for i, rec in enumerate(parsed):
            opp = parsed[1 - i] if len(parsed) == 2 else {}
            rec.update(
                week=int(m.get("week", week_default)),
                matchup_id=f"{m.get('week', week_default)}-{idx}",
                opponent_key=opp.get("team_key"),
                status=m.get("status", "postevent"),
                is_playoffs=str(m.get("is_playoffs", "0")) == "1",
                is_consolation=str(m.get("is_consolation", "0")) == "1",
                week_start=m.get("week_start"),
                week_end=m.get("week_end"),
                team_name=rec.pop("name", None),
            )
            rows.append(rec)
    df = pd.DataFrame(rows)
    for col in TEAM_WEEK_COLUMNS:
        if col not in df.columns:
            df[col] = float("nan")
    return df[TEAM_WEEK_COLUMNS]


def parse_transactions(raw: list[dict], tz: str = "America/New_York") -> pd.DataFrame:
    """``league.transactions(...)`` -> one row per player movement.

    A single add/drop transaction touches two players, a trade touches several; each
    becomes its own row with ``action`` in {add, drop, trade} and the team that gained
    (add/trade) or lost (drop) the player.
    """
    rows = []
    for tx in raw:
        players = tx.get("players", {})
        if not isinstance(players, dict):
            continue
        ts = datetime.fromtimestamp(int(tx.get("timestamp", 0)), tz=timezone.utc)
        for entry in _iter_numbered(players):
            player = entry.get("player", [])
            if len(player) < 2:
                continue
            info = {}
            for frag in player[0]:
                if isinstance(frag, dict):
                    if "player_id" in frag:
                        info["player_id"] = int(frag["player_id"])
                    if "name" in frag:
                        info["player_name"] = frag["name"].get("full")
                    if "editorial_team_abbr" in frag:
                        info["nba_team"] = frag["editorial_team_abbr"]
            tdata = player[1].get("transaction_data", {})
            tdata = tdata[0] if isinstance(tdata, list) else tdata
            action = tdata.get("type", tx.get("type"))
            if action == "drop":
                team_key, team_name = tdata.get("source_team_key"), tdata.get("source_team_name")
            else:
                team_key = tdata.get("destination_team_key")
                team_name = tdata.get("destination_team_name")
            rows.append({
                "transaction_id": tx.get("transaction_id"),
                "transaction_type": tx.get("type"),
                "status": tx.get("status"),
                "timestamp": ts,
                "action": action,
                "source_type": tdata.get("source_type"),
                "team_key": team_key,
                "team_name": team_name,
                **info,
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["transaction_id", "transaction_type", "status", "timestamp",
                                     "action", "source_type", "team_key", "team_name",
                                     "player_id", "player_name", "nba_team"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(tz)
    return df.sort_values("timestamp").reset_index(drop=True)


def scoring_from_raw_settings(raw: dict, scoring_type: str | None = None) -> Scoring:
    """``league/{key}/settings`` JSON -> Scoring."""
    league = raw["fantasy_content"]["league"]
    settings = dict(league[1]["settings"][0])
    fields = league[0] if isinstance(league[0], dict) else {}
    settings.setdefault("scoring_type", scoring_type or fields.get("scoring_type", "head"))
    return scoring_from_settings(settings)


def parse_user_leagues(raw: dict) -> list[dict]:
    """``users;use_login=1/games;game_codes=nba/leagues`` -> the user's NBA leagues.

    Yahoo nests leagues as ``{"league": [{...fields...}]}`` inside numbered dicts; we
    walk the document instead of hard-coding the path, which Yahoo has changed before.
    """
    found: dict[str, dict] = {}

    def walk(node):
        if isinstance(node, dict):
            if "league_key" in node and "name" in node:
                key = str(node["league_key"])
                found.setdefault(key, {
                    "league_key": key, "name": node.get("name"), "season": str(node.get("season", "")),
                    "num_teams": int(node.get("num_teams") or 0), "scoring_type": node.get("scoring_type"),
                    "logo_url": node.get("logo_url") or None, "url": node.get("url"),
                    "is_finished": str(node.get("is_finished", "0")) == "1",
                })
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(raw)
    return sorted(found.values(), key=lambda d: (d["is_finished"], -int(d["league_key"].split(".")[0]),
                                                 d["name"] or ""))


def user_leagues(sc) -> list[dict]:
    """Every NBA league the signed-in user is in (current season first)."""
    import yahoo_fantasy_api as yfa
    gm = yfa.Game(sc, "nba")
    return parse_user_leagues(gm.yhandler.get("users;use_login=1/games;game_codes=nba/leagues"))


def open_league(sc, league_key: str):
    import yahoo_fantasy_api as yfa
    return yfa.Game(sc, "nba").to_league(league_key)


# ------------------------------------------------------------------ league facade
@dataclass
class LeagueMeta:
    league_key: str
    name: str
    season: str
    scoring_type: str          # "head" (every category counts) | "headone" (one win per matchup)
    num_teams: int
    current_week: int
    start_week: int
    end_week: int
    playoff_start_week: int
    num_playoff_teams: int
    roster_size: int
    scoring: Scoring = DEFAULT
    logo_url: str | None = None


class YahooLeague:
    """Thin, cache-friendly facade over ``yahoo_fantasy_api.League``."""

    def __init__(self, lg):
        self.lg = lg

    def meta(self) -> LeagueMeta:
        s = self.lg.settings()
        try:
            positions = self.lg.positions()
            # Injured-list slots don't hold productive players, so they don't set replacement level.
            roster_size = sum(int(v.get("count", 0)) for k, v in positions.items()
                              if k not in ("IL", "IL+", "IR"))
        except Exception:  # positions endpoint is occasionally flaky; 13 is Yahoo's default
            roster_size = 13
        end_week = int(s.get("end_week", self.lg.end_week()))
        scoring = self.scoring(s.get("scoring_type", "head"))
        return LeagueMeta(
            league_key=s.get("league_key", self.lg.league_id),
            name=s.get("name", "Fantasy League"),
            season=str(s.get("season", "")),
            scoring_type=s.get("scoring_type", "head"),
            num_teams=int(s.get("num_teams", 10)),
            current_week=int(self.lg.current_week()),
            start_week=int(s.get("start_week", 1)),
            end_week=end_week,
            playoff_start_week=int(s.get("playoff_start_week", end_week + 1) or end_week + 1),
            num_playoff_teams=int(s.get("num_playoff_teams", 6) or 6),
            roster_size=roster_size or 13,
            scoring=scoring,
            logo_url=s.get("logo_url") or None,
        )

    def scoring(self, scoring_type: str = "head") -> Scoring:
        """The league's categories (or points modifiers), from the raw settings document."""
        try:
            raw = self.lg.yhandler.get_settings_raw(self.lg.league_id)
            return scoring_from_raw_settings(raw, scoring_type)
        except Exception as exc:  # never take the page down over settings; flag it instead
            log.warning("Couldn't read league scoring settings (%s); assuming 9-cat.", exc)
            return Scoring(DEFAULT.categories, scoring_type, ("settings unavailable",))

    def teams(self) -> pd.DataFrame:
        rows = []
        for key, t in self.lg.teams().items():
            logos = t.get("team_logos") or [{}]
            logo = logos[0].get("team_logo", {}).get("url") if isinstance(logos[0], dict) else None
            managers = t.get("managers") or [{}]
            mgr = managers[0].get("manager", {}).get("nickname") if isinstance(managers[0], dict) else None
            rows.append({"team_key": key, "team_name": t.get("name"), "logo_url": logo,
                         "manager": mgr, "moves": t.get("number_of_moves"),
                         "trades": t.get("number_of_trades")})
        return pd.DataFrame(rows)

    def standings(self) -> pd.DataFrame:
        rows = []
        for t in self.lg.standings():
            ot = t.get("outcome_totals", {})
            rows.append({"team_key": t.get("team_key"), "team_name": t.get("name"),
                         "rank": int(t.get("rank") or 0),
                         "wins": int(ot.get("wins", 0)), "losses": int(ot.get("losses", 0)),
                         "ties": int(ot.get("ties", 0)),
                         "points": _to_float(t.get("points_for"))})
        return pd.DataFrame(rows)

    def scoreboard(self, week: int) -> pd.DataFrame:
        return parse_scoreboard(self.lg.matchups(week=week))

    def transactions(self) -> pd.DataFrame:
        return parse_transactions(self.lg.transactions("add,drop,trade", ""))

    def rosters(self, team_keys: Iterable[str]) -> pd.DataFrame:
        """One Yahoo call per team, made in parallel: a 12-team league's rosters take one
        round trip instead of twelve."""
        from concurrent.futures import ThreadPoolExecutor
        keys = list(team_keys)

        def one(key):
            roster = pd.DataFrame(self.lg.to_team(key).roster())
            if not roster.empty:
                roster["team_key"] = key
            return roster

        with ThreadPoolExecutor(max_workers=max(1, min(6, len(keys)))) as pool:
            frames = [f for f in pool.map(one, keys) if not f.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def free_agents(self, positions: tuple[str, ...] = ("G", "F", "C")) -> pd.DataFrame:
        from concurrent.futures import ThreadPoolExecutor
        seen: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=len(positions)) as pool:
            batches = list(pool.map(self.lg.free_agents, positions))
        for batch in batches:
            for p in batch:
                seen.setdefault(p["player_id"], p)
        df = pd.DataFrame(list(seen.values()))
        if not df.empty:
            df["eligible_positions"] = df["eligible_positions"].apply(
                lambda xs: ",".join(x for x in xs if x not in ("Util", "IL", "IL+")))
        return df
