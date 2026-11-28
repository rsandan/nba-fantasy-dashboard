"""Sign in with Yahoo: per-user OAuth 2.0 for a multi-league site.

Flow (Yahoo's authorization-code grant, developer.yahoo.com/oauth2/guide/flows_authcode):

1. "Sign in with Yahoo" links to ``api.login.yahoo.com/oauth2/request_auth`` with a
   signed ``state``.
2. Yahoo sends the browser back to ``YAHOO_REDIRECT_URI?code=...&state=...``. That page
   load is a brand-new Streamlit session, so ``state`` is verified statelessly: it is an
   HMAC over a timestamp + nonce with the server secret, valid for 10 minutes.
3. The code is exchanged for an access token (1 hour) and a refresh token.
4. "Remember me": the refresh token is encrypted (Fernet, key derived from the server
   secret) into a cookie. The cookie is useless anywhere but this server, survives
   restarts and deploys, and a sign-out deletes it.

Tokens never appear in URLs, logs or error messages. Each browser session holds its
own ``UserSession``; nothing user-specific is shared between sessions.

The HTTP call is injectable (``post``) so the whole flow is unit-tested offline.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
STATE_TTL = 600                  # seconds a sign-in link stays valid
COOKIE_NAME = "fx_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 60   # 60 days; Yahoo refresh tokens last until revoked


class AuthError(RuntimeError):
    """Sign-in failed. The message is safe to show (it never contains a token)."""


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = ""
    app_secret: str = field(default="", repr=False)   # signs state, encrypts the cookie; keep stable

    @classmethod
    def from_env(cls, env=os.environ) -> "OAuthConfig | None":
        """Configured when the Yahoo app's id/secret, its redirect URI and a server secret
        are all set. Falls back to the consumer key/secret in KEYPAIR_JSON."""
        cid = env.get("YAHOO_CLIENT_ID", "").strip()
        csec = env.get("YAHOO_CLIENT_SECRET", "").strip()
        if not (cid and csec):
            raw = env.get("KEYPAIR_JSON") or env.get("YAHOO_OAUTH_JSON")
            if raw:
                try:
                    kp = json.loads(raw)
                    cid, csec = kp.get("consumer_key", ""), kp.get("consumer_secret", "")
                except ValueError:
                    pass
        redirect = env.get("YAHOO_REDIRECT_URI", "").strip()
        app_secret = env.get("APP_SECRET", "").strip()
        if cid and csec and redirect and len(app_secret) >= 16:
            return cls(cid, csec, redirect, app_secret)
        return None


# ------------------------------------------------------------------ signed state
def _sign(secret: str, msg: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()[:18]).decode()


def make_state(cfg: OAuthConfig, now: float | None = None, extra: str = "") -> str:
    """``<ts>.<nonce>.<extra>.<sig>``. ``extra`` carries a short hint (e.g. which page
    to return to); it is signed, so it can't be tampered with."""
    ts = str(int(now if now is not None else time.time()))
    nonce = secrets.token_urlsafe(9)
    extra = "".join(ch for ch in extra if ch.isalnum() or ch in "-_")[:40]
    body = f"{ts}.{nonce}.{extra}"
    return f"{body}.{_sign(cfg.app_secret, body)}"


def verify_state(cfg: OAuthConfig, state: str, now: float | None = None) -> str:
    """Return the state's ``extra`` if it is ours and fresh; raise AuthError otherwise."""
    try:
        ts, nonce, extra, sig = (state or "").split(".")
    except ValueError:
        raise AuthError("That sign-in link isn't valid. Please try again.") from None
    body = f"{ts}.{nonce}.{extra}"
    if not hmac.compare_digest(sig, _sign(cfg.app_secret, body)):
        raise AuthError("That sign-in link isn't valid. Please try again.")
    age = (now if now is not None else time.time()) - int(ts)
    if not 0 <= age <= STATE_TTL:
        raise AuthError("That sign-in link expired. Please sign in again.")
    return extra


def authorize_url(cfg: OAuthConfig, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": cfg.client_id, "redirect_uri": cfg.redirect_uri,
        "response_type": "code", "state": state, "language": "en-us",
    })


# ------------------------------------------------------------------ token endpoint
PostFn = Callable[[str, dict, dict], dict]


