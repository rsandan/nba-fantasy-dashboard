"""Love Island (NBA) fantasy dashboard. Built for iPhone first.

Run:  streamlit run app.py                  (live, needs Yahoo OAuth secrets)
      FANTASY_DEMO=1 streamlit run app.py   (synthetic league, no credentials)

Four tabs, one job each:
  This Week   how is my matchup going, category by category
  Standings   record, schedule-neutral power, playoff odds
  Players     who to pick up today, and a searchable 9-cat ranking
  Trade       should I take this deal
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from fantasy import matchup_model as mm
from fantasy import nba_data, power, trade
from fantasy import ui
from fantasy.categories import CATEGORIES, CATEGORY_NAMES, category_value, compare
from fantasy.data_source import connect
from fantasy.ui import e, pct, signed
from fantasy.valuation import Z_COLS, value_players

st.set_page_config(page_title="Love Island", page_icon="🏀", layout="centered",
                   initial_sidebar_state="collapsed")
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
    frames = [load_final_week(KEY, w) for w in range(META.start_week, META.current_week)]
    frames.append(load_live_week(KEY, META.current_week))
    return pd.concat([f for f in frames if not f.empty], ignore_index=True)


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


@st.cache_data(ttl=21600, show_spinner="Loading player stats…")
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


@st.cache_data(ttl=300, show_spinner="Crunching the numbers…")
def fit_model(tw: pd.DataFrame, priors: pd.DataFrame | None, team_keys: tuple) -> mm.TeamModel:
    return mm.fit_team_model(tw, teams=list(team_keys), priors=priors)


@st.cache_data(ttl=300, show_spinner="Simulating this week…")
def week_sims(_model: mm.TeamModel, live: pd.DataFrame, model_sig: str) -> list:
    return mm.live_matchups(_model, live, n_sims=4000)


@st.cache_data(ttl=300, show_spinner="Simulating the rest of the season…")
def season_sim(_model: mm.TeamModel, tw: pd.DataFrame, future: pd.DataFrame, model_sig: str):
    return power.playoff_odds(_model, tw, future, META.num_playoff_teams, META.scoring_type,
                              META.playoff_start_week, n_sims=2000)


@st.cache_data(ttl=300, show_spinner="Rating teams…")
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


# ===================================================================== "who am I"
# The user's team lives in session state and is mirrored into the URL (?team=3), so a
# bookmark or an Add to Home Screen icon opens straight to their own matchup.
NAMES = dict(zip(load_teams(KEY)["team_key"], load_teams(KEY)["team_name"]))


def _short(team_key: str) -> str:
    return str(team_key).split(".t.")[-1]


def my_team() -> str | None:
    me = st.session_state.get("me")
    if me in NAMES:
        return me
    q = st.query_params.get("team")
    for k in NAMES:
        if q and _short(k) == q:
            st.session_state["me"] = k
            return k
    return None


def _pick_team(widget_key: str):
    st.session_state["me"] = st.session_state[widget_key]


def team_picker(widget_key: str, label: str = "Your team"):
    keys = list(NAMES)
    me = my_team()
    st.radio(label, keys, index=keys.index(me) if me in keys else None, format_func=NAMES.get,
             key=widget_key, on_change=_pick_team, args=(widget_key,))


# ========================================================================= chrome
def title_bar(title: str, sub: str = "", live: bool = False):
    with st.container(horizontal=True, vertical_alignment="bottom", key="titlebar"):
        st.html(ui.large_title(title, sub, live))
        with st.popover("", icon=":material/account_circle:", type="tertiary", key="profile"):
            team_picker("me_profile")
            st.caption("Saved in this page's link. In Safari tap Share, then **Add to Home Screen**, "
                       "and it opens straight to your team.")
            st.caption("By Ryan Sandan · [rsandan.github.io](https://rsandan.github.io)")
    if SOURCE.is_demo and FALLBACK_REASON and FALLBACK_REASON != "FANTASY_DEMO is set":
        st.caption(f"Yahoo is unreachable, so this is a demo league. ({FALLBACK_REASON})")


def fmt_cat(value: float, cat_name: str) -> str:
    if value != value:
        return "–"
    return f"{value:.3f}".lstrip("0") if cat_name in ("FG%", "FT%") else f"{value:,.0f}"


def banked_score(a: pd.Series, b: pd.Series) -> tuple[int, int, int]:
    res = [compare(category_value(a, c), category_value(b, c), c) for c in CATEGORIES]
    return sum(r == 1 for r in res), sum(r == 0 for r in res), sum(r == 0.5 for r in res)


def oriented(sim, live: pd.DataFrame, me: str | None) -> dict:
    """Put `me` on the left when I'm in this matchup."""
    a = live[live["team_key"] == sim.team_a].iloc[0]
    b = live[live["team_key"] == sim.team_b].iloc[0]
    if me is not None and sim.team_b == me:
        return dict(ka=sim.team_b, kb=sim.team_a, a=b, b=a, pa=sim.p_loss, pb=sim.p_win, tie=sim.p_tie,
                    cat=1 - sim.cat_prob, proj_a=sim.proj_b, proj_b=sim.proj_a)
    return dict(ka=sim.team_a, kb=sim.team_b, a=a, b=b, pa=sim.p_win, pb=sim.p_loss, tie=sim.p_tie,
                cat=sim.cat_prob, proj_a=sim.proj_a, proj_b=sim.proj_b)


