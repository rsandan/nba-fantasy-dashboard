"""Scoring-category definitions for a standard Yahoo 9-cat H2H league.

Everything downstream (parsing, valuation, simulation) keys off this module so a
league that swaps a category (e.g. 3PT% for 3PTM, or adds DD) only needs edits here.

Two kinds of category:

* ``count``  - additive per game (PTS, REB, ...). Weekly total = sum of games.
* ``pct``    - a ratio of two additive components (FGM/FGA). The weekly value is
               sum(makes) / sum(attempts), never the mean of per-game percentages.
               Averaging percentages is the most common fantasy-analytics bug and
               it silently overweights low-volume games.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    name: str                      # display name, as Yahoo shows it
    kind: str                      # "count" | "pct"
    higher_is_better: bool = True
    stat: str | None = None        # internal column for count stats
    makes: str | None = None       # internal columns for pct stats
    attempts: str | None = None
    decimals: int = 0              # Yahoo's display precision (ties resolve at this precision)


CATEGORIES: tuple[Category, ...] = (
    Category("FG%", "pct", makes="FGM", attempts="FGA", decimals=3),
    Category("FT%", "pct", makes="FTM", attempts="FTA", decimals=3),
    Category("3PTM", "count", stat="FG3M"),
    Category("PTS", "count", stat="PTS"),
    Category("REB", "count", stat="REB"),
    Category("AST", "count", stat="AST"),
    Category("STL", "count", stat="STL"),
    Category("BLK", "count", stat="BLK"),
    Category("TO", "count", higher_is_better=False, stat="TOV"),
)

CATEGORY_NAMES: list[str] = [c.name for c in CATEGORIES]
BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}

# Additive per-game components. Every category is a function of these, which is what
# lets us simulate percentages correctly (simulate makes and attempts, then divide).
COUNT_COLS: list[str] = [c.stat for c in CATEGORIES if c.kind == "count"]
PCT_PARTS: list[tuple[str, str]] = [(c.makes, c.attempts) for c in CATEGORIES if c.kind == "pct"]
ADDITIVE_COLS: list[str] = ["FGM", "FGA", "FTM", "FTA", *COUNT_COLS]

# Yahoo NBA stat ids (stable across seasons for the standard game).
YAHOO_STAT_IDS: dict[str, str] = {
    "9004003": "FGM/A",
    "5": "FG%",
    "9007006": "FTM/A",
    "8": "FT%",
    "10": "3PTM",
    "12": "PTS",
    "15": "REB",
    "16": "AST",
    "17": "STL",
    "18": "BLK",
    "19": "TO",
}
YAHOO_NAME_TO_COL: dict[str, str] = {
    "3PTM": "FG3M", "PTS": "PTS", "REB": "REB", "AST": "AST",
    "STL": "STL", "BLK": "BLK", "TO": "TOV",
}


def category_value(totals: dict, cat: Category) -> float:
    """Weekly category value from additive totals (dict or pandas row)."""
    if cat.kind == "pct":
        att = float(totals[cat.attempts])
        return float(totals[cat.makes]) / att if att > 0 else float("nan")
    return float(totals[cat.stat])


def compare(a: float, b: float, cat: Category) -> float:
    """1 if a wins the category, 0 if it loses, 0.5 on a tie (at Yahoo precision).

    A team with zero attempts has an undefined percentage; Yahoo treats that as a
    loss to any team with a defined value and a tie with another undefined one.
    """
    a_nan, b_nan = a != a, b != b  # NaN check without numpy
    if a_nan and b_nan:
        return 0.5
    if a_nan:
        return 0.0
    if b_nan:
        return 1.0
    a, b = round(a, cat.decimals), round(b, cat.decimals)
    if a == b:
        return 0.5
    return 1.0 if (a > b) == cat.higher_is_better else 0.0
