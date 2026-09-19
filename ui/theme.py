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

  /* st.navigation renders the section menu here - it is the primary navigation
     now, so it must stay visible (an older build hid it). */
  div[data-testid="stSidebarNav"] {{ padding-top: 4px; }}
  div[data-testid="stSidebarNav"] a {{ border-radius: 8px; }}
  section[data-testid="stSidebar"] {{ background: #0d1117; border-right: 1px solid {COLORS['border']}; }}
  .stButton button {{ border-radius: 8px; font-size: 0.82rem; }}
  a {{ color: #7fb0ff; }}

  /* ---------------------------------------------------- responsive grid --
     A real CSS grid rather than fixed Streamlit columns, so the feed reflows
     4 -> 3 -> 2 -> 1 with the viewport instead of squashing four columns onto
     a phone. minmax() does the work; no breakpoints to keep in sync. */
  .radar-grid {{
      display: grid; gap: 14px; margin: 4px 0 6px 0;
      grid-template-columns: repeat(auto-fill, minmax(272px, 1fr));
  }}
  .radar-grid.wide {{ grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }}
  @media (max-width: 640px) {{
      .radar-grid, .radar-grid.wide {{ grid-template-columns: 1fr; }}
  }}

  /* ------------------------------------------------------------- cards -- */
  .ncard {{
      display: flex; flex-direction: column; overflow: hidden;
      background: {COLORS['panel']}; border: 1px solid {COLORS['border']};
      border-radius: 12px; text-decoration: none; color: inherit;
      transition: border-color .15s ease, transform .15s ease;
  }}
  .ncard:hover {{ border-color: #3a4657; transform: translateY(-2px); }}
  .ncard .thumb {{ position: relative; aspect-ratio: 40 / 21; background: #10141b; overflow: hidden; }}
  .ncard .thumb img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .ncard .thumb .ph-note {{
      position: absolute; left: 0; bottom: 0; right: 0; font-size: 0.6rem; letter-spacing: 0.6px;
      color: #8b94a6; background: #0b0e13cc; padding: 2px 8px;
  }}
  .ncard .body {{ padding: 11px 13px 12px 13px; display: flex; flex-direction: column; gap: 7px; flex: 1; }}
  .ncard h3 {{
      font-size: 0.97rem; font-weight: 700; margin: 0; line-height: 1.34; color: {COLORS['text']};
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }}
  .ncard .sum {{
      color: #aab3c2; font-size: 0.8rem; line-height: 1.45; margin: 0;
      display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }}
  .ncard .foot {{
      margin-top: auto; padding-top: 8px; border-top: 1px solid {COLORS['border']};
      color: {COLORS['muted']}; font-size: 0.72rem; display: flex; justify-content: space-between;
      gap: 8px; align-items: center;
  }}
  .ncard .foot .more {{ color: {COLORS['accent_soft']}; font-weight: 700; letter-spacing: 0.6px; white-space: nowrap; }}
  .ncard .badges {{ display: flex; flex-wrap: wrap; gap: 5px; }}

  /* Status-specific treatments. Never colour alone - every card also carries
     the status word and its icon in the badge. */
  .ncard.breaking {{ border-color: #d20a0a88; box-shadow: 0 0 0 1px #d20a0a33 inset; }}
  .ncard.breaking .thumb::after {{
      content: "BREAKING"; position: absolute; top: 8px; left: 8px; background: {COLORS['accent']};
      color: #fff; font-size: 0.62rem; font-weight: 800; letter-spacing: 1.2px; padding: 3px 8px;
      border-radius: 4px;
  }}
  .ncard.rumor {{ border-style: dashed; border-color: #ef444477; background: #17121a; }}
  .ncard.rumor .thumb::after {{
      content: "UNCONFIRMED"; position: absolute; top: 8px; left: 8px; background: #2a1016;
      color: #ff8a8a; border: 1px solid #ef444477; font-size: 0.6rem; font-weight: 700;
      letter-spacing: 1px; padding: 3px 8px; border-radius: 4px;
  }}
  .ncard.contested {{ border-color: #a855f777; }}
  .ncard.developing {{ border-color: #ff913066; }}

  .claimline {{
      font-size: 0.78rem; color: #d7c2e8; background: #1d1526; border: 1px solid #3a2b4a;
      border-radius: 7px; padding: 6px 9px; line-height: 1.4;
  }}

  /* ---------------------------------------------------- event lifecycle -- */
  .lifepill {{
      display: inline-flex; align-items: center; gap: 6px; padding: 3px 11px; border-radius: 999px;
      font-size: 0.72rem; font-weight: 700; letter-spacing: 1px; white-space: nowrap;
  }}
  .lifepill.live {{ background: #2a0f12; color: #ff6b6b; border: 1px solid #ef444488; }}
  .lifepill.live .dot {{
      width: 7px; height: 7px; border-radius: 50%; background: #ef4444;
      animation: radarpulse 1.8s ease-in-out infinite;
  }}
  @keyframes radarpulse {{ 0%,100% {{ opacity: 1 }} 50% {{ opacity: .35 }} }}
  @media (prefers-reduced-motion: reduce) {{ .lifepill.live .dot {{ animation: none }} }}

  .evcard {{
      background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; border-radius: 12px;
      padding: 13px 15px; display: flex; flex-direction: column; gap: 7px;
  }}
  .evcard.live {{ border-color: #ef444488; }}
  .evcard .name {{ font-weight: 700; font-size: 0.98rem; color: {COLORS['text']}; line-height: 1.3; }}
  .evcard .count {{ font-size: 1.28rem; font-weight: 800; color: {COLORS['accent_soft']}; letter-spacing: 0.5px; }}

  .statusbar {{
      display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 14px 0;
      padding: 9px 12px; background: {COLORS['panel']}; border: 1px solid {COLORS['border']};
      border-radius: 10px; font-size: 0.75rem; color: {COLORS['muted']};
      align-items: center;
  }}
  .statusbar b {{ color: {COLORS['text']}; font-weight: 700; }}
  .statusbar .sep {{ color: #39424f; }}

  .emptystate {{
      text-align: center; padding: 46px 20px; background: {COLORS['panel']};
      border: 1px dashed {COLORS['border']}; border-radius: 14px; margin: 10px 0 16px 0;
  }}
  .emptystate .big {{ font-size: 1.28rem; font-weight: 800; letter-spacing: 1px; color: {COLORS['text']}; }}
  .emptystate .sub {{ color: {COLORS['muted']}; font-size: 0.87rem; margin-top: 7px; line-height: 1.55; }}

  /* Focus ring: keyboard users must be able to see where they are. */
  .ncard:focus-visible, a:focus-visible, .stButton button:focus-visible {{
      outline: 2px solid {COLORS['accent_soft']}; outline-offset: 2px;
  }}
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