def scoreboard(o: dict, card: bool = True) -> str:
    """Apple-Sports-style matchup card: win probability up top, nine categories below."""
    started = o["a"]["status"] != "preevent"
    na, nb = NAMES.get(o["ka"], o["ka"]), NAMES.get(o["kb"], o["kb"])
    w, l, t = banked_score(o["a"], o["b"]) if started else (0, 0, 0)
    a_fav = o["pa"] >= o["pb"]
    gl = lambda r: "?" if pd.isna(r["remaining_games"]) else f"{r['remaining_games']:.0f}"
    tie = f" · tie {pct(o['tie'])}" if o["tie"] >= 0.01 else ""
    html = [
        f'<div class="hero" style="{"" if card else "background:transparent;padding:0"}">'
        '<div class="top">'
        f'<div><div class="tn">{e(na)}</div><div class="big {"" if a_fav else "dim"}">{pct(o["pa"])}</div></div>'
        f'<div class="mid"><div class="score">{f"{w}–{l}–{t}" if started else "vs"}</div>'
        f'<div class="cap">{"categories" if started else "not started"}</div></div>'
        f'<div class="r"><div class="tn">{e(nb)}</div><div class="big {"dim" if a_fav else ""}">{pct(o["pb"])}</div></div>'
        "</div>",
        f'<div class="bar"><span style="width:{(o["pa"] + o["tie"] / 2) * 100:.1f}%"></span></div>',
        f'<div class="meta">Chance to win the week · {gl(o["a"])} vs {gl(o["b"])} games left{tie}</div>',
    ]
    for cat in CATEGORIES:
        va, vb = category_value(o["a"], cat), category_value(o["b"], cat)
        pa, pb = o["proj_a"][cat.name], o["proj_b"][cat.name]
        shown_a, shown_b = (va, vb) if started else (pa, pb)
        lead = compare(shown_a, shown_b, cat)
        p = float(o["cat"][cat.name])
        proj = lambda v: f'<div class="proj">proj {fmt_cat(v, cat.name)}</div>' if started else ""
        html.append(
            '<div class="cat">'
            f'<div><div class="v {"lead" if lead == 1 else ""}">{fmt_cat(shown_a, cat.name)}</div>{proj(pa)}</div>'
            f'<div class="c"><div class="cn">{cat.name}</div>{ui.mini_bar(p)}<div class="p">{pct(p)}</div></div>'
            f'<div class="r"><div class="v r {"lead" if lead == 0 else ""}">{fmt_cat(shown_b, cat.name)}</div>'
            f'<div class="proj" style="text-align:right">{"proj " + fmt_cat(pb, cat.name) if started else ""}</div></div>'
            "</div>")
    html.append("</div>")
    return "".join(html)


def show_more(key: str, total: int, step: int) -> int:
    n = st.session_state.get(key, step)
    return min(n, total)


def more_button(key: str, shown: int, total: int, step: int):
    if shown < total:
        if st.button(f"Show {min(step, total - shown)} more", key=f"{key}_btn", type="tertiary",
                     width="stretch"):
            st.session_state[key] = shown + step
            st.rerun()


