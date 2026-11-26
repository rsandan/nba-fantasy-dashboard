"""Snapshot layer: CDN parsing, aggregation, and never-hang / never-crash reads."""
from datetime import date

import pandas as pd
import pytest

from fantasy import nba_data, snapshot
from fantasy.valuation import value_players

SCHEDULE = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [
    {"gameDate": "10/02/2026 00:00:00", "games": [
        {"gameId": "0012600001", "gameStatus": 3, "gameDateEst": "2026-10-02T00:00:00Z",
         "homeTeam": {"teamTricode": "LAL"}, "awayTeam": {"teamTricode": "GSW"}}]},
    {"gameDate": "10/20/2026 00:00:00", "games": [
        {"gameId": "0022600001", "gameStatus": 3, "gameDateEst": "2026-10-20T00:00:00Z",
         "homeTeam": {"teamTricode": "OKC"}, "awayTeam": {"teamTricode": "HOU"}},
        {"gameId": "0022600002", "gameStatus": 1, "gameDateEst": "2026-10-20T00:00:00Z",
         "homeTeam": {"teamTricode": "LAL"}, "awayTeam": {"teamTricode": "MIN"}}]},
]}}


def _player(pid, name, minutes, played="1", **stats):
    base = dict(fieldGoalsMade=8, fieldGoalsAttempted=16, freeThrowsMade=4, freeThrowsAttempted=5,
                threePointersMade=2, points=22, reboundsTotal=6, assists=5, steals=1, blocks=1,
                turnovers=3, minutes=minutes)
    base.update(stats)
    return {"personId": pid, "name": name, "played": played, "statistics": base}


def _box(gid, when, home, away, home_players, away_players):
    return {"game": {"gameId": gid, "gameEt": when,
                     "homeTeam": {"teamTricode": home, "players": home_players},
                     "awayTeam": {"teamTricode": away, "players": away_players}}}


def test_parse_minutes_formats():
    assert snapshot.parse_minutes("PT34M12.00S") == pytest.approx(34.2)
    assert snapshot.parse_minutes("PT00M00.00S") == 0
    assert snapshot.parse_minutes("31:30") == pytest.approx(31.5)
    assert snapshot.parse_minutes(None) == 0


def test_schedule_keeps_regular_season_only():
    df = snapshot.parse_cdn_schedule(SCHEDULE)
    assert list(df["game_id"]) == ["0022600001", "0022600002"]
    assert df.loc[0, "game_date"] == date(2026, 10, 20)
    assert list(df["status"]) == [3, 1]


def test_boxscore_skips_dnps_and_aggregates_per_game():
    g1 = snapshot.parse_cdn_boxscore(_box(
        "0022600001", "2026-10-20T19:30:00Z", "OKC", "HOU",
        [_player(1, "Shai Gilgeous-Alexander", "PT36M00.00S", points=30),
         _player(2, "Bench Guy", "PT00M00.00S", played="0")],
        [_player(3, "Alperen Sengun", "PT32M00.00S", points=20)]))
    g2 = snapshot.parse_cdn_boxscore(_box(
        "0022600010", "2026-10-22T19:30:00Z", "BKN", "OKC",
        [], [_player(1, "Shai Gilgeous-Alexander", "PT34M00.00S", points=40)]))
    assert set(g1["PLAYER_ID"]) == {1, 3}
    agg = snapshot.aggregate_per_game(pd.concat([g1, g2]))
    sga = agg.set_index("PLAYER_ID").loc[1]
    assert sga["GP"] == 2 and sga["PTS"] == 35 and sga["MIN"] == 35
    last1 = snapshot.aggregate_per_game(pd.concat([g1, g2]), last_n=1).set_index("PLAYER_ID")
    assert last1.loc[1, "PTS"] == 40 and last1.loc[1, "GP"] == 1


def test_aggregate_output_feeds_valuation():
    rows = []
    for pid in range(1, 200):
        for g in range(5):
            rows.append(dict(game_id=f"g{g}", game_date="2026-10-2%d" % g, PLAYER_ID=pid,
                             PLAYER_NAME=f"P{pid}", TEAM="OKC", MIN=20 + pid % 15,
                             FGM=3 + pid % 5, FGA=8 + pid % 7, FTM=2, FTA=3, FG3M=pid % 3,
                             PTS=10 + pid % 20, REB=pid % 9, AST=pid % 7, STL=1, BLK=pid % 2, TOV=2))
    per_game = nba_data.standardize_player_frame(snapshot.aggregate_per_game(pd.DataFrame(rows)))
    res = value_players(per_game, n_teams=8, roster_size=13)
    assert not res.table.empty


def test_blend_with_no_data_anywhere_is_empty_not_a_crash():
    out = nba_data.blend_seasons(pd.DataFrame(), pd.DataFrame())
    assert out.empty and "GP_TOT" in out.columns


def test_read_falls_back_to_local_then_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "RAW_BASE", "")
    monkeypatch.setattr(snapshot, "LOCAL_DIR", tmp_path)
    assert snapshot.load_players("2026-27").empty
    pd.DataFrame([dict(PLAYER_ID=1, PLAYER_NAME="A B", TEAM="OKC", GP=3, MIN=30, FGM=5, FGA=10,
                       FTM=2, FTA=2, FG3M=1, PTS=13, REB=4, AST=3, STL=1, BLK=0, TOV=2)]
                 ).to_csv(tmp_path / snapshot.players_file("2026-27"), index=False)
    df = snapshot.load_players("2026-27")
    assert len(df) == 1 and df.loc[0, "name_key"] == "a b"


def test_game_ids():
    assert snapshot.regular_season_game_id("2025-26", 7) == "0022500007"
