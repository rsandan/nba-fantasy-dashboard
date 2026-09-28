"""Uniform data access for the app: live Yahoo + NBA, or the synthetic demo league.

The Streamlit layer wraps these functions in ``st.cache_data`` with TTLs chosen by how
fast each source changes. Completed weeks never change, so they're cached for the
life of the process; the live week refreshes every few minutes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from . import demo, nba_data
from .yahoo_client import LeagueMeta, YahooLeague, authenticate, discover_league


def demo_requested() -> bool:
    return os.getenv("FANTASY_DEMO", "").strip().lower() in ("1", "true", "yes")


@dataclass
class Source:
    """Either a live YahooLeague or a pre-generated demo bundle."""
    live: YahooLeague | None
    demo_bundle: dict | None

    @property
    def is_demo(self) -> bool:
        return self.live is None

    # ---- league
    def meta(self) -> LeagueMeta:
        return self.demo_bundle["meta"] if self.is_demo else self.live.meta()

    def teams(self) -> pd.DataFrame:
        return self.demo_bundle["teams"] if self.is_demo else self.live.teams()

    def week(self, week: int) -> pd.DataFrame:
        if self.is_demo:
            tw = self.demo_bundle["team_weeks"]
            return tw[tw["week"] == week].reset_index(drop=True)
        return self.live.scoreboard(week)

    def transactions(self) -> pd.DataFrame:
        return self.demo_bundle["transactions"] if self.is_demo else self.live.transactions()

    def rosters(self, team_keys) -> pd.DataFrame:
        return self.demo_bundle["rosters"] if self.is_demo else self.live.rosters(team_keys)

    def free_agents(self) -> pd.DataFrame:
        return self.demo_bundle["free_agents"] if self.is_demo else self.live.free_agents()

    # ---- NBA
    def players(self, season: str, last_n_games: int = 0) -> pd.DataFrame:
        if self.is_demo:
            return self.demo_bundle["players"] if season == nba_data.season_for(self.today()) \
                else self.demo_bundle["players_prior"]
        return nba_data.fetch_player_per_game(season, last_n_games)

    def schedule(self, season: str) -> pd.DataFrame:
        return self.demo_bundle["schedule"] if self.is_demo else nba_data.fetch_schedule(season)

    def today(self) -> date:
        """League-local 'today'. The demo league is frozen mid-week so every page has data."""
        if self.is_demo:
            tw = self.demo_bundle["team_weeks"]
            live = tw[tw["status"] == "midevent"].iloc[0]
            return (pd.Timestamp(live["week_start"]) + pd.Timedelta(days=3)).date()
        return datetime.now(pd.Timestamp.now(tz="America/New_York").tz).date()


def connect() -> tuple[Source, str | None]:
    """Live source if credentials work, otherwise the demo league plus a reason string."""
    if demo_requested():
        return Source(None, demo.make_league()), "FANTASY_DEMO is set"
    try:
        lg = discover_league(authenticate())
        return Source(YahooLeague(lg), None), None
    except Exception as exc:  # missing/expired credentials shouldn't take the site down
        return Source(None, demo.make_league()), f"{type(exc).__name__}: {exc}"
