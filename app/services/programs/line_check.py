"""Mobile or landline? (Twilio Lookup, Line Type Intelligence - about $0.008 a number.)

A text to a landline is billed and never arrives. Each SCI contact's number is
looked up ONCE (re-checked only if the number changes); a landline is not
texted - it shows "Landline" on Choose & Send and the family can still be
emailed or called. Approved by Mike 2026-10-12.
"""
import logging
from datetime import datetime
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _digits10(phone) -> str:
    d = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return d[1:] if len(d) == 11 and d[0] == "1" else d


def needs_check(lead) -> bool:
    d = _digits10(getattr(lead, "phone", None))
    if len(d) != 10:
        return False
    return getattr(lead, "phone_line_number", None) != d or not getattr(lead, "phone_line_type", None)


def check_lead(db: Session, lead, adapter=None) -> Optional[str]:
    """Look the number up (once) and store mobile / landline / voip / unknown / invalid."""
    if not needs_check(lead):
        return getattr(lead, "phone_line_type", None)
    from app.services.evosense.vendors import TwilioLookupLineType
    adapter = adapter or TwilioLookupLineType()
    d = _digits10(lead.phone)
    try:
        res = adapter.validate_phone(d)
    except Exception as exc:                       # a failed lookup is retried later, never guessed
        log.warning("line check failed lead=%s: %s", lead.id, exc)
        return None
    line = res.get("line_type") or ("invalid" if res.get("validation") == "invalid" else "unknown")
    lead.phone_line_type, lead.phone_line_number = line, d
    lead.phone_line_checked_at = datetime.utcnow()
    return line


# A family that said no is never looked up (the lookup is paid, and nothing
# will be texted to them anyway).
OPTED_OUT_STATUSES = {"dnc", "do_not_contact", "opted_out", "deceased"}


def opted_out(lead) -> bool:
    return ((getattr(lead, "status", None) or "").lower() in OPTED_OUT_STATUSES
            or getattr(lead, "allow_sms", None) is False)


def run_for_org(db: Session, org_id: str, limit: int = 100, adapter=None, *,
                source_lead_ids: Optional[Iterable[str]] = None) -> Dict:
    """Check up to `limit` not-yet-checked SCI numbers; commit as it goes.

    source_lead_ids: only the families behind these source Lead IDs (one
    imported file), and never a family that opted out. None (every existing
    caller) checks the whole program exactly as before."""
    from app.models.models import Lead
    from app.models.program_models import ProgramSourceRecord
    base = db.query(ProgramSourceRecord.lead_id).filter(
        ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.lead_id.isnot(None))
    if source_lead_ids is None:
        ids = [x for (x,) in base.distinct().all()]
    else:
        want, found = sorted(set(source_lead_ids)), set()
        for i in range(0, len(want), 900):
            found.update(x for (x,) in base.filter(
                ProgramSourceRecord.source_lead_id.in_(want[i:i + 900])).distinct().all())
        ids = sorted(found)
    done, counts = 0, {}
    remaining = skipped_opted_out = 0
    for i in range(0, len(ids), 500):
        for lead in db.query(Lead).filter(Lead.id.in_(ids[i:i + 500])).all():
            if not needs_check(lead):
                continue
            if source_lead_ids is not None and opted_out(lead):
                skipped_opted_out += 1
                continue
            if done >= limit:
                remaining += 1
                continue
            line = check_lead(db, lead, adapter)
            done += 1
            counts[line or "failed"] = counts.get(line or "failed", 0) + 1
            if done % 25 == 0:
                db.commit()
    db.commit()
    out = {"checked": done, "results": counts, "remaining": remaining}
    if source_lead_ids is not None:
        out["skipped_opted_out"] = skipped_opted_out
    return out
