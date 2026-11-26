import pandas as pd

from fantasy import demo, leaders


def _tw():
    base = dict(FGM=40, FGA=100, FTM=20, FTA=25, FG3M=10, FG3A=30, PTS=110, REB=50, AST=25, STL=8, BLK=5, TOV=15)
    rows = []
    for wk in (1, 2):
        rows.append(dict(week=wk, team_key="a", status="postevent", **base))
        rows.append(dict(week=wk, team_key="b", status="postevent",
                         **{**base, "PTS": 90, "TOV": 10, "FGM": 2, "FGA": 3}))
    return pd.DataFrame(rows)


def test_ranks_turnovers_lowest_first_and_points_highest_first():
    rk = leaders.ranks(leaders.week_table(_tw(), 1))
    assert rk.at["a", "PTS"] == 1 and rk.at["b", "PTS"] == 2
    assert rk.at["b", "TO"] == 1 and rk.at["a", "TO"] == 2


def test_season_percentages_use_makes_over_attempts():
    s = leaders.season_table(_tw())
    assert abs(s.at["b", "FG%"] - 4 / 6) < 1e-9          # summed makes / attempts
    assert s.at["a", "PTS"] == 110                       # per-week average


def test_trend_excludes_live_week_and_demo_runs():
    tw = demo.make_league(seed=7)["team_weeks"]
    tr = leaders.trend(tw, "REB")
    assert set(tr["week"]) == set(tw.loc[tw["status"] == "postevent", "week"])
    assert leaders.bar_share(pd.Series({"a": 10.0, "b": 5.0}), "TO")["b"] == 1.0
