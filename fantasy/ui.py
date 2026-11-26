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
  --blue:#007aff; --green:#248a3d; --red:#d70015; --orange:#ff9500; --bar:rgba(255,255,255,.72); --seg:#fff;
  --tab:#8e8e93; --edge:rgba(0,0,0,.06); --shadow:0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06);
  --display:"SF Pro Display",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#000; --card:#1c1c1e; --sep:rgba(84,84,88,.65); --label:#fff; --label2:rgba(235,235,245,.6);
    --label3:rgba(235,235,245,.3); --fill:rgba(120,120,128,.36); --fill2:rgba(120,120,128,.18);
    --blue:#0a84ff; --green:#30d158; --red:#ff453a; --orange:#ff9f0a; --bar:rgba(30,30,32,.72); --seg:#636366;
    --tab:#8e8e93; --edge:rgba(255,255,255,.08); --shadow:none;
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
.st-key-titlebar [data-testid="stPopover"] { margin-top:4px; margin-right:10px; }
.st-key-titlebar [data-testid="stPopover"] button [data-testid="stIconMaterial"] { font-size:30px; color:var(--blue); }
.st-key-titlebar [data-testid="stPopover"] button > div > div:last-child:not(:first-child) { display:none; }

/* ---------- tab bar ---------- */
.st-key-tabbar { position:fixed; left:50%; transform:translateX(-50%); width:min(calc(100% - 24px), 560px);
  bottom:calc(10px + env(safe-area-inset-bottom)); z-index:1000; background:var(--bar);
  -webkit-backdrop-filter:saturate(180%) blur(24px); backdrop-filter:saturate(180%) blur(24px);
  border:.5px solid var(--edge); border-radius:30px; box-shadow:0 10px 30px rgba(0,0,0,.18);
  padding:5px !important; justify-content:space-around !important; flex-wrap:nowrap !important; gap:0 !important; }
.st-key-tabbar > div { flex:1 1 0 !important; min-width:0 !important; width:auto !important;
  display:flex !important; justify-content:center; }
.st-key-tabbar [data-testid="stElementContainer"], .st-key-tabbar .stPageLink,
.st-key-tabbar .stPageLink > div { width:100% !important; margin:0 !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] { display:flex; flex-direction:column; align-items:center;
  justify-content:center; gap:4px; width:100%; min-height:52px; padding:7px 0 5px; margin:0;
  background:transparent !important; color:var(--tab) !important; border-radius:24px; transition:color .2s; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"]:hover { background:transparent !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] > span { display:flex; align-items:center; justify-content:center;
  margin:0 !important; padding:0 !important; line-height:1; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] span[data-testid="stIconMaterial"] { font-size:24px; width:24px;
  height:24px; line-height:24px; margin:0 !important; color:inherit !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] [data-testid="stMarkdownContainer"],
.st-key-tabbar a[data-testid="stPageLink-NavLink"] > span:last-child { width:auto; color:inherit !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] > span:last-child { margin-top:4px !important; }
.st-key-tabbar a[data-testid="stPageLink-NavLink"] p { font-size:10.5px !important; font-weight:600; letter-spacing:0;
  line-height:12px; color:inherit !important; margin:0; text-align:center; white-space:nowrap; }
.st-key-tabbar .tab-active a[data-testid="stPageLink-NavLink"],
.st-key-tabbar a[data-testid="stPageLink-NavLink"][aria-current="page"] { color:var(--blue) !important; }

/* ---------- typography ---------- */
.lt { margin:4px 0 0; }
.lt .h1 { font-family:var(--display); font-size:36px; line-height:42px; font-weight:800; letter-spacing:-.8px;
  color:var(--label); }
.lt .sub { font-size:15px; color:var(--label2); margin-top:2px; }
.live { color:var(--red); font-weight:700; font-size:12px; letter-spacing:.4px; text-transform:uppercase;
  background:color-mix(in srgb, var(--red) 14%, transparent); padding:2px 8px 2px 7px; border-radius:20px;
  vertical-align:1px; }