# =========================================================================== pages
def page_week():
    c = core()
    tw, model = c["tw"], c["model"]
    me = my_team()
    live = tw[tw["week"] == META.current_week]
    started = not live.empty and (live["status"] != "preevent").any()
    title_bar(f"Week {META.current_week}", e(META.name), live=started and live["status"].eq("midevent").any())

    if me is None:
        ui.section("Welcome")
        with st.container(border=False, key="onboard"):
            team_picker("me_onboard", "Which team is yours?")
        ui.footnote("Pick once and your matchup leads this screen. You can change it from the "
                    "profile button up top.")

    if live.empty:
        ui.footnote("No matchups yet. They show up once the league schedule is set.")
        return

    sims = week_sims(model, live, c["sig"])
    mine = [s for s in sims if me in (s.team_a, s.team_b)]
    others = [s for s in sims if me not in (s.team_a, s.team_b)]

    if mine:
        st.html(scoreboard(oriented(mine[0], live, me)))
        ui.footnote("Each matchup is played out 4,000 times from what's already banked plus every "
                    "team's remaining games. The bar under each category is your chance to win it.")

    ui.section("Your league" if mine else "Matchups")
    for s in others:
        o = oriented(s, live, None)
        na, nb = NAMES.get(o["ka"], ""), NAMES.get(o["kb"], "")
        with st.expander(f"{na}  {pct(o['pa'])}  ·  {pct(o['pb'])}  {nb}"):
            st.html(scoreboard(o, card=False))

    now = pd.Timestamp.now(tz=ET)
    with st.container(horizontal=True, vertical_alignment="center", key="refresh_row"):
        ui.footnote(f"Updated {now:%-I:%M %p} ET")
        if st.button("Refresh", icon=":material/refresh:", type="tertiary"):
            load_live_week.clear()
            week_sims.clear()
            st.rerun()


def _cat_summary(rates: pd.DataFrame, team_key: str) -> str:
    if team_key not in rates.index:
        return ""
    r = rates.loc[team_key].reindex(CATEGORY_NAMES).dropna()
    strong = r[r >= 0.6].sort_values(ascending=False).index[:3].tolist()
    weak = r[r <= 0.4].sort_values().index[:2].tolist()
    parts = []
    if strong:
        parts.append("Strong " + ", ".join(strong))
    if weak:
        parts.append("Weak " + ", ".join(weak))
    return " · ".join(parts) or "Balanced"


def page_standings():
    c = core()
    tw, model, ap = c["tw"], c["model"], c["ap"]
    me = my_team()
    title_bar("Standings", e(META.name))
    summ = ap["summary"]
    if summ.empty:
        ui.footnote("Standings show up after the first week is complete.")
        return

    view = st.segmented_control("View", ["Standings", "Power", "Playoffs"], default="Standings",
                                required=True, label_visibility="collapsed", key="stand_view",
                                width="stretch")
    t = summ.set_index("team_key")
    head = META.scoring_type == "head"

    if view == "Standings":
        rec, rp = ("cat_record", "cat_pct") if head else ("h2h_record", "h2h_pct")
        luck = t["luck_category"] if head else t["luck_matchup"]
        order = t.sort_values(rp, ascending=False).index
        rows = [ui.row(e(NAMES.get(k, k)),
                       f"All-play {t.at[k, 'ap_pct']:.3f} · luck {signed(luck[k], 3)}".replace("0.", "."),
                       t.at[k, rec], f"{t.at[k, rp]:.3f}".lstrip("0"), lead=ui.rank(i), me=k == me)
                for i, k in enumerate(order, 1)]
        ui.group(rows)
        ui.footnote("<b>All-play</b> is your record if you played every team every week. "
                    "<b>Luck</b> is the gap between that and your real record: positive means a soft schedule.")

    elif view == "Power":
        pr = ratings(model, c["sig"])
        form = power.recent_form(ap["weekly"])
        order = pr.reindex(t.index).sort_values(ascending=False).index
        rates = ap["category_rates"]
        rows = [ui.row(e(NAMES.get(k, k)), e(_cat_summary(rates, k)), pct(pr.get(k)),
                       f"form {form.get(k, float('nan')):.3f}".replace("0.", "."),
                       lead=ui.rank(i), me=k == me, extra=ui.mini_bar(pr.get(k, 0)))
                for i, k in enumerate(order, 1)]
        ui.group(rows)
        ui.footnote("<b>Power</b> is each team's chance to beat an average league opponent in a normal "
                    "week, from the matchup model. It ignores the schedule. <b>Form</b> weights recent weeks.")

    else:
        future = load_future_weeks(KEY, META.current_week + 1, META.playoff_start_week - 1) \
            if not SOURCE.is_demo else tw[tw["status"] == "preevent"]
        live = tw[tw["week"] == META.current_week]
        fut = pd.concat([live, future], ignore_index=True) if not future.empty else live
        odds = season_sim(model, tw, fut, c["sig"]).set_index("team_key")
        order = odds["playoff_odds"].sort_values(ascending=False).index
        rows = []
        for i, k in enumerate(order, 1):
            if i == META.num_playoff_teams + 1:
                rows.append('<div class="cut">Playoff line</div>')
            rows.append(ui.row(e(NAMES.get(k, k)),
                               f"Proj. {odds.at[k, 'exp_win_pct']:.3f}".replace("0.", ".") + f" · #1 seed {pct(odds.at[k, 'top_seed_odds'])}",
                               pct(odds.at[k, "playoff_odds"]), lead=ui.rank(i), me=k == me,
                               extra=ui.mini_bar(odds.at[k, "playoff_odds"])))
        ui.group(rows)
        ui.footnote(f"The rest of the season played out 2,000 times on the real schedule. "
                    f"Top {META.num_playoff_teams} make it; exact ties are broken at random.")


