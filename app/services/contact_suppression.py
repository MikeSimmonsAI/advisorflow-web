"""DESTINATION-LEVEL DO-NOT-CONTACT (Issue #507, Nov 15 Restland reset).

One question, asked everywhere a family can be imported, enrolled or sent to:
"is any of this family's phones or addresses blocked, and on which channel?"
The answer comes from two tables that do not depend on contact rows:

  suppression_entries   phone numbers (texts / calls) - unchanged, still the
                        SMS and voice source of truth
  contact_suppressions  phones and emails with a scope ("family" = every
                        channel; "email" = email only)

So a family deleted and later imported again - under a new record, in another
phone format (+1, dashes, parentheses) or with its email in other letter case
- is blocked exactly as before.
"""
import logging
import re
from typing import Dict, Iterable, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

FAMILY, EMAIL_ONLY = "family", "email"


def norm_phone(raw) -> Optional[str]:
    """Same rule as the importer and every send path (dedup_service /
    compliance_service.usable_us_phone): 11 digits starting with 1."""
    if not raw:
        return None
    d = re.sub(r"\D", "", str(raw))
    if len(d) == 10:
        d = "1" + d
    return d if len(d) == 11 and d.startswith("1") else None


def norm_email(raw) -> Optional[str]:
    """Same rule as the email paths (lower(trim(email)))."""
    e = (str(raw or "")).strip().lower()
    return e if "@" in e else None


def _phones(lead) -> Iterable[str]:
    for f in ("phone", "mobile_phone", "secondary_phone", "callback_phone"):
        v = norm_phone(getattr(lead, f, None))
        if v:
            yield v


def blocks(db: Session, org_id: Optional[str], *, phones=(), emails=()) -> Dict[str, Optional[str]]:
    """{"family": reason|None, "sms": reason|None, "email": reason|None} for
    these destinations in this organization."""
    from app.models.models import ContactSuppression, SuppressionEntry
    out = {"family": None, "sms": None, "email": None}
    if not org_id:
        return out
    ph = sorted({p for p in (norm_phone(x) for x in phones) if p})
    em = sorted({e for e in (norm_email(x) for x in emails) if e})
    try:
        # Only workspaces with records here are affected (the Restland reset);
        # every other workspace keeps its existing checks unchanged.
        if db.query(ContactSuppression.id).filter(ContactSuppression.organization_id == org_id).first() is None:
            return out
        if ph:
            if db.query(SuppressionEntry.id).filter(SuppressionEntry.organization_id == org_id,
                                                    SuppressionEntry.phone.in_(ph)).first() is not None:
                out["sms"] = "phone number is on the suppression list"
        rows = []
        if ph:
            rows += db.query(ContactSuppression).filter(ContactSuppression.organization_id == org_id,
                                                        ContactSuppression.kind == "phone",
                                                        ContactSuppression.value.in_(ph)).all()
        if em:
            rows += db.query(ContactSuppression).filter(ContactSuppression.organization_id == org_id,
                                                        ContactSuppression.kind == "email",
                                                        ContactSuppression.value.in_(em)).all()
    except Exception:                                    # noqa: BLE001 - fail CLOSED
        log.exception("suppression lookup failed for org %s", org_id)
        return {"family": "suppression list could not be read", "sms": "suppression list could not be read",
                "email": "suppression list could not be read"}
    for r in rows:
        if r.scope == FAMILY:
            out["family"] = out["family"] or "this family asked not to be contacted"
        elif r.kind == "email":
            out["email"] = out["email"] or "email address is on the do-not-email list"
    if out["family"]:
        out["sms"] = out["sms"] or out["family"]
        out["email"] = out["email"] or out["family"]
    return out


def for_lead(db: Session, lead) -> Dict[str, Optional[str]]:
    if lead is None:
        return {"family": None, "sms": None, "email": None}
    return blocks(db, getattr(lead, "organization_id", None), phones=list(_phones(lead)),
                  emails=[getattr(lead, "email", None)])


def add(db: Session, org_id: str, kind: str, raw: str, *, scope: str, reason: str, source: str) -> bool:
    """Record one block. Idempotent; a family scope never narrows to email."""
    from app.models.models import ContactSuppression
    value = norm_phone(raw) if kind == "phone" else norm_email(raw)
    if not value:
        return False
    row = db.query(ContactSuppression).filter_by(organization_id=org_id, kind=kind, value=value).first()
    if row is not None:
        if scope == FAMILY and row.scope != FAMILY:
            row.scope = FAMILY
            row.reason = reason[:500]
        return False
    sp = db.begin_nested()
    try:
        db.add(ContactSuppression(organization_id=org_id, kind=kind, value=value, scope=scope,
                                  reason=reason[:500], source=source[:60]))
        db.flush()
        sp.commit()
        return True
    except Exception:                                    # noqa: BLE001 - raced: already there
        sp.rollback()
        return False


# ── import-time: a new record for a blocked family arrives already blocked ──

def mark_new_lead(db: Session, lead) -> Optional[str]:
    """Called for every NEW Lead row before it is written (any importer, any
    form). A family-wide block makes it do-not-contact; an email-only block
    turns its email off; a suppressed phone turns texting off."""
    b = for_lead(db, lead)
    if b["family"]:
        lead.status = "dnc"
        return b["family"]
    if b["email"]:
        lead.allow_email = False
    if b["sms"]:
        lead.allow_sms = False
    return b["email"] or b["sms"]


def _org_sets(session, org_id: str) -> Dict[str, set]:
    """Every blocked destination of one organization, loaded once per flush.

    Only organizations with records in contact_suppressions are affected (the
    Restland reset); every other workspace returns empty and is left exactly
    as before."""
    from app.models.models import ContactSuppression, SuppressionEntry
    sets = {"sms_phone": set(), "family_phone": set(), "family_email": set(), "email": set()}
    rows = session.query(ContactSuppression).filter(ContactSuppression.organization_id == org_id).all()
    if not rows:
        return sets
    for r in rows:
        if r.scope == FAMILY:
            sets["family_phone" if r.kind == "phone" else "family_email"].add(r.value)
        elif r.kind == "email":
            sets["email"].add(r.value)
    for (p,) in session.query(SuppressionEntry.phone).filter(SuppressionEntry.organization_id == org_id).all():
        n = norm_phone(p)
        if n:
            sets["sms_phone"].add(n)
    return sets


def _before_flush(session, flush_context, instances):
    from app.models.models import Lead
    new = [o for o in session.new if isinstance(o, Lead) and getattr(o, "organization_id", None)]
    if not new:
        return
    cache: Dict[str, Dict[str, set]] = {}
    with session.no_autoflush:
        for lead in new:
            org = lead.organization_id
            try:
                if org not in cache:
                    cache[org] = _org_sets(session, org)
                s = cache[org]
            except Exception:                            # noqa: BLE001 - fail closed for this record
                log.exception("import-time suppression check failed (org %s)", org)
                lead.status = "dnc"
                continue
            if not any(s.values()):
                continue
            phones = set(_phones(lead))
            email = norm_email(getattr(lead, "email", None))
            if phones & s["family_phone"] or (email and email in s["family_email"]):
                lead.status = "dnc"
                continue
            if email and email in s["email"]:
                lead.allow_email = False
            if phones & s["sms_phone"]:
                lead.allow_sms = False


_registered = False


def register() -> None:
    global _registered
    if _registered:
        return
    from sqlalchemy import event
    from sqlalchemy.orm import Session as _S
    event.listen(_S, "before_flush", _before_flush)
    _registered = True
