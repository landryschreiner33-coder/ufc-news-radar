"""Story imagery, and the rule that a picture must never imply a claim.

Priority, per the product rules:

    1. the image the source itself published with the article
    2. an image collected for the related event or fighter
    3. a generic, obviously-generic category graphic

A photograph of a fighter who is not in the story would suggest something the
sources never said, so (3) is never a photo. The fallbacks are flat SVG
graphics built here - a category word on a coloured field - which read
immediately as the app's own placeholder rather than as source photography.
They are inline data URIs, so they need no network request and cannot fail to
load.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from models.types import Category

#: Category -> (label shown on the placeholder, accent colour, glyph).
_CATEGORY_ART: Dict[str, tuple] = {
    Category.FIGHT_ANNOUNCEMENT.value: ("FIGHT ANNOUNCED", "#e11d48", "◆"),
    Category.INJURY.value: ("INJURY", "#f97316", "✕"),
    Category.CANCELLATION.value: ("CANCELLED", "#ef4444", "⊘"),
    Category.REPLACEMENT.value: ("REPLACEMENT", "#f59e0b", "⇄"),
    Category.RANKING.value: ("RANKINGS", "#3b82f6", "▲"),
    Category.RESULT.value: ("RESULT", "#19c37d", "✓"),
    Category.RETIREMENT.value: ("RETIREMENT", "#8b5cf6", "●"),
    Category.SUSPENSION.value: ("SUSPENSION", "#dc2626", "⚠"),
    Category.CONTROVERSY.value: ("CONTROVERSY", "#f43f5e", "⚡"),
    Category.FIGHTER_STATEMENT.value: ("FIGHTER STATEMENT", "#0ea5e9", "❝"),
    Category.TITLE.value: ("TITLE", "#eab308", "★"),
    Category.EVENT_CHANGE.value: ("CARD CHANGE", "#f59e0b", "↻"),
    Category.BUSINESS.value: ("UFC BUSINESS", "#64748b", "■"),
    Category.INTERVIEW.value: ("INTERVIEW", "#06b6d4", "❞"),
    Category.PROMO.value: ("PROMO", "#94a3b8", "▶"),
    Category.RUMOR.value: ("RUMOR", "#a855f7", "?"),
    Category.GENERAL.value: ("UFC NEWS", "#71717a", "●"),
}

_DEFAULT_ART = ("NEWS IMAGE UNAVAILABLE", "#71717a", "●")

#: What a placeholder is standing in for. The label has to match the thing on
#: screen: an event card captioned "CARD CHANGE" tells the reader something
#: about the event that is simply not true.
KIND_EVENT = "event"
KIND_FIGHTER = "fighter"
KIND_NEWS = "news"
KIND_CARD_CHANGE = "card_change"

_KIND_ART: Dict[str, tuple] = {
    KIND_EVENT: ("UFC EVENT", "#3b82f6", "▣"),
    KIND_FIGHTER: ("FIGHTER IMAGE UNAVAILABLE", "#64748b", "◍"),
    KIND_NEWS: _DEFAULT_ART,
    KIND_CARD_CHANGE: ("CARD CHANGE", "#f59e0b", "↻"),
}

#: Hosts that serve tracking pixels or logos rather than article imagery.
_BLOCKED_IMAGE_HOSTS = {
    "feeds.feedburner.com", "feedburner.com", "doubleclick.net",
    "googleadservices.com", "scorecardresearch.com", "stats.wordpress.com",
}

_TINY_MARKERS = ("1x1", "pixel.gif", "blank.gif", "spacer.gif", "/ads/")


def is_usable_image(url: Optional[str]) -> bool:
    """Reject tracking pixels, ad slots and anything that is not really an image."""
    if not url or not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate.lower().startswith(("http://", "https://")):
        return False
    lowered = candidate.lower()
    if any(marker in lowered for marker in _TINY_MARKERS):
        return False
    host = (urlparse(candidate).netloc or "").lower().removeprefix("www.")
    if host in _BLOCKED_IMAGE_HOSTS:
        return False
    return True


def placeholder_for(category: Optional[str] = None, headline: str = "",
                    kind: str = KIND_NEWS) -> str:
    """A generic graphic as an inline SVG data URI.

    Deliberately abstract: no faces, no fight imagery, nothing that could be
    mistaken for a photograph of the people in the story.

    ``kind`` says what the image stands in for. For a news card the story's
    own category gives a more useful label ("INJURY"), so it wins; for an
    event, a fighter or a card change the kind decides, because borrowing
    another kind's label makes a false statement about the subject.
    """
    if kind != KIND_NEWS:
        label, accent, glyph = _KIND_ART[kind]
    else:
        label, accent, glyph = _CATEGORY_ART.get(str(category or ""), _DEFAULT_ART)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 210" width="400" height="210" role="img" aria-label="Generic {label} graphic - not a photograph">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#14161a"/>
      <stop offset="100%" stop-color="#1f2430"/>
    </linearGradient>
  </defs>
  <rect width="400" height="210" fill="url(#g)"/>
  <rect x="0" y="0" width="6" height="210" fill="{accent}"/>
  <g opacity="0.10">
    <circle cx="330" cy="46" r="90" fill="{accent}"/>
  </g>
  <text x="28" y="104" font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif" font-size="46"
        fill="{accent}" opacity="0.85">{glyph}</text>
  <text x="28" y="146" font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif" font-size="17"
        font-weight="700" letter-spacing="2.2" fill="#e8eaed">{label}</text>
  <text x="28" y="172" font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif" font-size="11"
        letter-spacing="1.4" fill="#7c8597">NO SOURCE IMAGE COLLECTED</text>
</svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def image_for_story(story: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the image for a story card.

    Returns the URL plus ``is_placeholder`` so the interface can caption a
    generic graphic honestly instead of passing it off as source photography.
    """
    candidate = story.get("image_url")
    if is_usable_image(candidate):
        return {"url": candidate, "is_placeholder": False,
                "caption": story.get("primary_source_name") or "Source image"}

    if event and is_usable_image(event.get("image_url")):
        return {"url": event["image_url"], "is_placeholder": False,
                "caption": f"Event image: {event.get('name')}"}

    return {
        "url": placeholder_for(story.get("category"), story.get("headline") or "",
                               kind=KIND_NEWS),
        "is_placeholder": True,
        "caption": "Generic graphic - no image was collected with this story",
    }


