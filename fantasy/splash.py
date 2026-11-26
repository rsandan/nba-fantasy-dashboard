"""Animated loading screen shown on the first load of a visit.

Three variants (bounce, scoreboard, swish), one picked at random per visit. Pure CSS:
Streamlit doesn't run scripts inside st.html, and CSS animations cost nothing to load.
The overlay is fixed full-screen above the app, so the page can build underneath it;
``app.py`` clears it the moment the first page has finished rendering.
"""
from __future__ import annotations

import html
import random

MESSAGES = ["Connecting to Yahoo…", "Checking the waiver wire…", "Running 4,000 simulations…",
            "Scouting your opponent…", "Grading every trade…"]
VARIANTS = ("bounce", "scoreboard", "swish")

# Drawn with plain CSS, not an inline SVG: iOS Safari can skip painting an inline SVG that sits
# inside an element with a running transform animation, which left the bounce splash with
# only its shadow on iPhones.
_BALL = '<div class="bb" aria-hidden="true"></div>'

CSS = """<style>
.spl{position:fixed;inset:0;z-index:100000;background:#000;color:#fff;display:flex;flex-direction:column;
 align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Roboto,sans-serif}
.spl .ttl{font-size:26px;font-weight:800;letter-spacing:-.6px;line-height:1.15;text-align:center;padding:0 28px;margin-top:26px}
.spl .msg{position:relative;height:20px;width:280px;margin-top:12px}
.spl .msg span{position:absolute;inset:0;text-align:center;font-size:14px;color:#8e8e93;opacity:0;
 animation:spl-msg 11s infinite}
.spl .msg span:nth-child(2){animation-delay:2.2s}.spl .msg span:nth-child(3){animation-delay:4.4s}
.spl .msg span:nth-child(4){animation-delay:6.6s}.spl .msg span:nth-child(5){animation-delay:8.8s}
@keyframes spl-msg{0%{opacity:0;transform:translateY(4px)}3%,17%{opacity:1;transform:none}20%,100%{opacity:0}}
.spl .ball{width:52px;height:52px;animation:spl-bounce 1.1s cubic-bezier(.45,0,.55,1) infinite}
.spl .bb{position:relative;width:100%;height:100%;box-sizing:border-box;border-radius:50%;overflow:hidden;
 border:2px solid #7a3a00;background:linear-gradient(#7a3a00,#7a3a00) center/100% 2px no-repeat,
 linear-gradient(#7a3a00,#7a3a00) center/2px 100% no-repeat,#ff9f0a;-webkit-transform:translateZ(0)}
.spl .bb::before,.spl .bb::after{content:"";position:absolute;top:-12%;width:64%;height:124%;
 box-sizing:border-box;border:2px solid #7a3a00;border-radius:50%}
.spl .bb::before{left:-40%}.spl .bb::after{right:-40%}
.spl .ball .bb{animation:spl-spin 2.2s linear infinite}
.spl .shadow{width:48px;height:9px;border-radius:50%;background:#ff9f0a;margin-top:4px;
 animation:spl-shadow 1.1s cubic-bezier(.45,0,.55,1) infinite}
@keyframes spl-bounce{0%,100%{transform:translateY(0) scale(1.15,.85)}12%{transform:translateY(-6px)}
 50%{transform:translateY(-80px) scale(.95,1.05)}88%{transform:translateY(-6px)}}
@keyframes spl-shadow{0%,100%{transform:scale(1);opacity:.5}50%{transform:scale(.45);opacity:.15}}
@keyframes spl-spin{to{transform:rotate(360deg)}}
.spl .shim{background:linear-gradient(90deg,currentColor 0%,currentColor 40%,#0a84ff 50%,currentColor 60%,currentColor 100%);
 background-size:420px 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;
 animation:spl-shim 2.4s linear infinite}
@keyframes spl-shim{from{background-position:-210px 0}to{background-position:210px 0}}
.spl .live{font-size:12px;font-weight:800;letter-spacing:.7px;color:#ff453a;background:rgba(255,69,58,.15);
 padding:4px 10px;border-radius:20px;margin-bottom:16px}
.spl .board{border:1.5px solid #2c2c2e;background:#111;border-radius:18px;padding:14px 18px;display:flex;gap:7px;align-items:center}
.spl .dig{width:34px;height:48px;border-radius:8px;background:#1c1c1e;overflow:hidden;font-size:30px;font-weight:800;
 color:#ff9f0a;font-variant-numeric:tabular-nums;line-height:48px;text-align:center}
.spl .dig .strip{animation:spl-roll 4s steps(1) infinite}
.spl .dig.slow .strip{animation-duration:40s}
.spl .dig .strip b{display:block;height:48px}
@keyframes spl-roll{0%{transform:translateY(0)}10%{transform:translateY(-48px)}20%{transform:translateY(-96px)}
 30%{transform:translateY(-144px)}40%{transform:translateY(-192px)}50%{transform:translateY(-240px)}
 60%{transform:translateY(-288px)}70%{transform:translateY(-336px)}80%{transform:translateY(-384px)}
 90%{transform:translateY(-432px)}}
.spl .dots{display:flex;gap:7px;margin-top:18px}
.spl .dots i{width:8px;height:8px;border-radius:50%;background:#0a84ff;animation:spl-pulse 1.2s ease-in-out infinite}
.spl .dots i:nth-child(2){animation-delay:.2s}.spl .dots i:nth-child(3){animation-delay:.4s}
@keyframes spl-pulse{0%,100%{opacity:.25;transform:scale(.8)}50%{opacity:1;transform:none}}
.spl .court{width:240px;height:130px;position:relative}
.spl .shot{position:absolute;left:0;top:0;width:32px;height:32px;
 animation:spl-arc 1.8s cubic-bezier(.3,.1,.5,1) infinite alternate}

@keyframes spl-arc{0%{transform:translate(0,96px)}50%{transform:translate(92px,-8px) scale(.92)}
 82%{transform:translate(176px,24px) scale(.8)}100%{transform:translate(176px,76px) scale(.8);opacity:0}}
.spl .net{animation:spl-sway 1.8s ease infinite alternate;transform-origin:top;transform-box:fill-box}
@keyframes spl-sway{0%,70%{transform:scaleY(1)}82%{transform:scaleY(1.25)}100%{transform:scaleY(1)}}
.spl .word{display:inline-block;opacity:0;animation:spl-rise .5s ease forwards}
@keyframes spl-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.spl .clock{height:4px;width:180px;border-radius:2px;background:#1c1c1e;margin-top:18px;overflow:hidden}
.spl .clock span{display:block;height:100%;background:#0a84ff;animation:spl-clock 24s linear forwards}
@keyframes spl-clock{from{width:100%}to{width:0}}
@media (prefers-color-scheme: light){.spl{background:#f2f2f7;color:#000}.spl .msg span{color:#6c6c70}
 .spl .board{background:#fff;border-color:#d1d1d6}.spl .dig{background:#f2f2f7}.spl .clock{background:#e5e5ea}}
@media (prefers-reduced-motion: reduce){.spl *{animation-duration:0s!important;animation-iteration-count:1!important}
 .spl .msg span:first-child,.spl .word{opacity:1}}
</style>"""


