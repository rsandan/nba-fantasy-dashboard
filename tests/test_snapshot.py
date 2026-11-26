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


BBREF_HTML = """<html><body><table id="per_game_stats"><thead><tr>
<th>Rk</th><th>Player</th><th>Age</th><th>Team</th><th>Pos</th><th>G</th><th>GS</th><th>MP</th>
<th>FG</th><th>FGA</th><th>FG%</th><th>3P</th><th>3PA</th><th>FT</th><th>FTA</th><th>TRB</th>
<th>AST</th><th>STL</th><th>BLK</th><th>TOV</th><th>PTS</th></tr></thead><tbody>
<tr><td>1</td><td>Shai Gilgeous-Alexander</td><td>28</td><td>OKC</td><td>PG</td><td>18</td><td>18</td>
<td>34.1</td><td>11.2</td><td>21.0</td><td>.533</td><td>2.1</td><td>5.5</td><td>8.0</td><td>8.9</td>
<td>5.1</td><td>6.3</td><td>1.8</td><td>0.9</td><td>2.4</td><td>32.5</td></tr>
<tr><td>2</td><td>Traded Guy*</td><td>27</td><td>2TM</td><td>SF</td><td>10</td><td>4</td><td>25.0</td>
<td>4.0</td><td>9.0</td><td>.444</td><td>1.0</td><td>3.0</td><td>1.0</td><td>1.5</td><td>4.0</td>
<td>2.0</td><td>1.0</td><td>0.5</td><td>1.0</td><td>10.0</td></tr>
<tr><td>2</td><td>Traded Guy*</td><td>27</td><td>BRK</td><td>SF</td><td>6</td><td>4</td><td>26.0</td>
<td>4.0</td><td>9.0</td><td>.444</td><td>1.0</td><td>3.0</td><td>1.0</td><td>1.5</td><td>4.0</td>
<td>2.0</td><td>1.0</td><td>0.5</td><td>1.0</td><td>10.0</td></tr>
<tr><td>2</td><td>Traded Guy*</td><td>27</td><td>PHO</td><td>SF</td><td>4</td><td>0</td><td>24.0</td>
<td>4.0</td><td>9.0</td><td>.444</td><td>1.0</td><td>3.0</td><td>1.0</td><td>1.5</td><td>4.0</td>
<td>2.0</td><td>1.0</td><td>0.5</td><td>1.0</td><td>10.0</td></tr>
<tr><td>Rk</td><td>Player</td><td>Age</td><td>Team</td><td>Pos</td><td>G</td><td>GS</td><td>MP</td>
<td>FG</td><td>FGA</td><td>FG%</td><td>3P</td><td>3PA</td><td>FT</td><td>FTA</td><td>TRB</td>
<td>AST</td><td>STL</td><td>BLK</td><td>TOV</td><td>PTS</td></tr>
<tr><td></td><td>League Average</td><td></td><td></td><td></td><td>10</td><td></td><td>20</td>
<td>3</td><td>7</td><td>.45</td><td>1</td><td>3</td><td>1</td><td>2</td><td>3</td><td>2</td><td>1</td>
<td>0.5</td><td>1</td><td>9</td></tr>
</tbody></table></body></html>"""


def test_bbref_keeps_combined_line_with_latest_team():
    df = snapshot.parse_bbref_per_game(BBREF_HTML).set_index("PLAYER_NAME")
    assert list(df.index) == ["Shai Gilgeous-Alexander", "Traded Guy"]
    assert df.loc["Traded Guy", "GP"] == 10 and df.loc["Traded Guy", "TEAM"] == "PHX"
    assert df.loc["Shai Gilgeous-Alexander", "FG3M"] == 2.1 and df.loc["Shai Gilgeous-Alexander", "REB"] == 5.1


def test_attach_nba_ids_matches_real_players_and_flags_unknown():
    df = snapshot.attach_nba_ids(snapshot.parse_bbref_per_game(BBREF_HTML)).set_index("PLAYER_NAME")
    assert df.loc["Shai Gilgeous-Alexander", "PLAYER_ID"] == 1628983
    assert df.loc["Traded Guy", "PLAYER_ID"] < 0
    ready = nba_data.standardize_player_frame(df.reset_index())
    assert {"PLAYER_ID", "name_key", "GP"} <= set(ready.columns)


def test_espn_scoreboard_maps_team_codes():
    raw = {"events": [{"season": {"type": 2}, "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"abbreviation": "GS"}},
        {"homeAway": "away", "team": {"abbreviation": "UTAH"}}]}]},
        {"season": {"type": 1}, "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"abbreviation": "LAL"}},
            {"homeAway": "away", "team": {"abbreviation": "BOS"}}]}]}]}
    df = snapshot.parse_espn_scoreboard(raw, date(2026, 11, 26))
    assert df.to_dict("records") == [{"game_date": date(2026, 11, 26), "home": "GSW", "away": "UTA"}]


def test_window_from_history_diffs_cumulative_totals():
    day1 = pd.DataFrame([dict(PLAYER_ID=1, PLAYER_NAME="A", TEAM="OKC", GP=10, MIN=30, FGM=5, FGA=10,
                              FTM=2, FTA=2, FG3M=1, PTS=20, REB=5, AST=5, STL=1, BLK=1, TOV=2)])
    day15 = day1.assign(GP=14, PTS=(20 * 10 + 40 * 4) / 14)       # 4 new games at 40 ppg
    hist = pd.concat([snapshot.to_totals(day1, "2026-11-10"), snapshot.to_totals(day15, "2026-11-25")])
    w = snapshot.window_from_history(hist, "2026-11-25", 14)
    assert w.loc[0, "GP"] == 4 and w.loc[0, "PTS"] == pytest.approx(40, abs=0.1)
    assert snapshot.window_from_history(hist, "2026-11-25", 30).empty   # not enough history yet
