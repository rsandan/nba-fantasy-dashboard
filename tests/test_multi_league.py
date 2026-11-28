"""Any Yahoo league: scoring formats, sign-in, isolation between leagues, every page.

The leagues come from tests/fake_yahoo.py: generated data pushed through Yahoo's JSON
shapes for each format and parsed back with the real parsers.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fantasy import auth, categories as C, matchup_model as mm
from fantasy.valuation import value_players
from fantasy.yahoo_client import parse_user_leagues, scoring_from_raw_settings
from fantasy import fake_yahoo as fy

ROOT = Path(__file__).resolve().parents[1]


# ============================================================ scoring formats
EXPECTED = {
    "love_island": (["FG%", "FT%", "3PT%", "PTS", "REB", "AST", "STL", "BLK", "TO"], ()),
    "standard_3ptm": (["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"], ()),
    "eight_cat": (["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK"], ()),
    "eleven_cat_one_win": (["FG%", "FT%", "3PTM", "3PT%", "PTS", "REB", "AST", "STL", "BLK", "TO", "A/T"], ()),
    "with_dd": (["FGM", "FG%", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"], ("DD",)),
    "roto": (["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"], ()),
}


@pytest.mark.parametrize("slug", list(EXPECTED))
def test_category_leagues_parse_their_own_categories(slug):
    sc = scoring_from_raw_settings(fy.settings_json(slug))
    names, unsupported = EXPECTED[slug]
    assert sc.names == names
    assert sc.unsupported == unsupported
    assert sc.by_name.get("TO") is None or sc.by_name["TO"].higher_is_better is False
    assert sc.fmt == fy.LEAGUES[slug]["fmt"]


def test_points_league_reads_modifiers_and_flags_double_doubles():
    sc = scoring_from_raw_settings(fy.settings_json("points"))
    assert sc.is_points and sc.names == ["FPTS"] and sc.unsupported == ("DD",)
    w = dict(sc.categories[0].weights)
    assert w == {"PTS": 1.0, "REB": 1.2, "AST": 1.5, "STL": 3.0, "BLK": 3.0, "TOV": -1.0, "FG3M": 0.5}
    assert sc.weekly and not sc.each_category_counts


def test_format_flags():
    head = scoring_from_raw_settings(fy.settings_json("standard_3ptm"))
    one = scoring_from_raw_settings(fy.settings_json("eleven_cat_one_win"))
    roto = scoring_from_raw_settings(fy.settings_json("roto"))
    assert head.each_category_counts and head.weekly
    assert not one.each_category_counts and one.weekly
    assert not roto.weekly
    assert len({head.sig, one.sig, roto.sig, C.DEFAULT.sig}) == 4


def test_3ptm_league_scoreboards_have_no_attempts_and_model_still_works():
    lg = fy.FakeLeague("standard_3ptm")
    wk = lg.scoreboard(3)
    assert wk["FG3A"].isna().all() and wk["FG3M"].notna().all()   # Yahoo doesn't send 3PA here
    sc = lg.meta().scoring
    with C.scoped(sc):
        comps = mm.components_for()
        assert "FG3M" in comps and "FG3A" not in comps and "FG3P" not in comps
        tw = pd.concat([lg.scoreboard(w) for w in range(1, 10)], ignore_index=True)
        model = mm.fit_team_model(tw)
        a, b = model.teams()[:2]
        sim = mm.simulate_matchup(model, a, b, n_sims=1500)
        assert list(sim.cat_prob.index) == sc.names
        assert abs(sim.p_win + sim.p_tie + sim.p_loss - 1) < 1e-9
        assert sim.cat_prob.between(0, 1).all()


def test_points_league_banked_points_match_yahoo_team_points():
    lg = fy.FakeLeague("points")
    wk = lg.scoreboard(2)
    cat = lg.meta().scoring.categories[0]
    for _, r in wk.iterrows():
        assert C.category_value(r, cat) == pytest.approx(r["team_points"], abs=0.06)


def test_points_league_simulation_is_one_category_and_coherent():
    lg = fy.FakeLeague("points")
    with C.scoped(lg.meta().scoring):
        tw = pd.concat([lg.scoreboard(w) for w in range(1, 11)], ignore_index=True)
        model = mm.fit_team_model(tw)
        sims = mm.live_matchups(model, tw[tw["week"] == 10], n_sims=1500)
        assert sims and all(list(s.cat_prob.index) == ["FPTS"] for s in sims)
        for s in sims:   # one category: win prob is just P(more points)
            assert s.p_win == pytest.approx(s.cat_prob["FPTS"] - 0.5 * s.p_tie, abs=1e-9)


def test_unreported_components_fall_back_to_priors_not_nan():
    lg = fy.FakeLeague("eight_cat")
    with C.scoped(lg.meta().scoring):
        tw = pd.concat([lg.scoreboard(w) for w in range(1, 10)], ignore_index=True)
        model = mm.fit_team_model(tw)
        assert np.isfinite(model.mu.values).all() and np.isfinite(model.cov).all()


def test_user_leagues_parse():
    leagues = parse_user_leagues(fy.users_leagues_json(["points", "roto", "love_island"]))
    assert {l["league_key"] for l in leagues} == {fy.league_key(s) for s in ("points", "roto", "love_island")}
    assert {l["scoring_type"] for l in leagues} == {"headpoint", "roto", "head"}


# ============================================================ isolation between leagues
def test_active_scoring_never_leaks_between_threads():
    """Two leagues valued at the same time on different threads (= two browser sessions)
    must get exactly what each gets alone."""
    players = fy.FakeLeague("tiny").b["players"]
    s1 = scoring_from_raw_settings(fy.settings_json("love_island"))
    s2 = scoring_from_raw_settings(fy.settings_json("points"))
    with C.scoped(s1):
        ref1 = value_players(players).table.set_index("PLAYER_ID")["value"]
    with C.scoped(s2):
        ref2 = value_players(players).table.set_index("PLAYER_ID")["value"]
    assert not np.allclose(ref1.sort_index(), ref2.reindex(ref1.index).sort_index())
    errors = []

    def work(sc, ref):
        try:
            C.use(sc)
            for _ in range(6):
                got = value_players(players).table.set_index("PLAYER_ID")["value"]
                assert np.allclose(got.reindex(ref.index), ref), "scoring leaked across threads"
                time.sleep(0.001)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=((s1, ref1) if i % 2 else (s2, ref2))) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors


# ============================================================ sign in with Yahoo
CFG = auth.OAuthConfig("cid", "csecret", "https://example.test/", "x" * 32)


def test_state_round_trip_and_rejections():
    s = auth.make_state(CFG, now=1000)
    auth.verify_state(CFG, s, now=1100)
    with pytest.raises(auth.AuthError):
        auth.verify_state(CFG, s, now=1000 + auth.STATE_TTL + 1)            # expired
    with pytest.raises(auth.AuthError):
        auth.verify_state(CFG, s[:-2] + "AA", now=1100)                    # forged signature
    other = auth.OAuthConfig("cid", "csecret", "https://example.test/", "y" * 32)
    with pytest.raises(auth.AuthError):
        auth.verify_state(other, s, now=1100)                              # another server's state
    with pytest.raises(auth.AuthError):
        auth.verify_state(CFG, "garbage", now=1100)


def test_code_exchange_and_refresh_rotation():
    seen = []

    def post(url, data, headers):
        seen.append((url, dict(data), headers["Authorization"]))
        if data["grant_type"] == "authorization_code":
            assert data["code"] == "abc" and data["redirect_uri"] == CFG.redirect_uri
            return {"access_token": "A1", "refresh_token": "R1", "expires_in": 3600, "xoauth_yahoo_guid": "G"}
        assert data["refresh_token"] == "R1"
        return {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}

    t = auth.exchange_code(CFG, "abc", post)
    assert (t.access_token, t.refresh_token, t.guid) == ("A1", "R1", "G")
    assert seen[0][2].startswith("Basic ") and seen[0][0] == auth.TOKEN_URL
    u = auth.UserSession(CFG, t, post=post)
    assert u.session.headers["Authorization"] == "Bearer A1"
    u.tokens.expires_at = 0                       # force expiry
    u.ensure_fresh()
    assert u.session.headers["Authorization"] == "Bearer A2" and u.rotated
    assert "A2" not in repr(u) and "R2" not in repr(u)


def test_remember_me_cookie_is_encrypted_and_tamper_proof():
    t = auth.Tokens("A", "REFRESH-SECRET", 0, "G")
    c = auth.seal(CFG, t)
    assert "REFRESH-SECRET" not in c
    assert auth.unseal(CFG, c) == {"r": "REFRESH-SECRET", "g": "G"}
    assert auth.unseal(CFG, c[:-4] + "AAAA") is None
    assert auth.unseal(auth.OAuthConfig("c", "s", "r", "z" * 32), c) is None
    assert auth.unseal(CFG, "") is None


def test_config_requires_every_piece():
    assert auth.OAuthConfig.from_env({}) is None
    full = {"YAHOO_CLIENT_ID": "i", "YAHOO_CLIENT_SECRET": "s", "YAHOO_REDIRECT_URI": "https://x/",
            "APP_SECRET": "k" * 32}
    assert auth.OAuthConfig.from_env(full) is not None
    assert auth.OAuthConfig.from_env({**full, "APP_SECRET": "short"}) is None


# ============================================================ every page, every league
pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

WEEKLY_PAGES = ["page_home", "page_week", "page_standings", "page_players", "page_trade"]
SEASON_PAGES = ["page_standings", "page_players", "page_trade"]


def _run(page: str, slug: str | None, monkeypatch, team: str | None = "2", query_league: str | None = None,
         slugs: list[str] | None = None):
    monkeypatch.setenv("FANTASY_DEMO", "1")
    seed = ("from fantasy import fake_yahoo as _fy\n"
            f"_fy.seed_session(st.session_state, {slugs!r})\n")
    src = (ROOT / "app.py").read_text()
    src = src.replace("CTX = accounts.resolve()", seed + "CTX = accounts.resolve()", 1)
    src = src.replace("nav.run()", f"{page}()")
    script = ROOT / f"_mltest_{page}_{slug}.py"
    script.write_text(src)
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        if slug:
            at.query_params["league"] = fy.league_key(slug)
        if query_league:
            at.query_params["league"] = query_league
        if team:
            at.query_params["team"] = team
        at.run()
        return at
    finally:
        script.unlink(missing_ok=True)


def _text(at) -> str:
    out = []
    for kind in ("markdown", "caption", "text", "html"):
        for el in getattr(at, kind, []):
            out.append(str(getattr(el, "value", "") or getattr(el, "body", "")))
    try:
        out += [str(h.proto.body) for h in at.get("html")]
    except Exception:
        pass
    return "\n".join(out)


CASES = [(slug, page) for slug, cfg in fy.LEAGUES.items()
         for page in (SEASON_PAGES if cfg["fmt"] in ("roto", "point") else WEEKLY_PAGES)]


@pytest.mark.parametrize("slug,page", CASES)
def test_every_page_renders_in_every_league(slug, page, monkeypatch):
    at = _run(page, slug, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.main.children) > 2


@pytest.mark.parametrize("slug", ["standard_3ptm", "points", "eleven_cat_one_win"])
def test_this_week_shows_this_leagues_categories(slug, monkeypatch):
    at = _run("page_week", slug, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    html = _text(at)
    sc = scoring_from_raw_settings(fy.settings_json(slug))
    for name in sc.names:
        assert name in html, f"{name} missing from {slug}"
    if slug == "standard_3ptm":
        assert "3PT%" not in html.split("Matchups")[0]   # Love Island's category doesn't leak in


def test_league_not_on_your_account_is_refused(monkeypatch):
    # Signed in to two leagues; a link to a third (real-looking) league must not open it.
    at = _run("page_standings", None, monkeypatch, query_league=fy.league_key("roto"),
              slugs=["points", "tiny"])
    assert not at.exception
    assert at.session_state["league"] in (fy.league_key("points"), fy.league_key("tiny"))
    assert at.query_params.get("league") != [fy.league_key("roto")]


# ============================================================ sign-in, end to end in the app
OAUTH_ENV = {"YAHOO_CLIENT_ID": "cid", "YAHOO_CLIENT_SECRET": "csecret",
             "YAHOO_REDIRECT_URI": "https://example.test/", "APP_SECRET": "x" * 32}


def _app(monkeypatch, env: dict, page: str = "page_standings"):
    import streamlit as st
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    st.cache_resource.clear()            # oauth_config()/featured() are process-wide
    src = (ROOT / "app.py").read_text().replace("nav.run()", f"{page}()")
    script = ROOT / f"_authtest_{page}.py"
    script.write_text(src)
    return script


def test_yahoo_callback_signs_in_and_scrubs_the_url(monkeypatch):
    from fantasy import accounts
    calls = {}

    def fake_post(url, data, headers):
        calls["data"] = dict(data)
        return {"access_token": "A", "refresh_token": "R", "expires_in": 3600, "xoauth_yahoo_guid": "G"}

    monkeypatch.setattr(auth, "_http_post", fake_post)
    monkeypatch.setattr(accounts, "user_leagues",
                        lambda user: parse_user_leagues(fy.users_leagues_json(["points", "tiny"])))
    slug_of = {fy.league_key(s): s for s in fy.LEAGUES}
    monkeypatch.setattr(accounts, "open_league", lambda user, key: slug_of[key])
    monkeypatch.setattr(accounts, "YahooLeague", lambda slug: slug)
    monkeypatch.setattr(accounts, "Source", lambda slug, _bundle: fy.FakeSource(slug))
    script = _app(monkeypatch, {**OAUTH_ENV, "FANTASY_DEMO": "1"})
    try:
        state = auth.make_state(auth.OAuthConfig.from_env(dict(OAUTH_ENV)))
        at = AppTest.from_file(str(script), default_timeout=300)
        at.query_params["code"] = "the-code"
        at.query_params["state"] = state
        at.run()
        assert calls["data"]["code"] == "the-code"
        assert "code" not in at.query_params and "state" not in at.query_params
        assert not at.exception, [e.value for e in at.exception]
        assert at.session_state["league"] == fy.league_key("points")
        assert "user" in at.session_state
        assert at.query_params.get("league") == [fy.league_key("points")] or \
            at.query_params.get("league") == fy.league_key("points")
    finally:
        script.unlink(missing_ok=True)
        import streamlit as st
        st.cache_resource.clear()


def test_forged_callback_is_rejected(monkeypatch):
    monkeypatch.setattr(auth, "_http_post", lambda *a: pytest.fail("must not call Yahoo"))
    script = _app(monkeypatch, {**OAUTH_ENV, "FANTASY_DEMO": "1"})
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        at.query_params["code"] = "stolen"
        at.query_params["state"] = "1.2.3.forged"
        at.run()
        assert not at.exception
        assert "user" not in at.session_state
        assert "code" not in at.query_params
    finally:
        script.unlink(missing_ok=True)
        import streamlit as st
        st.cache_resource.clear()


def test_signed_out_visitor_cannot_open_a_private_league(monkeypatch):
    script = _app(monkeypatch, {"FANTASY_DEMO": "1"})
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        at.query_params["league"] = fy.league_key("standard_3ptm")
        at.run()
        assert not at.exception
        assert at.session_state["league"] == "demo"        # featured (demo here), not the private one
    finally:
        script.unlink(missing_ok=True)


def test_landing_page_when_nothing_is_featured(monkeypatch):
    script = _app(monkeypatch, {**OAUTH_ENV, "FANTASY_DEMO": "1", "FEATURED_LEAGUE": "0"})
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        at.run()
        assert not at.exception
        html = "\n".join(str(h.proto.body) for h in at.get("html"))
        assert "Sign in with Yahoo" in html and "api.login.yahoo.com/oauth2/request_auth" in html
        assert "client_secret" not in html and "csecret" not in html
    finally:
        script.unlink(missing_ok=True)
        import streamlit as st
        st.cache_resource.clear()