WINDOWS = {"Blended": ("blend", 0), "This season": ("cur", 0), "Last 15": ("cur", 15),
           "Last 30": ("cur", 30), "Last season": ("prior", 0)}


def window_players(label: str) -> pd.DataFrame:
    kind, n = WINDOWS[label]
    if kind == "blend":
        return blended_players(SEASON)
    df = load_players(SEASON if kind == "cur" else PRIOR, n)
    return df.assign(GP_TOT=df["GP"]) if not df.empty else df


def _top_cats(r: pd.Series, n: int = 2) -> str:
    z = r[[c for c in Z_COLS if c in r.index]].astype(float)
    return " ".join(c.replace("z_", "") for c in z.sort_values(ascending=False).index[:n] if z[c] > 0.3)


def page_players():
    c = core()
    me = my_team()
    title_bar("Players")
    view = st.segmented_control("View", ["Pickups", "Rankings"], default="Pickups", required=True,
                                label_visibility="collapsed", key="players_view", width="stretch")
    photos = not SOURCE.is_demo
    if view == "Pickups":
        _pickups(c, me, photos)
    else:
        _rankings(photos)


def _pickups(c: dict, me: str | None, photos: bool):
    tw, model = c["tw"], c["model"]
    live = tw[tw["week"] == META.current_week]
    with st.popover("Ignore categories", icon=":material/tune:", type="tertiary"):
        punts = st.pills("Ignore", CATEGORY_NAMES, selection_mode="multi", key="stream_punts",
                         label_visibility="collapsed")
    weights = trade.context_weights(None, punts)
    week_end = TODAY
    ctx = []
    row = live[live["team_key"] == me] if me else live.iloc[0:0]
    if not row.empty:
        r = row.iloc[0]
        sims = week_sims(model, live, c["sig"])
        mine = [s for s in sims if me in (s.team_a, s.team_b)]
        if mine:
            o = oriented(mine[0], live, me)
            weights = trade.context_weights(o["cat"], punts, floor=0.1)
            swing = [k for k, p in o["cat"].items() if 0.25 <= p <= 0.75 and k not in punts]
            ctx.append(ui.row(f"vs {e(NAMES.get(o['kb'], ''))}", "Your chance to win the week", pct(o["pa"])))
            ctx.append(ui.row("Swing categories", e(", ".join(swing) or "None, it's mostly decided")))
        if isinstance(r["week_end"], str) and r["week_end"]:
            week_end = date.fromisoformat(r["week_end"])
    if ctx:
        ui.group(ctx)
    elif me is None:
        ui.footnote("Set your team with the profile button and pickups get weighted toward the "
                    "categories still in play in your matchup.")

    games_left = nba_data.games_by_team(load_schedule(SEASON), TODAY, week_end)
    fa = load_free_agents(KEY)
    if fa.empty or c["vals"] is None:
        ui.footnote("No free agents or player stats yet.")
        return
    board = trade.streaming_board(fa, c["vals"].table, games_left, weights, c["vals"].replacement)
    board = board[board["games_left"] > 0]
    ui.section(f"Best adds through {week_end:%a %b %-d}")
    n = show_more("pick_n", len(board), 15)
    rows = []
    for _, p in board.head(n).iterrows():
        pos = str(p.get("eligible_positions", "")).replace(",", "/")
        rows.append(ui.row(
            e(p["name"]), e(f"{p.get('TEAM') or ''} · {pos} · {int(p['games_left'])} game{'s' if p['games_left'] != 1 else ''} left"),
            signed(p["stream_score"]), f"{signed(p['weighted_per_game'], 2)} /game",
            lead=ui.avatar(p["name"], p.get("PLAYER_ID"), photos),
            e1_class=ui.sign_class(p["stream_score"])))
    ui.group(rows)
    more_button("pick_n", n, len(board), 15)
    ui.footnote("Score = how much better than a waiver-level player each game is, times games left "
                "this week, tilted toward your swing categories.")


