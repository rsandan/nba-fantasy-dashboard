"""Load + isolation test: many people, many leagues, one server, all at once.

Starts ``scripts/fake_server_app.py`` (the real app on fake Yahoo leagues), then opens
``--per-league`` browsers for every league *simultaneously* and, for each one:

* times the first full render (cold: Yahoo fetch + models) and a second wave (warm),
* opens This Week (or Standings for roto) and checks it shows *that league's* scoring
  and nothing from another league (a 3PTM league must not show 3PT%, a points league
  must show FPTS and no FG%),
* fails on any Streamlit exception box.

Prints a summary and the server's peak memory. Usage:

    python scripts/load_test.py --per-league 2 --latency 0.15
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fantasy import fake_yahoo as fy  # noqa: E402
from fantasy.yahoo_client import scoring_from_raw_settings  # noqa: E402

PORT = 8655
IDLE = "!document.querySelector('[data-testid=\"stStatusWidget\"]')"


def expectations(slug: str) -> tuple[list[str], list[str]]:
    """(must appear, must not appear) on the league's main matchup/standings page."""
    sc = scoring_from_raw_settings(fy.settings_json(slug))
    must = list(sc.names) if sc.weekly else []
    others = {"3PT%", "3PTM", "FPTS", "A/T", "FGM"} - set(sc.names)
    if sc.is_points:
        others |= {"FG%", "FT%"}
    return must, sorted(others)


async def visit(browser, slug: str, results: list, wave: str):
    from playwright.async_api import TimeoutError as PWTimeout
    key = fy.league_key(slug)
    ctx = await browser.new_context(viewport={"width": 393, "height": 852})
    page = await ctx.new_page()
    rec = {"slug": slug, "wave": wave, "errors": []}
    try:
        t0 = time.perf_counter()
        await page.goto(f"http://localhost:{PORT}/?league={key}&team=2", timeout=180_000)
        await page.wait_for_selector(".st-key-tabbar", timeout=180_000)
        await page.wait_for_function("!document.querySelector('[data-testid=\"stStatusWidget\"]')",
                                     timeout=180_000)
        rec["first_render_s"] = time.perf_counter() - t0
        weekly = scoring_from_raw_settings(fy.settings_json(slug)).weekly
        t1 = time.perf_counter()
        target = "week" if weekly else "standings"
        await page.get_by_text("This Week" if weekly else "Standings", exact=True).last.click()
        await page.wait_for_url(f"**/{target}**", timeout=60_000)
        heading = "Week " if weekly else "Standings"
        await page.wait_for_function(
            f"[...document.querySelectorAll('.lt .h1')].some(e => e.textContent.startsWith({heading!r}))",
            timeout=180_000)
        must, must_not = expectations(slug)
        # Streamlit streams a page in; wait until this league's category labels are all
        # on screen (or give up after 60 s and report what's missing).
        probe = " && ".join(f"document.body.innerText.includes({m!r})" for m in must) or "true"
        try:
            await page.wait_for_function(probe, timeout=60_000)
        except PWTimeout:
            pass
        await page.wait_for_function(IDLE, timeout=180_000)
        rec["tab_switch_s"] = time.perf_counter() - t1
        body = await page.inner_text("body")
        for m in must:
            if m not in body:
                rec["errors"].append(f"missing {m}")
        for m in must_not:
            if m in body:
                rec["errors"].append(f"leaked {m}")
        if fy.LEAGUES[slug]["name"].split()[0] not in body and weekly:
            rec["errors"].append("league name missing")
        n_exc = await page.locator('[data-testid="stException"]').count()
        if n_exc:
            rec["errors"].append(f"{n_exc} exception(s)")
    except PWTimeout as exc:
        rec["errors"].append(f"timeout: {exc}")
    finally:
        await ctx.close()
        results.append(rec)


def cpu_s(pid: int) -> float:
    try:
        import psutil
        proc = psutil.Process(pid)
        return sum(sum(p.cpu_times()[:2]) for p in [proc, *proc.children(recursive=True)])
    except Exception:
        return float("nan")


async def run(per_league: int, slugs: list[str], pid: int | None = None):
    from playwright.async_api import async_playwright
    results: list[dict] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for wave in ("cold", "warm"):
            t0, c0 = time.perf_counter(), cpu_s(pid) if pid else float("nan")
            await asyncio.gather(*[visit(browser, s, results, wave) for s in slugs for _ in range(per_league)])
            wall, cpu = time.perf_counter() - t0, (cpu_s(pid) - c0) if pid else float("nan")
            n = len(slugs) * per_league
            print(f"{wave} wave: {n} sessions at once in {wall:.1f}s wall; server used {cpu:.1f} CPU-s "
                  f"({cpu / n:.2f} CPU-s per visit: home + one tab)")
        await browser.close()
    return results


def rss_mb(pid: int) -> float:
    try:
        import psutil
        proc = psutil.Process(pid)
        return sum(p.memory_info().rss for p in [proc, *proc.children(recursive=True)]) / 2**20
    except Exception:
        return float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-league", type=int, default=2)
    ap.add_argument("--latency", type=float, default=0.15)
    ap.add_argument("--leagues", nargs="*", default=list(fy.LEAGUES))
    args = ap.parse_args()

    env = {**os.environ, "FANTASY_DEMO": "1", "FAKE_YAHOO_LATENCY": str(args.latency)}
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "scripts/fake_server_app.py",
                               "--server.headless", "true", "--server.port", str(PORT)],
                              cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        time.sleep(6)
        base = rss_mb(server.pid)
        peak = [base]
        stop = False

        async def sample():
            while not stop:
                peak.append(rss_mb(server.pid))
                await asyncio.sleep(0.5)

        async def both():
            nonlocal stop
            s = asyncio.create_task(sample())
            res = await run(args.per_league, args.leagues, server.pid)
            stop = True
            await s
            return res

        results = asyncio.run(both())
    finally:
        server.terminate()
        err = server.stderr.read().decode(errors="ignore") if server.stderr else ""

    bad = [r for r in results if r["errors"]]
    for wave in ("cold", "warm"):
        rs = [r for r in results if r["wave"] == wave and "first_render_s" in r]
        if rs:
            fr = [r["first_render_s"] for r in rs]
            ts = [r["tab_switch_s"] for r in rs if "tab_switch_s" in r]
            print(f"{wave:>4}: first render median {statistics.median(fr):.1f}s, max {max(fr):.1f}s; "
                  f"tab switch median {statistics.median(ts):.1f}s, max {max(ts):.1f}s")
    print(f"server memory: {peak[0]:.0f} MB idle -> {max(peak):.0f} MB peak")
    print(f"sessions: {len(results)}, with problems: {len(bad)}")
    for r in bad:
        print("  ", r["slug"], r["wave"], r["errors"][:4])
    if "Traceback" in err:
        print("server traceback:\n", err[-3000:])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
