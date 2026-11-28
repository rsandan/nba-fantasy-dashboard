"""Run the real app against fake Yahoo leagues (every scoring format), for load tests.

    FANTASY_DEMO=1 FAKE_YAHOO_LATENCY=0.15 streamlit run scripts/fake_server_app.py

Every browser session is "signed in" as a test user who belongs to all the leagues in
``fantasy/fake_yahoo.py``; ``?league=<key>`` picks one. Each fake Yahoo call sleeps
FAKE_YAHOO_LATENCY seconds to mimic the network. Never deploy this file.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import streamlit as st  # noqa: E402

from fantasy import fake_yahoo  # noqa: E402

@st.cache_resource(show_spinner=False)
def _fake_sources(latency: float) -> dict:
    return {fake_yahoo.league_key(s): fake_yahoo.FakeSource(s, latency) for s in fake_yahoo.LEAGUES}


fake_yahoo.seed_session(st.session_state,
                        sources=_fake_sources(float(os.getenv("FAKE_YAHOO_LATENCY", "0"))))
_app = ROOT / "app.py"
exec(compile(_app.read_text(), str(_app), "exec"))
