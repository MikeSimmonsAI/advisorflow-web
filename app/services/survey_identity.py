"""Who a survey is from.

A family who gets a "how did we do?" text from an outreach program (SCI) was
dealing with ONE funeral home or cemetery. The text and the page must name that
location and show only that location's own phone number - never the corporate
name, never a person's cell. A program contact whose location is not resolved
gets no survey at all: nothing goes out under an unknown name.
"""
from typing import Optional

from sqlalchemy.orm import Session


def proper_name(name: Optional[str]) -> str:
    """'jackson' / 'JACKSON' -> 'Jackson'. Mixed case is left as typed (McDonald)."""
    n = " ".join((name or "").split())
    if n and (n.islower() or n.isupper()):
        return n.title()
    return n


def location_identity(db: Session, lead) -> Optional[dict]:
    """None when the lead is not in an outreach program. Otherwise
    {"name", "phone", "resolved"} for the location the lead belongs to."""
    if lead is None:
        return None
    from app.services.programs.identity import location_profile_for_lead, program_for_org
    if program_for_org(db, lead.organization_id) is None:
        return None
    prof = location_profile_for_lead(db, lead)
    if prof is None:
        return {"name": None, "phone": None, "resolved": False, "location_id": None}
    return {"name": prof.official_name, "phone": (prof.facility_phone or "").strip() or None,
            "resolved": True, "location_id": prof.location_id}


HELD_NO_LOCATION = "held: program contact has no resolved location - no survey under an unknown name"
HELD_NO_LINK = "held: no branded survey link for this business - no survey text without a link"