def _messages() -> str:
    return '<div class="msg">' + "".join(f"<span>{html.escape(m)}</span>" for m in MESSAGES) + "</div>"


def _digit(slow: bool = False) -> str:
    strip = "".join(f"<b>{d}</b>" for d in range(10))
    return f'<div class="dig{" slow" if slow else ""}"><div class="strip">{strip}</div></div>'


def _title_lines(title: str) -> tuple[str, str]:
    """'Season 4 of Love Island (NBA)' -> ('Season 4 of', 'Love Island (NBA)')."""
    if " of " in title:
        a, b = title.split(" of ", 1)
        return f"{a} of", b
    return title, ""


def render(title: str, variant: str | None = None) -> str:
    variant = variant if variant in VARIANTS else random.choice(VARIANTS)
    l1, l2 = (html.escape(x) for x in _title_lines(title))
    br = f"<br>{l2}" if l2 else ""
    if variant == "bounce":
        body = (f'<div class="ball">{_BALL}</div><div class="shadow"></div>'
                f'<div class="ttl shim">{l1}{br}</div>')
    elif variant == "scoreboard":
        body = ('<div class="live">● LIVE</div><div class="board">'
                '<div class="dig">W</div><div class="dig">K</div><div style="width:10px"></div>'
                f'{_digit(slow=True)}{_digit()}</div><div class="ttl">{l1}{br}</div>'
                '<div class="dots"><i></i><i></i><i></i></div>')
    else:
        n = 0
        lines = []
        for line in (l1, l2):
            spans = []
            for w in line.split():
                spans.append(f'<span class="word" style="animation-delay:{n * 0.12:.2f}s">{w}</span>')
                n += 1
            if spans:
                lines.append(" ".join(spans))
        title_html = "<br>".join(lines)
        body = ('<div class="court"><svg viewBox="0 0 240 130" width="240" height="130" aria-hidden="true" '
                'style="position:absolute;inset:0"><rect x="186" y="10" width="44" height="30" rx="3" fill="none" '
                'stroke="#3a3a3c" stroke-width="2"/><line x1="187" y1="47" x2="229" y2="47" stroke="#ff453a" '
                'stroke-width="3" stroke-linecap="round"/><g class="net"><path d="M190 49 L196 75 M208 49 L208 75 '
                'M226 49 L220 75 M193 60 L223 60 M196 71 L220 71" stroke="#8e8e93" stroke-width="1.4" fill="none"/>'
                f'</g></svg><div class="shot">{_BALL}</div></div><div class="ttl">{title_html}</div>'
                '<div class="clock"><span></span></div>')
    return f'{CSS}<div class="spl" role="status" aria-label="Loading">{body}{_messages()}</div>'
