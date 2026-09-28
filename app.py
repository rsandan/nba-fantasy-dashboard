"""Love Island (NBA) fantasy dashboard.

Run:  streamlit run app.py              (live, needs Yahoo OAuth secrets)
      FANTASY_DEMO=1 streamlit run app.py   (synthetic league, no credentials)
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

from fantasy import charts, nba_data
from fantasy import matchup_model as mm
from fantasy import power, trade
from fantasy import transactions as txa
from fantasy.categories import CATEGORIES, CATEGORY_NAMES, category_value, compare
from fantasy.data_source import connect
from fantasy.valuation import Z_COLS, value_players

st.set_page_config(page_title="Love Island (NBA)", page_icon="🏀", layout="wide",
                   initial_sidebar_state="expanded")
ET = "America/New_York"


# ============================================================================ data
@st.cache_resource(show_spinner="Connecting to Yahoo…")
def get_source():
    return connect()


SOURCE, FALLBACK_REASON = get_source()


@st.cache_data(ttl=600, show_spinner=False)
def load_meta(_tag: str):
    return SOURCE.meta()


META = load_meta("demo" if SOURCE.is_demo else "live")
KEY = META.league_key


@st.cache_data(ttl=3600, show_spinner=False)
def load_teams(key: str) -> pd.DataFrame:
    return SOURCE.teams()


@st.cache_data(ttl=None, max_entries=64, show_spinner=False)
def load_final_week(key: str, week: int) -> pd.DataFrame:
    """Completed weeks are immutable: cache for the life of the process."""
    return SOURCE.week(week)


@st.cache_data(ttl=300, show_spinner=False)
def load_live_week(key: str, week: int) -> pd.DataFrame:
    return SOURCE.week(week)


@st.cache_data(ttl=43200, show_spinner=False)
def load_future_weeks(key: str, first: int, last: int) -> pd.DataFrame:
    frames = []
    for w in range(first, last + 1):
        try:
            frames.append(SOURCE.week(w))
        except Exception:
            break  # Yahoo stops returning pairings past the known schedule
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def team_weeks() -> pd.DataFrame:
    """Weeks 1..current. The fetch loop is cheap after the first load (per-week cache)."""
    frames = [load_final_week(KEY, w) for w in range(META.start_week, META.current_week)]
    frames.append(load_live_week(KEY, META.current_week))
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    return df


@st.cache_data(ttl=900, show_spinner=False)
def load_transactions(key: str) -> pd.DataFrame:
    return SOURCE.transactions()


@st.cache_data(ttl=1800, show_spinner=False)
def load_rosters(key: str, team_keys: tuple) -> pd.DataFrame:
    return SOURCE.rosters(list(team_keys))


@st.cache_data(ttl=1800, show_spinner=False)
def load_free_agents(key: str) -> pd.DataFrame:
    return SOURCE.free_agents()


@st.cache_data(ttl=21600, show_spinner=False)
def load_players(season: str, last_n: int = 0) -> pd.DataFrame:
    return SOURCE.players(season, last_n)


@st.cache_data(ttl=86400, show_spinner=False)
def load_schedule(season: str) -> pd.DataFrame:
    return SOURCE.schedule(season)


# ======================================================================= analytics
TODAY = SOURCE.today()
SEASON = nba_data.season_for(TODAY)
PRIOR = nba_data.previous_season(SEASON)


@st.cache_data(ttl=21600, show_spinner="Blending current and prior season…")
def blended_players(season: str) -> pd.DataFrame:
    return nba_data.blend_seasons(load_players(season), load_players(nba_data.previous_season(season)))


@st.cache_data(ttl=1800, show_spinner=False)
def valuation(players: pd.DataFrame, punts: tuple, n_teams: int, roster_size: int):
    return value_players(players, n_teams=n_teams, roster_size=roster_size, punts=punts)


@st.cache_data(ttl=1800, show_spinner=False)
def matched_rosters(key: str, values: pd.DataFrame) -> pd.DataFrame:
    teams = load_teams(key)
    ros = load_rosters(key, tuple(teams["team_key"]))
    return trade.match_players(ros, values) if not ros.empty else ros


@st.cache_data(ttl=300, show_spinner="Fitting team-strength model…")
def fit_model(tw: pd.DataFrame, priors: pd.DataFrame | None, team_keys: tuple) -> mm.TeamModel:
    return mm.fit_team_model(tw, teams=list(team_keys), priors=priors)


@st.cache_data(ttl=None, max_entries=8, show_spinner="Backtesting the model week by week…")
def run_backtest(done: pd.DataFrame) -> dict:
    return mm.backtest(done, n_sims=1500)


@st.cache_data(ttl=300, show_spinner="Simulating the rest of the season…")
def season_sim(_model: mm.TeamModel, tw: pd.DataFrame, future: pd.DataFrame, model_sig: str):
    return power.playoff_odds(_model, tw, future, META.num_playoff_teams, META.scoring_type,
                              META.playoff_start_week, n_sims=2000)


@st.cache_data(ttl=300, show_spinner=False)
def ratings(_model: mm.TeamModel, model_sig: str) -> pd.Series:
    return power.power_ratings(_model, n_sims=1200)


def model_signature(tw: pd.DataFrame) -> str:
    return f"{KEY}|{len(tw)}|{tw[['games', 'PTS']].sum().sum():.0f}"


def core():
    """Everything most pages need, computed once per rerun (each step is cached)."""
    tw = team_weeks()
    teams = load_teams(KEY)
    names = dict(zip(teams["team_key"], teams["team_name"]))
    players = blended_players(SEASON)
    vals = valuation(players, (), META.num_teams, META.roster_size) if not players.empty else None
    ros = matched_rosters(KEY, vals.table) if vals is not None else pd.DataFrame()
    priors = mm.roster_priors(ros, players) if vals is not None else None
    model = fit_model(tw, priors, tuple(teams["team_key"]))
    ap = power.all_play(tw)
    return dict(tw=tw, teams=teams, names=names, players=players, vals=vals, rosters=ros,
                model=model, ap=ap, sig=model_signature(tw))


def banked_score(a: pd.Series, b: pd.Series) -> tuple[int, int, int]:
    res = [compare(category_value(a, c), category_value(b, c), c) for c in CATEGORIES]
    return sum(r == 1 for r in res), sum(r == 0 for r in res), sum(r == 0.5 for r in res)


def fmt_cat(value: float, cat_name: str) -> str:
    if value != value:
        return "–"
    return f"{value:.3f}".lstrip("0") if cat_name in ("FG%", "FT%") else f"{value:.0f}"


def pct(x) -> str:
    """Probability label that never claims certainty a simulation can't support."""
    if x is None or x != x:
        return "–"
    if x < 0.005:
        return "<1%"
    if x > 0.995:
        return ">99%"
    return f"{x:.0%}"


