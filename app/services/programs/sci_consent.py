"""SCI OPT-INS & OPT-OUTS - one read-only list of every family's texting status.

Read only: nothing here changes who is texted. The send gate
(sms_programs.sci_check) stays the one place that decides.

For each real SCI family with a phone number:
  * opted_out - a STOP / opt-out reply, a withdrawn consent record, the number
    on the suppression list, or the family marked do-not-contact.
  * opted_in  - an SCI SMS consent of record (the owner's consent forms are
    recorded automatically at a family's first text; a web sign-up counts too).
  * no_record - neither yet (typically not texted yet; under the owner's
    standing consent their record is filed at the first text).
"""
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

STATUSES = ("opted_in", "opted_out", "no_record")
_STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "OPTOUT", "OPT OUT", "REVOKE"}
METHOD_WORDS = {
    "owner_attested": "Your consent forms (recorded at first text)",
    "owner_attested_import": "Your consent forms (recorded at first text)",
    "web_form_checkbox": "Signed up online",
}


def _iso(d) -> Optional[str]:
    return (d.isoformat() + "Z") if isinstance(d, datetime) else None


def families(db: Session, org_id: str) -> List[Dict]:
    from app.models.models import Lead
    from app.models.program_models import LocationProfile, ProgramResponse, ProgramSourceRecord
    from app.models.sms_consent_models import SmsConsentRecord
    from app.services import sms_programs, test_records, wholesale_sms

    p = sms_programs.PROGRAMS[sms_programs.SCI]
    leads = [l for l in db.query(Lead).filter(Lead.organization_id == org_id).all()
             if not test_records.is_test_record(l) and not getattr(l, "is_demo", False)]
    recs: Dict[str, list] = {}
    for r in db.query(SmsConsentRecord).filter(SmsConsentRecord.organization_id == org_id,
                                               SmsConsentRecord.program == p.consent_program).all():
        recs.setdefault(r.phone_normalized, []).append(r)
    suppressed = set()
    try:
        from app.models.models import SuppressionEntry
        for e in db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org_id).all():
            v = getattr(e, "phone_normalized", None) or wholesale_sms.normalize_e164(getattr(e, "phone", None))
            if v:
                suppressed.add(v)
    except Exception:                                    # noqa: BLE001
        db.rollback()
    stops: Dict[str, object] = {}
    for r in (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                               ProgramResponse.response_class == "opt_out")
              .order_by(ProgramResponse.received_at).all()):
        stops[r.lead_id] = r                             # latest wins
    loc_of = {r.lead_id: r.location_id for r in db.query(ProgramSourceRecord.lead_id, ProgramSourceRecord.location_id)
              .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.lead_id.isnot(None)).all()}
    names = {lp.location_id: lp.official_name for lp in db.query(LocationProfile)
             .filter(LocationProfile.organization_id == org_id).all()}

    out = []
    for l in leads:
        e164 = wholesale_sms.normalize_e164(l.phone)
        rs = recs.get(e164 or "-", [])
        withdrawn = [r for r in rs if r.status != "opted_in" or r.opted_out_at]
        good = [r for r in rs if r not in withdrawn and sms_programs.consent_counts(p, r)]
        stop = stops.get(l.id)
        row = {"lead_id": l.id, "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title() or "Family",
               "phone": l.phone, "location": names.get(loc_of.get(l.id)), "location_id": loc_of.get(l.id)}
        if stop is not None or withdrawn or (e164 and e164 in suppressed) or (l.status or "").lower() == "dnc":
            w = sorted(withdrawn, key=lambda r: r.opted_out_at or r.updated_at or datetime.min)
            when = (stop.received_at if stop is not None else None) or (w[-1].opted_out_at if w else None)
            said = (getattr(stop, "body_excerpt", None) or "").strip()
            how = (("Replied STOP" if said.upper().strip(" .!") in _STOP_WORDS or not said else "Asked to stop texts")
                   if stop is not None
                   else "Replied STOP" if (w and (w[-1].opt_out_source or "") == "reply_stop")
                   else "Marked do-not-contact" if (l.status or "").lower() == "dnc"
                   else "On the do-not-text list" if e164 in suppressed else "Consent withdrawn")
            row.update(status="opted_out", how=how, at=_iso(when),
                       words=(said or (w[-1].opt_out_keyword if w else None)))
        elif good:
            g = max(good, key=lambda r: r.consented_at or datetime.min)
            row.update(status="opted_in", how=METHOD_WORDS.get(g.consent_method) or (
                           "Your consent forms" if "owner" in (g.consent_method or "") else "Consent of record"),
                       at=_iso(g.consented_at), words=None)
        elif e164:
            row.update(status="no_record", how="Not texted yet - recorded at the first text", at=None, words=None)
        else:
            continue                                     # no phone: texting does not apply
        out.append(row)
    return out


def listing(db: Session, org_id: str, *, status: Optional[str] = None, q: Optional[str] = None,
            location_id: Optional[str] = None, limit: int = 500) -> Dict:
    rows = families(db, org_id)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUSES}
    if location_id:
        rows = [r for r in rows if r.get("location_id") == location_id]
    if status in STATUSES:
        rows = [r for r in rows if r["status"] == status]
    if q:
        ql = q.strip().lower()
        digits = "".join(ch for ch in ql if ch.isdigit())
        rows = [r for r in rows if ql in (r["name"] or "").lower() or ql in (r.get("location") or "").lower()
                or (digits and digits in "".join(ch for ch in (r.get("phone") or "") if ch.isdigit()))]
    rows.sort(key=lambda r: (r.get("at") or ""), reverse=True)
    return {"counts": counts, "total": len(rows), "items": rows[:limit]}
