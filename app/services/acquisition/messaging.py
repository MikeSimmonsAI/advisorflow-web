"""Category-aware outreach wording (#452 multi-industry, 2026-11-03).

The first acquisition client was a commercial cleaner, and its email said so
to everyone. A window-washing or pest-control client must never send a
cleaning pitch. Every phrase here is chosen from the client's SERVICE
CATEGORY (its organization industry) and the client's own profile - nothing
is claimed that the client did not record (no prices, insurance, years in
business, certifications or results unless the profile states them).
"""
from typing import Any, Dict, List, Optional

from app.services.acquisition import common as C

# category -> (what they do, the visit they book, what the prospect gets)
_COPY: Dict[str, Dict[str, str]] = {
    "cleaning": {"trade": "a commercial cleaning company", "visit": "walkthrough",
                 "offer": "a free quote", "subject": "Cleaning for {company}"},
    "residential_cleaning": {"trade": "a residential cleaning company", "visit": "walkthrough",
                             "offer": "a free quote", "subject": "Cleaning for {company}"},
    "window_washing": {"trade": "a window washing company", "visit": "on-site estimate",
                       "offer": "a free estimate", "subject": "Window washing for {company}"},
    "pest_control": {"trade": "a pest control company", "visit": "inspection",
                     "offer": "a free inspection and quote", "subject": "Pest control for {company}"},
    "landscaping": {"trade": "a landscaping company", "visit": "property walk",
                    "offer": "a free estimate", "subject": "Grounds care for {company}"},
    "pressure_washing": {"trade": "a pressure washing company", "visit": "on-site estimate",
                         "offer": "a free estimate", "subject": "Pressure washing for {company}"},
    "property_maintenance": {"trade": "a property maintenance company", "visit": "site visit",
                             "offer": "a free estimate", "subject": "Property maintenance for {company}"},
}
_NEUTRAL = {"trade": "a local service company", "visit": "short visit", "offer": "a free estimate",
            "subject": "A quick question for {company}"}


def category(org) -> str:
    ind = (getattr(org, "industry", None) or "").strip().lower()
    return ind if ind in C.SERVICE_CATEGORY_KEYS else "service_other"


def copy_for(org) -> Dict[str, str]:
    return dict(_COPY.get(category(org), _NEUTRAL))


def visit_label(org) -> str:
    v = copy_for(org)["visit"]
    return v[:1].upper() + v[1:]


def services_line(p) -> str:
    svcs = [s for s in (C.jload(getattr(p, "services", None), []) or []) if isinstance(s, str) and s.strip()]
    return ", ".join(svcs[:3])


def first_touch(org, p, company: str, first_name: str, signer: str,
                evidence: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    """A short first email from facts only. `evidence` (verified, sourced
    facts about the prospect) adds ONE grounded line when there is one."""
    c = copy_for(org)
    who = ((getattr(p, "business_name", None) if p else None) or getattr(org, "name", None) or "our team").strip()
    area = ""
    cities = C.jload(getattr(p, "service_area", None), []) if p else []
    if cities:
        area = " serving %s" % ", ".join(cities[:2])
    svc = services_line(p) if p else ""
    lines = ["Hi %s," % (first_name or "there"), ""]
    lead = "I'm reaching out for %s, %s%s." % (who, c["trade"], area)
    if svc:
        lead += " We handle %s." % svc
    lines.append(lead)
    grounded = _grounded_line(evidence or [], company)
    if grounded:
        lines += ["", grounded]
    offer = (getattr(p, "approved_offer", None) or "").strip() if p else ""
    ask = ("Would you be open to a %s at %s? %s" % (c["visit"], company, offer) if offer
           else "Would you be open to a %s at %s so we can give you %s?" % (c["visit"], company, c["offer"]))
    lines += ["", ask.strip(), "", "Thank you,", signer]
    return {"subject": c["subject"].format(company=company)[:150], "body": "\n".join(lines)}


def booking_reply(org, p, first_name: str, url: str, signer: str) -> Dict[str, str]:
    c = copy_for(org)
    body = ("Hi %s,\n\nThank you for getting back to us. Here's a link to pick a time for the %s - it only "
            "shows times we're actually free:\n\n%s\n\nThank you,\n%s") % (first_name or "there", c["visit"], url, signer)
    return {"subject": "Scheduling your %s" % c["visit"], "body": body}


def _grounded_line(evidence: List[Dict[str, Any]], company: str) -> str:
    """One sentence referencing a verified, sourced fact - never an invented one."""
    for e in evidence:
        if e.get("kind") == "segment" and e.get("value") and e.get("source_url"):
            return "I saw %s is %s." % (company, str(e["value"]).strip().rstrip(".")[:120])
    return ""


def followup_plan(org) -> List[Dict[str, Any]]:
    """The follow-ups the workforce proposes. Each one is drafted for approval
    when it falls due; none is sent automatically."""
    c = copy_for(org)
    return [{"day": 4, "purpose": "Short check-in on the first email"},
            {"day": 10, "purpose": "Offer %s once more, then stop" % c["offer"]}]
