"""RESTLAND WORKSPACE RESET - DO-NOT-CONTACT PRESERVATION (Issue #507, Nov 15).

Before any Restland contact row is deleted, every real do-not-contact is moved
onto the DESTINATION (phone / email), where deleting and re-importing families
cannot remove it:

  * phones -> suppression_entries (texts and calls, as today) AND
              contact_suppressions scope "family" (so email and any new record
              for that family stay blocked too)
  * emails -> contact_suppressions scope "family" (or "email" for an address
              an advisor marked bad)

What counts as a real block (fails closed: unclear = blocked):
  verified   a non-duplicate family the source marked do-not-contact; an
             existing suppression entry; a duplicate whose parent family is DNC
  unresolved an advisor "remove_all" flag; an inbound reply classified DNC or
             opening with opt-out words ("stop", "unsubscribe", "remove me",
             "do not contact")
Not preserved - CONCLUSIVELY duplicate-only: a family marked DNC only because
the old importer coupled "duplicate" to "do not contact" (registry duplicate
linked to a parent; that branch ran only when the source did not restrict
contact), whose parent is contactable, with no reply, flag or suppression.

`preserve()` is idempotent and is run again immediately before deletion so a
late opt-out is captured. `verify()` proves, read-only, that every preserved
destination is still blocked in other formats and for a brand-new record.
Counts only in logs - no names, numbers, addresses or message text.
"""
import json
import re
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import ContactSuppression, Lead, Organization, Reply, SuppressionEntry
from app.services import contact_suppression as CS

ORG_REQUIRED = "01fed629-9a95-412b-895a-c8b09f37a98c"   # Restland - this module refuses any other org
OPT_OUT_OPENING = re.compile(r"^\s*(stop\b|unsubscribe|remove me|do not (contact|text|call|email)|"
                             r"don.?t (contact|text|call|email)|opt.?out)", re.I)


class ResetRefused(ValueError):
    pass


def _guard(db: Session, org: Optional[Organization]) -> None:
    from app.services import sms_programs
    if org is None or org.id != ORG_REQUIRED:
        raise ResetRefused("Refused: this step is scoped to the Restland workspace only.")
    if sms_programs.is_sci_org(db, org.id):
        raise ResetRefused("Refused: SCI organization.")


def classify(db: Session, org_id: str) -> Dict:
    leads = db.query(Lead).filter(Lead.organization_id == org_id).all()
    by_id = {l.id: l for l in leads}
    supp_phones = {CS.norm_phone(p) for (p,) in db.query(SuppressionEntry.phone)
                   .filter(SuppressionEntry.organization_id == org_id).all()}
    opted_reply = set()
    for (lid, body, cls) in (db.query(Reply.lead_id, Reply.body, Reply.classification)
                             .join(Lead, Lead.id == Reply.lead_id).filter(Lead.organization_id == org_id).all()):
        c = getattr(cls, "value", cls)
        if str(c or "").upper() == "DNC" or OPT_OUT_OPENING.search(body or ""):
            opted_reply.add(lid)
    cats: Dict[str, List[Lead]] = {"source_dnc": [], "parent_dnc": [], "suppressed_phone": [],
                                   "advisor_remove_all": [], "opt_out_reply": [], "bad_email": []}
    dup_only, other_dnc = [], []
    for l in leads:
        st = (l.status or "").lower()
        phones = set(CS._phones(l))
        real_reason = bool(l.manual_flag and l.manual_flag != "bad_email") or bool(phones & supp_phones) \
            or l.id in opted_reply
        if l.id in opted_reply:
            cats["opt_out_reply"].append(l)
        if l.manual_flag == "remove_all":
            cats["advisor_remove_all"].append(l)
        if phones & supp_phones:
            cats["suppressed_phone"].append(l)
        if l.manual_flag == "bad_email":
            cats["bad_email"].append(l)
        if st != "dnc":
            continue
        if not l.is_duplicate:
            cats["source_dnc"].append(l)
            continue
        parent = by_id.get(l.duplicate_of_lead_id) if l.duplicate_of_lead_id else None
        if parent is not None and (parent.status or "").lower() == "dnc":
            cats["parent_dnc"].append(l)
        elif parent is not None and not real_reason:
            dup_only.append(l)
        elif not real_reason:
            other_dnc.append(l)                          # no parent to explain it: unresolved, kept
    cats["unexplained_dnc"] = other_dnc
    return {"cats": cats, "dup_only": dup_only, "dnc_total": sum(1 for l in leads if (l.status or "") == "dnc"),
            "suppression_entries": len([p for p in supp_phones if p])}


VERIFIED = ("source_dnc", "parent_dnc", "suppressed_phone")
UNRESOLVED = ("advisor_remove_all", "opt_out_reply", "unexplained_dnc")


