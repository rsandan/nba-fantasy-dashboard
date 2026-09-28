"""Unit tests for parsing, valuation, simulation and league analytics (no network)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from fantasy import demo, nba_data, power, trade, transactions
from fantasy import matchup_model as mm
from fantasy.categories import BY_NAME, CATEGORY_NAMES, compare
from fantasy.valuation import value_players
from fantasy.yahoo_client import parse_scoreboard, parse_transactions


@pytest.fixture(scope="module")
def league():
    return demo.make_league(seed=7)


# ------------------------------------------------------------------ categories
def test_compare_turnovers_lower_wins():
    assert compare(20, 25, BY_NAME["TO"]) == 1.0
    assert compare(25, 20, BY_NAME["TO"]) == 0.0


def test_compare_percentage_ties_at_display_precision():
    assert compare(0.47249, 0.47201, BY_NAME["FG%"]) == 0.5   # both display .472
    assert compare(0.4730, 0.4720, BY_NAME["FG%"]) == 1.0


def test_compare_undefined_percentage_loses():
    assert compare(float("nan"), 0.45, BY_NAME["FT%"]) == 0.0
    assert compare(float("nan"), float("nan"), BY_NAME["FT%"]) == 0.5


# --------------------------------------------------------------------- parsing
def test_scoreboard_round_trip_keeps_makes_and_attempts(league):
    wk = league["team_weeks"]
    week = wk[wk["week"] == 3]
    parsed = parse_scoreboard(demo.to_yahoo_scoreboard(week))
    assert len(parsed) == len(week)
    merged = parsed.merge(week, on="team_key", suffixes=("", "_true"))
    for col in ("FGM", "FGA", "FTM", "FTA", "PTS", "TOV", "games", "remaining_games"):
        np.testing.assert_allclose(merged[col], merged[f"{col}_true"])
    # opponents are symmetric
    pairs = dict(zip(parsed["team_key"], parsed["opponent_key"]))
    assert all(pairs[pairs[k]] == k for k in pairs)
    assert set(parsed["status"]) == {"postevent"}


def test_scoreboard_handles_blank_stats(league):
    wk = league["team_weeks"]
    pre = wk[wk["status"] == "preevent"]
    pre = pre[pre["week"] == pre["week"].min()]
    parsed = parse_scoreboard(demo.to_yahoo_scoreboard(pre))
    assert (parsed["FGA"] == 0).all() and parsed["FG%"].isna().all()


def _tx(tid, ttype, ts, players):
    body = {str(i): {"player": [[{"player_key": f"466.p.{pid}"}, {"player_id": str(pid)},
                                 {"name": {"full": name}}, {"editorial_team_abbr": "GS"}], {"transaction_data": td}]}
            for i, (pid, name, td) in enumerate(players)}
    body["count"] = len(players)
    return {"transaction_key": f"466.l.1.tr.{tid}", "transaction_id": str(tid), "type": ttype,
            "status": "successful", "timestamp": str(ts), "players": body}


def test_parse_transactions_add_drop_and_trade():
    raw = [
        _tx(1, "add/drop", 1_761_000_000, [
            (10, "A Guy", [{"type": "add", "source_type": "freeagents", "destination_type": "team",
                            "destination_team_key": "t.1", "destination_team_name": "One"}]),
            (11, "B Guy", {"type": "drop", "source_type": "team", "source_team_key": "t.1",
                           "source_team_name": "One", "destination_type": "waivers"})]),
        _tx(2, "trade", 1_761_100_000, [
            (12, "C Guy", {"type": "trade", "source_type": "team", "source_team_key": "t.2",
                           "destination_team_key": "t.1", "destination_team_name": "One"})]),
    ]
    df = parse_transactions(raw)
    assert list(df["action"]) == ["add", "drop", "trade"]
    assert list(df["team_key"]) == ["t.1", "t.1", "t.1"]
    assert df["timestamp"].dt.tz is not None


# ------------------------------------------------------------------ NBA helpers
@pytest.mark.parametrize("d,expected", [(date(2026, 9, 27), "2026-27"), (date(2027, 3, 1), "2026-27"),
                                        (date(2027, 7, 1), "2026-27"), (date(2027, 8, 15), "2027-28")])
def test_season_for(d, expected):
    assert nba_data.season_for(d) == expected


def test_normalize_name_handles_accents_and_suffixes():
    assert nba_data.normalize_name("Nikola Jokić") == nba_data.normalize_name("Nikola Jokic")
    assert nba_data.normalize_name("Jaren Jackson Jr.") == "jaren jackson"
    assert nba_data.normalize_name("De'Aaron Fox") == "deaaron fox"


def test_summarize_logs_uses_volume_weighted_pct():
    logs = pd.DataFrame({"FGM": [1, 4], "FGA": [1, 16], "FTM": [0, 0], "FTA": [0, 0], "PTS": [2, 8]})
    out = nba_data.summarize_logs(logs)
    assert out["FG%"] == pytest.approx(5 / 17, abs=1e-3)   # not mean(1.0, 0.25) = 0.625


def test_blend_weights_follow_games_played(league):
    b = nba_data.blend_seasons(league["players"], league["players_prior"], k=15)
    row = b[b["GP_CUR"] > 0].iloc[0]
    assert row["blend_w"] == pytest.approx(row["GP_CUR"] / (row["GP_CUR"] + 15))
    no_cur = b[b["GP_CUR"] == 0]
    assert (no_cur["blend_w"] == 0).all()


# ------------------------------------------------------------------- valuation
def _toy_players():
    base = dict(GP=60, TEAM="BOS", MIN=30, FTM=3, FTA=4, FG3M=2, PTS=20, REB=6, AST=4, STL=1, BLK=1, TOV=2)
    rows = [dict(PLAYER_ID=i, PLAYER_NAME=f"P{i}", FGM=5, FGA=10, **base) for i in range(40)]
    rows.append(dict(PLAYER_ID=100, PLAYER_NAME="Low volume 60%", FGM=1.8, FGA=3, **base))
    rows.append(dict(PLAYER_ID=101, PLAYER_NAME="High volume 55%", FGM=11, FGA=20, **base))
    rows.append(dict(PLAYER_ID=102, PLAYER_NAME="Turnover machine", FGM=5, FGA=10, **{**base, "TOV": 5}))
    return pd.DataFrame(rows)


def test_percentage_value_is_volume_weighted():
    res = value_players(_toy_players(), n_teams=4, roster_size=10).table.set_index("PLAYER_ID")
    assert res.at[101, "z_FG%"] > res.at[100, "z_FG%"] > 0


def test_turnovers_are_negative_value():
    res = value_players(_toy_players(), n_teams=4, roster_size=10).table.set_index("PLAYER_ID")
    assert res.at[102, "z_TO"] < 0


def test_punts_drop_category_from_total(league):
    players = nba_data.blend_seasons(league["players"], league["players_prior"])
    full = value_players(players, punts=())
    punted = value_players(players, punts=("FT%",))
    t = punted.table
    assert np.allclose(t["value"], t[[f"z_{c}" for c in CATEGORY_NAMES if c != "FT%"]].sum(axis=1))
    assert punted.punts == ("FT%",) and full.pool_size == punted.pool_size


# ------------------------------------------------------------------- simulation
def test_identical_teams_are_coin_flips(league):
    model = mm.fit_team_model(league["team_weeks"])
    t = model.teams()[0]
    model.mu.loc["clone"] = model.mu.loc[t]
    model.games_per_week["clone"] = model.games_per_week[t]
    model.n_eff["clone"] = model.n_eff[t]
    sim = mm.simulate_matchup(model, t, "clone", n_sims=20000, seed=1)
    assert sim.p_win + 0.5 * sim.p_tie == pytest.approx(0.5, abs=0.02)
    assert (sim.cat_prob.sub(0.5).abs() < 0.03).all()


def test_banked_week_is_deterministic(league):
    model = mm.fit_team_model(league["team_weeks"])
    a, b = model.teams()[:2]
    big = dict(FGM=500, FGA=900, FTM=200, FTA=220, FG3M=150, PTS=1350, REB=600, AST=350, STL=120, BLK=80, TOV=40)
    small = dict(FGM=300, FGA=800, FTM=100, FTA=200, FG3M=50, PTS=750, REB=300, AST=150, STL=40, BLK=20, TOV=120)
    sim = mm.simulate_matchup(model, a, b, big, small, games_a=0, games_b=0, n_sims=500)
    assert sim.p_win == 1.0 and (sim.cat_prob == 1.0).all()


def test_covariance_is_positive_definite(league):
    model = mm.fit_team_model(league["team_weeks"])
    np.linalg.cholesky(model.cov)
    assert model.diagnostics["cov_source"].startswith("pooled")


def test_backtest_beats_baselines_and_is_calibrated(league):
    bt = mm.backtest(league["team_weeks"], n_sims=600)
    m = bt["metrics"].set_index("model")
    assert m.at["Model", "category_brier"] < m.at["Empirical all-play baseline", "category_brier"]
    assert m.at["Model", "category_BSS"] > 0.1
    cal = bt["calibration"]
    weighted_gap = np.average((cal["mean_pred"] - cal["observed"]).abs(), weights=cal["n"])
    assert weighted_gap < 0.08


# ------------------------------------------------------------------ league tools
def test_all_play_records_are_consistent(league):
    ap = power.all_play(league["team_weeks"])
    s = ap["summary"]
    n_weeks = league["team_weeks"].query("status == 'postevent'")["week"].nunique()
    games = s["ap_record"].str.split("-", expand=True).astype(int).sum(axis=1)
    assert (games == n_weeks * (len(s) - 1)).all()
    assert ap["category_rates"].stack().between(0, 1).all()
    # all-play is zero-sum across the league
    assert s["ap_pct"].mean() == pytest.approx(0.5, abs=1e-9)


def test_playoff_odds_sum_to_playoff_spots(league):
    tw = league["team_weeks"]
    model = mm.fit_team_model(tw)
    odds = power.playoff_odds(model, tw, tw[tw["status"] != "postevent"], 6, "head", 21, n_sims=400)
    assert odds["playoff_odds"].sum() == pytest.approx(6.0)
    assert odds["top_seed_odds"].sum() == pytest.approx(1.0)


def test_trade_evaluation_is_antisymmetric(league):
    players = nba_data.blend_seasons(league["players"], league["players_prior"])
    res = value_players(players)
    ids = res.table["PLAYER_ID"].tolist()
    one = trade.evaluate_trade(res.table, [ids[3]], [ids[10], ids[30]], res.replacement)
    back = trade.evaluate_trade(res.table, [ids[10], ids[30]], [ids[3]], res.replacement)
    assert one.raw_delta == pytest.approx(-back.raw_delta)
    # the 2-for-1 receiver must drop someone: priced at replacement level
    assert np.allclose(one.table["roster_spot_adj"], -res.replacement.values)


def test_context_weights_peak_at_coin_flip():
    w = trade.context_weights(pd.Series({"FG%": 0.5, "FT%": 0.95, "PTS": 0.05}), punts=["AST"])
    assert w["FG%"] == pytest.approx(1.0)
    assert w["FT%"] < 0.5 and w["PTS"] < 0.5 and w["AST"] == 0


def test_streaming_prefers_more_games_for_equal_players(league):
    players = nba_data.blend_seasons(league["players"], league["players_prior"])
    res = value_players(players)
    fa = league["free_agents"].head(30)
    games = pd.Series(1.0, index=demo.NBA_TEAMS)
    board = trade.streaming_board(fa, res.table, games, trade.context_weights(None), res.replacement)
    top = board.iloc[0]
    games2 = games.copy()
    games2[top["TEAM"]] = 4
    board2 = trade.streaming_board(fa, res.table, games2, trade.context_weights(None), res.replacement)
    assert board2.loc[board2["name"] == top["name"], "stream_score"].iloc[0] == pytest.approx(4 * top["stream_score"])


def test_hold_pairing(league):
    tx = league["transactions"]
    holds = transactions.pair_adds_drops(tx)
    assert (holds["hold_days"] >= 0).all()
    assert len(holds) == (tx["action"] == "add").sum()
    act = transactions.team_activity(tx, holds)
    assert act["stream_rate"].between(0, 1).all()
