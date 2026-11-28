"""Who is this visitor, and which league are they looking at? (the Streamlit layer)

Three ways in:

* **Featured league** (Love Island): the server's own Yahoo token, no sign-in. This is
  how the site always worked, so existing links and home-screen icons keep working.
* **Signed in**: "Sign in with Yahoo" gives a per-person token (see ``auth``). They can
  open any NBA league *their* Yahoo account is in, and nothing else.
* **Demo**: a synthetic league, for anyone who wants to look around first.

Data caches are keyed by league, so two members of the same league share one copy of
the Yahoo data, while the token that fetched it never leaves its own browser session.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import streamlit as st

from . import auth, demo
from .data_source import Source, connect, demo_requested
from .yahoo_client import YahooLeague, open_league, user_leagues

log = logging.getLogger(__name__)
DEMO_KEY = "demo"


@dataclass
class Context:
    source: Source
    league_key: str                 # cache key: the Yahoo league key, or "demo"
    kind: str                       # "featured" | "user" | "demo"
    user: auth.UserSession | None
    fallback_reason: str | None = None


# ------------------------------------------------------------------ config
@st.cache_resource(show_spinner=False)
def oauth_config() -> auth.OAuthConfig | None:
    return auth.OAuthConfig.from_env()


def featured_enabled() -> bool:
    return os.getenv("FEATURED_LEAGUE", "1").strip().lower() not in ("0", "false", "no", "off")


@st.cache_resource(show_spinner=False)
def featured() -> dict:
    """The server-token league (or the demo, if FANTASY_DEMO is set / Yahoo is down)."""
    source, reason = connect()
    key = DEMO_KEY if source.is_demo else source.meta().league_key
    return {"source": source, "reason": reason, "key": key}


@st.cache_resource(show_spinner=False)
def demo_source() -> Source:
    return Source(None, demo.make_league())


# ------------------------------------------------------------------ cookie jar
def _cookie_js(value: str | None) -> str:
    if value is None:
        assign = f"{auth.COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax"
        return f"document.cookie = {json.dumps(assign)};"
    assign = f"{auth.COOKIE_NAME}={value}; path=/; max-age={auth.COOKIE_MAX_AGE}; SameSite=Lax"
    return (f"document.cookie = {json.dumps(assign)} + "
            "(location.protocol === 'https:' ? '; Secure' : '');")


def flush_cookie():
    """Write or clear the remember-me cookie, if this run changed it. Streamlit can't set
    response headers, so a zero-height component sets it from the page (same origin)."""
    if "_cookie" not in st.session_state:
        return
    value = st.session_state.pop("_cookie")
    import streamlit.components.v1 as components
    with st.container(key="cookiejar"):
        components.html(f"<script>{_cookie_js(value)}</script>", height=0)


# ------------------------------------------------------------------ sign-in
def _start_user(cfg: auth.OAuthConfig, tokens: auth.Tokens) -> auth.UserSession:
    user = auth.UserSession(cfg, tokens)
    user.leagues = user_leagues(user)
    st.session_state["user"] = user
    st.session_state["_cookie"] = auth.seal(cfg, tokens)
    return user


def handle_callback(cfg: auth.OAuthConfig | None):
    """Yahoo just sent the browser back with ?code=...&state=... (or ?error=...)."""
    qp = st.query_params
    if not any(k in qp for k in ("code", "state", "error")):
        return
    code, state, error = qp.get("code"), qp.get("state"), qp.get("error")
    for k in ("code", "state", "error", "error_description"):
        if k in qp:
            del st.query_params[k]          # never leave a sign-in code in the address bar
    if cfg is None:
        return
    if error:
        st.session_state["auth_msg"] = ("Sign-in was cancelled." if error == "access_denied"
                                        else "Yahoo couldn't finish the sign-in. Please try again.")
        return
    try:
        auth.verify_state(cfg, state or "")
        user = _start_user(cfg, auth.exchange_code(cfg, code or ""))
        st.session_state.pop("league", None)
        st.session_state["auth_msg"] = (None if user.leagues else
                                        "You're signed in, but this Yahoo account isn't in any NBA leagues.")
    except auth.AuthError as exc:
        st.session_state["auth_msg"] = str(exc)
    except Exception as exc:                 # Yahoo hiccup: say so, keep the site up
        log.warning("Sign-in failed: %s", type(exc).__name__)
        st.session_state["auth_msg"] = "Yahoo couldn't finish the sign-in. Please try again."


def restore(cfg: auth.OAuthConfig | None):
    """Signed in on an earlier visit? Turn the cookie back into a session (once per visit)."""
    if cfg is None or "user" in st.session_state or st.session_state.get("_restored"):
        return
    st.session_state["_restored"] = True
    try:
        raw = st.context.cookies.get(auth.COOKIE_NAME)
    except Exception:
        raw = None
    if not isinstance(raw, str) or not raw:
        return
    data = auth.unseal(cfg, raw)
    if not data or not data.get("r"):
        st.session_state["_cookie"] = None
        return
    try:
        tokens = auth.refresh_tokens(cfg, data["r"])
        tokens.guid = tokens.guid or data.get("g", "")
        _start_user(cfg, tokens)
    except Exception as exc:                 # revoked or expired: sign out quietly
        log.info("Remembered sign-in no longer valid (%s)", type(exc).__name__)
        st.session_state["_cookie"] = None


def sign_in_url(cfg: auth.OAuthConfig) -> str:
    return auth.authorize_url(cfg, auth.make_state(cfg))


def sign_out():
    for k in ("user", "league", "me", "sources"):
        st.session_state.pop(k, None)
    st.session_state["_cookie"] = None
    if "league" in st.query_params:
        del st.query_params["league"]
    if "team" in st.query_params:
        del st.query_params["team"]


# ------------------------------------------------------------------ league choice
def choose_league(key: str):
    st.session_state["league"] = key
    for k in ("me",):
        st.session_state.pop(k, None)
    if "team" in st.query_params:
        del st.query_params["team"]


def user_league_keys(user: auth.UserSession | None) -> list[str]:
    return [lg["league_key"] for lg in (user.leagues if user else [])]


def resolve() -> Context | None:
    """This run's league, or None when there's nothing to show yet (landing page)."""
    cfg = oauth_config()
    handle_callback(cfg)
    restore(cfg)
    user: auth.UserSession | None = st.session_state.get("user")
    if user is not None:
        try:
            user.ensure_fresh()
            if user.rotated:
                st.session_state["_cookie"] = auth.seal(cfg, user.tokens)
                user.rotated = False
        except Exception:
            st.session_state["auth_msg"] = "Your Yahoo sign-in expired. Please sign in again."
            sign_out()
            user = None

    feat = featured() if featured_enabled() else None
    allowed = user_league_keys(user)
    wanted = st.query_params.get("league") or st.session_state.get("league")
    if wanted and wanted not in allowed and wanted != DEMO_KEY and not (feat and wanted == feat["key"]):
        # A shared link to someone else's league, or a league this account left.
        st.session_state["auth_msg"] = ("That league is private. Sign in with a Yahoo account that's in it."
                                        if user is None else "Your Yahoo account isn't in that league.")
        wanted = None
        st.session_state.pop("league", None)
        if "league" in st.query_params:
            del st.query_params["league"]
    if not wanted:
        current = [lg["league_key"] for lg in (user.leagues if user else []) if not lg.get("is_finished")]
        wanted = (current or allowed or [feat["key"] if feat else None])[0]
    if not wanted:
        return None
    st.session_state["league"] = wanted

    if wanted == DEMO_KEY:
        return Context(demo_source(), DEMO_KEY, "demo", user, None)
    if feat and wanted == feat["key"]:
        if "league" in st.query_params and wanted == feat["key"]:
            del st.query_params["league"]   # keep the featured league's links clean
        return Context(feat["source"], feat["key"], "featured", user, feat["reason"])
    sources = st.session_state.setdefault("sources", {})
    if wanted not in sources:
        sources[wanted] = Source(YahooLeague(open_league(user, wanted)), None)
    st.query_params["league"] = wanted
    return Context(sources[wanted], wanted, "user", user, None)


def league_options(user: auth.UserSession | None) -> list[tuple[str, str]]:
    """(key, label) for the league picker: the user's leagues, the featured league, the demo."""
    out = []
    for lg in (user.leagues if user else []):
        label = lg["name"] or lg["league_key"]
        if lg.get("is_finished"):
            label += f" ({lg.get('season')})"
        out.append((lg["league_key"], label))
    if featured_enabled():
        f = featured()
        if f["key"] not in [k for k, _ in out] and f["key"] != DEMO_KEY:
            out.append((f["key"], _featured_name()))
    out.append((DEMO_KEY, "Demo league"))
    return out


@st.cache_resource(show_spinner=False)
def _featured_name() -> str:
    try:
        return featured()["source"].meta().name
    except Exception:
        return "Featured league"


def is_demo_only() -> bool:
    return demo_requested()