def image_for_event(event: Dict[str, Any]) -> Dict[str, Any]:
    if is_usable_image(event.get("image_url")):
        return {"url": event["image_url"], "is_placeholder": False,
                "caption": event.get("name") or "Event image"}
    return {"url": placeholder_for(headline=event.get("name") or "", kind=KIND_EVENT),
            "is_placeholder": True,
            "caption": "Generic graphic - no event image was collected"}


def image_for_card_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """Artwork for a fight-card change entry."""
    return {"url": placeholder_for(headline=change.get("after_text") or "",
                                   kind=KIND_CARD_CHANGE),
            "is_placeholder": True,
            "caption": "Generic graphic - fight card change"}


def image_for_fighter(fighter: Dict[str, Any]) -> Dict[str, Any]:
    """Fighter imagery, or an initials tile.

    Never another fighter's photograph: an unrelated face on a fighter page is
    a factual claim the app has no basis for.
    """
    if is_usable_image(fighter.get("image_url")):
        return {"url": fighter["image_url"], "is_placeholder": False,
                "caption": fighter.get("name") or "Fighter image"}
    name = str(fighter.get("name") or "?")
    initials = "".join(part[0] for part in name.split()[:2] if part).upper() or "?"
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200" role="img" aria-label="Initials placeholder for {name}">
  <rect width="200" height="200" fill="#1a1d24"/>
  <circle cx="100" cy="100" r="62" fill="#242935"/>
  <text x="100" y="118" text-anchor="middle" font-family="Inter,Segoe UI,Helvetica,Arial,sans-serif"
        font-size="46" font-weight="700" fill="#8b94a6">{initials}</text>
</svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return {"url": f"data:image/svg+xml;base64,{encoded}", "is_placeholder": True,
            "caption": "No photo collected"}
