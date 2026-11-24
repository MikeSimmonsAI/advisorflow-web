"""EvoSys Sales one-time owner-ordered changes (run at deploy by app.migrate).

Nov 23, 2026 - owner: "If they click the booking link, that means yes. I don't
want to see another text come back that means yes." The two government-list
cleaner campaigns still asked people to "Reply YES". They get the booking-link
wording the Facebook campaign already uses. Saved through campaigns.save, so
the campaign stays off and needs the owner's approval of this exact version
before anything sends. Runs only while a step still says "Reply YES";
afterwards it finds nothing to change.
"""
import logging
from typing import Dict

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

GOV_STEPS = [
    {"day": 0, "channel": "text", "subject": "",
     "body": "Hi {company}, Mike with EvoSys Pro. I saw you're registered to bid on government cleaning work. "
             "While you wait on bids, I want to put you in front of at least 3 businesses that need cleaning in the "
             "next 30 days, completely FREE. Grab a time on my calendar and we'll get you set up: {booking_link}"},
    {"day": 3, "channel": "text", "subject": "",
     "body": "{company}, my offer still stands: at least 3 appointments in the next 30 days with businesses looking "
             "for cleaning. No cost and no obligation. You set your price, do the walkthrough and keep the "
             "contract. Pick a time here: {booking_link}"},
    {"day": 8, "channel": "text", "subject": "",
     "body": "Last note from Mike at EvoSys Pro. 3 free appointments for {company} within 30 days are still open. "
             "Book your time here: {booking_link}"},
]


def gov_lists_booking_link(db: Session) -> Dict:
    from app.models.evosys_sales_models import EvoSysCampaign
    from app.services.evosys_sales import campaigns as CA
    changed = []
    for c in db.query(EvoSysCampaign).filter(EvoSysCampaign.name.like("Gov List Cleaners%")).all():
        if not any("reply yes" in str((st or {}).get("body") or "").lower() for st in (c.steps or [])):
            continue
        # updated_by must be a real user (foreign key): keep whoever last edited it.
        if CA.save(db, c, {"steps": [dict(s) for s in GOV_STEPS]}, c.updated_by, origin="manual"):
            changed.append(c.name)
    db.commit()
    return {"gov_lists_booking_link": changed} if changed else {}


def apply_pending(db: Session) -> Dict:
    out = {}
    try:
        out.update(gov_lists_booking_link(db))
    except Exception:                                   # noqa: BLE001
        db.rollback()
        log.exception("evosys sales owner change failed")
    return out