# ========================================================================== header
def header(title: str, subtitle: str | None = None):
    st.title(title)
    now = datetime.now(pd.Timestamp.now(tz=ET).tz).strftime("%b %d, %Y · %I:%M %p ET")
    src = "Demo league (synthetic data)" if SOURCE.is_demo else "Live from Yahoo Fantasy"
    st.caption(f"Week {META.current_week} · {src} · snapshot {now}"
               + (f" · {subtitle}" if subtitle else ""))
    if SOURCE.is_demo and FALLBACK_REASON and FALLBACK_REASON != "FANTASY_DEMO is set":
        st.info(f"Couldn't reach Yahoo, so this is the demo league. Reason: `{FALLBACK_REASON}`")


# =========================================================================== pages
def page_home():
    header(META.name)
    c = core()
    tw, names, model, ap = c["tw"], c["names"], c["model"], c["ap"]
    if st.button("🔄 Refresh live scores"):
        load_live_week.clear()
        st.rerun()

    live = tw[tw["week"] == META.current_week]
    st.subheader(f"Week {META.current_week} matchups")
    if live.empty:
        st.write("No matchups scheduled yet. Check back after the draft.")
    else:
        st.caption("Win probabilities come from 4,000 simulations of each matchup: what's already "
                   "banked this week plus each team's remaining games drawn from its fitted "
                   "per-game rates (with category correlations). Ties in a category count half.")
        sims = mm.live_matchups(model, live, n_sims=4000)
        cols = st.columns(2)
        for i, sim in enumerate(sims):
            a = live[live["team_key"] == sim.team_a].iloc[0]
            b = live[live["team_key"] == sim.team_b].iloc[0]
            w, l, t = banked_score(a, b) if a["status"] != "preevent" else (0, 0, 0)
            with cols[i % 2].container(border=True):
                na, nb = names.get(sim.team_a, sim.team_a), names.get(sim.team_b, sim.team_b)
                st.markdown(f"**{na}** vs **{nb}**")
                m1, m2, m3 = st.columns(3)
                m1.metric(f"{na[:18]} win", pct(sim.p_win))
                m2.metric("Tie", pct(sim.p_tie))
                m3.metric(f"{nb[:18]} win", pct(sim.p_loss))
                gl = lambda r: "?" if pd.isna(r["remaining_games"]) else f"{r['remaining_games']:.0f}"
                st.caption(f"Current score {w}-{l}-{t} · games left {gl(a)} vs {gl(b)} · "
                           f"projected categories {sim.exp_cats_a:.1f}–{9 - sim.exp_cats_a:.1f}")
                rows = []
                for cat in CATEGORIES:
                    rows.append({"Cat": cat.name,
                                 na[:14]: fmt_cat(category_value(a, cat), cat.name),
                                 nb[:14]: fmt_cat(category_value(b, cat), cat.name),
                                 "Proj": f"{fmt_cat(sim.proj_a[cat.name], cat.name)} – "
                                         f"{fmt_cat(sim.proj_b[cat.name], cat.name)}",
                                 "Win %": sim.cat_prob[cat.name]})
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                             column_config={"Win %": st.column_config.ProgressColumn(
                                 f"{na[:10]} wins", format="percent", min_value=0, max_value=1)})

    st.subheader("Standings")
    summ = ap["summary"]
    if summ.empty:
        st.write("Standings appear after the first completed week.")
        return
    table = summ.assign(team=summ["team_key"].map(names))
    rec_col = "cat_record" if META.scoring_type == "head" else "h2h_record"
    rec_pct = "cat_pct" if META.scoring_type == "head" else "h2h_pct"
    table = table.sort_values(rec_pct, ascending=False)
    table.insert(0, "Rank", range(1, len(table) + 1))
    luck = "luck_category" if META.scoring_type == "head" else "luck_matchup"
    st.dataframe(
        table[["Rank", "team", rec_col, rec_pct, "ap_record", "ap_pct", luck]],
        hide_index=True, width="stretch",
        column_config={
            "team": "Team", rec_col: "Record", rec_pct: st.column_config.NumberColumn("Win %", format="%.3f"),
            "ap_record": "All-play record",
            "ap_pct": st.column_config.NumberColumn("All-play %", format="%.3f",
                                                    help="Record if you played every team every week"),
            luck: st.column_config.NumberColumn("Luck", format="%+.3f",
                                                help="Actual win% minus all-play win%. Positive = soft schedule."),
        })


