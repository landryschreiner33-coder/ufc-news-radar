"""The one place that decides what a bout's status means.

Two different questions were being answered by two fields that could
contradict each other, and the interface showed both:

    OFFICIAL BOUTS: 0
    [REPORTED - not on the official card yet]  Jones vs. Aspinall
        ... confidence: official - source: UFC.com

Both statements were true of their own field and nonsense together. The
underlying confusion is that *"UFC.com published an article about this"* is
not the same claim as *"this bout is on UFC's official card"*. An official
outlet reporting a matchup is strong evidence; it is still reporting.

So the two questions are separated and named:

``official_status``
    Is this bout on UFC's own card?  ``official`` is set **only** by the
    official card collector reading a UFC event page. Everything else is
    ``reported``, ``rumored`` or ``cancelled``.

``evidence_level``
    How strong is the evidence behind it, independently of the above:
    ``official_card`` > ``official_source`` > ``credible_reporting`` >
    ``unconfirmed``.

The pairing is constrained: ``official_status='official'`` implies
``evidence_level='official_card'`` and nothing else can claim that level, so
the contradiction above cannot be represented at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from models.types import CREDIBLE_REPORTING_TYPES, SourceType, normalize_source_type

# -- official_status: is the bout on UFC's own card? -------------------------
OFFICIAL = "official"
REPORTED = "reported"
RUMORED = "rumored"
CANCELLED = "cancelled"

# -- evidence_level: how strong is the evidence? -----------------------------
EVIDENCE_OFFICIAL_CARD = "official_card"
EVIDENCE_OFFICIAL_SOURCE = "official_source"
EVIDENCE_CREDIBLE_REPORTING = "credible_reporting"
EVIDENCE_UNCONFIRMED = "unconfirmed"

EVIDENCE_ORDER = [
    EVIDENCE_UNCONFIRMED,
    EVIDENCE_CREDIBLE_REPORTING,
    EVIDENCE_OFFICIAL_SOURCE,
    EVIDENCE_OFFICIAL_CARD,
]

OFFICIAL_STATUS_LABELS = {
    OFFICIAL: "On the official card",
    REPORTED: "Reported only",
    RUMORED: "Rumoured only",
    CANCELLED: "Removed from the card",
}

EVIDENCE_LABELS = {
    EVIDENCE_OFFICIAL_CARD: "listed on UFC's own event page",
    EVIDENCE_OFFICIAL_SOURCE: "reported by an official UFC channel, not on the card page",
    EVIDENCE_CREDIBLE_REPORTING: "reported by credible journalism",
    EVIDENCE_UNCONFIRMED: "no credible source collected yet",
}

#: Values ``official_status`` is allowed to take, for validation.
VALID_OFFICIAL_STATUS = set(OFFICIAL_STATUS_LABELS)
VALID_EVIDENCE_LEVELS = set(EVIDENCE_LABELS)


@dataclass(frozen=True)
class BoutStatus:
    official_status: str
    evidence_level: str
    source_type: str

    @property
    def is_official_card_entry(self) -> bool:
        return self.official_status == OFFICIAL

    @property
    def explanation(self) -> str:
        """One sentence that can never contradict the two fields."""
        return (f"{OFFICIAL_STATUS_LABELS[self.official_status]} - "
                f"{EVIDENCE_LABELS[self.evidence_level]}.")


def classify(
    from_official_card: bool = False,
    source_type: Optional[str] = None,
    rumored: bool = False,
    cancelled: bool = False,
) -> BoutStatus:
    """Derive the canonical pair from what was actually observed.

    ``from_official_card`` must be True only when the bout was read off a UFC
    event page by ``collectors/ufc_event_card.py``. An article published by
    UFC.com is ``source_type=OFFICIAL`` but not an official card entry.
    """
    normalized = normalize_source_type(source_type)
    if from_official_card:
        # The card page is the strongest evidence there is, and the only thing
        # that may claim the bout is officially booked.
        return BoutStatus(OFFICIAL, EVIDENCE_OFFICIAL_CARD, normalized)

    if normalized == SourceType.OFFICIAL.value:
        evidence = EVIDENCE_OFFICIAL_SOURCE
    elif normalized in CREDIBLE_REPORTING_TYPES:
        evidence = EVIDENCE_CREDIBLE_REPORTING
    else:
        evidence = EVIDENCE_UNCONFIRMED

    if cancelled:
        return BoutStatus(CANCELLED, evidence, normalized)
    if rumored or evidence == EVIDENCE_UNCONFIRMED:
        return BoutStatus(RUMORED, evidence, normalized)
    return BoutStatus(REPORTED, evidence, normalized)


def of_row(row) -> BoutStatus:
    """Read the canonical pair back off a stored ``fight_card_items`` row."""
    official_status = str(row.get("official_status") or REPORTED)
    evidence_level = str(row.get("evidence_level") or EVIDENCE_UNCONFIRMED)
    if official_status not in VALID_OFFICIAL_STATUS:
        official_status = REPORTED
    if evidence_level not in VALID_EVIDENCE_LEVELS:
        evidence_level = EVIDENCE_UNCONFIRMED
    if not is_consistent(official_status, evidence_level):
        # A row from an older build. Trust the weaker of the two claims: the
        # app may understate its evidence, never overstate it.
        if official_status == OFFICIAL:
            evidence_level = EVIDENCE_OFFICIAL_CARD
        else:
            evidence_level = EVIDENCE_OFFICIAL_SOURCE if evidence_level == EVIDENCE_OFFICIAL_CARD \
                else evidence_level
    return BoutStatus(official_status, evidence_level,
                      str(row.get("source_type") or SourceType.UNKNOWN.value))


def describe_bout(row) -> str:
    """One provenance line for a stored bout, safe to show anywhere."""
    status = of_row(row)
    source = row.get("source_name") or "source not recorded"
    return (f"{row.get('status') or 'scheduled'} · {status.explanation} "
            f"Source: {source}.")


def is_consistent(official_status: Optional[str], evidence_level: Optional[str]) -> bool:

    """The invariant the validator and the UI both rely on."""
    status = str(official_status or "")
    evidence = str(evidence_level or "")
    if status not in VALID_OFFICIAL_STATUS or evidence not in VALID_EVIDENCE_LEVELS:
        return False
    # Only an official card entry may claim the official-card evidence level,
    # and an official card entry may claim nothing weaker.
    return (status == OFFICIAL) == (evidence == EVIDENCE_OFFICIAL_CARD)


def strongest(*levels: Optional[str]) -> str:
    """The strongest of several evidence levels."""
    best = EVIDENCE_UNCONFIRMED
    for level in levels:
        value = str(level or "")
        if value in VALID_EVIDENCE_LEVELS and EVIDENCE_ORDER.index(value) > EVIDENCE_ORDER.index(best):
            best = value
    return best


def from_legacy(confidence: Optional[str], official_status: Optional[str]) -> Tuple[str, str]:
    """Map a pre-migration ``confidence`` value onto the canonical pair.

    Old rows carry ``confidence`` in {official, reported, rumored} and an
    ``official_status`` that a migration defaulted to 'reported' without
    backfilling, which is how the contradiction was stored in the first place.
    The old ``confidence='official'`` meant "an official source was behind
    it", which is ``official_source`` - never a claim about the card page.
    """
    old_confidence = str(confidence or "").strip().lower()
    old_status = str(official_status or "").strip().lower()

    if old_status == OFFICIAL:
        return OFFICIAL, EVIDENCE_OFFICIAL_CARD
    if old_status == CANCELLED:
        return CANCELLED, (EVIDENCE_OFFICIAL_SOURCE if old_confidence == OFFICIAL
                           else EVIDENCE_CREDIBLE_REPORTING)
    if old_confidence == OFFICIAL:
        return REPORTED, EVIDENCE_OFFICIAL_SOURCE
    if old_confidence == RUMORED or old_status == RUMORED:
        return RUMORED, EVIDENCE_UNCONFIRMED
    return REPORTED, EVIDENCE_CREDIBLE_REPORTING