def _rankings(photos: bool):
    with st.container(horizontal=True, vertical_alignment="center", key="searchrow"):
        q = st.text_input("Search", placeholder="Search players", label_visibility="collapsed",
                          key="rank_q")
        with st.popover("", icon=":material/tune:", key="rank_filters"):
            window = st.radio("Stats", list(WINDOWS), key="rank_window",
                              help="Blended leans on last season until a player has ~15 games in.")
            who = st.segmented_control("Show", ["All", "Free agents", "Rostered"], default="All",
                                       required=True, key="rank_who")
            punts = st.pills("Punt", CATEGORY_NAMES, selection_mode="multi", key="rank_punts")
    players = window_players(window)
    if players.empty:
        ui.footnote("No stats for this window yet. Try Blended or Last season.")
        return
    res = valuation(players, tuple(punts), META.num_teams, META.roster_size)
    table = res.table
    ros = matched_rosters(KEY, table)
    owner = dict(zip(ros["PLAYER_ID"], ros["team_key"])) if not ros.empty else {}
    table = table.assign(owner=table["PLAYER_ID"].map(owner))
    if who == "Free agents":
        table = table[table["owner"].isna()]
    elif who == "Rostered":
        table = table[table["owner"].notna()]
    if q:
        table = table[table["PLAYER_NAME"].str.contains(q, case=False, regex=False)]
    sub = [window] + ([f"punting {', '.join(punts)}"] if punts else [])
    ui.section(" · ".join(sub))
    n = show_more("rank_n", len(table), 25)
    rows = []
    for _, p in table.head(n).iterrows():
        own = NAMES.get(p["owner"], "Free agent") if p["owner"] == p["owner"] else "Free agent"
        rows.append(ui.row(
            e(p["PLAYER_NAME"]), e(f"{p.get('TEAM', '')} · {own}"),
            signed(p["value"], 2), e(_top_cats(p)), lead=ui.rank(int(p["rank"])),
            e1_class=ui.sign_class(p["value"])))
    if rows:
        ui.group(rows)
    else:
        ui.footnote("No players match.")
    more_button("rank_n", n, len(table), 25)
    ui.footnote("Value per game, in z-scores against the players worth rostering in a "
                f"{META.num_teams}-team league. FG% and FT% count shot volume.")


def _roster_options(team_key: str, ros: pd.DataFrame, vals: pd.DataFrame) -> dict:
    if not ros.empty and team_key in set(ros["team_key"]):
        o = ros[(ros["team_key"] == team_key) & ros["PLAYER_ID"].notna()]
        return dict(zip(o["PLAYER_ID"], o["name"]))
    top = vals.head(400)
    return dict(zip(top["PLAYER_ID"], top["PLAYER_NAME"]))


