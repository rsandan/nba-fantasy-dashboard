"""Scoring categories, per league.

Every league scores differently: 9-cat with 3PT% (Love Island), 9-cat with 3PTM, 8-cat
without turnovers, 11-cat with A/T and FGM, or head-to-head points. This module turns a
league's Yahoo settings into a ``Scoring`` and makes it the *active* scoring for the
current Streamlit session, so every model downstream (valuation, simulations, trades)
reads the same definition without passing it through dozens of call sites.

Why a ContextVar: Streamlit runs each browser session's script in its own thread, and
two people in different leagues can be on the site at the same time. A module-level
global would let one session's league leak into the other's numbers. A ContextVar is
per-thread, so each run sets its own league's scoring at the top and nothing leaks.
Anything cached across sessions must include ``active().sig`` in its cache key.

Kinds of category:

* ``count``  - additive per game (PTS, REB, 3PTM ...). Weekly total = sum of games.
* ``pct``    - a ratio of two additive totals: FG% = FGM/FGA, and A/T = AST/TOV. The
               weekly value is sum(numerator) / sum(denominator), never the mean of
               per-game ratios (that silently overweights low-volume games).
* ``points`` - head-to-head points leagues: one category, a weighted sum of stats.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# Additive per-game stats the NBA data and the simulator both carry. A category or a
# points modifier built only from these can be modeled; anything else (double-doubles,
# offensive rebounds, fouls) is reported as unsupported instead of guessed at.
ADDITIVE_COLS: list[str] = ["FGM", "FGA", "FTM", "FTA", "FG3M", "FG3A",
                            "PTS", "REB", "AST", "STL", "BLK", "TOV"]


@dataclass(frozen=True)
class Category:
    name: str                      # display name, as Yahoo shows it (ST shown as STL)
    kind: str                      # "count" | "pct" | "points"
    higher_is_better: bool = True
    stat: str | None = None        # internal column for count stats
    makes: str | None = None       # numerator / denominator columns for pct stats
    attempts: str | None = None
    decimals: int = 0              # Yahoo's display precision (ties resolve at this precision)
    weights: tuple = ()            # points: ((column, points per unit), ...)

    @property
    def columns(self) -> tuple[str, ...]:
        if self.kind == "pct":
            return (self.makes, self.attempts)
        if self.kind == "points":
            return tuple(c for c, _ in self.weights)
        return (self.stat,)

    @property
    def is_shooting(self) -> bool:
        return self.kind == "pct" and self.makes in ("FGM", "FTM", "FG3M")


# ------------------------------------------------------------------ the catalog
def _pct(name, makes, attempts, decimals=3):
    return Category(name, "pct", makes=makes, attempts=attempts, decimals=decimals)


def _count(name, stat, higher=True):
    return Category(name, "count", higher_is_better=higher, stat=stat)


# Every NBA category we can model, keyed by our display name.
CATALOG: dict[str, Category] = {c.name: c for c in (
    _pct("FG%", "FGM", "FGA"), _pct("FT%", "FTM", "FTA"), _pct("3PT%", "FG3M", "FG3A"),
    _pct("A/T", "AST", "TOV", decimals=2),
    _count("PTS", "PTS"), _count("REB", "REB"), _count("AST", "AST"), _count("STL", "STL"),
    _count("BLK", "BLK"), _count("TO", "TOV", higher=False), _count("3PTM", "FG3M"),
    _count("FGM", "FGM"), _count("FGA", "FGA"), _count("FTM", "FTM"), _count("FTA", "FTA"),
    _count("3PTA", "FG3A"),
)}

# Yahoo NBA stat ids -> our names. Yahoo also sends display names, which we match first;
# ids are the fallback. Ids with no entry in CATALOG are real Yahoo stats we can't model.
YAHOO_STAT_IDS: dict[str, str] = {
    "0": "GP", "1": "GS", "2": "MIN", "3": "FGA", "4": "FGM", "5": "FG%", "6": "FTA", "7": "FTM",
    "8": "FT%", "9": "3PTA", "10": "3PTM", "11": "3PT%", "12": "PTS", "13": "OREB", "14": "DREB",
    "15": "REB", "16": "AST", "17": "STL", "18": "BLK", "19": "TO", "20": "A/T", "21": "PF",
    "22": "DISQ", "23": "TECH", "24": "EJCT", "25": "FF", "26": "MPG", "27": "DD", "28": "TD",
    "9004003": "FGM/A", "9007006": "FTM/A", "9010009": "3PTM/A",
}
_DISPLAY_ALIASES = {"ST": "STL", "STL": "STL", "TO": "TO", "TOV": "TO", "3PM": "3PTM", "3PA": "3PTA",
                    "AST/TO": "A/T", "A/TO": "A/T"}
# Our name -> column, for parsing team stats and points modifiers.
STAT_TO_COL: dict[str, str] = {
    "FGM": "FGM", "FGA": "FGA", "FTM": "FTM", "FTA": "FTA", "3PTM": "FG3M", "3PTA": "FG3A",
    "PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "TO": "TOV",
}
SPLIT_STATS = {"FGM/A": ("FGM", "FGA"), "FTM/A": ("FTM", "FTA"), "3PTM/A": ("FG3M", "FG3A")}

# Backward-compatible names used by the Yahoo parser.
YAHOO_NAME_TO_COL = {"3PTM": "FG3M", "3PTA": "FG3A", "PTS": "PTS", "REB": "REB", "AST": "AST",
                     "STL": "STL", "BLK": "BLK", "TO": "TOV", "FGM": "FGM", "FGA": "FGA",
                     "FTM": "FTM", "FTA": "FTA"}


def stat_name(stat_id, display: str | None = None) -> str:
    """Our name for a Yahoo stat, from its display name if we know it, else its id."""
    d = (display or "").strip().upper()
    if d in _DISPLAY_ALIASES:
        return _DISPLAY_ALIASES[d]
    if d in CATALOG or d in SPLIT_STATS:
        return d
    return YAHOO_STAT_IDS.get(str(stat_id), d or str(stat_id))


# ------------------------------------------------------------------ league scoring
FORMATS = {
    "head": "Head-to-head categories",
    "headone": "Head-to-head, one win per week",
    "headpoint": "Head-to-head points",
    "roto": "Rotisserie",
    "point": "Season points",
}


@dataclass(frozen=True)
class Scoring:
    categories: tuple[Category, ...]
    fmt: str = "head"
    unsupported: tuple[str, ...] = ()          # league stats we can't model, by Yahoo name
    _by_name: dict = field(default=None, compare=False, hash=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "_by_name", {c.name: c for c in self.categories})

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.categories]

    @property
    def by_name(self) -> dict[str, Category]:
        return self._by_name

    @property
    def is_points(self) -> bool:
        return any(c.kind == "points" for c in self.categories)

    @property
    def weekly(self) -> bool:
        """Has weekly head-to-head matchups (everything but roto / season points)."""
        return self.fmt in ("head", "headone", "headpoint")

    @property
    def each_category_counts(self) -> bool:
        """Standings count every category (6-3), rather than one win per matchup."""
        return self.fmt == "head"

    @property
    def label(self) -> str:
        if self.is_points:
            return "points"
        return f"{len(self.categories)}-cat"

    @property
    def sig(self) -> str:
        """Short stable fingerprint, for cache keys shared across leagues."""
        raw = repr((self.fmt, [(c.name, c.kind, c.higher_is_better, c.weights) for c in self.categories]))
        return hashlib.sha1(raw.encode()).hexdigest()[:10]

    def describe(self) -> str:
        what = "Points" if self.is_points else " · ".join(self.names)
        return f"{FORMATS.get(self.fmt, self.fmt)} · {what}"


DEFAULT = Scoring(tuple(CATALOG[n] for n in
                        ("FG%", "FT%", "3PT%", "PTS", "REB", "AST", "STL", "BLK", "TO")), "head")

# The default league's definitions as plain constants, for scripts and tests. App code
# must use ``cats()`` / ``cat_names()`` / ``by_name()`` so each session gets its own league.
CATEGORIES: tuple[Category, ...] = DEFAULT.categories
CATEGORY_NAMES: list[str] = DEFAULT.names
BY_NAME: dict[str, Category] = DEFAULT.by_name

# Yahoo's default points scoring, used for the "Points" sort in category leagues.
YAHOO_DEFAULT_POINTS = Category("FPTS", "points", decimals=1, weights=(
    ("PTS", 1.0), ("REB", 1.2), ("AST", 1.5), ("STL", 3.0), ("BLK", 3.0), ("TOV", -1.0)))


def scoring_from_settings(settings: dict) -> Scoring:
    """Build a league's Scoring from Yahoo's raw ``league/{key}/settings`` document.

    ``settings`` is ``fantasy_content.league[1].settings[0]`` plus ``scoring_type``.
    """
    fmt = str(settings.get("scoring_type") or "head")
    stats = [s.get("stat", s) for s in (settings.get("stat_categories") or {}).get("stats", [])]
    scored = [s for s in stats
              if str(s.get("enabled", "1")) == "1" and not str(s.get("is_only_display_stat", "0")) == "1"]
    unsupported: list[str] = []

    if fmt in ("headpoint", "point"):
        mods = {str(m.get("stat", m).get("stat_id")): float(m.get("stat", m).get("value", 0))
                for m in (settings.get("stat_modifiers") or {}).get("stats", [])}
        names = {str(s.get("stat_id")): stat_name(s.get("stat_id"), s.get("display_name")) for s in stats}
        weights = []
        for sid, w in mods.items():
            name = names.get(sid) or stat_name(sid)
            col = STAT_TO_COL.get(name)
            if col is None:
                if w:
                    unsupported.append(name)
                continue
            if w:
                weights.append((col, w))
        if not weights:
            weights = list(YAHOO_DEFAULT_POINTS.weights)
        return Scoring((Category("FPTS", "points", decimals=1, weights=tuple(weights)),), fmt,
                       tuple(unsupported))

    cats: list[Category] = []
    for s in scored:
        name = stat_name(s.get("stat_id"), s.get("display_name"))
        base = CATALOG.get(name)
        if base is None:
            unsupported.append(name)
            continue
        order = s.get("sort_order")
        if order is not None and str(order) in ("0", "1"):
            higher = str(order) == "1"
            if higher != base.higher_is_better:
                base = Category(base.name, base.kind, higher, base.stat, base.makes, base.attempts,
                                base.decimals, base.weights)
        if base.name not in [c.name for c in cats]:
            cats.append(base)
    if not cats:
        return Scoring(DEFAULT.categories, fmt, tuple(unsupported))
    return Scoring(tuple(cats), fmt, tuple(unsupported))


# ------------------------------------------------------------------ active scoring
_ACTIVE: ContextVar[Scoring] = ContextVar("fantasy_scoring", default=DEFAULT)


def active() -> Scoring:
    return _ACTIVE.get()


def use(scoring: Scoring) -> None:
    """Make ``scoring`` active for the rest of this thread's script run."""
    _ACTIVE.set(scoring)


