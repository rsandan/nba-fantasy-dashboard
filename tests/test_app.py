"""Smoke test: every page renders without exceptions on the demo league."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["page_home", "page_power", "page_matchup_lab", "page_values", "page_trade",
         "page_streaming", "page_free_agency", "page_compare"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page, monkeypatch):
    monkeypatch.setenv("FANTASY_DEMO", "1")
    src = (ROOT / "app.py").read_text().replace("nav.run()", f"{page}()")
    script = ROOT / f"_pagetest_{page}.py"   # same dir so relative assets (logo) resolve
    script.write_text(src)
    try:
        at = AppTest.from_file(str(script), default_timeout=300)
        at.run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.title, "page should render a title"
    finally:
        script.unlink(missing_ok=True)
