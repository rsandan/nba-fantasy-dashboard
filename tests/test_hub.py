"""Trade hub, team profiles and lineup checks on the demo league."""
import pandas as pd
import pytest

from fantasy import demo, hub, nba_data, power, trade
from fantasy.valuation import value_players


@pytest.fixture(scope="module")
def ctx():
    lg = demo.make_league(seed=7)
    players = nba_data.blend_seasons(lg["players"], lg["players_prior"])
    vals = value_players(players, n_teams=10, roster_size=13)
    ros = trade.match_players(lg["rosters"], vals.table)
    rates = power.all_play(lg["team_weeks"])["category_rates"]
    names = dict(zip(lg["teams"]["team_key"], lg["teams"]["team_name"]))
    return dict(lg=lg, vals=vals, ros=ros, rates=rates, names=names, rz=hub.roster_z(ros, vals.table))


def test_profile_labels_are_consistent(ctx):
    me = next(iter(ctx["names"]))
    prof = hub.team_profile(me, ctx["rates"], ctx["rz"])
    assert set(prof.strengths).isdisjoint(prof.weaknesses)
    assert len(prof.table) == 9 and prof.advice


def test_suggested_trades_help_both_sides(ctx):
    for me in list(ctx["names"])[:4]:
        partners = hub.trade_partners(me, list(ctx["names"]), ctx["rates"], ctx["rz"])
        assert all(0 <= p.fit <= 100 for p in partners) and me not in {p.team_key for p in partners}
        ideas = hub.suggest_trades(me, ctx["ros"], ctx["vals"].table, ctx["vals"].replacement,
                                   ctx["rates"], partners, ctx["names"])
        for i in ideas:
            assert i.mine > 0 and i.theirs > 0
            assert 1 <= len(i.give) <= 2 and 1 <= len(i.get) <= 2 and len(i.give) + len(i.get) <= 3
            mine = set(ctx["ros"][ctx["ros"]["team_key"] == me]["PLAYER_ID"])
            assert set(i.give) <= mine


def test_suggested_trades_skip_injured_players(ctx):
    me = next(iter(ctx["names"]))
    partners = hub.trade_partners(me, list(ctx["names"]), ctx["rates"], ctx["rz"])
    ideas = hub.suggest_trades(me, ctx["ros"], ctx["vals"].table, ctx["vals"].replacement,
                               ctx["rates"], partners, ctx["names"])
    hurt = set(ctx["ros"][ctx["ros"]["status"] == "O"]["PLAYER_ID"])
    assert all(not (set(i.give) | set(i.get)) & hurt for i in ideas)


def test_lineup_check_flags_injured_starter_and_idle_slot():
    ros = pd.DataFrame([
        dict(team_key="t1", name="Hurt Guy", status="O", selected_position="PG",
             eligible_positions=["PG", "G", "Util"], TEAM="BOS"),
        dict(team_key="t1", name="Idle Starter", status="", selected_position="C",
             eligible_positions=["C", "Util"], TEAM="LAL"),
        dict(team_key="t1", name="Bench Big", status="", selected_position="BN",
             eligible_positions=["C", "Util"], TEAM="BOS"),
        dict(team_key="t1", name="Bench Guard", status="", selected_position="BN",
             eligible_positions=["PG", "G", "Util"], TEAM="LAL"),
    ])
    issues, ok = hub.lineup_check("t1", ros, playing_today={"BOS"})
    titles = [i.title for i in issues]
    assert ok
    assert any("Hurt Guy is out" in t for t in titles)
    assert "Start Bench Big over Idle Starter" in titles
    assert not any("Bench Guard" in t for t in titles)       # no game today


def test_lineup_check_without_slot_data_is_silent():
    issues, ok = hub.lineup_check("t1", pd.DataFrame([dict(team_key="t1", name="A")]), {"BOS"})
    assert issues == [] and ok is False


def test_suggested_trades_are_fair_on_overall_value(ctx):
    for me in ctx["names"]:
        partners = hub.trade_partners(me, list(ctx["names"]), ctx["rates"], ctx["rz"])
        for i in hub.suggest_trades(me, ctx["ros"], ctx["vals"].table, ctx["vals"].replacement,
                                    ctx["rates"], partners, ctx["names"]):
            g = hub.market_value(ctx["vals"].table, i.give, ctx["vals"].replacement)
            t = hub.market_value(ctx["vals"].table, i.get, ctx["vals"].replacement)
            assert abs(t - g) / max(t, g) <= hub.FAIR_GAP + 1e-9


def test_two_stars_for_a_role_player_is_flagged_as_overpay(ctx):
    vals = ctx["vals"].table
    stars = vals.nsmallest(2, "rank")["PLAYER_ID"].tolist()
    role = vals[vals["rank"] == 120]["PLAYER_ID"].tolist()
    g = hub.market_value(vals, stars, ctx["vals"].replacement)
    t = hub.market_value(vals, role, ctx["vals"].replacement)
    assert hub.fairness(g, t)[1] == "You overpay"


def test_team_report_gives_every_team_one_good_and_one_bad(ctx):
    from fantasy import insights, power
    lg = ctx["lg"]
    tw = lg["team_weeks"]
    live = tw[tw["status"] == "midevent"]
    now = pd.Timestamp(live.iloc[0]["week_start"]).tz_localize("America/New_York") + pd.Timedelta(days=3)
    notes = insights.team_report(ctx["names"], lg["transactions"], insights.player_index(ctx["vals"].table),
                                 now, {}, tw, ctx["rates"], ctx["ros"])
    assert {n.team_key for n in notes} == set(ctx["names"])
    assert all(n.good and n.bad for n in notes)
