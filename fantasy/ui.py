"""Mobile UI kit: iOS-style components rendered as HTML inside Streamlit.

Design rules (iPhone first):
- Follow the system appearance. Colors are iOS system colors, switched by
  `prefers-color-scheme`, matching the light/dark themes in .streamlit/config.toml.
- Numbers are the interface. Big tabular figures, labels in secondary gray.
- Inset grouped lists instead of data tables: nothing scrolls sideways.
- Color is never the only cue: every +/- carries a sign, every probability a number.
- Tap targets are 44pt or taller, inputs are 16px so Safari doesn't zoom on focus.
"""
from __future__ import annotations

from html import escape

import streamlit as st

CSS = """
<style>
:root {
  --bg:#f2f2f7; --card:#ffffff; --sep:rgba(60,60,67,.18); --label:#000; --label2:rgba(60,60,67,.6);
  --label3:rgba(60,60,67,.35); --fill:rgba(120,120,128,.16); --fill2:rgba(120,120,128,.08);
  --blue:#007aff; --green:#248a3d; --red:#d70015; --bar:rgba(249,249,249,.94); --seg:#fff; --tab:#8e8e93;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#000; --card:#1c1c1e; --sep:rgba(84,84,88,.65); --label:#fff; --label2:rgba(235,235,245,.6);
    --label3:rgba(235,235,245,.3); --fill:rgba(120,120,128,.36); --fill2:rgba(120,120,128,.18);
    --blue:#0a84ff; --green:#30d158; --red:#ff453a; --bar:rgba(22,22,24,.94); --seg:#636366; --tab:#8e8e93;
  }
}
html, body, .stApp { -webkit-text-size-adjust:100%; touch-action:manipulation;
  font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
.stApp { background:var(--bg); }

/* Streamlit chrome off: this is an app, not a notebook */
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebarCollapsedControl"], footer, #MainMenu, .stAppDeployButton { display:none !important; }
[data-testid="stMainBlockContainer"] { padding:calc(8px + env(safe-area-inset-top)) 16px
  calc(104px + env(safe-area-inset-bottom)) !important; max-width:620px; }
[data-testid="stVerticalBlock"] { gap:.75rem; }

/* 16px inputs: iOS Safari zooms the page on focus below that */
input, textarea, select, [data-baseweb="select"] * { font-size:16px !important; }
button[kind], [data-testid="stPopover"] button { min-height:44px; }

.st-key-titlebar { flex-wrap:nowrap !important; align-items:flex-start !important; }
.st-key-titlebar [data-testid="stPopover"] { margin-top:4px; }
.st-key-titlebar [data-testid="stPopover"] button [data-testid="stIconMaterial"] { font-size:30px; color:var(--blue); }
.st-key-titlebar [data-testid="stPopover"] button > div > div:last-child:not(:first-child) { display:none; }

/* ---------- tab bar ---------- */
.st-key-tabbar { position:fixed; left:0; right:0; bottom:0; z-index:1000; background:var(--bar);
  -webkit-backdrop-filter:saturate(180%) blur(20px); backdrop-filter:saturate(180%) blur(20px);
  border-top:.5px solid var(--sep); padding:6px 4px calc(4px + env(safe-area-inset-bottom)) !important;
  justify-content:space-around !important; flex-wrap:nowrap !important; gap:0 !important; }
.st-key-tabbar > div { flex:1 1 0 !important; min-width:0 !important; width:auto !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] { flex-direction:column; gap:1px; padding:4px 0 2px;
  width:100%; justify-content:center; align-items:center; background:transparent !important;
  color:var(--tab) !important; min-height:48px; border-radius:0; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] span[data-testid="stIconMaterial"] { font-size:27px;
  margin:0 !important; color:inherit !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] p { font-size:10px !important; font-weight:500;
  letter-spacing:.1px; color:inherit !important; line-height:1.2; margin:0; }
.st-key-tabbar .tab-active a[data-testid="stPageLink-NavLink"],
.st-key-tabbar a[data-testid="stPageLink-NavLink"][aria-current="page"] { color:var(--blue) !important; }

/* ---------- typography ---------- */
.lt { margin:4px 0 0; }
.lt .h1 { font-size:34px; line-height:41px; font-weight:700; letter-spacing:.37px; color:var(--label); }
.lt .sub { font-size:15px; color:var(--label2); margin-top:2px; }
.live { color:var(--red); font-weight:600; }
.live i { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--red);
  margin-right:5px; vertical-align:1px; animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
@media (prefers-reduced-motion: reduce) { .live i { animation:none; } }
.sh { font-size:13px; text-transform:uppercase; letter-spacing:-.08px; color:var(--label2);
  margin:10px 16px -4px; }
.foot { font-size:13px; color:var(--label2); margin:-2px 16px 0; line-height:1.4; }
.num { font-variant-numeric:tabular-nums; }

/* ---------- inset grouped list ---------- */
.list { background:var(--card); border-radius:12px; overflow:hidden; }
.row { display:flex; align-items:center; gap:12px; padding:10px 16px; min-height:44px; position:relative; }
.row + .row::before { content:""; position:absolute; top:0; right:0; left:16px; border-top:.5px solid var(--sep); }
.row.me { background:var(--fill2); }
.row .rk { width:22px; text-align:center; font-size:15px; color:var(--label2); font-variant-numeric:tabular-nums; }
.row .main { flex:1; min-width:0; }
.row .t1 { font-size:17px; color:var(--label); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.row .t2 { font-size:13px; color:var(--label2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:1px; }
.row .end { text-align:right; flex:none; }
.row .e1 { font-size:17px; font-weight:600; color:var(--label); font-variant-numeric:tabular-nums; }
.row .e2 { font-size:13px; color:var(--label2); font-variant-numeric:tabular-nums; margin-top:1px; }
.row .you { font-size:11px; font-weight:600; color:var(--blue); margin-left:6px; vertical-align:1px; }
.cut { font-size:12px; font-weight:600; color:var(--blue); text-transform:uppercase; letter-spacing:.3px;
  padding:6px 16px; background:var(--fill2); border-top:.5px solid var(--sep); border-bottom:.5px solid var(--sep); }
.mini { height:4px; border-radius:2px; background:var(--fill); margin-top:6px; overflow:hidden; }
.mini > span { display:block; height:100%; background:var(--blue); border-radius:2px; }
.pos { color:var(--green) !important; } .neg { color:var(--red) !important; }

/* ---------- avatars ---------- */
.av { width:40px; height:40px; border-radius:50%; flex:none; background:var(--fill) center 20%/cover no-repeat;
  display:flex; align-items:center; justify-content:center; font-size:15px; font-weight:600; color:var(--label2); }

/* ---------- scoreboard (matchup hero) ---------- */
.hero { background:var(--card); border-radius:16px; padding:16px 16px 6px; }
.hero .top { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); align-items:end; gap:8px; }
.hero .tn { font-size:15px; font-weight:600; color:var(--label); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.hero .r { text-align:right; }
.hero .big { font-size:44px; line-height:1.05; font-weight:700; letter-spacing:-.5px; font-variant-numeric:tabular-nums; }
.hero .dim { color:var(--label3); }
.hero .mid { text-align:center; padding-bottom:6px; }
.hero .score { white-space:nowrap; font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; color:var(--label); }
.hero .cap { font-size:12px; color:var(--label2); }
.bar { display:flex; height:6px; border-radius:3px; overflow:hidden; background:var(--fill); margin:12px 0 4px; }
.bar > span { background:var(--blue); }
.hero .meta { font-size:12px; color:var(--label2); text-align:center; margin-bottom:6px; }
.cat { display:grid; grid-template-columns:minmax(0,1fr) 96px minmax(0,1fr); align-items:center; padding:7px 0;
  border-top:.5px solid var(--sep); }
.cat .v { font-size:17px; line-height:22px; font-variant-numeric:tabular-nums; color:var(--label2); }
.cat .v.lead { color:var(--label); font-weight:600; }
.cat .v.r { text-align:right; }
.cat .p { font-size:11px; line-height:13px; color:var(--label2); font-variant-numeric:tabular-nums; }
.cat .c { text-align:center; }
.cat .cn { font-size:13px; line-height:16px; font-weight:600; color:var(--label); }
.cat .mini { margin:3px 10px 2px; }
.cat .proj { font-size:11px; line-height:13px; color:var(--label3); }

/* ---------- trade chips ---------- */
.chips { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.chip { background:var(--card); border-radius:10px; padding:8px 10px; }
.chip .k { font-size:12px; color:var(--label2); font-weight:600; }
.chip .v { font-size:17px; font-weight:600; font-variant-numeric:tabular-nums; color:var(--label); }
.chip.off { opacity:.45; }
.verdict { background:var(--card); border-radius:16px; padding:16px; }
.verdict .h { font-size:22px; font-weight:700; color:var(--label); }
.verdict .b { font-size:15px; color:var(--label2); margin-top:4px; line-height:1.35; }
.verdict .nums { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:12px; }
.verdict .n .k { font-size:12px; color:var(--label2); }
.verdict .n .v { font-size:28px; font-weight:700; font-variant-numeric:tabular-nums; }

/* native widgets: soften to match, 44pt rows for radio lists */
[data-testid="stRadioGroup"] { gap:0 !important; width:100%; }
[data-testid="stRadioGroup"] > div { width:100%; }
[data-testid="stRadio"], [data-testid="stElementContainer"]:has(> [data-testid="stRadio"]) { width:100% !important; }
[data-testid="stRadioOption"] { min-height:44px; display:flex; align-items:center; margin:0 !important; width:100%; }
[data-testid="stRadioGroup"] > div + div { border-top:.5px solid var(--sep); }
[data-testid="stRadioOption"] p { font-size:17px !important; }
.st-key-onboard { background:var(--card); border-radius:12px; padding:10px 16px 4px; }
[data-testid="stExpander"] details { background:var(--card); border:none !important; border-radius:12px; }
[data-testid="stExpander"] summary { min-height:48px; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding-left:12px; padding-right:12px; }
@media (max-width:380px) { .hero .big { font-size:36px; } }
/* iOS segmented control: gray track, raised thumb */
[data-testid="stButtonGroup"] [role="radiogroup"]:has(> button[data-variant="segmented_control"]) {
  display:flex; width:100%; background:var(--fill); border-radius:9px; padding:2px; gap:0; }
button[data-variant="segmented_control"] { flex:1 1 0; min-height:32px; border:none !important;
  background:transparent !important; border-radius:7px !important; color:var(--label) !important;
  box-shadow:none !important; margin:0 !important; }
button[data-variant="segmented_control"] p { font-size:13px !important; font-weight:500; }
button[data-variant="segmented_control"][aria-checked="true"] { background:var(--seg) !important;
  box-shadow:0 3px 8px rgba(0,0,0,.12), 0 3px 1px rgba(0,0,0,.04) !important; }
button[data-variant="segmented_control"][aria-checked="true"] p { font-weight:600; }
</style>
"""

