"""File Review 6 Block family networks and At-Need 6 Block family-care cases (s22-29).

* One parent batch per family file review (idempotent); each contact is its
  own record with its own validation, eligibility and campaign state.
* Six is the collection target, not a gate: fewer closes with a reason,
  more are welcome.
* A contact becomes a family record (Lead) of its own - source "File Review 6 Block"
  or "At-Need 6 Block" - so it is worked, booked and sold like any family and its
  origin is never lost (workflow.attribution reads it back).
* Duplicates are caught on normalized phone/email. An advisor is told only
  that the person is already on file - never whose they are.
* Nothing is sent from here. Enrollment needs a person's documented outreach
  authorization, then management approval, and the campaign template for
  that kind must be approved by management (BookIT settings) before anyone
  is enrolled. At-Need 6 Block contacts never enter ordinary pre-planning campaigns.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.bookit_models import (ADD_STATUSES, PRIORITIES, BookitAddCase, BookitReferralBatch,
                                      BookitReferralContact)
from app.services.bookit.core import (MANAGEMENT, BookitError, Who, audit, cfg, notify, require_family,
                                      team_names)
from app.services.programs import restland_sheet as RS

SOURCE = {"evo6": "evo6_block", "add": "add_lead"}
LABEL = {"evo6": "File Review 6 Block", "add": "At-Need 6 Block"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def norm_phone(v: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 10:
        d = "1" + d
    return d if len(d) == 11 and d.startswith("1") else None


def norm_email(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip().lower()
    return v if _EMAIL.match(v) else None


# ── batches ─────────────────────────────────────────────────────────────────

def _batch(db, prog, w: Who, batch_id: str) -> BookitReferralBatch:
    b = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == prog.organization_id,
                                             BookitReferralBatch.id == batch_id).first()
    if b is None:
        raise BookitError("Not found.", 404)
    owners = [b.submitting_advisor] + [a for (a,) in db.query(BookitReferralContact.assigned_advisor)
                                       .filter(BookitReferralContact.batch_id == b.id)]
    if not _on_team(db, prog.organization_id, b.submitting_advisor):
        # filed under someone who isn't an advisor (e.g. started from the owner's
        # account): it belongs to the advisor who did the review. An advisor's own
        # network stays theirs even if the family is reassigned (owner, Nov 26).
        owners.append(_review_owner(db, prog.organization_id, b.source_appointment_id))
    if not w.sees(*owners):
        raise BookitError("Not found.", 404)
    return b


def _on_team(db, org: str, name: Optional[str]) -> bool:
    from app.models.program_models import ProgramTeamMember as M
    return bool(name) and db.query(M.id).filter(M.organization_id == org, M.name == name).first() is not None


def _review_owner(db, org: str, appointment_id: Optional[str]) -> Optional[str]:
    """The advisor the file review (appointment) is assigned to."""
    if not appointment_id:
        return None
    from app.models.program_models import HandoffSheetRow as R
    rec = db.query(R).filter(R.organization_id == org, R.row_id == appointment_id).first()
    return rec.assigned_to if rec is not None else None


def create_batch(db: Session, prog, w: Who, body: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.bookit.workflow import attribution
    org = prog.organization_id
    kind = body.get("kind") or "evo6"
    if kind not in SOURCE:
        raise BookitError("Unknown kind.")
    key = (body.get("idempotency_key") or "").strip()
    if not key:
        raise BookitError("Missing idempotency key.")
    have = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == org,
                                                BookitReferralBatch.idempotency_key == key).first()
    if have is not None:
        return batch_dict(db, w, have)
    origin = body.get("origin_lead_id")
    case_id = body.get("add_case_id")
    if kind == "evo6":
        require_family(db, prog, w, origin)
    elif case_id:
        case = _case(db, prog, w, case_id)
        origin = origin or case.contact_lead_id
    # a second batch for the same family review is the same batch
    if body.get("source_appointment_id"):
        same = db.query(BookitReferralBatch).filter(
            BookitReferralBatch.organization_id == org, BookitReferralBatch.kind == kind,
            BookitReferralBatch.source_appointment_id == body["source_appointment_id"]).first()
        if same is not None:
            return batch_dict(db, w, same)
    if kind == "evo6" and body.get("source_appointment_id"):
        # the review must be one this caller may work (advisors: their own)
        from app.services.bookit.workflow import appointment as _appt
        _appt(db, prog, w, body["source_appointment_id"])
    # The network belongs to the advisor who did the review - also when a
    # manager starts it (Nov 26: one started from the owner's account was
    # filed under the owner and hidden from the advisor).
    advisor = (body.get("submitting_advisor") if w.manager and body.get("submitting_advisor") else None) \
        or (_review_owner(db, org, body.get("source_appointment_id")) if w.manager else None) \
        or w.name or w.user_name
    att = attribution(db, prog, origin, body.get("source_appointment_id")) if origin else {}
    b = BookitReferralBatch(organization_id=org, idempotency_key=key[:120], kind=kind, origin_lead_id=origin,
                            source_appointment_id=body.get("source_appointment_id"), add_case_id=case_id,
                            submitting_advisor=advisor, original_campaign=att.get("campaign_key"))
    db.add(b)
    db.flush()
    audit(db, org, w, "referral_batch", b.id, "create", "kind", None, kind)
    return batch_dict(db, w, b)


def _recount(db, b: BookitReferralBatch) -> None:
    n = db.query(BookitReferralContact).filter(BookitReferralContact.batch_id == b.id,
                                               BookitReferralContact.validation_status.in_(["pending", "valid"])).count()
    if b.collection_status != "declined":
        b.collection_status = "not_started" if n == 0 else ("complete" if n >= 6 else "partial")


def save_contact(db: Session, prog, w: Who, batch_id: str, body: Dict[str, Any],
                 contact_id: Optional[str] = None) -> Dict[str, Any]:
    org = prog.organization_id
    b = _batch(db, prog, w, batch_id)
    if contact_id:
        c = db.query(BookitReferralContact).filter(BookitReferralContact.batch_id == b.id,
                                                   BookitReferralContact.id == contact_id).first()
        if c is None or not w.sees(c.assigned_advisor, b.submitting_advisor):
            raise BookitError("Not found.", 404)
    else:
        pos = db.query(BookitReferralContact).filter(BookitReferralContact.batch_id == b.id).count()
        c = BookitReferralContact(organization_id=org, batch_id=b.id, position=pos,
                                  assigned_advisor=b.submitting_advisor)
        db.add(c)
    for k, attr in (("first_name", "first_name"), ("last_name", "last_name"), ("relationship", "relationship"),
                    ("street", "street"), ("city", "city"), ("state", "state"), ("zip", "zip_code"),
                    ("notes", "notes")):
        if k in body:
            setattr(c, attr, (body[k] or "").strip()[:500] or None)
    if "phone" in body:
        c.phone = (body["phone"] or "").strip()[:40] or None
        c.norm_phone = norm_phone(c.phone)
        if c.phone and not c.norm_phone:
            raise BookitError("That phone number needs 10 digits.")
    if "email" in body:
        c.email = (body["email"] or "").strip()[:200] or None
        c.norm_email = norm_email(c.email)
        if c.email and not c.norm_email:
            raise BookitError("That email address doesn't look right.")
    if "outreach_authorized" in body:
        c.outreach_authorized = bool(body["outreach_authorized"])
        audit(db, org, w, "referral_contact", c.id or "-", "update", "outreach_authorized", None,
              c.outreach_authorized)
    if "assigned_advisor" in body and w.manager:
        if body["assigned_advisor"] and body["assigned_advisor"] not in team_names(db, org):
            raise BookitError("That person isn't on the team.")
        audit(db, org, w, "referral_contact", c.id or "-", "reassign", "assigned_advisor", c.assigned_advisor,
              body["assigned_advisor"])
        c.assigned_advisor = body["assigned_advisor"] or None
    if not (c.first_name or c.last_name):
        raise BookitError("Add the person's name.")
    # duplicates inside this batch
    for other in db.query(BookitReferralContact).filter(BookitReferralContact.batch_id == b.id,
                                                        BookitReferralContact.id != (c.id or "-")):
        if (c.norm_phone and c.norm_phone == other.norm_phone) or (c.norm_email and c.norm_email == other.norm_email):
            raise BookitError("That person is already in this family network.")
    db.flush()
    _validate(db, prog, w, b, c)
    _recount(db, b)
    db.flush()
    return contact_dict(c, w)


def _validate(db, prog, w: Who, b: BookitReferralBatch, c: BookitReferralContact) -> None:
    """Name + a usable phone or email -> valid, and the contact gets (or is
    matched to) a family record. Someone already on file is recorded as a
    duplicate - linked, never overwritten."""
    from app.models.models import Lead
    org = prog.organization_id
    if c.suppressed:
        return
    if not (c.norm_phone or c.norm_email):
        c.validation_status = "pending"
        return
    if c.lead_id:
        c.validation_status = "valid"
        return
    dup = RS._existing(db, org, ("+" + c.norm_phone) if c.norm_phone else None, c.norm_email)
    if dup is not None:
        c.validation_status, c.duplicate_of_lead_id = "duplicate", dup.id
        RS._note(db, org, dup.id, "Restland BookIT (%s): also named in a %s from another family"
                 % (b.submitting_advisor or "the team", LABEL[b.kind]))
        return
    origin = db.query(Lead).filter(Lead.id == b.origin_lead_id).first() if b.origin_lead_id else None
    who = "%s %s" % ((origin.first_name or "") if origin else "", (origin.last_name or "") if origin else "")
    detail = "%s - from %s%s; entered by %s" % (LABEL[b.kind], who.strip() or "a family",
                                                " (%s)" % c.relationship if c.relationship else "",
                                                b.submitting_advisor or "the team")
    lead = Lead(organization_id=org, first_name=c.first_name, last_name=c.last_name, phone=c.norm_phone,
                phone_raw=c.phone, email=c.norm_email, street_address=c.street, city=c.city, state=c.state,
                zip_code=c.zip_code, status=RS.REFERRAL_STATUS, source=SOURCE[b.kind], source_detail=detail[:250],
                contact_channel="sms" if c.norm_phone else "email_only")
    db.add(lead)
    db.flush()
    c.lead_id, c.validation_status = lead.id, "valid"
    RS._note(db, org, lead.id, "Restland BookIT: added by %s - %s%s" % (
        b.submitting_advisor or "the team", detail, "; notes: %s" % c.notes if c.notes else ""))


def contact_dict(c: BookitReferralContact, w: Who) -> Dict[str, Any]:
    d = {k: getattr(c, k) for k in ("id", "batch_id", "position", "first_name", "last_name", "relationship",
                                    "phone", "email", "street", "city", "state", "zip_code", "notes",
                                    "validation_status", "outreach_authorized", "suppressed", "suppression_reason",
                                    "assigned_advisor", "lead_id", "enrollment_state", "enrollment_step")}
    d["eligible"] = eligible(c)[0]
    d["eligibility_reason"] = eligible(c)[1]
    if w.manager:
        d["duplicate_of_lead_id"] = c.duplicate_of_lead_id
    d["status_color"] = ("red" if c.suppressed or c.validation_status == "invalid" else
                         "emerald" if d["eligible"] or c.enrollment_state in ("enrolled", "completed") else
                         "gray" if not (c.first_name or c.last_name) else "champagne")
    return d


def eligible(c: BookitReferralContact):
    if c.suppressed:
        return False, "Suppressed: %s" % (c.suppression_reason or "do not contact")
    if c.validation_status == "duplicate":
        return False, "Already on file - worked from the existing family record"
    if c.validation_status != "valid":
        return False, "Needs a valid phone or email"
    if not c.outreach_authorized:
        return False, "Outreach not documented as authorized yet"
    return True, "Eligible"


def batch_dict(db: Session, w: Who, b: BookitReferralBatch) -> Dict[str, Any]:
    from app.models.models import Lead
    contacts = (db.query(BookitReferralContact).filter(BookitReferralContact.batch_id == b.id)
                .order_by(BookitReferralContact.position).all())
    if not w.manager:
        contacts = [c for c in contacts if w.sees(c.assigned_advisor, b.submitting_advisor)]
    origin = db.query(Lead).filter(Lead.id == b.origin_lead_id).first() if b.origin_lead_id else None
    cs = [contact_dict(c, w) for c in contacts]
    lead_ids = [c.lead_id for c in contacts if c.lead_id]
    booked = sold = 0
    if lead_ids:
        from app.models.bookit_models import BookitSale
        from app.models.models import BookingLink
        booked = db.query(BookingLink.lead_id).filter(BookingLink.lead_id.in_(lead_ids),
                                                      BookingLink.booked_time.isnot(None)).distinct().count()
        sold = db.query(BookitSale.id).filter(BookitSale.referral_batch_id == b.id,
                                              BookitSale.status == "closed").count()
    return {"id": b.id, "kind": b.kind, "label": LABEL[b.kind], "origin_lead_id": b.origin_lead_id,
            "origin_family": ("%s %s" % (origin.first_name or "", origin.last_name or "")).strip() if origin else None,
            "source_appointment_id": b.source_appointment_id, "add_case_id": b.add_case_id,
            "submitting_advisor": b.submitting_advisor, "original_campaign": b.original_campaign,
            "collection_status": b.collection_status, "explanation": b.explanation,
            "created_at": b.created_at.isoformat() + "Z" if b.created_at else None,
            "contacts": cs,
            "progress": {"collected": len(cs), "validated": sum(1 for c in cs if c["validation_status"] == "valid"),
                         "eligible": sum(1 for c in cs if c["eligible"]),
                         "pending_verification": sum(1 for c in cs if c["validation_status"] == "pending"),
                         "enrolled": sum(1 for c in cs if c["enrollment_state"] == "enrolled"),
                         "contacted": sum(1 for c in cs if (c["enrollment_step"] or 0) > 0),
                         "appointments_booked": booked, "sales_attributed": sold}}


def set_batch(db: Session, prog, w: Who, batch_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    b = _batch(db, prog, w, batch_id)
    if "explanation" in body:
        b.explanation = (body["explanation"] or "").strip()[:1000] or None
    if body.get("declined"):
        if not b.explanation:
            raise BookitError("Say why no family contacts were collected.")
        b.collection_status = "declined"
    db.flush()
    return batch_dict(db, w, b)


def batches(db: Session, prog, w: Who, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    q = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == prog.organization_id)
    if kind:
        q = q.filter(BookitReferralBatch.kind == kind)
    out = []
    for b in q.order_by(BookitReferralBatch.created_at.desc()).limit(500):
        d = batch_dict(db, w, b)
        if (w.manager or b.submitting_advisor == w.name or d["contacts"]
                or (w.name and not _on_team(db, prog.organization_id, b.submitting_advisor)
                    and _review_owner(db, prog.organization_id, b.source_appointment_id) == w.name)):
            out.append(d)
    return out


# ── the per-contact journey ────────────────────────────────────────────────

def template_approved(prog, kind: str) -> bool:
    return bool(((cfg(prog).get("campaigns") or {}).get(kind) or {}).get("approved"))


def enroll(db: Session, prog, w: Who, contact_id: str, action: str, reason: Optional[str] = None):
    """request (advisor) -> pending_approval; approve (management) ->
    enrolled (only with an approved template); pause / stop (either)."""
    org = prog.organization_id
    c = db.query(BookitReferralContact).filter(BookitReferralContact.organization_id == org,
                                               BookitReferralContact.id == contact_id).first()
    if c is None:
        raise BookitError("Not found.", 404)
    b = _batch(db, prog, w, c.batch_id)
    if not w.sees(c.assigned_advisor, b.submitting_advisor):
        raise BookitError("Not found.", 404)
    ok, why = eligible(c)
    if action == "request":
        if not ok:
            raise BookitError(why)
        if c.enrollment_state in ("pending_approval", "enrolled"):
            return contact_dict(c, w)
        c.enrollment_state = "pending_approval"
        notify(db, org, MANAGEMENT, "enrollment_request", "%s contact ready for approval" % LABEL[b.kind],
               ref_type="referral_contact", ref_id=c.id)
    elif action == "approve":
        if not w.manager:
            raise BookitError("Only management approves enrollment.", 403)
        if not ok:
            raise BookitError(why)
        if not template_approved(prog, b.kind):
            raise BookitError("The %s message template hasn't been approved yet (BookIT settings)." % LABEL[b.kind])
        if _active_elsewhere(db, org, c):
            raise BookitError("This person is already in an active campaign.")
        c.enrollment_state, c.enrolled_at, c.enrollment_step = "enrolled", datetime.utcnow(), 0
    elif action in ("pause", "stop"):
        c.enrollment_state = "paused" if action == "pause" else "stopped"
    else:
        raise BookitError("Unknown action.")
    audit(db, org, w, "referral_contact", c.id, "enrollment", "enrollment_state", None, c.enrollment_state, reason)
    db.flush()
    return contact_dict(c, w)


def _active_elsewhere(db, org, c) -> bool:
    if not c.lead_id:
        return False
    from app.models.program_models import ProgramConversation
    other = db.query(BookitReferralContact.id).filter(
        BookitReferralContact.organization_id == org, BookitReferralContact.lead_id == c.lead_id,
        BookitReferralContact.id != c.id, BookitReferralContact.enrollment_state == "enrolled").first()
    conv = db.query(ProgramConversation.id).filter(
        ProgramConversation.lead_id == c.lead_id,
        ProgramConversation.state.in_(["ai_active", "human", "waiting_future", "callback_requested"])).first()
    return bool(other or conv)


# ── At-Need 6 Block cases ─────────────────────────────────────────────────────────

def _case(db, prog, w: Who, case_id: str) -> BookitAddCase:
    c = db.query(BookitAddCase).filter(BookitAddCase.organization_id == prog.organization_id,
                                       BookitAddCase.id == case_id).first()
    if c is None or not w.sees(c.assigned_advisor):
        raise BookitError("Not found.", 404)
    return c


def case_dict(c: BookitAddCase) -> Dict[str, Any]:
    d = {k: getattr(c, k) for k in ("id", "deceased_name", "report_source", "verification_status",
                                    "arrangement_status", "contact_name", "contact_relationship", "contact_phone",
                                    "contact_email", "contact_lead_id", "preferred_channel", "preferred_time",
                                    "immediate_needs", "assigned_advisor", "priority", "status", "notes",
                                    "resolution")}
    d["date_of_passing"] = c.date_of_passing.isoformat() if c.date_of_passing else None
    d["reported_at"] = c.reported_at.isoformat() + "Z" if c.reported_at else None
    d["updated_at"] = c.updated_at.isoformat() + "Z" if c.updated_at else None
    return d


def _pick_advisor(db, org) -> Optional[str]:
    """The active advisor with the fewest open ADD cases (ties: by name)."""
    names = sorted(team_names(db, org, active_only=True))
    if not names:
        return None
    load = {n: 0 for n in names}
    for (a,) in db.query(BookitAddCase.assigned_advisor).filter(
            BookitAddCase.organization_id == org, BookitAddCase.status != "resolved"):
        if a in load:
            load[a] += 1
    return min(names, key=lambda n: (load[n], n))


def create_case(db: Session, prog, w: Who, body: Dict[str, Any]) -> Dict[str, Any]:
    org = prog.organization_id
    key = (body.get("idempotency_key") or "").strip()
    if not key:
        raise BookitError("Missing idempotency key.")
    have = db.query(BookitAddCase).filter(BookitAddCase.organization_id == org,
                                          BookitAddCase.idempotency_key == key).first()
    if have is not None:
        return case_dict(have)
    name = (body.get("deceased_name") or "").strip()
    if not name:
        raise BookitError("Add the name of the person who passed.")
    if not ((body.get("contact_phone") or "").strip() or (body.get("contact_email") or "").strip()):
        raise BookitError("Add a phone or email for the family contact.")
    if body.get("contact_phone") and not norm_phone(body["contact_phone"]):
        raise BookitError("That phone number needs 10 digits.")
    if body.get("priority") and body["priority"] not in PRIORITIES:
        raise BookitError("Priority isn't one of the choices.")
    try:
        dop = date.fromisoformat(body["date_of_passing"]) if body.get("date_of_passing") else None
    except ValueError:
        raise BookitError("Date of passing must be a date.")
    assigned = (body.get("assigned_advisor") if w.manager else w.name) or _pick_advisor(db, org)
    c = BookitAddCase(organization_id=org, idempotency_key=key[:120], deceased_name=name[:200],
                      date_of_passing=dop, report_source=(body.get("report_source") or "").strip()[:200] or None,
                      contact_name=(body.get("contact_name") or "").strip()[:200] or None,
                      contact_relationship=(body.get("contact_relationship") or "").strip()[:100] or None,
                      contact_phone=(body.get("contact_phone") or "").strip()[:40] or None,
                      contact_email=norm_email(body.get("contact_email")),
                      preferred_channel=(body.get("preferred_channel") or "").strip()[:40] or None,
                      preferred_time=(body.get("preferred_time") or "").strip()[:100] or None,
                      immediate_needs=(body.get("immediate_needs") or "").strip()[:4000] or None,
                      arrangement_status=(body.get("arrangement_status") or "").strip()[:200] or None,
                      notes=(body.get("notes") or "").strip()[:4000] or None, priority=body.get("priority") or "high",
                      assigned_advisor=assigned, created_by=w.name or w.user_name, status="intake")
    db.add(c)
    db.flush()
    audit(db, org, w, "add_case", c.id, "create", "assigned_advisor", None, assigned)
    if assigned:
        notify(db, org, assigned, "add_assigned", "New At-Need 6 Block assigned to you", ref_type="add_case", ref_id=c.id)
    notify(db, org, MANAGEMENT, "add_new", "New At-Need 6 Block awaiting review", ref_type="add_case", ref_id=c.id)
    db.flush()
    return case_dict(c)


def update_case(db: Session, prog, w: Who, case_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    org = prog.organization_id
    c = _case(db, prog, w, case_id)
    for k in ("verification_status", "arrangement_status", "contact_name", "contact_relationship",
              "preferred_channel", "preferred_time", "immediate_needs", "notes", "resolution", "report_source"):
        if k in body:
            setattr(c, k, (body[k] or "").strip()[:4000] or None)
    if "priority" in body:
        if body["priority"] not in PRIORITIES:
            raise BookitError("Priority isn't one of the choices.")
        c.priority = body["priority"]
    if "assigned_advisor" in body:
        if not w.manager:
            raise BookitError("Only management reassigns a case.", 403)
        if body["assigned_advisor"] and body["assigned_advisor"] not in team_names(db, org):
            raise BookitError("That person isn't on the team.")
        audit(db, org, w, "add_case", c.id, "reassign", "assigned_advisor", c.assigned_advisor,
              body["assigned_advisor"])
        c.assigned_advisor = body["assigned_advisor"] or None
        if c.assigned_advisor:
            notify(db, org, c.assigned_advisor, "add_assigned", "At-Need 6 Block assigned to you", ref_type="add_case",
                   ref_id=c.id, dedupe="add_assigned:%s:%s" % (c.id, c.assigned_advisor))
    if "status" in body:
        st = body["status"]
        if st not in ADD_STATUSES:
            raise BookitError("Status isn't one of the choices.")
        if st == "resolved" and not c.resolution:
            raise BookitError("Write down how the family was helped first.")
        audit(db, org, w, "add_case", c.id, "update", "status", c.status, st)
        c.status = st
    db.flush()
    return case_dict(c)


def cases(db: Session, prog, w: Who) -> List[Dict[str, Any]]:
    q = db.query(BookitAddCase).filter(BookitAddCase.organization_id == prog.organization_id)
    if not w.manager:
        q = q.filter(BookitAddCase.assigned_advisor == (w.name or "-"))
    return [case_dict(c) for c in q.order_by(BookitAddCase.reported_at.desc()).limit(500)]


# ── completed file reviews -> View network / Start 6 Block (owner, Nov 26 SS6) ──

def reviews(db: Session, prog, w: Who) -> Dict[str, Any]:
    """Completed file reviews (appointments marked Showed) this caller may see,
    each with its existing network or none. Reading this never creates
    anything: a network is only made by an explicit Start from a login that
    may write (never the read-only preview)."""
    from app.models.bookit_models import BookitPaperwork
    from app.services.programs import team_board as TB
    org = prog.organization_id
    appts = TB.work(db, prog, person=None if w.manager else w.name)["appointments"]
    done = [a for a in appts if a.get("lead_id") and (a.get("outcome") or a.get("status")) == "Showed"]
    done.sort(key=lambda a: ((a.get("date") or ""), a.get("time") or ""), reverse=True)
    nets = db.query(BookitReferralBatch).filter(BookitReferralBatch.organization_id == org,
                                                BookitReferralBatch.kind == "evo6").all()
    by_appt = {b.source_appointment_id: b for b in nets if b.source_appointment_id}
    by_lead = {b.origin_lead_id: b for b in nets if b.origin_lead_id and not b.source_appointment_id}
    pta = {p.lead_id: p.pta_status for p in db.query(BookitPaperwork).filter(
        BookitPaperwork.organization_id == org, BookitPaperwork.lead_id.in_([a["lead_id"] for a in done] or ["-"]))}
    rows, totals = [], {"reviews": len(done), "networks": 0, "collected": 0, "eligible": 0, "booked": 0}
    for a in done:
        b = by_appt.get(a["id"]) or by_lead.get(a["lead_id"])
        net = None
        if b is not None:
            d = batch_dict(db, w, b)
            p = d["progress"]
            net = {"id": b.id, "status": b.collection_status, "collected": p["collected"],
                   "eligible": p["eligible"], "booked": p["appointments_booked"]}
            totals["networks"] += 1
            totals["collected"] += p["collected"]
            totals["eligible"] += p["eligible"]
            totals["booked"] += p["appointments_booked"]
        rows.append({"appointment_id": a["id"], "lead_id": a["lead_id"], "family": a["family"],
                     "date": a["date"], "time": a["time"], "assigned": a["assigned"],
                     "pta": pta.get(a["lead_id"]) or "not_recorded", "network": net,
                     "can_start": net is None and not w.view_only and (w.manager or a["assigned"] == w.name)})
    return {"reviews": rows, "totals": totals, "view_only": bool(w.view_only)}

