"""Dark theme + small HTML helpers for the dashboard.

Colours come from one place so status badges, borders and meters stay
consistent across every page.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from models.types import status_style

COLORS = {
    "bg": "#0b0e13",
    "panel": "#141922",
    "panel_alt": "#1a202b",
    "border": "#242c3a",
    "text": "#e8eaed",
    "muted": "#96a0b0",
    "accent": "#d20a0a",
    "accent_soft": "#ff3b3b",
    "green": "#19c37d",
    "yellow": "#e8c547",
    "orange": "#ff9130",
    "red": "#ef4444",
    "blue": "#3b82f6",
    "grey": "#9aa4b2",
}

CSS = f"""
<style>
  .stApp {{ background: {COLORS['bg']}; }}
  .block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px; }}

  .radar-header {{
      display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
      border-bottom: 2px solid {COLORS['accent']}; padding-bottom: 10px; margin-bottom: 14px;
  }}
  .radar-title {{
      font-size: 2.05rem; font-weight: 800; letter-spacing: 2.5px;
      color: {COLORS['text']}; margin: 0; text-transform: uppercase;
  }}
  .radar-title span {{ color: {COLORS['accent_soft']}; }}
  .radar-sub {{ color: {COLORS['muted']}; font-size: 0.86rem; }}
  .radar-title.story {{
      font-size: 1.55rem; letter-spacing: 0.4px; text-transform: none; line-height: 1.3;
      max-width: 1000px;
  }}

  .metric-row {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 6px 0 18px 0; }}
  .metric-box {{
      background: {COLORS['panel']}; border: 1px solid {COLORS['border']};
      border-radius: 10px; padding: 9px 14px; min-width: 104px;
  }}
  .metric-box .label {{
      color: {COLORS['muted']}; font-size: 0.68rem; text-transform: uppercase;
      letter-spacing: 1px; margin-bottom: 2px;
  }}
  .metric-box .value {{ color: {COLORS['text']}; font-size: 1.32rem; font-weight: 700; line-height: 1.1; }}
  .metric-box.alert .value {{ color: {COLORS['accent_soft']}; }}

  .section-head {{
      display: flex; align-items: center; gap: 10px; margin: 26px 0 8px 0;
      padding-bottom: 6px; border-bottom: 1px solid {COLORS['border']};
  }}
  .section-head h2 {{
      font-size: 1.12rem; font-weight: 700; letter-spacing: 1.4px; margin: 0;
      text-transform: uppercase; color: {COLORS['text']};
  }}
  .section-head .count {{
      color: {COLORS['muted']}; font-size: 0.8rem; background: {COLORS['panel']};
      border: 1px solid {COLORS['border']}; border-radius: 999px; padding: 1px 9px;
  }}
  .section-note {{ color: {COLORS['muted']}; font-size: 0.8rem; margin: 2px 0 10px 0; }}

  .badge {{
      display: inline-block; padding: 2px 9px; border-radius: 999px;
      font-size: 0.7rem; font-weight: 700; letter-spacing: 0.7px; white-space: nowrap;
  }}
  .chip {{
      display: inline-block; padding: 1px 8px; margin: 2px 4px 2px 0; border-radius: 6px;
      font-size: 0.72rem; background: {COLORS['panel_alt']}; border: 1px solid {COLORS['border']};
      color: {COLORS['muted']};
  }}
  .chip.fighter {{ color: #cfd6e2; }}
  .chip.event {{ color: {COLORS['orange']}; border-color: #3a2d1d; }}

  .story-card {{
      background: {COLORS['panel']}; border: 1px solid {COLORS['border']};
      border-left: 4px solid {COLORS['grey']}; border-radius: 10px;
      padding: 12px 15px 8px 15px; margin-bottom: 6px;
  }}
  .story-card {{ min-height: 232px; }}
  .story-card h3 {{ font-size: 1.02rem; font-weight: 700; margin: 6px 0 6px 0; color: {COLORS['text']}; line-height: 1.35; }}
  .story-card .summary {{ color: #c3cbd8; font-size: 0.86rem; line-height: 1.45; margin-bottom: 8px; }}
  .story-card .meta {{ color: {COLORS['muted']}; font-size: 0.75rem; }}
  .story-card .meta b {{ color: #c3cbd8; font-weight: 600; }}

  .relevance-bar {{ height: 4px; background: {COLORS['panel_alt']}; border-radius: 3px; margin-top: 8px; }}
  .relevance-bar > div {{ height: 4px; border-radius: 3px; background: {COLORS['accent']}; }}

  .panel {{
      background: {COLORS['panel']}; border: 1px solid {COLORS['border']};
      border-radius: 10px; padding: 14px 16px; margin-bottom: 12px;
  }}
  .panel h4 {{
      margin: 0 0 8px 0; font-size: 0.8rem; letter-spacing: 1.2px;
      text-transform: uppercase; color: {COLORS['muted']};
  }}
  .kv {{ color: #c3cbd8; font-size: 0.85rem; line-height: 1.6; }}
  .muted {{ color: {COLORS['muted']}; font-size: 0.8rem; }}
  .warn-box {{
      background: #2a1d0f; border: 1px solid #5a3d16; color: #ffce87;
      border-radius: 8px; padding: 10px 13px; font-size: 0.83rem; margin-bottom: 10px;
  }}
  .demo-box {{
      background: #2a1030; border: 1px solid #6b2b7a; color: #f0b9ff;
      border-radius: 8px; padding: 10px 13px; font-size: 0.83rem; margin-bottom: 10px;
  }}
  .timeline-item {{
      border-left: 2px solid {COLORS['border']}; padding: 2px 0 12px 14px; margin-left: 6px;
      color: #c3cbd8; font-size: 0.85rem;
  }}
  .timeline-item .when {{ color: {COLORS['muted']}; font-size: 0.74rem; letter-spacing: 0.4px; }}

  div[data-testid="stSidebarNav"] {{ display: none; }}
  section[data-testid="stSidebar"] {{ background: #0d1117; border-right: 1px solid {COLORS['border']}; }}
  .stButton button {{ border-radius: 8px; font-size: 0.82rem; }}
  a {{ color: #7fb0ff; }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def status_badge_html(status: Optional[str]) -> str:
    style = status_style(status)
    return (
        f'<span class="badge" style="background:{style.color}22;color:{style.color};'
        f'border:1px solid {style.color}55">{style.emoji} {style.label}</span>'
    )


def status_color(status: Optional[str]) -> str:
    return status_style(status).color


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip {kind}">{text}</span>'


def relevance_bar(score: float) -> str:
    width = max(2, min(100, float(score or 0)))
    color = COLORS["accent"] if width >= 70 else (COLORS["orange"] if width >= 55 else COLORS["grey"])
    return f'<div class="relevance-bar"><div style="width:{width}%;background:{color}"></div></div>'


def support_meter_html(score: float, label: str) -> str:
    width = max(2, min(100, float(score or 0)))
    color = (
        COLORS["green"] if width >= 65 else
        COLORS["yellow"] if width >= 45 else
        COLORS["orange"] if width >= 25 else COLORS["red"]
    )
    return (
        f'<div style="margin:4px 0 2px 0"><span style="color:{color};font-weight:700;'
        f'font-size:0.85rem">{label}</span> '
        f'<span class="muted">({width:.0f}/100)</span></div>'
        f'<div class="relevance-bar"><div style="width:{width}%;background:{color}"></div></div>'
    )