HEAD_JS = """
<script>
(function () {
  const d = window.parent && window.parent.document ? window.parent.document : document;
  const add = (tag, attrs) => {
    const key = attrs.name || attrs.rel;
    if (d.head.querySelector(`${tag}[${attrs.name ? 'name' : 'rel'}="${key}"]`)) return;
    const el = d.createElement(tag); Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
    d.head.appendChild(el);
  };
  const vp = d.head.querySelector('meta[name="viewport"]');
  if (vp && !vp.content.includes('viewport-fit')) vp.content += ', viewport-fit=cover';
  add('meta', {name: 'apple-mobile-web-app-capable', content: 'yes'});
  add('meta', {name: 'mobile-web-app-capable', content: 'yes'});
  add('meta', {name: 'apple-mobile-web-app-status-bar-style', content: 'default'});
  add('meta', {name: 'apple-mobile-web-app-title', content: 'Love Island'});
  add('link', {rel: 'apple-touch-icon', href: './app/static/apple-touch-icon.png'});
})();
</script>
"""


def inject():
    st.html(CSS)
    st.html(HEAD_JS, unsafe_allow_javascript=True)


# ------------------------------------------------------------------ helpers
def e(x) -> str:
    return escape(str(x))


def pct(x) -> str:
    """Probability label that never claims certainty a simulation can't support."""
    if x is None or x != x:
        return "–"
    if x < 0.005:
        return "<1%"
    if x > 0.995:
        return ">99%"
    return f"{x:.0%}"


