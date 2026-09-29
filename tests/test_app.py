"""Smoke test: every page renders without exceptions on the demo league, both for a
first-time visitor and for someone whose team is saved in the link (?team=1)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["page_week", "page_standings", "page_players", "page_trade"]


@pytest.mark.parametrize("team", [None, "1"])
@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page, team, monkeypatch):
    monkeypatch.setenv("FANTASY_DEMO", "1")
    src = (ROOT / "app.py").read_text().replace("nav.run()", f"{page}()")
    script = ROOT / f"_pagetest_{page}.py"   # same dir so relative assets resolve
    script.write_text(src)
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        if team:
            at.query_params["team"] = team
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert len(at.main.children) > 2, "page should render content"
    finally:
        script.unlink(missing_ok=True)
