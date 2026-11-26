"""Build the NBA data snapshots the dashboard reads (see fantasy/snapshot.py).

    python scripts/refresh_data.py --out <dir>

Writes into <dir>:
  schedule_<season>.csv                   current season, every regular-season game
  players_<season>_last{0,15,30}.csv      per-game lines (season, last 15, last 30)
  players_<prior>_last0.csv               last season (written once, it never changes)
  games_<season>.csv.gz                   player-game rows, only when the box-score
                                          fallback is used (lets later runs go incremental)

Source order for each table: stats.nba.com (one league-wide call), then cdn.nba.com
box scores. The CDN is static JSON and isn't rate limited, so the fallback works from
GitHub Actions even when stats.nba.com blocks the runner. Existing files are only
replaced by non-empty results, so a bad run never wipes good data.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy import nba_data, snapshot  # noqa: E402

log = logging.getLogger("refresh_data")
WINDOWS = (0, 15, 30)
MAX_PRIOR_GAMES = 1230   # 30 teams x 82 / 2


def get_json(url: str, timeout: float = 20) -> dict | None:
    req = urllib.request.Request(url, headers=snapshot.CDN_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 404):
            log.warning("%s -> HTTP %s", url, exc.code)
        return None
    except Exception as exc:
        log.warning("%s -> %s", url, exc)
        return None


def fetch_boxscores(game_ids: list[str], workers: int = 8) -> pd.DataFrame:
    def one(gid):
        raw = get_json(snapshot.CDN_BOXSCORE.format(game_id=gid))
        return snapshot.parse_cdn_boxscore(raw) if raw else None

    frames = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for df in pool.map(one, game_ids):
            if df is not None and not df.empty:
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=snapshot.GAME_COLUMNS)


def write_csv(df: pd.DataFrame, path: Path) -> bool:
    if df is None or df.empty:
        log.warning("skip %s: no rows", path.name)
        return False
    df.to_csv(path, index=False)
    log.info("wrote %s (%d rows)", path.name, len(df))
    return True


def refresh_schedule(season: str, out: Path) -> pd.DataFrame:
    """Schedule with game ids and status. CDN first: it's fast and never blocked."""
    raw = get_json(snapshot.CDN_SCHEDULE)
    sched = snapshot.parse_cdn_schedule(raw) if raw else pd.DataFrame()
    cdn_season = (raw or {}).get("leagueSchedule", {}).get("seasonYear")
    if not sched.empty and cdn_season and str(cdn_season)[:4] != season[:4]:
        log.warning("CDN schedule is for %s, expected %s; ignoring it", cdn_season, season)
        sched = pd.DataFrame()
    if sched.empty:
        sched = nba_data.fetch_schedule(season)
    write_csv(sched, out / snapshot.schedule_file(season))
    return sched


def players_from_stats(season: str) -> dict[int, pd.DataFrame]:
    tables = {}
    for n in WINDOWS:
        df = nba_data.fetch_player_per_game(season, n)
        if df.empty:
            return {}            # blocked: don't mix sources within one run
        tables[n] = df
    return tables


def players_from_boxscores(season: str, sched: pd.DataFrame, out: Path) -> dict[int, pd.DataFrame]:
    """Incremental: keep every player-game row, fetch only finished games we don't have."""
    store_path = out / snapshot.games_file(season)
    store = pd.read_csv(store_path) if store_path.exists() else pd.DataFrame(columns=snapshot.GAME_COLUMNS)
    have = set(store["game_id"].astype(str).str.zfill(10)) if not store.empty else set()
    if sched is not None and not sched.empty and "status" in sched:
        finished = sched[(sched["status"] == 3) & (pd.to_datetime(sched["game_date"]) <= pd.Timestamp(date.today()))]
        wanted = [g for g in finished["game_id"].astype(str) if g not in have]
    else:
        wanted = [snapshot.regular_season_game_id(season, i) for i in range(1, MAX_PRIOR_GAMES + 1)
                  if snapshot.regular_season_game_id(season, i) not in have]
    log.info("box-score fallback for %s: %d games stored, %d to fetch", season, len(have), len(wanted))
    new = fetch_boxscores(wanted)
    if not new.empty:
        store = pd.concat([store, new], ignore_index=True)
        store["game_id"] = store["game_id"].astype(str).str.zfill(10)
        store = store.drop_duplicates(["game_id", "PLAYER_ID"])
        store.to_csv(store_path, index=False, compression="gzip")
    return {n: snapshot.aggregate_per_game(store, n) for n in WINDOWS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--season", default=None, help="e.g. 2026-27 (default: from today's date)")
    ap.add_argument("--force-boxscores", action="store_true", help="skip stats.nba.com")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    season = args.season or nba_data.season_for()
    prior = nba_data.previous_season(season)

    sched = refresh_schedule(season, out)

    tables = {} if args.force_boxscores else players_from_stats(season)
    source = "stats.nba.com"
    if not tables:
        source = "cdn box scores"
        tables = players_from_boxscores(season, sched, out)
    wrote = [write_csv(df, out / snapshot.players_file(season, n)) for n, df in tables.items()]
    log.info("current season %s from %s", season, source)

    prior_path = out / snapshot.players_file(prior, 0)
    if prior_path.exists():
        log.info("%s already present; last season doesn't change", prior_path.name)
    else:
        df = pd.DataFrame() if args.force_boxscores else nba_data.fetch_player_per_game(prior)
        if df.empty:
            df = players_from_boxscores(prior, None, out)[0]
        write_csv(df, prior_path)

    (out / "README.md").write_text(
        "Auto-generated NBA snapshots for the Love Island dashboard. "
        "Built by scripts/refresh_data.py on main; do not edit by hand.\n")
    return 0 if any(wrote) else 1


if __name__ == "__main__":
    sys.exit(main())