@contextmanager
def scoped(scoring: Scoring):
    token = _ACTIVE.set(scoring)
    try:
        yield scoring
    finally:
        _ACTIVE.reset(token)


def cats() -> tuple[Category, ...]:
    return _ACTIVE.get().categories


def cat_names() -> list[str]:
    return _ACTIVE.get().names


def by_name() -> dict[str, Category]:
    return _ACTIVE.get().by_name


def z_cols() -> list[str]:
    return [f"z_{c}" for c in cat_names()]


# ------------------------------------------------------------------ values
def category_value(totals, cat: Category) -> float:
    """Weekly category value from additive totals (dict or pandas row)."""
    if cat.kind == "pct":
        att = float(totals[cat.attempts])
        return float(totals[cat.makes]) / att if att > 0 else float("nan")
    if cat.kind == "points":
        return float(sum(float(totals[c]) * w for c, w in cat.weights))
    return float(totals[cat.stat])


def per_week_value(tot, cat: Category, weeks):
    """Season value from summed totals: makes/attempts for ratios, per-week average otherwise.

    ``tot`` is a DataFrame of summed additive columns (one row per team) and ``weeks`` the
    matching week counts. Returns NaN where a needed column isn't reported.
    """
    import pandas as pd
    if any(c not in tot for c in cat.columns):
        return pd.Series(float("nan"), index=tot.index)
    if cat.kind == "pct":
        return tot[cat.makes] / tot[cat.attempts].where(tot[cat.attempts] > 0)
    if cat.kind == "points":
        return sum(tot[c] * w for c, w in cat.weights) / weeks
    return tot[cat.stat] / weeks


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


def fmt_value(value: float, cat: Category) -> str:
    """How Yahoo shows a category value: .478, 1.85, 1,234 or 1,234.5."""
    if value != value:
        return "–"
    if cat.kind == "pct":
        s = f"{value:.{cat.decimals}f}"
        return s.lstrip("0") if abs(value) < 1 else s
    return f"{value:,.{cat.decimals}f}"
