"""Plotly figure builders. Palette: validated reference categorical/diverging steps for a
dark surface (the app runs Streamlit's dark theme). Rules followed: one axis per chart,
thin marks, recessive grid, diverging scales centred on a neutral gray, text in ink
colours rather than series colours, hover on every mark."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

SURFACE = "#1a1a19"
INK = "#ffffff"
INK_2 = "#c3c2b7"
GRID = "#383835"
BLUE = "#3987e5"       # categorical slot 1 / diverging positive pole
ORANGE = "#d95926"     # categorical slot 2
RED = "#e66767"        # diverging negative pole
NEUTRAL = "#383835"    # diverging midpoint
SEQ_BLUE = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]
DIVERGING = [[0.0, RED], [0.5, NEUTRAL], [1.0, BLUE]]


def _base(fig: go.Figure, height: int = 380) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK_2, size=12), height=height, margin=dict(l=10, r=10, t=30, b=10),
        hoverlabel=dict(bgcolor=SURFACE, font_color=INK), showlegend=False,
    )
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


def luck_bars(df: pd.DataFrame, value_col: str, label_col: str = "team_name") -> go.Figure:
    d = df.sort_values(value_col)
    colors = [BLUE if v >= 0 else RED for v in d[value_col]]
    fig = go.Figure(go.Bar(
        x=d[value_col], y=d[label_col], orientation="h", marker_color=colors,
        marker_line_width=0, hovertemplate="%{y}: %{x:+.1%}<extra></extra>"))
    fig.update_xaxes(tickformat="+.0%", title="Actual win% minus all-play win%")
    return _base(fig, 40 * len(d) + 60)


def category_heatmap(rates: pd.DataFrame, names: dict[str, str]) -> go.Figure:
    z = rates.values
    fig = go.Figure(go.Heatmap(
        z=z, x=list(rates.columns), y=[names.get(i, i) for i in rates.index],
        colorscale=DIVERGING, zmid=0.5, zmin=0, zmax=1, xgap=2, ygap=2,
        text=np.vectorize(lambda v: f"{v:.0%}")(z), texttemplate="%{text}",
        textfont=dict(color=INK, size=11),
        hovertemplate="%{y} · %{x}: wins %{z:.0%} of all-play category matchups<extra></extra>",
        colorbar=dict(tickformat=".0%", title="")))
    return _base(fig, 36 * len(rates) + 80)


def probability_bars(df: pd.DataFrame, value_col: str, label_col: str = "team_name",
                     title: str = "") -> go.Figure:
    d = df.sort_values(value_col)
    fig = go.Figure(go.Bar(
        x=d[value_col], y=d[label_col], orientation="h", marker_color=BLUE, marker_line_width=0,
        text=[f"{v:.0%}" for v in d[value_col]], textposition="outside", textfont=dict(color=INK_2),
        hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
    fig.update_xaxes(range=[0, 1.12], tickformat=".0%", title=title)
    return _base(fig, 40 * len(d) + 60)


def calibration_plot(cal: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(color=INK_2, dash="dot", width=1),
                             hoverinfo="skip", name="Perfect calibration"))
    fig.add_trace(go.Scatter(
        x=cal["mean_pred"], y=cal["observed"], mode="lines+markers", line=dict(color=BLUE, width=2),
        marker=dict(size=9 + 10 * cal["n"] / max(cal["n"].max(), 1), color=BLUE,
                    line=dict(color=SURFACE, width=2)),
        customdata=cal["n"], name="Model",
        hovertemplate="Predicted %{x:.0%} → happened %{y:.0%} (n=%{customdata})<extra></extra>"))
    fig.update_xaxes(range=[0, 1], tickformat=".0%", title="Predicted category win probability")
    fig.update_yaxes(range=[0, 1], tickformat=".0%", title="Observed win rate")
    fig = _base(fig, 420)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.08, x=0))
    return fig


def timing_heatmap(grid: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Heatmap(
        z=grid.values, x=[f"{h:02d}:00" for h in grid.columns], y=list(grid.index),
        colorscale=[[0, SURFACE], [0.25, "#184f95"], [0.5, "#256abf"], [0.75, BLUE], [1, "#9ec5f4"]],
        xgap=2, ygap=2, hovertemplate="%{y} %{x}: %{z} adds<extra></extra>", colorbar=dict(title="")))
    fig.update_xaxes(title="Hour (ET)")
    fig.update_yaxes(autorange="reversed")  # Monday on top, like a calendar
    return _base(fig, 300)


def activity_scatter(frame: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=frame["add"], y=frame["ap_pct"], mode="markers+text", text=frame["team_name"],
        textposition="top center", textfont=dict(color=INK_2, size=11),
        marker=dict(size=11, color=BLUE, line=dict(color=SURFACE, width=2)),
        hovertemplate="%{text}<br>%{x} adds · all-play %{y:.1%}<extra></extra>"))
    fig.update_xaxes(title="Players added")
    fig.update_yaxes(title="All-play win%", tickformat=".0%")
    return _base(fig, 420)


def hold_histogram(holds: pd.DataFrame) -> go.Figure:
    d = holds[holds["dropped"]]
    fig = go.Figure(go.Histogram(x=d["hold_days"], xbins=dict(start=0, size=1), marker_color=BLUE,
                                 marker_line=dict(color=SURFACE, width=2),
                                 hovertemplate="%{x} days: %{y} pickups<extra></extra>"))
    fig.update_xaxes(title="Days rostered before being dropped")
    fig.update_yaxes(title="Pickups")
    return _base(fig, 320)


def category_win_bars(cat_prob: pd.Series, team_a: str, team_b: str) -> go.Figure:
    """Diverging bars: P(A wins category) - 0.5, so the zero line is a coin flip."""
    p = cat_prob
    fig = go.Figure(go.Bar(
        x=p.values - 0.5, y=list(p.index), orientation="h", base=0.5,
        marker_color=[BLUE if v >= 0.5 else ORANGE for v in p.values], marker_line_width=0,
        hovertemplate=[f"{c}: {team_a} {v:.0%} · {team_b} {1 - v:.0%}<extra></extra>"
                       for c, v in p.items()]))
    fig.update_xaxes(range=[0, 1], tickformat=".0%", title=f"← {team_b}   ·   {team_a} →")
    fig.update_yaxes(autorange="reversed")
    return _base(fig, 320)