def page_power():
    header("Power rankings", "schedule-neutral team strength")
    c = core()
    tw, names, model, ap = c["tw"], c["names"], c["model"], c["ap"]
    summ = ap["summary"]
    if summ.empty:
        st.info("Power rankings need at least one completed week.")
        return
    pr = ratings(model, c["sig"])
    future = load_future_weeks(KEY, META.current_week + 1, META.playoff_start_week - 1) \
        if not SOURCE.is_demo else tw[tw["status"] == "preevent"]
    live = tw[tw["week"] == META.current_week]
    fut = pd.concat([live, future], ignore_index=True) if not future.empty else live
    odds = season_sim(model, tw, fut, c["sig"]).set_index("team_key")
    form = power.recent_form(ap["weekly"])

    t = summ.set_index("team_key")
    board = pd.DataFrame({
        "Team": [names.get(k, k) for k in t.index],
        "Power": pr.reindex(t.index).values,
        "All-play %": t["ap_pct"].values,
        "Form (3-wk half-life)": form.reindex(t.index).values,
        "Luck": (t["luck_category"] if META.scoring_type == "head" else t["luck_matchup"]).values,
        "Playoff odds": odds["playoff_odds"].reindex(t.index).values,
        "#1 seed": odds["top_seed_odds"].reindex(t.index).values,
        "Proj. final win %": odds["exp_win_pct"].reindex(t.index).values,
    }, index=t.index).sort_values("Power", ascending=False)
    board.insert(0, "Rank", range(1, len(board) + 1))
    st.dataframe(board, hide_index=True, width="stretch", column_config={
        "Power": st.column_config.ProgressColumn(
            "Power", format="%.3f", min_value=0, max_value=1,
            help="P(beating a random league opponent in a typical week), from the matchup model"),
        "All-play %": st.column_config.NumberColumn(format="%.3f"),
        "Form (3-wk half-life)": st.column_config.NumberColumn(format="%.3f"),
        "Luck": st.column_config.NumberColumn(format="%+.3f"),
        "Playoff odds": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        "#1 seed": st.column_config.NumberColumn(format="percent"),
        "Proj. final win %": st.column_config.NumberColumn(format="%.3f"),
    })
    with st.expander("How these are computed"):
        st.markdown(f"""
- **Power** asks the matchup model how often each team beats every other team over a full week with
  equal games, then averages. It's schedule-neutral, and early-season results are shrunk toward the
  league average (and toward roster projections when rosters are available).
- **All-play %** scores each completed week against *all* {META.num_teams - 1} opponents, not only the scheduled one.
- **Luck** = actual win % − all-play win %. Over a season it mostly reflects the schedule, not skill.
- **Playoff odds** simulate the remaining regular season 2,000 times with the real schedule and the
  league's scoring format (`{META.scoring_type}`); top {META.num_playoff_teams} make it. Exact ties are
  broken at random because Yahoo's tiebreakers aren't in the API.
""")

    l, r = st.columns(2)
    with l:
        st.subheader("Playoff odds")
        st.plotly_chart(charts.probability_bars(
            board.reset_index().rename(columns={"Team": "team_name"}), "Playoff odds"), width="stretch")
    with r:
        st.subheader("Schedule luck")
        st.plotly_chart(charts.luck_bars(
            board.reset_index().rename(columns={"Team": "team_name"}), "Luck"), width="stretch")
    st.subheader("Category strength (all-play category win rate)")
    st.caption("Blue = wins that category most weeks against the league, red = loses it. A column of "
               "red for one team is a punt, deliberate or not.")
    st.plotly_chart(charts.category_heatmap(ap["category_rates"], names), width="stretch")