.live i { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--red);
  margin-right:5px; vertical-align:1px; animation:pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity:.35; } }
@media (prefers-reduced-motion: reduce) { .live i { animation:none; } }
.sh { font-family:var(--display); font-size:20px; font-weight:700; letter-spacing:-.3px; color:var(--label);
  margin:14px 4px -4px; }
.foot { font-size:13px; color:var(--label2); margin:-2px 16px 0; line-height:1.4; }
.num { font-variant-numeric:tabular-nums; }

/* ---------- inset grouped list ---------- */
.list { background:var(--card); border-radius:18px; overflow:hidden; border:.5px solid var(--edge);
  box-shadow:var(--shadow); }
.row { display:flex; align-items:center; gap:12px; padding:10px 16px; min-height:44px; position:relative; }
.row + .row::before { content:""; position:absolute; top:0; right:0; left:16px; border-top:.5px solid var(--sep); }
.row.me { background:var(--fill2); }
.row .rk { width:22px; text-align:center; font-size:15px; color:var(--label2); font-variant-numeric:tabular-nums; }
.row .main { flex:1; min-width:0; }
.row .t1 { font-size:17px; line-height:21px; color:var(--label); overflow:hidden; overflow-wrap:anywhere;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
.row .t2 { font-size:13px; color:var(--label2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:1px; }
.row .end { text-align:right; flex:none; }
.row .e1 { font-size:17px; font-weight:600; color:var(--label); font-variant-numeric:tabular-nums; }
.row .e2 { font-size:13px; color:var(--label2); font-variant-numeric:tabular-nums; margin-top:1px; }
.row .you { font-size:10px; font-weight:700; letter-spacing:.4px; color:var(--blue); margin-left:7px; vertical-align:2px;
  background:color-mix(in srgb, var(--blue) 14%, transparent); padding:2px 6px; border-radius:6px; }
.cut { font-size:12px; font-weight:600; color:var(--blue); text-transform:uppercase; letter-spacing:.3px;
  padding:6px 16px; background:var(--fill2); border-top:.5px solid var(--sep); border-bottom:.5px solid var(--sep); }
.mini { height:5px; border-radius:3px; background:var(--fill2); margin-top:7px; overflow:hidden; }
.mini > span { display:block; height:100%; border-radius:3px;
  background:linear-gradient(90deg, color-mix(in srgb, var(--blue) 70%, transparent), var(--blue)); }
/* diverging category bar: fills from the center toward the favored side */
.dv { position:relative; height:6px; border-radius:3px; background:var(--fill2); margin:5px 8px 3px; overflow:hidden; }
.dv::after { content:""; position:absolute; left:50%; top:-1px; bottom:-1px; width:1.5px; margin-left:-.75px;
  background:var(--label3); }
.dv > span { position:absolute; top:0; bottom:0; border-radius:3px; }
.dv > .a { right:50%; background:var(--blue); }
.dv > .b { left:50%; background:var(--orange); }
.pos { color:var(--green) !important; } .neg { color:var(--red) !important; }

/* ---------- avatars ---------- */
.av { width:42px; height:42px; box-shadow:inset 0 0 0 .5px var(--edge); border-radius:50%; flex:none; background:var(--fill) center 20%/cover no-repeat;
  display:flex; align-items:center; justify-content:center; font-size:15px; font-weight:600; color:var(--label2); }

/* ---------- team logos (Yahoo profile pictures, initials if none) ---------- */
.tl { width:34px; height:34px; border-radius:50%; flex:none; object-fit:cover; background:var(--fill);
  box-shadow:0 0 0 .5px var(--edge); display:flex; align-items:center; justify-content:center;
  font-size:13px; font-weight:700; color:var(--label2); }
.tl.lg { width:52px; height:52px; font-size:18px; box-shadow:0 0 0 2px var(--card), 0 2px 10px rgba(0,0,0,.18); }
.row .rk + .tl { margin-left:-4px; }

/* ---------- scoreboard (matchup hero) ---------- */
.hero { background:linear-gradient(165deg, color-mix(in srgb, var(--blue) 13%, var(--card)) 0%, var(--card) 48%);
  border-radius:22px; padding:18px 16px 6px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.hero .top { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); align-items:end; gap:10px; }
.hero .tn { font-size:15px; line-height:19px; font-weight:600; color:var(--label); margin-top:8px; min-height:38px;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; overflow-wrap:anywhere; }
.hero .top > div:first-child, .hero .r { display:flex; flex-direction:column; min-width:0; }
.hero .r { align-items:flex-end; }
.hero .r { text-align:right; }
.hero .big { font-family:var(--display); font-size:52px; line-height:1; font-weight:800; letter-spacing:-2px;
  font-variant-numeric:tabular-nums; margin-top:4px; }
.hero .dim { color:var(--label3); }
.hero .mid { text-align:center; padding-bottom:6px; }
.hero .score { white-space:nowrap; font-family:var(--display); font-size:24px; font-weight:700; letter-spacing:-.5px; font-variant-numeric:tabular-nums; color:var(--label); }
.hero .cap { font-size:12px; color:var(--label2); }
.bar { display:flex; gap:3px; height:8px; margin:16px 0 6px; }
.bar > span { border-radius:4px; background:var(--blue); min-width:4px; }
.bar > span.b { background:var(--orange); flex:1; }
.hero .meta { font-size:12px; color:var(--label2); text-align:center; margin-bottom:6px; }
.cat { display:grid; grid-template-columns:minmax(0,1fr) 112px minmax(0,1fr); align-items:center; padding:9px 0;
  border-top:.5px solid var(--sep); }
.cat .v { font-size:17px; line-height:22px; font-variant-numeric:tabular-nums; color:var(--label2); }
.cat .v span { display:inline-block; padding:1px 0; border-radius:9px; }
.cat .v.lead { color:var(--label); font-weight:700; }
.cat .v.lead span { padding:1px 9px; margin:0 -9px; background:color-mix(in srgb, var(--blue) 16%, transparent); }
.cat .v.r.lead span { background:color-mix(in srgb, var(--orange) 18%, transparent); }
.cat .v.r { text-align:right; }
.cat .p { font-size:11px; line-height:13px; color:var(--label2); font-variant-numeric:tabular-nums; }
.cat .c { text-align:center; }
.cat .cn { font-size:12px; line-height:16px; font-weight:700; letter-spacing:.3px; color:var(--label2); }
.cat .mini { margin:3px 10px 2px; }
.cat .proj { font-size:11px; line-height:13px; color:var(--label3); }

/* ---------- trade chips ---------- */
.chips { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }
.chip { background:var(--card); border-radius:14px; padding:10px 12px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.chip .k { font-size:12px; color:var(--label2); font-weight:600; }
.chip .v { font-size:17px; font-weight:600; font-variant-numeric:tabular-nums; color:var(--label); }
.chip.off { opacity:.45; }
.verdict { background:var(--card); border-radius:22px; padding:18px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.verdict .h { font-family:var(--display); font-size:24px; font-weight:800; letter-spacing:-.5px; color:var(--label); }
.verdict .b { font-size:15px; color:var(--label2); margin-top:4px; line-height:1.35; }
.verdict .nums { display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:12px; }
.verdict .n .k { font-size:12px; color:var(--label2); }
.verdict .n .v { font-family:var(--display); font-size:32px; font-weight:800; letter-spacing:-1px; font-variant-numeric:tabular-nums; }

/* ---------- home: stories, game plan, wire ---------- */
.tag { display:inline-block; font-size:11px; font-weight:800; letter-spacing:.6px; padding:3px 8px;
  border-radius:6px; color:var(--tagc, var(--blue)); background:color-mix(in srgb, var(--tagc, var(--blue)) 15%, transparent); }
.tag.red { --tagc:var(--red); } .tag.orange { --tagc:var(--orange); } .tag.green { --tagc:var(--green); }
.tag.purple { --tagc:#af52de; } .tag.gray { --tagc:var(--label2); }
.story { background:var(--card); border-radius:22px; padding:16px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.story .hl { font-family:var(--display); font-size:19px; line-height:24px; font-weight:750; letter-spacing:-.3px;
  color:var(--label); margin-top:9px; }
.story .dk { font-size:15px; line-height:21px; color:var(--label2); margin-top:5px; }
.story .ft { display:flex; align-items:center; gap:10px; margin-top:12px; }
.story .ft .pa, .story .ft .pb { font-size:15px; font-weight:700; font-variant-numeric:tabular-nums; color:var(--label); }
.story .ft .pb { color:var(--label2); }
.story .ft .bar { flex:1; margin:0; height:6px; }
.story.top { padding:20px 18px 18px; background:linear-gradient(160deg,
  color-mix(in srgb, var(--blue) 22%, var(--card)) 0%, color-mix(in srgb, #af52de 10%, var(--card)) 55%, var(--card) 100%); }
.story.top .kick { font-size:12px; font-weight:800; letter-spacing:.8px; color:var(--label2); text-transform:uppercase; }
.story.top .hl { font-size:26px; line-height:31px; font-weight:800; letter-spacing:-.6px; }
.story.top .dk { font-size:16px; line-height:22px; }
.story .logos { display:flex; margin-top:14px; } .story .logos .tl { margin-right:-8px; box-shadow:0 0 0 2px var(--card); }
.plan { background:var(--card); border-radius:22px; padding:16px 16px 6px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.plan .ph { display:flex; align-items:center; gap:12px; }
.plan .ph .t { font-family:var(--display); font-size:19px; font-weight:800; letter-spacing:-.3px; color:var(--label); line-height:23px; }
.plan .ph .s { font-size:13px; color:var(--label2); margin-top:2px; }
.plan .ph .odds { margin-left:auto; text-align:right; font-family:var(--display); font-size:30px; font-weight:800;
  letter-spacing:-1px; font-variant-numeric:tabular-nums; }
.act { display:flex; gap:12px; padding:13px 0; border-top:.5px solid var(--sep); }
.plan .ph + .act { margin-top:12px; }
.act .n { flex:none; width:26px; height:26px; border-radius:50%; background:var(--blue); color:#fff; font-size:14px;
  font-weight:800; display:flex; align-items:center; justify-content:center; margin-top:1px; }
.act .b { flex:1; min-width:0; }
.act .at { font-size:16px; font-weight:700; color:var(--label); line-height:21px; }
.act .ad { font-size:14px; color:var(--label2); line-height:19px; margin-top:3px; }
.act .imp { flex:none; align-self:flex-start; font-size:12px; font-weight:800; font-variant-numeric:tabular-nums;
  padding:4px 8px; border-radius:8px; color:var(--green); background:color-mix(in srgb, var(--green) 15%, transparent);
  white-space:nowrap; }
.act .imp.neutral { color:var(--label2); background:var(--fill2); }
.wire .row { align-items:flex-start; padding:12px 16px; }
.wire .row .t1 { font-size:16px; font-weight:600; -webkit-line-clamp:3; }
.wire .row .t2 { white-space:normal; font-size:14px; line-height:19px; margin-top:3px; }
.wire .row .meta { display:flex; align-items:center; gap:8px; margin-bottom:5px; }
.wire .row .meta .when { font-size:12px; color:var(--label3); }

/* ---------- strengths & weaknesses ---------- */
.prof { background:var(--card); border-radius:22px; padding:16px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.prof .t { font-family:var(--display); font-size:19px; font-weight:800; letter-spacing:-.3px; color:var(--label); }
.prof .s { font-size:13px; color:var(--label2); margin-top:2px; }
.prof .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-top:14px; }
.pc { border-radius:14px; padding:10px 10px 9px; background:var(--fill2); position:relative; overflow:hidden; }
.pc.strength { background:color-mix(in srgb, var(--green) 14%, transparent); }
.pc.weakness { background:color-mix(in srgb, var(--red) 13%, transparent); }
.pc .k { font-size:12px; font-weight:700; letter-spacing:.3px; color:var(--label2); }
.pc .r { font-family:var(--display); font-size:26px; font-weight:800; letter-spacing:-.8px; color:var(--label);
  font-variant-numeric:tabular-nums; line-height:1.15; margin-top:2px; }
.pc.strength .r { color:var(--green); } .pc.weakness .r { color:var(--red); }
.pc .r small { font-size:13px; font-weight:600; color:var(--label3); letter-spacing:0; margin-left:1px; }
.pc .d { font-size:11px; color:var(--label2); font-variant-numeric:tabular-nums; margin-top:1px; }
.prof .adv { font-size:15px; line-height:21px; color:var(--label); margin-top:14px; padding-top:12px;
  border-top:.5px solid var(--sep); }

/* ---------- trade hub ---------- */
.fit { font-family:var(--display); font-size:20px; font-weight:800; letter-spacing:-.5px; font-variant-numeric:tabular-nums; }
.idea { background:var(--card); border-radius:20px; padding:14px 16px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.idea .hd { display:flex; align-items:center; gap:10px; }
.idea .hd .nm { font-size:15px; font-weight:700; color:var(--label); flex:1; min-width:0; }
.idea .sw { display:grid; grid-template-columns:1fr auto 1fr; gap:10px; align-items:center; margin-top:12px; }
.idea .sw .k { font-size:11px; font-weight:800; letter-spacing:.6px; color:var(--label3); text-transform:uppercase; }
.idea .sw .p { font-size:15px; font-weight:600; color:var(--label); line-height:20px; margin-top:2px; }
.idea .sw .ar { font-size:20px; color:var(--label3); }
.idea .sw .rt { text-align:right; }
.idea .nums { display:flex; gap:8px; margin-top:12px; }
.idea .nums span { font-size:12px; font-weight:800; padding:4px 8px; border-radius:8px; font-variant-numeric:tabular-nums;
  color:var(--green); background:color-mix(in srgb, var(--green) 14%, transparent); }
.idea .nums span.them { color:var(--blue); background:color-mix(in srgb, var(--blue) 14%, transparent); }
.idea .pt { font-size:14px; line-height:19px; color:var(--label2); margin-top:10px; }

/* ---------- lineup check ---------- */
.lu { background:var(--card); border-radius:22px; padding:14px 16px 4px; border:.5px solid var(--edge); box-shadow:var(--shadow); }
.lu .t { display:flex; align-items:center; gap:8px; font-family:var(--display); font-size:17px; font-weight:800; color:var(--label); }
.lu .t .dot { width:9px; height:9px; border-radius:50%; background:var(--orange); }
.lu .t .dot.ok { background:var(--green); }
.lu .i { padding:10px 0; border-top:.5px solid var(--sep); }
.lu .t + .i, .lu .t + .ok { margin-top:10px; }
.lu .i .a { font-size:15px; font-weight:700; color:var(--label); }
.lu .i .b { font-size:14px; color:var(--label2); margin-top:2px; line-height:19px; }
.lu .ok { font-size:14px; color:var(--label2); padding:0 0 10px; }

/* native widgets: soften to match, 44pt rows for radio lists */
[data-testid="stRadioGroup"] { gap:0 !important; width:100%; }
[data-testid="stRadioGroup"] > div { width:100%; }
[data-testid="stRadio"], [data-testid="stElementContainer"]:has(> [data-testid="stRadio"]) { width:100% !important; }
[data-testid="stRadioOption"] { min-height:44px; display:flex; align-items:center; margin:0 !important; width:100%; }
[data-testid="stRadioGroup"] > div + div { border-top:.5px solid var(--sep); }
[data-testid="stRadioOption"] p { font-size:17px !important; }
.st-key-onboard { background:var(--card); border-radius:18px; padding:10px 16px 4px; border:.5px solid var(--edge);
  box-shadow:var(--shadow); }
[data-testid="stExpander"] details { background:var(--card); border:.5px solid var(--edge) !important; border-radius:18px;
  box-shadow:var(--shadow); }
[data-testid="stExpander"] summary { min-height:52px; }
[data-testid="stExpander"] summary p { font-size:15px !important; font-weight:600; font-variant-numeric:tabular-nums; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] { padding-left:12px; padding-right:12px; }
@media (max-width:380px) { .hero .big { font-size:40px; } .cat { grid-template-columns:minmax(0,1fr) 96px minmax(0,1fr); } }
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
    if photo and nba_id is not None and nba_id == nba_id and int(nba_id) > 0:
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


def team_logo(url, name: str, size: str = "") -> str:
    """Team profile picture from Yahoo; initials in a circle when there isn't one."""
    cls = f"tl {size}".strip()
    if isinstance(url, str) and url.startswith("https://"):
        return (f'<img class="{cls}" src="{e(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">')
    return f'<div class="{cls}">{e(initials(name))}</div>'


def diverging(p: float) -> str:
    """Bar that grows from the center: left (blue) when the left team is favored, right
    (orange) when the right team is. Width is how far the odds are from a coin flip."""
    p = 0.5 if p != p else max(0.0, min(1.0, p))
    w = abs(p - 0.5) * 100
    side = "a" if p >= 0.5 else "b"
    return f'<div class="dv"><span class="{side}" style="width:{w:.1f}%"></span></div>'


TAG_COLORS = {"COIN FLIP": "orange", "UPSET WATCH": "red", "COMEBACK": "purple", "BLOWOUT": "gray",
              "IN CONTROL": "", "TIGHT": "orange", "TRADE": "purple", "SCOOPED": "orange", "STEAL": "green",
              "REGRET?": "red", "GRINDER": "", "HOT": "red", "QUIET": "gray", "STREAK": "green", "SKID": "red"}


def tag(label: str) -> str:
    return f'<span class="tag {TAG_COLORS.get(label, "")}">{e(label)}</span>'


def story_card(st_obj, logo_fn, names: dict, top: bool = False) -> str:
    """One headline card. ``logo_fn(team_key)`` renders a team picture."""
    kick = '<div class="kick">Top story</div><div style="height:8px"></div>' if top else ""
    foot = ""
    if st_obj.p:
        a, pa, b, pb = st_obj.p
        foot = (f'<div class="ft">{logo_fn(a)}<span class="pa">{pct(pa)}</span>'
                f'<div class="bar"><span style="width:{pa * 100:.1f}%"></span><span class="b"></span></div>'
                f'<span class="pb">{pct(pb)}</span>{logo_fn(b)}</div>')
    elif st_obj.teams:
        foot = '<div class="logos">' + "".join(logo_fn(t) for t in st_obj.teams[:3]) + "</div>"
    when = f' <span style="font-size:12px;color:var(--label3);margin-left:6px">{e(st_obj.when)}</span>' if st_obj.when else ""
    return (f'<div class="story{" top" if top else ""}">{kick}{tag(st_obj.tag)}{when}'
            f'<div class="hl">{e(st_obj.headline)}</div><div class="dk">{e(st_obj.dek)}</div>{foot}</div>')


def plan_card(plan, my_name: str, opp_name: str, logo_html: str = "") -> str:
    w = plan.p_win + plan.p_tie / 2
    color = "var(--green)" if w >= 0.6 else "var(--red)" if w < 0.4 else "var(--orange)"
    acts = []
    for i, a in enumerate(plan.actions, 1):
        neutral = "" if "→" in a.impact else " neutral"
        imp = f'<div class="imp{neutral}">{e(a.impact)}</div>' if a.impact else ""
        acts.append(f'<div class="act"><div class="n">{i}</div><div class="b"><div class="at">{e(a.title)}</div>'
                    f'<div class="ad">{e(a.detail)}</div></div>{imp}</div>')
    if not acts:
        acts.append('<div class="act"><div class="b"><div class="ad">Nothing urgent. Set your lineup and ride it.</div></div></div>')
    return (f'<div class="plan"><div class="ph">{logo_html}<div><div class="t">{e(plan.headline)}</div>'
            f'<div class="s">Your game plan vs {e(opp_name)}</div></div>'
            f'<div class="odds" style="color:{color}">{pct(w)}</div></div>{"".join(acts)}</div>')


def wire_list(stories, logo_fn) -> str:
    rows = []
    for s in stories:
        lead = logo_fn(s.teams[0]) if s.teams else '<div class="tl">🏀</div>'
        when = f'<span class="when">{e(s.when)}</span>' if s.when else ""
        rows.append(f'<div class="row">{lead}<div class="main"><div class="meta">{tag(s.tag)}{when}</div>'
                    f'<div class="t1">{e(s.headline)}</div><div class="t2">{e(s.dek)}</div></div></div>')
    return '<div class="list wire">' + "".join(rows) + "</div>"


def profile_card(prof, title: str, sub: str) -> str:
    cells = []
    for c, r in prof.table.iterrows():
        rank = r["avg_rank"]
        shown = int(round(rank))
        bits = []
        if r["results_rank"] is not None and r["results_rank"] == r["results_rank"]:
            bits.append(f"results #{int(r['results_rank'])}")
        if r["roster_rank"] is not None and r["roster_rank"] == r["roster_rank"]:
            bits.append(f"roster #{int(r['roster_rank'])}")
        cells.append(f'<div class="pc {r["label"]}"><div class="k">{e(c)}</div>'
                     f'<div class="r">{shown}<small>{_ordinal(shown)}</small></div>'
                     f'<div class="d">{e(" · ".join(bits))}</div></div>')
    return (f'<div class="prof"><div class="t">{e(title)}</div><div class="s">{e(sub)}</div>'
            f'<div class="grid">{"".join(cells)}</div><div class="adv">{e(prof.advice)}</div></div>')


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def idea_card(idea, partner_name: str, logo_html: str) -> str:
    give = "<br>".join(e(n) for n in idea.give_names)
    get = "<br>".join(e(n) for n in idea.get_names)
    return (f'<div class="idea"><div class="hd">{logo_html}<div class="nm">with {e(partner_name)}</div></div>'
            f'<div class="sw"><div><div class="k">You send</div><div class="p">{give}</div></div>'
            f'<div class="ar">⇄</div><div class="rt"><div class="k">You get</div><div class="p">{get}</div></div></div>'
            f'<div class="nums"><span>You {signed(idea.mine)}</span><span class="them">Them {signed(idea.theirs)}</span></div>'
            f'<div class="pt">{e(idea.pitch)}</div></div>')


def lineup_card(issues, today_label: str) -> str:
    if not issues:
        return (f'<div class="lu"><div class="t"><span class="dot ok"></span>Lineup check · {e(today_label)}</div>'
                '<div class="ok">Your lineup is set. Every active player has a game or there is no better option on the bench.</div></div>')
    rows = "".join(f'<div class="i"><div class="a">{e(i.title)}</div><div class="b">{e(i.detail)}</div></div>'
                   for i in issues)
    n = len(issues)
    return (f'<div class="lu"><div class="t"><span class="dot"></span>{n} lineup fix{"es" if n != 1 else ""} · '
            f'{e(today_label)}</div>{rows}</div>')


def group(rows: list[str]) -> None:
    st.html('<div class="list">' + "".join(rows) + "</div>")
