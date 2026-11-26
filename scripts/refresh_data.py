"""Build the NBA data snapshots the dashboard reads (see fantasy/snapshot.py).

    python scripts/refresh_data.py --out <dir>

Writes into <dir>:
  players_<season>_last0.csv     season per-game lines
  players_<season>_last14.csv    last ~2 weeks, from the daily history below
  players_<season>_last30.csv    last ~30 days
  players_<prior>_last0.csv      last season (written once; it never changes)
  history_<season>.csv.gz        season-to-date totals, one snapshot per day
  schedule_<season>.csv          games from yesterday through the next 3 weeks (merged)

NBA blocks cloud runners on both stats.nba.com (timeouts) and cdn.nba.com (403), so
each table tries a quick stats.nba.com probe, then Basketball-Reference (per-game
stats) or ESPN (schedule). Existing files are only replaced by non-empty results, so
a bad run never wipes good data.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy import nba_data, snapshot  # noqa: E402

log = logging.getLogger("refresh_data")
WINDOW_DAYS = (14, 30)
SCHEDULE_DAYS_AHEAD = 21
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get(url: str, timeout: float = 30, as_json: bool = False):
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        return json.loads(body) if as_json else body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        log.warning("%s -> HTTP %s", url, exc.code)
    except Exception as exc:
        log.warning("%s -> %s", url, exc)
    return None


def write_csv(df: pd.DataFrame, path: Path, **kw) -> bool:
    if df is None or df.empty:
        log.warning("skip %s: no rows", path.name)
        return False
    df.to_csv(path, index=False, **kw)
    log.info("wrote %s (%d rows)", path.name, len(df))
    return True


# ----------------------------------------------------------------- player stats
def stats_nba_reachable() -> bool:
    """One short probe so a blocked runner costs ~15 s, not minutes of retries."""
    df = nba_data.fetch_player_per_game(nba_data.season_for(), 0, timeout=15, attempts=1)
    return not df.empty


def per_game_from_bbref(season: str) -> pd.DataFrame:
    url = snapshot.BBREF_PER_GAME.format(end_year=snapshot.bbref_end_year(season))
    html = get(url)
    if not html:
        return pd.DataFrame()
    try:
        df = snapshot.parse_bbref_per_game(html)
    except Exception as exc:
        log.warning("couldn't parse %s: %s", url, exc)
        return pd.DataFrame()
    return snapshot.attach_nba_ids(df)


def season_per_game(season: str, use_stats: bool) -> tuple[pd.DataFrame, str]:
    if use_stats:
        df = nba_data.fetch_player_per_game(season, 0, timeout=20, attempts=2)
        if not df.empty:
            return df, "stats.nba.com"
    return per_game_from_bbref(season), "basketball-reference"


def update_history(season: str, per_game: pd.DataFrame, out: Path, today: date) -> pd.DataFrame:
    path = out / f"history_{season}.csv.gz"
    hist = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if not hist.empty and not set(snapshot.TOTAL_COLS) <= set(hist.columns):
        log.info("history_%s predates a stat column; starting it over", season)
        hist = pd.DataFrame()
    snap = snapshot.to_totals(per_game, today)
    if not hist.empty:
        hist = hist[hist["as_of"] != snap["as_of"].iloc[0]]     # one snapshot per day
    hist = pd.concat([hist, snap], ignore_index=True)
    hist.to_csv(path, index=False, compression="gzip")
    log.info("history_%s: %d days", season, hist["as_of"].nunique())
    return hist


# --------------------------------------------------------------------- schedule
def schedule_window(season: str, out: Path, today: date) -> None:
    """Merge yesterday..+3 weeks into the stored schedule. CDN, then ESPN, then stats."""
    path = out / snapshot.schedule_file(season)
    raw = get(snapshot.CDN_SCHEDULE, as_json=True)
    full = snapshot.parse_cdn_schedule(raw) if raw else pd.DataFrame()
    if not full.empty:
        write_csv(full[["game_date", "home", "away"]], path)
        return
    days = [today + timedelta(days=i) for i in range(-1, SCHEDULE_DAYS_AHEAD + 1)]
    frames, fetched = [], set()
    for d in days:
        raw_day = get(snapshot.ESPN_SCOREBOARD.format(yyyymmdd=d.strftime("%Y%m%d")), as_json=True)
        if raw_day is not None:
            frames.append(snapshot.parse_espn_scoreboard(raw_day, d))
            fetched.add(d)
        time.sleep(0.2)
    fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not fetched:
        fresh = nba_data.fetch_schedule(season, timeout=15, attempts=1)
        write_csv(fresh, path)
        return
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["game_date", "home", "away"])
    if not old.empty:
        old["game_date"] = pd.to_datetime(old["game_date"]).dt.date
        old = old[~old["game_date"].isin(fetched)]   # replace days we just re-read
    merged = pd.concat([old, fresh], ignore_index=True).sort_values("game_date")
    write_csv(merged, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--season", default=None, help="e.g. 2026-27 (default: from today's date)")
    ap.add_argument("--skip-stats-nba", action="store_true", help="don't even probe stats.nba.com")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    today = date.today()
    season = args.season or nba_data.season_for(today)
    prior = nba_data.previous_season(season)
    use_stats = not args.skip_stats_nba and stats_nba_reachable()
    log.info("stats.nba.com %s", "reachable" if use_stats else "blocked; using fallbacks")

    schedule_window(season, out, today)

    cur, source = season_per_game(season, use_stats)
    ok = write_csv(cur, out / snapshot.players_file(season, 0))
    log.info("%s per-game from %s", season, source)
    if ok:
        hist = update_history(season, cur, out, today)
        for days in WINDOW_DAYS:
            if use_stats:
                df = nba_data.fetch_player_per_game(season, days, timeout=20, attempts=2)
            else:
                df = snapshot.window_from_history(hist, today, days)
            write_csv(df, out / snapshot.players_file(season, days))

    prior_path = out / snapshot.players_file(prior, 0)
    if prior_path.exists() and set(nba_data.STAT_COLS) <= set(pd.read_csv(prior_path, nrows=1).columns):
        log.info("%s already present; last season doesn't change", prior_path.name)
    else:
        time.sleep(4)   # be polite to Basketball-Reference between page loads
        write_csv(season_per_game(prior, use_stats)[0], prior_path)

    (out / "README.md").write_text(
        "Auto-generated NBA snapshots for the Love Island dashboard. "
        "Built by scripts/refresh_data.py on main; do not edit by hand.\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