def page_matchup_lab():
    header("Matchup lab", "any two teams, plus how well the model has predicted")
    c = core()
    names, model = c["names"], c["model"]
    keys = list(names)
    col1, col2, col3 = st.columns([2, 2, 1])
    a = col1.selectbox("Team A", keys, format_func=names.get, index=0)
    b = col2.selectbox("Team B", keys, format_func=names.get, index=1 if len(keys) > 1 else 0)
    games = col3.number_input("Games each", 10, 60, int(round(model.g_ref)))
    if a == b:
        st.warning("Pick two different teams.")
    else:
        sim = mm.simulate_matchup(model, a, b, games_a=games, games_b=games, n_sims=6000)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{names[a]} win", pct(sim.p_win))
        m2.metric("Tie", pct(sim.p_tie))
        m3.metric(f"{names[b]} win", pct(sim.p_loss))
        m4.metric("Expected categories", f"{sim.exp_cats_a:.1f} – {9 - sim.exp_cats_a:.1f}")
        st.plotly_chart(charts.category_win_bars(sim.cat_prob, names[a], names[b]), width="stretch")
        dist = pd.Series(sim.cats_a + 0.5 * sim.ties).round(1).value_counts(normalize=True).sort_index()
        st.caption("Distribution of categories won by " + names[a] + ": " +
                   " · ".join(f"{k:g}: {v:.0%}" for k, v in dist.items() if v >= 0.01))

    st.divider()
    st.subheader("Model backtest")
    done = c["tw"][c["tw"]["status"] == "postevent"]
    if done["week"].nunique() < 5:
        st.info("The walk-forward backtest needs at least 5 completed weeks (3 to train, 2+ to test).")
        return
    bt = run_backtest(done)
    met = bt["metrics"]
    st.markdown(
        "Walk-forward: for each week *w*, the model is fit only on weeks before *w* and predicts every "
        "category of every matchup before the week starts. Lower Brier/log loss is better; **BSS** is "
        "the Brier skill score versus a coin flip (0 = no skill, 1 = perfect). The empirical baseline "
        "compares the two teams' past weekly results directly, with no model.")
    st.dataframe(met, hide_index=True, width="stretch", column_config={
        c_: st.column_config.NumberColumn(format="%.4f") for c_ in met.columns if c_ != "model"})
    l, r = st.columns([3, 2])
    with l:
        st.plotly_chart(charts.calibration_plot(bt["calibration"]), width="stretch")
    with r:
        per_cat = bt["categories"].groupby("category").apply(
            lambda d: pd.Series({"Brier (model)": np.mean((d.p_model - d.y) ** 2),
                                 "Brier (baseline)": np.mean((d.p_baseline - d.y) ** 2)}),
            include_groups=False).reindex(CATEGORY_NAMES)
        st.caption("Brier score by category (lower is better). Rate stats like STL and BLK are "
                   "noisier week to week, so expect them to be the hardest to call.")
        st.dataframe(per_cat, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.3f") for k in per_cat.columns})


WINDOWS = {"Blended (this season shrunk to last)": ("blend", 0), "This season": ("cur", 0),
           "Last 15 games": ("cur", 15), "Last 30 games": ("cur", 30), "Last season": ("prior", 0)}


def window_players(label: str) -> pd.DataFrame:
    kind, n = WINDOWS[label]
    if kind == "blend":
        return blended_players(SEASON)
    df = load_players(SEASON if kind == "cur" else PRIOR, n)
    if not df.empty:
        df = df.assign(GP_TOT=df["GP"])
    return df


def page_values():
    header("Player values", "9-cat z-scores against the rostered pool")
    c1, c2, c3 = st.columns([2, 3, 2])
    window = c1.selectbox("Stat window", list(WINDOWS), index=0)
    punts = c2.multiselect("Punt categories", CATEGORY_NAMES, help="Removed from the total and from pool selection")
    show = c3.selectbox("Show", ["All players", "Free agents only", "Rostered only"])
    players = window_players(window)
    if players.empty:
        st.warning("No NBA stats for this window yet (e.g. current season before opening night). "
                   "Try 'Blended' or 'Last season'.")
        return
    res = valuation(players, tuple(punts), META.num_teams, META.roster_size)
    table = res.table.copy()
    teams = load_teams(KEY)
    ros = matched_rosters(KEY, res.table)
    owner = dict(zip(ros.get("PLAYER_ID", []), ros.get("team_key", []))) if not ros.empty else {}
    names = dict(zip(teams["team_key"], teams["team_name"]))
    table["Owner"] = table["PLAYER_ID"].map(owner).map(names).fillna("FA")
    if show == "Free agents only":
        table = table[table["Owner"] == "FA"]
    elif show == "Rostered only":
        table = table[table["Owner"] != "FA"]
    q = st.text_input("Search player", "")
    if q:
        table = table[table["PLAYER_NAME"].str.contains(q, case=False)]
    cols = ["rank", "PLAYER_NAME", "TEAM", "Owner", "GP", "MIN", "value", "value_adj", *Z_COLS]
    if "blend_w" in table:
        cols.insert(6, "blend_w")
    cfg = {z: st.column_config.NumberColumn(z.replace("z_", ""), format="%+.2f") for z in Z_COLS}
    cfg.update({"rank": "Rk", "PLAYER_NAME": "Player", "MIN": st.column_config.NumberColumn(format="%.1f"),
                "GP": st.column_config.NumberColumn(format="%d"),
                "value": st.column_config.NumberColumn("Value/gm", format="%.2f",
                                                       help="Sum of z over non-punted categories"),
                "value_adj": st.column_config.NumberColumn("Avail-adj", format="%.2f",
                                                           help="Value × share of team games played"),
                "blend_w": st.column_config.NumberColumn("Cur wt", format="%.2f",
                                                         help="Weight on this season vs last (GP/(GP+15))")})
    st.dataframe(table[[c_ for c_ in cols if c_ in table]], hide_index=True, width="stretch",
                 height=620, column_config=cfg)
    st.caption(f"Pool = top {res.pool_size} players ({META.num_teams} teams × {META.roster_size} roster spots). "
               "FG%/FT% use impact (pct above pool average × attempts), so volume matters. "
               "Replacement level per category (z): " +
               ", ".join(f"{k} {v:+.2f}" for k, v in res.replacement.items()))


def _roster_picker(label: str, team_key: str, ros: pd.DataFrame, vals: pd.DataFrame, key: str) -> list:
    if not ros.empty and team_key in set(ros["team_key"]):
        options = ros[(ros["team_key"] == team_key) & ros["PLAYER_ID"].notna()]
        mapping = dict(zip(options["PLAYER_ID"], options["name"]))
    else:
        top = vals.head(400)
        mapping = dict(zip(top["PLAYER_ID"], top["PLAYER_NAME"]))
    return st.multiselect(label, list(mapping), format_func=lambda i: mapping.get(i, str(i)), key=key)


def page_trade():
    header("Trade analyzer", "category-aware, roster-spot-aware")
    c = core()
    names, ap = c["names"], c["ap"]
    keys = list(names)
    col1, col2 = st.columns(2)
    me = col1.selectbox("Your team", keys, format_func=names.get)
    them = col2.selectbox("Trade partner", [k for k in keys if k != me], format_func=names.get)
    punts = st.multiselect("Your punts", CATEGORY_NAMES, key="trade_punts")
    res = valuation(c["players"], tuple(punts), META.num_teams, META.roster_size)
    ros = matched_rosters(KEY, res.table)
    give = _roster_picker("You give", me, ros, res.table, "give")
    get = _roster_picker("You get", them, ros, res.table, "get")
    if not give and not get:
        st.caption("Pick players on both sides. Rosters load from Yahoo; before the draft you can "
                   "choose from the top 400 players.")
        return
    rates = ap["category_rates"]
    w_me = trade.context_weights(rates.loc[me] if me in rates.index else None, punts)
    w_them = trade.context_weights(rates.loc[them] if them in rates.index else None)
    mine = trade.evaluate_trade(res.table, give, get, res.replacement, w_me)
    theirs = trade.evaluate_trade(res.table, get, give, res.replacement, w_them)
    m1, m2, m3 = st.columns(3)
    m1.metric("Your weighted Δz", f"{mine.weighted_delta:+.2f}", f"raw {mine.raw_delta:+.2f}", delta_color="off")
    m2.metric(f"{names[them]} weighted Δz", f"{theirs.weighted_delta:+.2f}", f"raw {theirs.raw_delta:+.2f}",
              delta_color="off")
    m3.metric("Verdict", mine.verdict.split(":")[0])
    st.write(mine.verdict)
    if mine.weighted_delta > 0 and theirs.weighted_delta > 0:
        st.success("Both sides gain on their own weights: a genuine fit trade, the easiest kind to get accepted.")
    st.dataframe(mine.table, width="stretch", column_config={
        k: st.column_config.NumberColumn(format="%+.2f") for k in mine.table.columns})
    st.caption("Weights: `0.25 + 0.75 × 4p(1−p)` where p is your all-play win rate in the category, so swing "
               "categories count most and locked/punted ones least. Roster-spot adj prices the extra waiver "
               "pickup (or forced drop) in uneven trades at replacement level.")


def page_streaming():
    header("Streaming board", "who to add for the rest of this week")
    c = core()
    names, tw, model = c["names"], c["tw"], c["model"]
    me = st.selectbox("Your team", list(names), format_func=names.get)
    live = tw[tw["week"] == META.current_week]
    row = live[live["team_key"] == me]
    punts = st.multiselect("Ignore categories", CATEGORY_NAMES, key="stream_punts")
    weights = trade.context_weights(None, punts)
    week_end = TODAY
    if not row.empty:
        r = row.iloc[0]
        opp = live[live["team_key"] == r["opponent_key"]]
        if not opp.empty:
            pair = pd.concat([row, opp])
            sim = mm.live_matchups(model, pair.assign(matchup_id="x"), n_sims=3000)[0]
            p = sim.cat_prob if sim.team_a == me else 1 - sim.cat_prob
            weights = trade.context_weights(p, punts, floor=0.1)
            st.caption(f"vs **{names.get(r['opponent_key'])}** · P(win) {pct(sim.p_win if sim.team_a == me else sim.p_loss)}. "
                       "Categories are weighted by how contested they still are this week, so a streamer who "
                       "helps a 50/50 category outranks one who pads a category you've already locked.")
            st.dataframe(pd.DataFrame({"P(win cat)": p, "weight": weights}).T, width="stretch",
                         column_config={k: st.column_config.NumberColumn(format="%.2f") for k in CATEGORY_NAMES})
        if isinstance(r["week_end"], str) and r["week_end"]:
            week_end = date.fromisoformat(r["week_end"])
    games_left = nba_data.games_by_team(load_schedule(SEASON), TODAY, week_end)
    fa = load_free_agents(KEY)
    if fa.empty or c["vals"] is None:
        st.info("No free agents or player stats available yet.")
        return
    board = trade.streaming_board(fa, c["vals"].table, games_left, weights, c["vals"].replacement)
    st.dataframe(board.head(40), hide_index=True, width="stretch", height=600, column_config={
        "name": "Player", "eligible_positions": "Pos", "percent_owned": st.column_config.NumberColumn("% own"),
        "MIN": st.column_config.NumberColumn(format="%.1f"), "GP": st.column_config.NumberColumn(format="%d"),
        "games_left": st.column_config.NumberColumn("Games left", help=f"NBA games {TODAY:%b %d}–{week_end:%b %d}"),
        "value": st.column_config.NumberColumn("Value/gm", format="%.2f"),
        "weighted_per_game": st.column_config.NumberColumn("Wtd/gm vs repl", format="%+.2f"),
        "stream_score": st.column_config.ProgressColumn(
            "Stream score", format="%.2f", min_value=0,
            max_value=float(max(board["stream_score"].max(), 1e-6)) if not board.empty else 1.0),
        **{z: st.column_config.NumberColumn(z.replace("z_", ""), format="%+.1f") for z in Z_COLS}})


def page_free_agency():
    header("Free agency", "who works the wire, when, and whether it pays off")
    c = core()
    tx = load_transactions(KEY)
    if tx.empty:
        st.info("No transactions yet.")
        return
    holds = txa.pair_adds_drops(tx)
    act = txa.team_activity(tx, holds)
    adds = tx[tx["action"] == "add"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Adds this season", f"{len(adds):,}")
    k2.metric("Most active", act.iloc[0]["team_name"] if not act.empty else "–")
    k3.metric("Median hold", f"{holds['hold_days'].median():.1f} days" if not holds.empty else "–")
    k4.metric("Dropped within a week", pct(((holds["hold_days"] <= 7) & holds["dropped"]).mean())
              if not holds.empty else "–")

    st.subheader("Activity by team")
    st.dataframe(act.drop(columns="team_key"), hide_index=True, width="stretch", column_config={
        "team_name": "Team", "add": "Adds", "drop": "Drops", "trade_players": "Players traded",
        "median_hold_days": st.column_config.NumberColumn("Median hold (days)", format="%.1f"),
        "stream_rate": st.column_config.ProgressColumn("Stream rate", format="percent", min_value=0, max_value=1,
                                                       help="Share of adds dropped within 7 days")})
    l, r = st.columns(2)
    with l:
        st.subheader("When moves happen")
        st.plotly_chart(charts.timing_heatmap(txa.timing_heatmap(tx)), width="stretch")
    with r:
        st.subheader("How long pickups last")
        st.plotly_chart(charts.hold_histogram(holds), width="stretch")

    st.subheader("Does grinding the wire win?")
    avp = txa.activity_vs_performance(act, c["ap"]["summary"])
    if avp["n"] > 2:
        st.plotly_chart(charts.activity_scatter(avp["frame"]), width="stretch")
        st.caption(f"Spearman ρ = {avp['rho']:+.2f} across {avp['n']} teams. With this few teams, |ρ| needs to "
                   "be above ~0.63 to be distinguishable from noise at the 5% level, and causality runs both "
                   "ways (losing teams churn more).")
    st.subheader("Most-added players")
    st.dataframe(txa.hot_players(tx, holds), hide_index=True, width="stretch", column_config={
        "player_id": None, "player_name": "Player", "times_added": "Times added", "distinct_teams": "Teams",
        "last_added": st.column_config.DatetimeColumn("Last added", format="MMM D, h:mm a"),
        "median_hold_days": st.column_config.NumberColumn("Median hold (days)", format="%.1f")})


def page_compare():
    header("Player comparison")
    window = st.selectbox("Stat window", list(WINDOWS), index=0, key="cmp_window")
    players = window_players(window)
    if players.empty:
        st.warning("No stats for this window yet.")
        return
    options = players.sort_values("MIN", ascending=False)
    mapping = dict(zip(options["PLAYER_ID"], options["PLAYER_NAME"]))
    picks = st.multiselect("Players (2–5)", list(mapping), default=list(mapping)[:2],
                           format_func=lambda i: mapping[i], max_selections=5)
    if len(picks) < 2:
        return
    sub = players[players["PLAYER_ID"].isin(picks)].copy()
    sub["FG%"] = sub["FGM"] / sub["FGA"].where(sub["FGA"] > 0)
    sub["FT%"] = sub["FTM"] / sub["FTA"].where(sub["FTA"] > 0)
    sub["Headshot"] = sub["PLAYER_ID"].map(
        lambda i: f"https://cdn.nba.com/headshots/nba/latest/1040x760/{int(i)}.png")
    res = valuation(players, (), META.num_teams, META.roster_size).table.set_index("PLAYER_ID")
    for z in Z_COLS + ["value"]:
        sub[z] = sub["PLAYER_ID"].map(res[z]) if z in res else np.nan
    cols = ["Headshot", "PLAYER_NAME", "TEAM", "GP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV",
            "FG%", "FT%", "FGA", "FTA", "value", *Z_COLS]
    st.dataframe(sub[[c_ for c_ in cols if c_ in sub]], hide_index=True, width="stretch", column_config={
        "Headshot": st.column_config.ImageColumn("", width="small"), "PLAYER_NAME": "Player",
        "FG%": st.column_config.NumberColumn(format="%.3f"), "FT%": st.column_config.NumberColumn(format="%.3f"),
        **{k: st.column_config.NumberColumn(format="%.1f") for k in
           ("MIN", "PTS", "REB", "AST", "STL", "BLK", "FG3M", "TOV", "FGA", "FTA")},
        "value": st.column_config.NumberColumn("Value/gm", format="%.2f"),
        **{z: st.column_config.NumberColumn(z.replace("z_", "z "), format="%+.2f") for z in Z_COLS}})
    st.caption("Shooting percentages are volume-weighted (total makes ÷ total attempts), not averages of "
               "per-game percentages.")


# ======================================================================== navigation
st.logo("ryanlogo.png", link="https://rsandan.github.io", size="large")
nav = st.navigation({
    "League": [st.Page(page_home, title="This week", icon="🏠", default=True),
               st.Page(page_power, title="Power rankings", icon="📈"),
               st.Page(page_matchup_lab, title="Matchup lab", icon="🧪")],
    "Players": [st.Page(page_values, title="Player values", icon="🧮"),
                st.Page(page_trade, title="Trade analyzer", icon="🔁"),
                st.Page(page_streaming, title="Streaming board", icon="📅"),
                st.Page(page_free_agency, title="Free agency", icon="🗣️"),
                st.Page(page_compare, title="Player comparison", icon="⛹🏽")],
})
nav.run()
st.caption(f"© {date.today().year} Ryan Sandan · [rsandan.github.io](https://rsandan.github.io)")