def page_trade():
    c = core()
    ap = c["ap"]
    me = my_team()
    title_bar("Trade")
    if me is None:
        ui.section("First, your team")
        team_picker("me_trade", "Your team")
        return
    punts = st.session_state.get("trade_punts") or []
    res = valuation(c["players"], tuple(punts), META.num_teams, META.roster_size)
    ros = matched_rosters(KEY, res.table)

    ui.section("You send")
    mine_opts = _roster_options(me, ros, res.table)
    give = st.multiselect("You send", list(mine_opts), format_func=lambda i: mine_opts.get(i, str(i)),
                          placeholder="Players from your roster", label_visibility="collapsed", key="give",
                          select_all=False, max_selections=5)
    ui.section("Trade partner")
    others = [k for k in NAMES if k != me]
    them = st.pills("Partner", others, format_func=NAMES.get, key="partner",
                    label_visibility="collapsed")
    if them is None:
        return
    ui.section("You receive")
    their_opts = _roster_options(them, ros, res.table)
    get = st.multiselect("You receive", list(their_opts), format_func=lambda i: their_opts.get(i, str(i)),
                         placeholder=f"Players from {NAMES[them]}", label_visibility="collapsed",
                         key=f"get_{them}", select_all=False, max_selections=5)
    with st.expander("Punting any categories?"):
        st.pills("Punts", CATEGORY_NAMES, selection_mode="multi", key="trade_punts",
                 label_visibility="collapsed")
    if not give and not get:
        return

    rates = ap["category_rates"]
    w_me = trade.context_weights(rates.loc[me] if me in rates.index else None, punts)
    w_them = trade.context_weights(rates.loc[them] if them in rates.index else None)
    mine = trade.evaluate_trade(res.table, give, get, res.replacement, w_me)
    theirs = trade.evaluate_trade(res.table, get, give, res.replacement, w_them)
    head, _, body = mine.verdict.partition(":")
    both = mine.weighted_delta > 0 and theirs.weighted_delta > 0
    if both:
        body += ". They come out ahead too, so it should be an easy sell."
    st.html(
        '<div class="verdict">'
        f'<div class="h">{e(head)}</div><div class="b">{e(body.strip().capitalize())}</div>'
        '<div class="nums">'
        f'<div class="n"><div class="k">You</div><div class="v {ui.sign_class(mine.weighted_delta)}">'
        f'{signed(mine.weighted_delta)}</div></div>'
        f'<div class="n"><div class="k">{e(NAMES[them])}</div><div class="v {ui.sign_class(theirs.weighted_delta)}">'
        f'{signed(theirs.weighted_delta)}</div></div>'
        "</div></div>")
    ui.section("What changes for you, by category")
    chips = []
    for cat in CATEGORY_NAMES:
        d = float(mine.table.at[cat, "delta"])
        off = mine.table.at[cat, "weight"] == 0
        chips.append(f'<div class="chip{" off" if off else ""}"><div class="k">{cat}</div>'
                     f'<div class="v {"" if off else ui.sign_class(d)}">{signed(d)}</div></div>')
    st.html('<div class="chips">' + "".join(chips) + "</div>")
    ui.footnote("Per-game z-score change. The headline numbers weight each category by how close "
                "that team is to 50/50 in it, and uneven trades are charged for the roster spot.")


# ======================================================================== navigation
PAGES = [
    st.Page(page_week, title="This Week", icon=":material/sports_basketball:", default=True),
    st.Page(page_standings, title="Standings", icon=":material/leaderboard:", url_path="standings"),
    st.Page(page_players, title="Players", icon=":material/person_search:", url_path="players"),
    st.Page(page_trade, title="Trade", icon=":material/swap_horiz:", url_path="trade"),
]
nav = st.navigation(PAGES, position="hidden")
ui.inject()

me = my_team()
qp = {"team": _short(me)} if me else None
if me:
    st.query_params["team"] = _short(me)
with st.container(horizontal=True, key="tabbar"):
    for i, p in enumerate(PAGES):
        with st.container(key=f"tab_{i}"):
            st.page_link(p, label=p.title, icon=p.icon, query_params=qp)
active = next((i for i, p in enumerate(PAGES) if p.title == nav.title), 0)
st.html(f"<style>.st-key-tab_{active} a[data-testid='stPageLink-NavLink']"
        "{color:var(--blue) !important}</style>")

nav.run()