def _http_post(url: str, data: dict, headers: dict) -> dict:
    import requests
    r = requests.post(url, data=data, headers=headers, timeout=15)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or "access_token" not in body:
        # Yahoo's error body is safe (no tokens); keep only its error fields.
        err = body.get("error_description") or body.get("error") or f"HTTP {r.status_code}"
        raise AuthError(f"Yahoo didn't accept the sign-in ({err}). Please try again.")
    return body


def _token_request(cfg: OAuthConfig, data: dict, post: PostFn | None) -> dict:
    basic = base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}
    return (post or _http_post)(TOKEN_URL, {**data, "redirect_uri": cfg.redirect_uri}, headers)


@dataclass
class Tokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float = 0.0
    guid: str = ""

    @classmethod
    def from_response(cls, body: dict, now: float | None = None, old_refresh: str = "") -> "Tokens":
        now = now if now is not None else time.time()
        return cls(body["access_token"], body.get("refresh_token") or old_refresh,
                   now + float(body.get("expires_in", 3600)), str(body.get("xoauth_yahoo_guid", "")))


def exchange_code(cfg: OAuthConfig, code: str, post: PostFn | None = None) -> Tokens:
    if not code or len(code) > 512:
        raise AuthError("Yahoo didn't send a sign-in code. Please try again.")
    return Tokens.from_response(_token_request(cfg, {"grant_type": "authorization_code", "code": code}, post))


def refresh_tokens(cfg: OAuthConfig, refresh_token: str, post: PostFn | None = None) -> Tokens:
    body = _token_request(cfg, {"grant_type": "refresh_token", "refresh_token": refresh_token}, post)
    return Tokens.from_response(body, old_refresh=refresh_token)


# ------------------------------------------------------------------ remember-me cookie
def _fernet(cfg: OAuthConfig):
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256(("fx-cookie:" + cfg.app_secret).encode()).digest())
    return Fernet(key)


def seal(cfg: OAuthConfig, tokens: Tokens) -> str:
    payload = json.dumps({"r": tokens.refresh_token, "g": tokens.guid}).encode()
    return _fernet(cfg).encrypt(payload).decode()


def unseal(cfg: OAuthConfig, value: str, max_age: int = COOKIE_MAX_AGE) -> dict | None:
    """The cookie's contents, or None if it's missing, tampered with or too old."""
    from cryptography.fernet import InvalidToken
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    try:
        return json.loads(_fernet(cfg).decrypt(value.encode(), ttl=max_age))
    except (InvalidToken, ValueError):
        return None


# ------------------------------------------------------------------ yahoo_fantasy_api glue
class _BearerService:
    """Stands in for rauth's OAuth2Service: yahoo_fantasy_api calls ``oauth.get_session``
    after a refresh."""

    @staticmethod
    def get_session(token: str):
        import requests
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {token}"
        return s


@dataclass
class UserSession:
    """One signed-in person. Duck-types ``yahoo_oauth.OAuth2`` for yahoo_fantasy_api:
    ``.session`` (a requests session), ``token_is_valid()``, ``refresh_access_token()``."""
    cfg: OAuthConfig
    tokens: Tokens
    post: PostFn | None = None
    leagues: list = field(default_factory=list)
    rotated: bool = False          # refresh token changed: the cookie needs rewriting

    def __post_init__(self):
        self.oauth = _BearerService()
        self.session = self.oauth.get_session(self.tokens.access_token)
        self.access_token = self.tokens.access_token

    def token_is_valid(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.tokens.expires_at - 60

    def refresh_access_token(self) -> dict:
        old = self.tokens.refresh_token
        self.tokens = refresh_tokens(self.cfg, old, self.post)
        self.rotated = self.rotated or self.tokens.refresh_token != old
        self.access_token = self.tokens.access_token
        self.session = self.oauth.get_session(self.tokens.access_token)
        return {"access_token": self.tokens.access_token}

    def ensure_fresh(self) -> "UserSession":
        if not self.token_is_valid():
            self.refresh_access_token()
        return self

    def __repr__(self) -> str:  # never print tokens, even in a traceback
        return f"UserSession(guid={self.tokens.guid[:6]}…, leagues={len(self.leagues)})"