def preserve(db: Session, org: Organization) -> Dict:
    from app.models.models import SuppressionSource
    from app.services.compliance_service import add_suppression_entry
    _guard(db, org)
    c = classify(db, org.id)
    added = Counter()
    families = {}
    # An existing suppression entry keeps its ORIGINAL scope (that phone, texts
    # and calls); only a family-wide do-not-contact is widened to every
    # destination of the family.
    for cat in ("source_dnc", "parent_dnc") + UNRESOLVED:
        for l in c["cats"][cat]:
            families.setdefault(l.id, (l, cat))
    for lid, (l, cat) in families.items():
        why = "Kept before the Restland reset (#507): %s" % cat.replace("_", " ")
        for p in CS._phones(l):
            try:
                before = db.query(SuppressionEntry.id).filter_by(organization_id=org.id, phone=p).first()
                add_suppression_entry(db, org.id, p, why, SuppressionSource.MANUAL)
                if before is None:
                    added["suppression_entries"] += 1
            except ValueError:
                pass
            if CS.add(db, org.id, "phone", p, scope=CS.FAMILY, reason=why, source="restland_reset"):
                added["family_phones"] += 1
        if CS.add(db, org.id, "email", l.email, scope=CS.FAMILY, reason=why, source="restland_reset"):
            added["family_emails"] += 1
    for l in c["cats"]["bad_email"]:
        if l.id in families:
            continue
        if CS.add(db, org.id, "email", l.email, scope=CS.EMAIL_ONLY,
                  reason="Kept before the Restland reset (#507): address marked unusable", source="restland_reset"):
            added["email_only"] += 1
    db.commit()
    return summary(db, org, c, dict(added))


def summary(db: Session, org: Organization, c: Optional[Dict] = None, added: Optional[Dict] = None) -> Dict:
    c = c or classify(db, org.id)
    cats = c["cats"]
    fam_phones = db.query(ContactSuppression).filter_by(organization_id=org.id, kind="phone").count()
    fam_emails = db.query(ContactSuppression).filter_by(organization_id=org.id, kind="email").count()
    sup = db.query(SuppressionEntry).filter_by(organization_id=org.id).count()
    verified = {l.id for k in VERIFIED for l in cats[k]}
    unresolved = {l.id for k in UNRESOLVED for l in cats[k]} - verified
    return {
        "dnc_marked_families": c["dnc_total"],
        "verified_opt_out_families": len(verified),
        "verified_by_reason": {k: len(cats[k]) for k in VERIFIED},
        "unresolved_blocked_families": len(unresolved),
        "unresolved_by_reason": {k: len(cats[k]) for k in UNRESOLVED},
        "duplicate_only_flags": len(c["dup_only"]),
        "bad_email_flags": len(cats["bad_email"]),
        "protected_destinations": {"suppressed_phones": sup, "blocked_phones_any_channel": fam_phones,
                                   "blocked_emails": fam_emails},
        "added_this_run": added or {},
    }


def _formats(phone11: str) -> List[str]:
    a, b, c = phone11[1:4], phone11[4:7], phone11[7:]
    return ["+%s" % phone11, phone11[1:], "(%s) %s-%s" % (a, b, c), "%s.%s.%s" % (a, b, c), "1-%s-%s-%s" % (a, b, c)]


def verify(db: Session, org: Organization) -> Dict:
    """READ-ONLY proof: each preserved destination is blocked in other formats,
    for a brand-new contact record (import), at enrollment and at sending.
    Writes nothing (the probe record is never added to the session)."""
    from app.services.compliance_service import check_compliance_preflight
    _guard(db, org)
    fails = Counter()
    checked = Counter()
    for r in db.query(ContactSuppression).filter_by(organization_id=org.id).all():
        if r.kind == "phone":
            for f in _formats(r.value):
                checked["phone_formats"] += 1
                b = CS.blocks(db, org.id, phones=[f])
                if not (b["sms"] and b["email"]):
                    fails["phone_format"] += 1
            probe = Lead(organization_id=org.id, first_name="Probe", phone=_formats(r.value)[2],
                         email="new.address.%s@example.invalid" % r.id[:8], status="new")
        else:
            variants = [r.value.upper(), "  %s " % r.value, r.value.title()]
            for v in variants:
                checked["email_formats"] += 1
                if not CS.blocks(db, org.id, emails=[v])["email"]:
                    fails["email_format"] += 1
            probe = Lead(organization_id=org.id, first_name="Probe", email=r.value.upper(), status="new")
        # import: a brand-new record for this destination arrives blocked
        checked["new_record"] += 1
        CS.mark_new_lead(db, probe)
        if r.scope == CS.FAMILY and probe.status != "dnc":
            fails["new_record_not_dnc"] += 1
        if r.scope == CS.EMAIL_ONLY and probe.allow_email is not False:
            fails["new_record_email_open"] += 1
        # dispatch / enrollment: the preflight refuses it (status reset so the
        # destination itself, not the dnc status, is what refuses)
        probe.status = "new"
        probe.allow_email = None
        for ch in ("email", "sms") if r.scope == CS.FAMILY else ("email",):
            checked["preflight_" + ch] += 1
            try:
                check_compliance_preflight(db, probe, channel=ch)
                fails["preflight_open_" + ch] += 1
            except ValueError:
                pass
    for (p,) in db.query(SuppressionEntry.phone).filter_by(organization_id=org.id).all():
        checked["suppressed_phone_formats"] += 1
        if not CS.blocks(db, org.id, phones=[_formats(p)[2]])["sms"]:
            fails["suppressed_phone_format"] += 1
    db.rollback()                                        # nothing written, by construction
    return {"checked": dict(checked), "failures": dict(fails), "ok": not fails}
