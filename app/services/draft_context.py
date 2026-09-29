"""What an AI draft needs to know about the BUSINESS and the LEAD.

WHY THIS EXISTS (owner, 2026-09-29, an Atlantis Light & Power lead): the email
draft read "I'm EvoSys Wholesale from EVO Integrated Solutions LLC ... any
service business needs you might have". Three separate faults:

  1. The business was the SENDER'S home organization, not the organization the
     lead belongs to. A platform owner or multi-workspace user working inside
     Atlantis introduced Atlantis's prospect to a different company.
  2. The sender "name" was an account label ("EvoSys Wholesale"), not a person.
  3. The model was told nothing about what the business does (energy rate
     reviews), what stage the lead is at, or what the chosen booking type was -
     so it could only write filler.

Everything here is read from real rows; nothing is invented. A field with no
value is left out rather than described.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

# Words that make a display name an account / business label, not a person.
_NOT_A_PERSON = re.compile(
    r"\b(wholesale|llc|l\.l\.c|inc|corp|corporation|company|co\.|solutions|services|"
    r"group|team|admin|administrator|support|office|platform|owner|sales|"
    r"marketing|operations|department|dept|account|workspace|enterprises?|"
    r"holdings|partners|systems|pro|evosys|advisorflow|bookaboost)\b", re.I)

# Industry-specific voice. Only a few industries need a stronger rule than the
# generic one; everything else gets the generic professional voice.
_VOICE = {
    "funeral": ("This is a funeral and cemetery context: respectful and calm, never "
                "cheerful, never salesy, never a marketing voice."),
    "energy": ("This is an energy / utility-rate business. Talk plainly about what the "
               "prospect gets: a review of their current electricity or gas rate, usage "
               "and contract (end date, supplier), and a comparison of supplier options "
               "that could lower or lock in their cost. Never promise a specific saving "
               "or rate, never claim to be their utility, and never invent a figure."),
    "wholesale_real_estate": ("This is a real-estate investor contacting a property owner. "
                              "Be plain and respectful; never pressure, never quote a price."),
}
_GENERIC_VOICE = "Professional, plain and human; never a marketing voice, never pushy."


def looks_like_person(name: Optional[str], org_names: List[str]) -> bool:
    n = (name or "").strip()
    if not n or len(n.split()) > 4:
        return False
    low = n.lower()
    if any(o and (low == o.lower() or o.lower() in low or low in o.lower()) for o in org_names):
        return False
    return not _NOT_A_PERSON.search(n)


def _json(v) -> Dict[str, Any]:
    if isinstance(v, dict):
        return v
    try:
        out = json.loads(v) if v else {}
        return out if isinstance(out, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def build(db: Session, lead, advisor, booking_type: Optional[str] = None) -> Dict[str, Any]:
    """Business + lead context for a draft, resolved from the LEAD's organization."""
    from app.models.models import Organization
    from app.services import industry_templates as IT

    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    ident_name = None
    try:
        from app.services.public_identity import identity_for_org
        ident_name = identity_for_org(db, str(lead.organization_id)).customer_facing_name
    except Exception:  # noqa: BLE001
        pass
    org_name = (ident_name or (org.brand_name if org is not None else None)
                or (org.name if org is not None else None) or "our organization")

    industry = IT.normalize(getattr(org, "industry", None)) if org is not None else "general"
    template = IT.resolve(getattr(org, "industry", None)) if org is not None else {}

    # The sender: a person's name, or the business's team when the account
    # carries a label rather than a name.
    names = [x for x in (org_name, getattr(org, "name", None), getattr(org, "brand_name", None)) if x]
    if getattr(advisor, "organization_id", None) and advisor.organization_id != lead.organization_id:
        home = db.query(Organization).filter(Organization.id == advisor.organization_id).first()
        if home is not None:
            names += [x for x in (home.name, home.brand_name) if x]
    raw_name = (getattr(advisor, "full_name", None) or "").strip()
    is_person = looks_like_person(raw_name, names)
    sender_name = raw_name if is_person else f"the {org_name} team"
    signature = raw_name if is_person else f"The {org_name} Team"
    job_title = (getattr(advisor, "job_title", None) or "").strip() if is_person else ""

    # What the business offers: its own product list, else the industry's.
    offers: List[str] = []
    try:
        offers = [p.get("label") for p in (IT.products_for_org(org).get("products") or [])
                  if isinstance(p, dict) and p.get("label")][:6]
    except Exception:  # noqa: BLE001
        offers = []

    tier_label = None
    try:
        for t in IT.org_lead_tiers(org):
            if t.get("value") == lead.tier:
                tier_label = t.get("label")
                if t.get("description"):
                    tier_label += f" ({t['description']})"
                break
    except Exception:  # noqa: BLE001
        pass

    cf = _json(getattr(lead, "custom_fields", None))
    vf = _json(getattr(lead, "vertical_fields", None)) if hasattr(lead, "vertical_fields") else {}
    field_labels = {f.get("key"): f.get("label") for f in (template.get("custom_fields") or [])
                    if isinstance(f, dict)}
    facts: List[str] = []
    for src in (cf, vf):
        for k, v in src.items():
            if k in ("offer_hook",) or v in (None, "", [], {}):
                continue
            if isinstance(v, (dict, list)):
                continue
            facts.append(f"{field_labels.get(k) or k.replace('_', ' ')}: {v}")
    place = ", ".join(x for x in (getattr(lead, "city", None), getattr(lead, "state", None)) if x)
    if place:
        facts.append(f"Location: {place}")
    for attr, label in (("source", "Came in from"), ("import_list_name", "List")):
        v = getattr(lead, attr, None)
        if v:
            facts.append(f"{label}: {v}")

    lines = [f"Business: {org_name}"]
    if template.get("label") and industry != "general":
        lines.append(f"What the business does: {template['label']}")
    if getattr(org, "tagline", None):
        lines.append(f"Tagline: {org.tagline}")
    if offers:
        lines.append("Services offered: " + "; ".join(offers))
    lines.append(f"Sender: {sender_name}" + (f", {job_title}" if job_title else ""))
    if not is_person:
        lines.append("There is no individual's name for the sender: write as the "
                     f"{org_name} team (\"we\"), and never invent a person's name.")
    if booking_type:
        lines.append(f"What the conversation / appointment would be: {booking_type}")
    if tier_label:
        lines.append(f"Lead stage: {tier_label}")

    return {
        "org_name": org_name,
        "industry": industry,
        "sender_name": sender_name,
        "signature": signature,
        "is_person": is_person,
        "voice": _VOICE.get(industry, _GENERIC_VOICE),
        "business_block": "\n".join(lines),
        "lead_facts": "\n".join(f"- {f}" for f in facts[:12]) or "- none on file",
        "booking_type": booking_type or None,
        "tier_label": tier_label,
        "offers": offers,
    }
