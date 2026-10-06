"""Inbox-placement checks: where does the REAL first touch land?

Authentication (SPF/DKIM/DMARC) gets mail delivered; it does not decide the
Gmail tab or the Outlook folder. That depends on the sending domain's
reputation, engagement and how the message reads - and nobody can guarantee
it. So it is measured, repeatably, before and during a launch:

    1. a manager sets seed inboxes the program owns (Gmail, Outlook, Yahoo,
       iCloud) in Settings - never customers;
    2. "Send placement check" mails each seed the program's actual first touch,
       built by the message brain for a real location, through the same
       identity, alias, headers and footer a family would get;
    3. a person opens each seed and records where it landed;
    4. the readiness gate wants a recent (PLACEMENT_MAX_AGE_DAYS) result in
       Primary / Inbox from every provider before campaigns are switched on.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.program_models import LocationProfile, OutreachProgram, ProgramPlacementCheck

log = logging.getLogger(__name__)

PROVIDERS = ("gmail", "outlook", "yahoo", "icloud")
FOLDERS = ("primary", "inbox", "promotions", "updates", "other_tab", "spam", "missing")
GOOD = ("primary", "inbox")
PLACEMENT_MAX_AGE_DAYS = 14


def seeds(prog: OutreachProgram) -> Dict[str, str]:
    try:
        data = json.loads(prog.placement_seed_addresses or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if k in PROVIDERS and v}


def _sample_location(db: Session, prog: OutreachProgram, location_id: Optional[str]) -> Optional[LocationProfile]:
    from app.services.programs import aliases
    q = db.query(LocationProfile).filter(LocationProfile.organization_id == prog.organization_id,
                                         LocationProfile.is_review_bucket.is_(False))
    if location_id:
        return q.filter(LocationProfile.location_id == location_id).first()
    profs = q.order_by(LocationProfile.official_name).all()
    return next((p for p in profs if aliases.receiving(prog, p)), profs[0] if profs else None)


def build_sample(db: Session, prog: OutreachProgram, prof: LocationProfile, family: str) -> Dict:
    """The first touch exactly as the brain writes it for this location."""
    from app.models.location_models import Location
    from app.services.programs import identity, message_brain as brain
    from app.services.programs.unsubscribe import postal_for
    loc = db.query(Location).filter(Location.id == prof.location_id).first()
    packet = {"first_name": None, "kerry": prog.primary_contact_name, "location": prof.official_name,
              "campaign_family": family, "display_name": identity.display_name(prog, prof),
              "postal": postal_for(prof.official_name, loc)}
    msg = brain.compose(packet, 1, "email")
    return {"message": msg, "postal": packet["postal"], "display_name": packet["display_name"]}


def send_checks(db: Session, prog: OutreachProgram, actor_id: str, *, location_id: Optional[str] = None,
                family: str = "veteran_planning_guide") -> List[ProgramPlacementCheck]:
    from app.services.email_service import send_email_via_provider
    from app.services.programs import aliases
    from app.services.programs.email_touches import text_to_html
    from app.services.programs.unsubscribe import footer_html, url_for
    from app.services.public_identity import sending_identity_for_org
    targets = seeds(prog)
    if not targets:
        raise ValueError("No seed inboxes are set (Settings -> Inbox placement).")
    prof = _sample_location(db, prog, location_id)
    if prof is None:
        raise ValueError("The program has no location to build a sample from.")
    sample = build_sample(db, prog, prof, family)
    out = []
    for provider, address in targets.items():
        row = ProgramPlacementCheck(organization_id=prog.organization_id, provider=provider,
                                    seed_address=address, subject=sample["message"]["subject"],
                                    location_id=prof.location_id, touch="1")
        db.add(row)
        db.flush()
        ident = sending_identity_for_org(db, prog.organization_id)
        try:
            ident.from_name = sample["display_name"]
        except AttributeError:
            pass
        aliases.apply(prog, prof, ident)
        token_id = "placement-%s" % row.id
        body = text_to_html(sample["message"]["body"]) + footer_html(token_id, sample["postal"])
        try:
            res = send_email_via_provider(
                to_email=address, subject=sample["message"]["subject"], body_html=body, org=ident,
                headers={"List-Unsubscribe": "<%s>" % url_for(token_id),
                         "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
            if res.get("success"):
                row.sent_at, row.provider_message_id = datetime.utcnow(), res.get("provider_message_id")
            else:
                row.send_error = (res.get("error") or "provider refused")[:500]
        except Exception as exc:                         # noqa: BLE001
            row.send_error = ("%s: %s" % (type(exc).__name__, exc))[:500]
        out.append(row)
    db.commit()
    return out


def record(db: Session, check: ProgramPlacementCheck, folder: str, user_id: str,
           note: Optional[str] = None) -> ProgramPlacementCheck:
    if folder not in FOLDERS:
        raise ValueError("folder must be one of: %s" % ", ".join(FOLDERS))
    check.folder, check.recorded_at, check.recorded_by = folder, datetime.utcnow(), user_id
    check.note = (note or "")[:500] or None
    db.commit()
    return check


def latest(db: Session, org_id: str) -> Dict[str, Optional[ProgramPlacementCheck]]:
    rows = (db.query(ProgramPlacementCheck).filter(ProgramPlacementCheck.organization_id == org_id,
                                                   ProgramPlacementCheck.folder.isnot(None))
            .order_by(ProgramPlacementCheck.recorded_at.desc()).limit(200).all())
    out: Dict[str, Optional[ProgramPlacementCheck]] = {p: None for p in PROVIDERS}
    for r in rows:
        if r.provider in out and out[r.provider] is None:
            out[r.provider] = r
    return out


def readiness_item(db: Session, prog: OutreachProgram, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=PLACEMENT_MAX_AGE_DAYS)
    parts, ok = [], True
    for provider, r in latest(db, prog.organization_id).items():
        if r is None:
            ok = False
            parts.append("%s: not checked" % provider)
        elif r.recorded_at < cutoff:
            ok = False
            parts.append("%s: %s (stale, %s)" % (provider, r.folder, r.recorded_at.date().isoformat()))
        else:
            ok = ok and r.folder in GOOD
            parts.append("%s: %s" % (provider, r.folder))
    if not seeds(prog):
        parts.insert(0, "no seed inboxes set")
    return {"key": "inbox_placement", "ok": ok,
            "detail": "; ".join(parts) + " - needs Primary/Inbox at every provider within %d days "
                      "(measured, never guaranteed)" % PLACEMENT_MAX_AGE_DAYS}