def signed(x: float, nd: int = 1) -> str:
    return "–" if x != x else f"{x:+.{nd}f}".replace("-", "−")


def sign_class(x: float, eps: float = 0.05) -> str:
    return "pos" if x > eps else "neg" if x < -eps else ""


def initials(name: str) -> str:
    parts = [p for p in str(name).replace(".", " ").split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def avatar(name: str, nba_id=None, photo: bool = True) -> str:
    if photo and nba_id is not None and nba_id == nba_id:
        url = f"https://cdn.nba.com/headshots/nba/latest/260x190/{int(nba_id)}.png"
        return f'<div class="av" style="background-image:url(\'{url}\')"></div>'
    return f'<div class="av">{e(initials(name))}</div>'


# --------------------------------------------------------------- components
def large_title(title: str, sub: str = "", live: bool = False) -> str:
    """iOS large title. `sub` must be escaped by the caller. A live week gets a red dot."""
    dot = '<span class="live"><i></i>Live</span> · ' if live else ""
    s = f'<div class="sub">{dot}{sub}</div>' if sub or live else ""
    return f'<div class="lt"><div class="h1">{e(title)}</div>{s}</div>'


def section(title: str) -> None:
    st.html(f'<div class="sh">{e(title)}</div>')


def footnote(text: str) -> None:
    st.html(f'<div class="foot">{text}</div>')


def row(t1: str, t2: str = "", e1: str = "", e2: str = "", lead: str = "", me: bool = False,
        e1_class: str = "", extra: str = "") -> str:
    """One list row. t1/e1 etc. must already be escaped where they contain user text."""
    you = '<span class="you">YOU</span>' if me else ""
    return (f'<div class="row{" me" if me else ""}">{lead}'
            f'<div class="main"><div class="t1">{t1}{you}</div>'
            + (f'<div class="t2">{t2}</div>' if t2 else "") + extra + "</div>"
            + (f'<div class="end"><div class="e1 {e1_class}">{e1}</div>'
               + (f'<div class="e2">{e2}</div>' if e2 else "") + "</div>" if e1 or e2 else "")
            + "</div>")


def rank(n: int) -> str:
    return f'<div class="rk">{n}</div>'


def mini_bar(p: float) -> str:
    p = 0.0 if p != p else max(0.0, min(1.0, p))
    return f'<div class="mini"><span style="width:{p * 100:.1f}%"></span></div>'


def group(rows: list[str]) -> None:
    st.html('<div class="list">' + "".join(rows) + "</div>")
