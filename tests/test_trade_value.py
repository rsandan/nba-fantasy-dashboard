"""Trade value on real per-game stats (top ~230 players, Nov 2026 snapshot).

Guards the cases that broke on the live league: plain 9-cat z-scores ranked Giannis
(#28) behind Dyson Daniels (#22), so the trade hub called Giannis + Jabari Smith for
Daniels "fair". Trade value now accounts for build-around (punt) value, H2H weekly
noise and the star premium, and 2-for-1s can't hand the consolidating side a discount.
"""
from pathlib import Path

import pandas as pd
import pytest

from fantasy import hub, nba_data
from fantasy.valuation import h2h_factors, trade_market, value_players

FIXTURE = Path(__file__).parent / "fixtures" / "players_nov2026.csv"


@pytest.fixture(scope="module")
def table():
    players = nba_data.standardize_player_frame(pd.read_csv(FIXTURE))
    return value_players(players, n_teams=8, roster_size=13).table


def _id(table, name):
    return int(table[table["PLAYER_NAME"].str.contains(name, regex=False)]["PLAYER_ID"].iloc[0])


def _label(table, give, get):
    m = trade_market(table)
    g = hub.market_value(table, [_id(table, n) for n in give], market=m)
    r = hub.market_value(table, [_id(table, n) for n in get], market=m)
    return hub.fairness(g, r, len(give), len(get))[1]


def test_giannis_is_a_top_five_trade_asset(table):
    m = trade_market(table).sort_values(ascending=False)
    assert _id(table, "Giannis") in set(m.index[:5])
    assert m[_id(table, "Giannis")] > 2 * m[_id(table, "Dyson Daniels")]


def test_giannis_plus_jabari_for_daniels_is_an_overpay(table):
    assert _label(table, ["Giannis", "Jabari Smith"], ["Dyson Daniels"]) == "You overpay"
    assert _label(table, ["Giannis"], ["Dyson Daniels", "Scottie Barnes"]) == "You overpay"


def test_giannis_takes_a_maxey_level_add_on(table):
    m = trade_market(table)
    g = hub.market_value(table, [_id(table, "Giannis")], market=m)
    r = hub.market_value(table, [_id(table, "Dyson Daniels"), _id(table, "Tyrese Maxey")], market=m)
    assert -0.15 < (r - g) / g < 0          # close, but the side getting Giannis still pays a bit more


def test_h2h_factors_discount_noisy_categories(table):
    f = h2h_factors(table)
    assert f["PTS"] > 0.9 and f["REB"] > f["STL"] and f["3PT%"] < 0.5
