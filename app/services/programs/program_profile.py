"""PROGRAM PROFILE - who a program workspace IS, for the shell to draw (Issue #507).

The outreach-program shell (/program, /program/live) was built for SCI and its
menu, title bar and footer said "SCI" for every program. Restland Cemetery and
Funeral Home now runs the same shell with ONE location. This module is the one
answer to "what does this program call itself, and how many locations does it
have", so the screens stop hard-coding SCI copy and hide multi-location
controls when there is a single location.

Read-only. Nothing here changes what a program may do or send; access is still
decided by the routers (program_router._program / lead_scope).

SCI keeps exactly the wording it has today: `is_sci` is decided by the existing
sms_programs.is_sci_org rule (env SMS_PROGRAM_ORG_SCI, else the org named
"Service Corporation International") - never by a slug or by this module.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.program_models import LocationProfile, OutreachProgram

SCI_SHORT = "SCI"

# PROGRAM COLOR THEMES (Issue #507). Presentation only, chosen from the
# server's own record of which workspace this is - never from the browser, a
# slug typed in a URL, or the industry (another funeral home must not turn
# Restland green). A location profile may also name its theme in
# brand_settings {"program_theme": "..."}; the id map is the default.
THEMES = ("restland",)
THEME_BY_ORG = {
    "01fed629-9a95-412b-895a-c8b09f37a98c": "restland",   # Restland Cemetery and Funeral Home
}
SCI_FOOTER = "SCI outreach proof of concept · EvoSys Pro-operated · Not corporate implementation"


def real_locations(db: Session, org_id: str):
    """The program's real locations (the Location Review bucket is not one)."""
    return (db.query(LocationProfile)
            .filter(LocationProfile.organization_id == org_id,
                    LocationProfile.is_review_bucket.isnot(True))
            .order_by(LocationProfile.official_name).all())


def single_location(db: Session, org_id: Optional[str]) -> Optional[LocationProfile]:
    """The one location of a single-location program, else None."""
    if not org_id:
        return None
    locs = real_locations(db, org_id)
    return locs[0] if len(locs) == 1 else None


def _is_sci(db: Session, org_id: str) -> bool:
    try:
        from app.services import sms_programs
        return sms_programs.is_sci_org(db, org_id)
    except Exception:  # pragma: no cover - never let a label break a screen
        return False


def profile(db: Session, prog: Optional[OutreachProgram]) -> Optional[dict]:
    if prog is None:
        return None
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    org_name = (getattr(org, "name", None) or prog.name or "").strip()
    sci = _is_sci(db, prog.organization_id)
    locs = real_locations(db, prog.organization_id)
    one = locs[0] if len(locs) == 1 else None
    if sci:
        short = SCI_SHORT
        footer = SCI_FOOTER
        pilot = "PILOT WORKSPACE"
    else:
        short = (prog.hero_title or org_name or prog.name or "Program").strip()
        footer = "%s · EvoSys Pro-operated" % org_name if org_name else "EvoSys Pro-operated"
        pilot = "SINGLE LOCATION" if one else "PROGRAM WORKSPACE"
    tz = (getattr(org, "timezone", None) or "America/Chicago")
    theme = None if sci else _theme(prog.organization_id, one)
    return {
        "is_sci": sci,
        "display_name": org_name or prog.name,
        "short_name": short,
        "live_label": "%s Live" % short,
        "menu_label": "%s menu" % short,
        "footer": footer,
        "pilot_label": pilot,
        "location_count": len(locs),
        "single_location": one is not None,
        "location": ({"location_id": one.location_id, "profile_id": one.id, "name": one.official_name}
                     if one else None),
        "timezone": tz,
        "theme": theme,
        # Panels built only for SCI's own campaigns (Central Alabama roster and
        # delivery, its prepare card) are not part of any other program.
        "sci_only_panels": sci,
        # The journey card's meeting line. SCI's own wording is kept; another
        # program states only what is true for every visit (no invented length).
        "visit_label": "In-person visit (60 min)" if sci else "In-person visit",
    }


def _theme(org_id: str, one) -> Optional[str]:
    import json
    try:
        named = (json.loads(getattr(one, "brand_settings", None) or "{}") or {}).get("program_theme") if one else None
    except (TypeError, ValueError):
        named = None
    t = named or THEME_BY_ORG.get(org_id)
    return t if t in THEMES else None
