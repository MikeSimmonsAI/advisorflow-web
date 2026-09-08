"""SEVERITY — the one vocabulary the platform uses to say how something is.

WHY THIS IS A MODULE AND NOT A CONVENTION
-----------------------------------------
Before this existed, three surfaces each invented their own words. Platform
Health said `ok / warn / bad / no_source`. Compensation said nothing at all and
left the reader to infer severity from whether a number was orange. A customer
screen said "Connected" or "Needs attention". An owner reading two of those
screens in the same minute had to hold two different scales in their head, and
"no_source" meant nothing to them in either.

So the vocabulary is defined once, here, and every surface maps into it.

THE FIVE VALUES, AND WHY THERE ARE FIVE
---------------------------------------
The important distinction — the one three-state models keep collapsing — is
between "this is fine", "this is broken", "nothing has happened yet" and "we
cannot see this". Those last two are NOT the same, and neither of them is
green:

    HEALTHY          Working, and we can see that it is working.
    ATTENTION        Working, but something here will bite if it is ignored.
    ACTION_REQUIRED  Not working, or working in a way that costs money now.
    NO_DATA          Nothing has happened yet. There is nothing wrong; there is
                     simply nothing to measure. A brand-new customer who has
                     sent no messages is here, not in ATTENTION.
    UNAVAILABLE      The platform cannot currently see this. The subsystem may
                     be perfect or may be on fire — we do not know, and saying
                     so is the honest answer. NEVER report UNAVAILABLE as
                     HEALTHY: green for silence is the exact failure this
                     vocabulary exists to prevent.

EVERY VALUE CARRIES OWNER-FACING WORDS. `LABELS` is what a business owner
reads. Engineering shorthand ("no source", "needs invoices + payments tables")
is not a status a customer should ever be shown — it describes our backlog, not
their business. A surface that has nothing to report says what that means for
THEM and what would have to change for it to be reportable, in their words.

ORDER matters: `rank()` sorts worst-first so a page can lead with what needs
doing. UNAVAILABLE ranks above NO_DATA because not knowing is worse than
knowing there is nothing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

HEALTHY = "healthy"
ATTENTION = "attention"
ACTION_REQUIRED = "action_required"
UNAVAILABLE = "unavailable"
NO_DATA = "no_data"

ALL = (HEALTHY, ATTENTION, ACTION_REQUIRED, UNAVAILABLE, NO_DATA)

# What an owner reads. Short enough for a pill, plain enough to need no gloss.
LABELS: Dict[str, str] = {
    HEALTHY: "Healthy",
    ATTENTION: "Needs attention",
    ACTION_REQUIRED: "Action required",
    UNAVAILABLE: "Can't check",
    NO_DATA: "Nothing yet",
}

# One sentence saying what the state MEANS, for a tooltip or a legend. Written
# for the person who owns the business, not the person who owns the codebase.
MEANINGS: Dict[str, str] = {
    HEALTHY: "Working normally.",
    ATTENTION: "Working, but something here needs a decision soon.",
    ACTION_REQUIRED: "Not working, or costing you money right now.",
    UNAVAILABLE: "We can't see this yet, so we won't guess. It is not a "
                 "report that anything is wrong.",
    NO_DATA: "Nothing has happened here yet, so there is nothing to measure.",
}

# Worst first. A page that sorts by this leads with what needs doing.
_ORDER = {
    ACTION_REQUIRED: 0,
    ATTENTION: 1,
    UNAVAILABLE: 2,
    NO_DATA: 3,
    HEALTHY: 4,
}

# THE LEGACY WORDS, mapped in one place rather than in each caller. Platform
# Health has shipped `ok / warn / bad / off / no_source` for a while and its
# stored strings and tests use them; this lets that endpoint speak the new
# vocabulary without a second source of truth about what "warn" means.
_LEGACY = {
    "ok": HEALTHY,
    "good": HEALTHY,
    "healthy": HEALTHY,
    "warn": ATTENTION,
    "warning": ATTENTION,
    "attention": ATTENTION,
    "bad": ACTION_REQUIRED,
    "critical": ACTION_REQUIRED,
    "error": ACTION_REQUIRED,
    "action_required": ACTION_REQUIRED,
    "off": NO_DATA,
    "empty": NO_DATA,
    "none": NO_DATA,
    "no_data": NO_DATA,
    "no_source": UNAVAILABLE,
    "unknown": UNAVAILABLE,
    "unavailable": UNAVAILABLE,
}


def normalize(value: Optional[str]) -> str:
    """Any known spelling of a status → one of the five.

    An UNKNOWN string resolves to UNAVAILABLE, never to HEALTHY. A typo must
    never be able to paint a subsystem green.
    """
    if not value:
        return UNAVAILABLE
    return _LEGACY.get(str(value).strip().lower(), UNAVAILABLE)


def rank(value: Optional[str]) -> int:
    """Sort key. Lower is worse, so `sorted(..., key=rank)` leads with trouble."""
    return _ORDER.get(normalize(value), _ORDER[UNAVAILABLE])


def worst(values: Iterable[Optional[str]]) -> str:
    """The overall state of a set of things.

    Empty means NO_DATA — a page with no sections has nothing to report, which
    is not the same as a page whose sections are all fine.
    """
    vals = [normalize(v) for v in values]
    if not vals:
        return NO_DATA
    return sorted(vals, key=rank)[0]


def describe(value: Optional[str]) -> Dict[str, Any]:
    """The status plus the words for it, so no surface has to keep its own copy."""
    s = normalize(value)
    return {"severity": s, "label": LABELS[s], "meaning": MEANINGS[s]}


def counts(values: Iterable[Optional[str]]) -> Dict[str, int]:
    """How many of each, every key present. A screen can render a legend from
    this without deciding what to do about a key that happens to be missing."""
    out = {s: 0 for s in ALL}
    for v in values:
        out[normalize(v)] += 1
    return out


def summarize(items: List[Dict[str, Any]], *, key: str = "severity") -> Dict[str, Any]:
    """Roll a list of severity-carrying dicts into one headline.

    Used by Platform Health and the Compensation Command Center so that "how is
    the platform" and "how is compensation" are answered the same way.
    """
    vals = [i.get(key) for i in items]
    overall = worst(vals)
    return {
        "overall": overall,
        "overall_label": LABELS[overall],
        "overall_meaning": MEANINGS[overall],
        "counts": counts(vals),
        "total": len(items),
    }
