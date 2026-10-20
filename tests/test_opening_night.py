"""Opening night: week 1 is live, no completed weeks, and stats.nba.com has no
current-season lines yet. Every page must still render (this is the live league's
state on the first day of the season)."""
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from fantasy import demo  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["page_week", "page_standings", "page_players", "page_trade"]


def opening_night_league(*args, **kwargs):
    kwargs.update(completed_weeks=0, current_progress=0.03)
    bundle = demo._make_league_orig(*args, **kwargs)
    bundle["players"] = bundle["players"].iloc[0:0]   # no 2026-27 games logged yet
    return bundle


@pytest.mark.parametrize("team", [None, "1"])
@pytest.mark.parametrize("page", PAGES)
def test_opening_night(page, team, monkeypatch):
    monkeypatch.setenv("FANTASY_DEMO", "1")
    if not hasattr(demo, "_make_league_orig"):
        demo._make_league_orig = demo.make_league
    monkeypatch.setattr(demo, "make_league", opening_night_league)
    src = (ROOT / "app.py").read_text().replace("nav.run()", f"{page}()")
    script = ROOT / f"_opening_{page}.py"
    script.write_text(src)
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        if team:
            at.query_params["team"] = team
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert len(at.main.children) > 2
    finally:
        script.unlink(missing_ok=True)
