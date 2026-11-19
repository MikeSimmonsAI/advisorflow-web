"""Client Growth: prospects, private invitations, submissions, review, tasks.

RULES THIS FILE KEEPS
  * Prospects are existing EvoSys Pro Sales `opportunities`; a new one is made
    only after a duplicate check, in the `discovery` stage (never a cold
    outreach stage, so EvoSys Sales automation never picks it up) and with
    provenance saying why it exists.
  * Creating or copying a link sends nothing. "Sent" is recorded only when a
    person says they sent it; delivery is never claimed.
  * A token is 256 bits from `secrets`; only its SHA-256 and an encrypted copy
    are stored. A wrong, revoked or expired token never reveals a prospect.
  * The public side never returns submitted answers, internal notes or other
    prospects. One submission per invitation (unique column).
  * Original answers are authoritative. The AI summary is internal, labelled,
    asked for by a person, and its failure changes nothing else.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.client_growth_models import (INVITE_ACTIVE, INVITE_REVOKED, INVITE_SUBMITTED,
                                             SUB_PROPOSAL_PREPARED, SUB_SUBMITTED, SUB_UNDER_REVIEW,
                                             TASK_DONE, TASK_OPEN, ClientGrowthInvitation,
                                             ClientGrowthSubmission, ClientGrowthTask, ClientGrowthTemplate)
from app.models.models import User
from app.models.sales_models import STAGE_DISCOVERY, STAGE_LABELS, Opportunity, OpportunityEvent
from app.services.client_growth import templates as T

log = logging.getLogger(__name__)

PUBLIC_PATH = "/growth-plan/"
DEFAULT_EXPIRY_DAYS = 30
SOURCE_SYSTEM = "client_growth"
TEST_SOURCE_SYSTEM = "client_growth_test"
INVITE_LABELS = {"active": "Link created", "sent": "Sent (recorded by staff)", "opened": "Opened",
                 "submitted": "Submitted", "revoked": "Revoked", "expired": "Expired"}
SUB_LABELS = {SUB_SUBMITTED: "Awaiting review", SUB_UNDER_REVIEW: "Under review",
              SUB_PROPOSAL_PREPARED: "Proposal prepared"}


def now() -> datetime:
    return datetime.utcnow()


def iso(d) -> Optional[str]:
    return d.replace(microsecond=0).isoformat() + "Z" if d else None


def public_origin() -> str:
    o = (os.environ.get("CLIENT_GROWTH_PUBLIC_ORIGIN") or os.environ.get("PUBLIC_APP_URL")
         or "https://app.evosyspro.live")
    return o.strip().rstrip("/")


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def _enc(token: str) -> Optional[str]:
    try:
        from app.utils.crypto import encrypt_value
        return encrypt_value(token)
    except Exception:  # noqa: BLE001 - without a key the link is shown once only
        return None


def _dec(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        from app.utils.crypto import decrypt_value
        return decrypt_value(value)
    except Exception:  # noqa: BLE001
        return None


def _audit(db: Session, actor: User, bso_id: str, action: str, target_type: str, target_id: str,
           before=None, after=None, note: Optional[str] = None) -> None:
    from app.routers.audit_log_router import log_action
    log_action(db, None, actor.id, action=action, target_type=target_type, target_id=target_id,
               brand_sales_org_id=bso_id, before=before, after=after, note=note, commit=False)


def _event(db: Session, opp_id: str, kind: str, summary: str, detail: Optional[str] = None,
           actor_id: Optional[str] = None) -> None:
    db.add(OpportunityEvent(opportunity_id=opp_id, event_type=kind, summary=summary[:250], detail=detail,
                            actor_user_id=actor_id, occurred_at=now()))


# ── templates ────────────────────────────────────────────────────────────────

def ensure_templates(db: Session) -> None:
    """Seed each built-in template version once. An existing version is never
    rewritten: answers already given against it keep meaning what they meant."""
    for key, spec in T.BUILTIN.items():
        have = (db.query(ClientGrowthTemplate.id)
                .filter(ClientGrowthTemplate.key == key, ClientGrowthTemplate.version == spec["version"]).first())
        if have:
            continue
        db.add(ClientGrowthTemplate(key=key, version=spec["version"], vertical=spec["vertical"],
                                    name=spec["name"], definition=spec["definition"](), is_active=True))
        try:
            db.flush()
        except IntegrityError:          # another worker seeded it first
            db.rollback()


def template_for(db: Session, key: str) -> ClientGrowthTemplate:
    ensure_templates(db)
    t = (db.query(ClientGrowthTemplate).filter(ClientGrowthTemplate.key == key,
                                               ClientGrowthTemplate.is_active.is_(True))
         .order_by(ClientGrowthTemplate.version.desc()).first())
    if t is None:
        raise HTTPException(404, "That questionnaire template is not available.")
    return t


def template_out(t: ClientGrowthTemplate, uses: int = 0) -> Dict[str, Any]:
    d = t.definition or {}
    return {"id": t.id, "key": t.key, "version": t.version, "vertical": t.vertical, "name": t.name,
            "is_active": bool(t.is_active), "steps": [{"key": s["key"], "title": s["title"],
                                                       "questions": len(s.get("fields", []))}
                                                      for s in d.get("steps", [])],
            "product": (d.get("public") or {}).get("product"), "invitations": uses,
            "created_at": iso(t.created_at)}


# ── prospects (existing opportunities) ───────────────────────────────────────

def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def duplicates(db: Session, bso_id: str, company: str, contact: str, email: str) -> List[Opportunity]:
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso_id)
    e = (email or "").strip().lower()
    hits: Dict[str, Opportunity] = {}
    if e:
        for o in q.filter(func.lower(Opportunity.email) == e).all():
            hits[o.id] = o
    nc, nn = _norm(company), _norm(contact)
    if nc or nn:
        for o in q.all():
            if (nc and _norm(o.company_name) == nc) or (nn and len(nn) > 3 and _norm(o.contact_name) == nn):
                hits[o.id] = o
    return list(hits.values())


def prospect_row(db: Session, o: Opportunity) -> Dict[str, Any]:
    inv = (db.query(func.count(ClientGrowthInvitation.id))
           .filter(ClientGrowthInvitation.opportunity_id == o.id).scalar() or 0)
    sub = (db.query(func.count(ClientGrowthSubmission.id))
           .filter(ClientGrowthSubmission.opportunity_id == o.id).scalar() or 0)
    return {"id": o.id, "company": o.company_name, "contact": o.contact_name, "email": o.email, "phone": o.phone,
            "industry": o.industry, "stage": o.stage, "stage_label": STAGE_LABELS.get(o.stage, o.stage),
            "status": o.status, "source": o.source, "created_at": iso(o.created_at),
            "invitations": inv, "submissions": sub,
            "is_test": o.source_system == TEST_SOURCE_SYSTEM}


def create_prospect(db: Session, actor: User, bso_id: str, *, company: str, contact: str, email: str,
                    phone: Optional[str], industry: Optional[str], force: bool, is_test: bool = False,
                    note: Optional[str] = None) -> Dict[str, Any]:
    company, contact, email = (company or "").strip(), (contact or "").strip(), (email or "").strip()
    if not company:
        raise HTTPException(422, "Business name is required.")
    if email and not T._EMAIL.match(email):
        raise HTTPException(422, "That email address does not look right.")
    dups = duplicates(db, bso_id, company, contact, email)
    if dups and not force:
        raise HTTPException(409, detail={"message": "This may already be a prospect. Choose the existing "
                                                    "record, or confirm this is a different business.",
                                         "duplicates": [prospect_row(db, o) for o in dups]})
    o = Opportunity(brand_sales_org_id=bso_id, owner_user_id=actor.id, company_name=company[:200],
                    contact_name=contact[:200] or None, email=email or None, phone=(phone or "").strip() or None,
                    industry=(industry or "").strip() or None, stage=STAGE_DISCOVERY, status="open",
                    source="Client Growth questionnaire" + (" (synthetic test)" if is_test else ""),
                    source_system=TEST_SOURCE_SYSTEM if is_test else SOURCE_SYSTEM,
                    intro_note=(note or "").strip()[:1000] or None, stage_changed_at=now())
    db.add(o)
    db.flush()
    _event(db, o.id, "created",
           "Prospect created for an authorized Client Growth discovery invitation",
           detail=("Synthetic verification record - not a real business." if is_test else
                   "Created in Client Growth by %s. Discovery stage; not enrolled in any outreach."
                   % (actor.full_name or actor.email)),
           actor_id=actor.id)
    _audit(db, actor, bso_id, "client_growth.prospect_created", "opportunity", o.id,
           after={"company": company, "contact": contact, "email": email, "is_test": is_test})
    return prospect_row(db, o)


def opp_for(db: Session, bso_id: str, opp_id: str) -> Opportunity:
    o = db.query(Opportunity).filter(Opportunity.id == opp_id, Opportunity.brand_sales_org_id == bso_id).first()
    if o is None:
        raise HTTPException(404, "Prospect not found.")
    return o


def record_preferred_email(db: Session, actor: User, o: Opportunity, email: str) -> None:
    """A newly supplied proposal email is recorded without overwriting what the
    record already holds: an empty email is filled; a different one is kept and
    the new one is written to the history."""
    email = (email or "").strip()
    if not email or (o.email or "").strip().lower() == email.lower():
        return
    if not o.email:
        o.email = email
        _event(db, o.id, "contact_updated", "Preferred proposal email recorded: %s" % email, actor_id=actor.id)
    else:
        _event(db, o.id, "contact_updated", "Newly supplied preferred proposal email: %s" % email,
               detail="Previous email %s kept on the record." % o.email, actor_id=actor.id)


# ── invitations ──────────────────────────────────────────────────────────────

def invite_state(i: ClientGrowthInvitation, at: Optional[datetime] = None) -> str:
    if i.status == INVITE_REVOKED:
        return "revoked"
    if i.status == INVITE_SUBMITTED:
        return "submitted"
    if i.expires_at and i.expires_at <= (at or now()):
        return "expired"
    if i.opened_at:
        return "opened"
    if i.sent_at:
        return "sent"
    return "active"


def link_for(i: ClientGrowthInvitation) -> Optional[str]:
    tok = _dec(i.token_enc)
    return public_origin() + PUBLIC_PATH + tok if tok else None


def invitation_row(db: Session, i: ClientGrowthInvitation, with_link: bool = False) -> Dict[str, Any]:
    o = db.get(Opportunity, i.opportunity_id)
    t = db.get(ClientGrowthTemplate, i.template_id)
    sub = db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.invitation_id == i.id).first()
    st = invite_state(i)
    row = {"id": i.id, "prospect_id": i.opportunity_id, "company": o.company_name if o else None,
           "contact": o.contact_name if o else None, "prepared_for": i.prepared_for,
           "template": {"id": t.id, "key": t.key, "version": t.version, "name": t.name} if t else None,
           "state": st, "state_label": INVITE_LABELS[st], "created_at": iso(i.created_at),
           "expires_at": iso(i.expires_at), "sent_at": iso(i.sent_at), "sent_note": i.sent_note,
           "last_copied_at": iso(i.last_copied_at), "opened_at": iso(i.opened_at),
           "revoked_at": iso(i.revoked_at), "revoke_reason": i.revoke_reason, "replaces_id": i.replaces_id,
           "submission_id": sub.id if sub else None, "is_test": bool(i.is_test),
           # Plain words for the one thing a link can never prove.
           "delivery": "Unknown - a link being created or copied does not mean it was sent or received."}
    if with_link:
        row["url"] = link_for(i)
    return row


def create_invitation(db: Session, actor: User, bso_id: str, *, opp_id: str, template_key: str,
                      prepared_for: Optional[str], prefill: Dict[str, Any], expires_days: int = DEFAULT_EXPIRY_DAYS,
                      replaces: Optional[ClientGrowthInvitation] = None) -> Dict[str, Any]:
    o = opp_for(db, bso_id, opp_id)
    t = template_for(db, template_key)
    expires_days = max(1, min(int(expires_days or DEFAULT_EXPIRY_DAYS), 180))
    clean_prefill = {}
    for k in ("contact_name", "business_name", "email"):
        v = (prefill or {}).get(k)
        if isinstance(v, str) and v.strip():
            clean_prefill[k] = v.strip()[:160]
    if "email" in clean_prefill and not T._EMAIL.match(clean_prefill["email"]):
        raise HTTPException(422, "The pre-filled email does not look right.")
    token = secrets.token_urlsafe(32)
    is_test = o.source_system == TEST_SOURCE_SYSTEM
    i = ClientGrowthInvitation(brand_sales_org_id=bso_id, opportunity_id=o.id, template_id=t.id,
                               token_hash=_hash(token), token_enc=_enc(token),
                               prepared_for=(prepared_for or o.contact_name or "").strip()[:120] or None,
                               prefill=clean_prefill, status=INVITE_ACTIVE,
                               expires_at=now() + timedelta(days=expires_days),
                               replaces_id=replaces.id if replaces else None, is_test=is_test,
                               created_by=actor.id, created_at=now())
    db.add(i)
    db.flush()
    if clean_prefill.get("email"):
        record_preferred_email(db, actor, o, clean_prefill["email"])
    _event(db, o.id, "questionnaire_link_created",
           "Discovery questionnaire link created (%s v%d) - not sent" % (t.name, t.version),
           detail=("Replaces an earlier link." if replaces else None), actor_id=actor.id)
    _audit(db, actor, bso_id, "client_growth.invitation_created", "client_growth_invitation", i.id,
           after={"prospect_id": o.id, "template": t.key, "version": t.version,
                  "expires_at": iso(i.expires_at), "replaces": replaces.id if replaces else None})
    row = invitation_row(db, i)
    row["url"] = public_origin() + PUBLIC_PATH + token     # always shown at creation
    return row


def invitation_for(db: Session, bso_id: str, inv_id: str) -> ClientGrowthInvitation:
    i = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.id == inv_id,
                                                 ClientGrowthInvitation.brand_sales_org_id == bso_id).first())
    if i is None:
        raise HTTPException(404, "Invitation not found.")
    return i


def copy_link(db: Session, actor: User, i: ClientGrowthInvitation) -> Dict[str, Any]:
    st = invite_state(i)
    if st in ("revoked", "expired"):
        raise HTTPException(409, "This link is %s. Create a new one instead." % st)
    url = link_for(i)
    if not url:
        raise HTTPException(409, "This link can't be shown again. Create a replacement link.")
    i.last_copied_at = now()
    return {"url": url, "state": st}


def mark_sent(db: Session, actor: User, i: ClientGrowthInvitation, note: Optional[str]) -> Dict[str, Any]:
    if invite_state(i) in ("revoked", "expired"):
        raise HTTPException(409, "This link is no longer usable.")
    i.sent_at, i.sent_by, i.sent_note = now(), actor.id, (note or "").strip()[:200] or None
    _event(db, i.opportunity_id, "questionnaire_sent",
           "Staff recorded that the questionnaire link was sent" + (" (%s)" % i.sent_note if i.sent_note else ""),
           detail="Recorded by a person; delivery is not confirmed.", actor_id=actor.id)
    _audit(db, actor, i.brand_sales_org_id, "client_growth.invitation_marked_sent", "client_growth_invitation",
           i.id, after={"note": i.sent_note})
    return invitation_row(db, i)


def revoke(db: Session, actor: User, i: ClientGrowthInvitation, reason: str) -> Dict[str, Any]:
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(422, "Give a short reason for revoking this link.")
    if i.status == INVITE_REVOKED:
        return invitation_row(db, i)
    before = invite_state(i)
    i.status, i.revoked_at, i.revoked_by, i.revoke_reason = INVITE_REVOKED, now(), actor.id, reason[:500]
    _event(db, i.opportunity_id, "questionnaire_revoked", "Questionnaire link revoked", detail=reason[:500],
           actor_id=actor.id)
    _audit(db, actor, i.brand_sales_org_id, "client_growth.invitation_revoked", "client_growth_invitation", i.id,
           before={"state": before}, after={"state": "revoked"}, note=reason[:500])
    return invitation_row(db, i)


def reissue(db: Session, actor: User, i: ClientGrowthInvitation, reason: str,
            expires_days: int = DEFAULT_EXPIRY_DAYS) -> Dict[str, Any]:
    """The deliberate correction path: a fresh link for the same prospect and
    template. An unanswered old link is revoked; a submitted one stays exactly
    as it was (its answers are never overwritten)."""
    t = db.get(ClientGrowthTemplate, i.template_id)
    if i.status == INVITE_ACTIVE:
        revoke(db, actor, i, reason or "Replaced with a new link")
    return create_invitation(db, actor, i.brand_sales_org_id, opp_id=i.opportunity_id, template_key=t.key,
                             prepared_for=i.prepared_for, prefill=i.prefill or {}, expires_days=expires_days,
                             replaces=i)


# ── public side ──────────────────────────────────────────────────────────────

_NOT_FOUND = "This questionnaire link isn't valid. Please check the link you were sent."


def _by_token(db: Session, token: str) -> ClientGrowthInvitation:
    if not token or len(token) < 20 or len(token) > 100 or not re.fullmatch(r"[A-Za-z0-9_\-]+", token):
        raise HTTPException(404, _NOT_FOUND)
    i = db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.token_hash == _hash(token)).first()
    if i is None:
        raise HTTPException(404, _NOT_FOUND)
    return i


def _first_name(name: Optional[str]) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else "there"


def public_view(db: Session, token: str) -> Dict[str, Any]:
    i = _by_token(db, token)
    st = invite_state(i)
    if st == "revoked":
        return {"state": "revoked", "message": "This questionnaire link is no longer active. "
                                               "Please contact the EvoSys Pro team for a new link."}
    if st == "expired":
        return {"state": "expired", "message": "This questionnaire link has expired. "
                                               "Please contact the EvoSys Pro team for a new link."}
    if st == "submitted":
        # Never the answers: holding the link is not proof of who you are.
        return {"state": "submitted", "message": "We've already received answers for this questionnaire. "
                                                 "If something needs to change, reply to the EvoSys Pro team "
                                                 "and we'll send a new link."}
    t = db.get(ClientGrowthTemplate, i.template_id)
    d = t.definition or {}
    pub = dict(d.get("public") or {})
    prepared = i.prepared_for or ""
    pub["eyebrow"] = pub.get("eyebrow", "").replace("{prepared_for}", prepared) if prepared else None
    pub.pop("success", None)
    if i.opened_at is None:
        i.opened_at = now()
        db.commit()
    return {"state": "ready", "public": pub, "steps": d.get("steps", []), "prefill": i.prefill or {},
            "template": {"key": t.key, "version": t.version}}


def _recipients(db: Session) -> List[User]:
    from app.services.client_growth import access as A
    ids = [p["user_id"] for p in A.people_with_access(db)]
    return db.query(User).filter(User.id.in_(ids), User.is_active.is_(True)).all() if ids else []


def _notify(db: Session, sub: ClientGrowthSubmission, company: str) -> int:
    """In-app notifications to the people who hold Client Growth access, through
    the existing notifications table. A failure is logged and changes nothing."""
    from app.models.models import Notification, NotificationType
    n = 0
    try:
        with db.begin_nested():
            for u in _recipients(db):
                db.add(Notification(user_id=u.id, type=NotificationType.CLIENT_GROWTH,
                                    message=("%sGrowth questionnaire submitted: %s"
                                             % ("[TEST] " if sub.is_test else "", company))[:500],
                                    link="/client-growth?submission=%s" % sub.id))
                n += 1
        db.commit()
    except Exception:  # noqa: BLE001 - the submission is already saved
        log.warning("client growth notification failed for %s", sub.id, exc_info=True)
        db.rollback()
        n = 0
    return n


def submit(db: Session, token: str, raw: Any) -> Dict[str, Any]:
    i = _by_token(db, token)
    st = invite_state(i)
    if st in ("revoked", "expired"):
        raise HTTPException(410, detail={"state": st, "message": "This questionnaire link is no longer active. "
                                                                 "Please contact the EvoSys Pro team."})
    t = db.get(ClientGrowthTemplate, i.template_id)
    d = t.definition or {}
    if st == "submitted":
        return {"ok": True, "already": True, "message": "We've already received your answers. Thank you."}
    answers, errors = T.validate(d, raw.get("answers") if isinstance(raw, dict) else None)
    if errors:
        raise HTTPException(422, detail={"message": "Please check the highlighted answers.", "errors": errors})
    o = db.get(Opportunity, i.opportunity_id)
    sub = ClientGrowthSubmission(invitation_id=i.id, brand_sales_org_id=i.brand_sales_org_id,
                                 opportunity_id=i.opportunity_id, template_id=t.id, template_key=t.key,
                                 template_version=t.version, answers=answers, status=SUB_SUBMITTED,
                                 submitted_at=now(), is_test=bool(i.is_test))
    try:
        db.add(sub)
        db.flush()
    except IntegrityError:
        db.rollback()               # a second click lost the race: the first one is the answer
        return {"ok": True, "already": True, "message": "We've already received your answers. Thank you."}
    i.status = INVITE_SUBMITTED
    company = (o.company_name if o else None) or answers.get("business_name") or "Prospect"
    contact = answers.get("contact_name") or (o.contact_name if o else "")
    db.add(ClientGrowthTask(brand_sales_org_id=i.brand_sales_org_id, opportunity_id=i.opportunity_id,
                            submission_id=sub.id, title=("Prepare %s %s proposal"
                                                         % (contact or company, _proposal_noun(t))).strip(),
                            status=TASK_OPEN, owner_user_id=o.owner_user_id if o else None, is_test=bool(i.is_test),
                            created_at=now()))
    detail = ["Template %s v%d." % (t.key, t.version)]
    if o and answers.get("email") and (o.email or "").lower() != answers["email"].lower():
        detail.append("Email given on the form: %s (record not changed)." % answers["email"])
    _event(db, i.opportunity_id, "questionnaire_submitted", "Discovery questionnaire submitted",
           detail=" ".join(detail))
    db.commit()
    _notify(db, sub, company)
    succ = (d.get("public") or {}).get("success") or "Thanks, {first_name}. We've received your answers."
    return {"ok": True, "already": False, "message": succ.replace("{first_name}", _first_name(contact))}


def _proposal_noun(t: ClientGrowthTemplate) -> str:
    return "agency growth" if (t.vertical == "insurance") else "growth"


# ── submissions, review, tasks ───────────────────────────────────────────────

def submission_for(db: Session, bso_id: str, sub_id: str) -> ClientGrowthSubmission:
    s = (db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.id == sub_id,
                                                 ClientGrowthSubmission.brand_sales_org_id == bso_id).first())
    if s is None:
        raise HTTPException(404, "Submission not found.")
    return s


def task_row(k: Optional[ClientGrowthTask]) -> Optional[Dict[str, Any]]:
    if k is None:
        return None
    return {"id": k.id, "title": k.title, "status": k.status, "prospect_id": k.opportunity_id,
            "submission_id": k.submission_id, "created_at": iso(k.created_at), "completed_at": iso(k.completed_at),
            "is_test": bool(k.is_test)}


def submission_row(db: Session, s: ClientGrowthSubmission, full: bool = False) -> Dict[str, Any]:
    o = db.get(Opportunity, s.opportunity_id)
    k = db.query(ClientGrowthTask).filter(ClientGrowthTask.submission_id == s.id).first()
    row = {"id": s.id, "prospect_id": s.opportunity_id, "company": o.company_name if o else None,
           "contact": (s.answers or {}).get("contact_name") or (o.contact_name if o else None),
           "invitation_id": s.invitation_id, "template_key": s.template_key, "template_version": s.template_version,
           "status": s.status, "status_label": SUB_LABELS.get(s.status, s.status),
           "submitted_at": iso(s.submitted_at), "review_started_at": iso(s.review_started_at),
           "proposal_prepared_at": iso(s.proposal_prepared_at), "task": task_row(k), "is_test": bool(s.is_test)}
    if full:
        t = db.get(ClientGrowthTemplate, s.template_id)
        row["answers"] = s.answers                                     # original, as received
        row["readable"] = T.readable(t.definition or {}, s.answers or {})
        row["ai_summary"] = ({"label": "AI summary - internal. Check against the original answers.",
                              "status": s.ai_summary_status, "content": s.ai_summary, "error": s.ai_summary_error,
                              "at": iso(s.ai_summary_at)} if s.ai_summary_status else None)
    return row


def start_review(db: Session, actor: User, s: ClientGrowthSubmission) -> Dict[str, Any]:
    if s.status == SUB_SUBMITTED:
        s.status, s.review_started_at, s.review_started_by = SUB_UNDER_REVIEW, now(), actor.id
        _event(db, s.opportunity_id, "questionnaire_review", "Questionnaire answers under review", actor_id=actor.id)
        _audit(db, actor, s.brand_sales_org_id, "client_growth.review_started", "client_growth_submission", s.id,
               before={"status": SUB_SUBMITTED}, after={"status": SUB_UNDER_REVIEW})
    return submission_row(db, s, full=True)


def proposal_prepared(db: Session, actor: User, s: ClientGrowthSubmission) -> Dict[str, Any]:
    if s.status == SUB_PROPOSAL_PREPARED:
        return submission_row(db, s, full=True)
    before = s.status
    if s.review_started_at is None:
        s.review_started_at, s.review_started_by = now(), actor.id
    s.status, s.proposal_prepared_at, s.proposal_prepared_by = SUB_PROPOSAL_PREPARED, now(), actor.id
    k = db.query(ClientGrowthTask).filter(ClientGrowthTask.submission_id == s.id).first()
    if k and k.status != TASK_DONE:
        k.status, k.completed_at, k.completed_by = TASK_DONE, now(), actor.id
    _event(db, s.opportunity_id, "proposal_prepared", "Proposal prepared from the discovery questionnaire",
           detail="Prepared, not sent. Nothing was sent to the prospect.", actor_id=actor.id)
    _audit(db, actor, s.brand_sales_org_id, "client_growth.proposal_prepared", "client_growth_submission", s.id,
           before={"status": before}, after={"status": SUB_PROPOSAL_PREPARED})
    return submission_row(db, s, full=True)


SUMMARY_SYSTEM = (
    "You summarise ONE prospective client's discovery questionnaire for an internal sales team. Use only the JSON "
    "answers given. Return JSON: {\"goals\":\"1-2 sentences\",\"budget\":\"1 sentence\",\"va_readiness\":"
    "\"1 sentence\",\"missing\":[\"short items the team should still ask about\"]}. Do not invent numbers, prices, "
    "forecasts, timelines, agreements or scope. If something was not answered, list it under missing.")


def _get_client():
    """Test seam: None in production (the gateway's own client)."""
    return None


def ai_summary(db: Session, actor: User, s: ClientGrowthSubmission) -> Dict[str, Any]:
    t = db.get(ClientGrowthTemplate, s.template_id)
    facts = {"template": t.name, "answers": T.readable(t.definition or {}, s.answers or {})}
    try:
        from app.services import ai_gateway
        resp = ai_gateway.chat_completion(
            feature="client_growth", capability="lead_analysis", mode=ai_gateway.MANUAL, actor=actor.id,
            org_id=s.brand_sales_org_id, client=_get_client(), response_format={"type": "json_object"},
            temperature=0.2, messages=[{"role": "system", "content": SUMMARY_SYSTEM},
                                       {"role": "user", "content": json.dumps(facts, default=str)}])
        raw = json.loads(resp.choices[0].message.content or "{}")
        out = {k: str(raw.get(k) or "")[:600] for k in ("goals", "budget", "va_readiness")}
        out["missing"] = [str(x)[:200] for x in (raw.get("missing") or [])][:10] \
            if isinstance(raw.get("missing"), list) else []
        s.ai_summary, s.ai_summary_status, s.ai_summary_error = out, "done", None
    except Exception as exc:  # noqa: BLE001 - original answers stay; nothing else changes
        name = type(exc).__name__
        s.ai_summary_status = "failed"
        s.ai_summary_error = ("AI is turned off for manual actions." if name == "AIDisabled" else
                              "The AI summary couldn't be made (%s). The original answers are unchanged." % name)
        log.warning("client growth ai summary failed for %s: %s", s.id, name)
    s.ai_summary_at, s.ai_summary_by = now(), actor.id
    return submission_row(db, s, full=True)


# ── counts and history ───────────────────────────────────────────────────────

def counts(db: Session, bso_id: str, include_test: bool = False) -> Dict[str, int]:
    """Real rows only. Synthetic verification records are left out unless asked."""
    iq = db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.brand_sales_org_id == bso_id)
    sq = db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.brand_sales_org_id == bso_id)
    if not include_test:
        iq = iq.filter(ClientGrowthInvitation.is_test.is_(False))
        sq = sq.filter(ClientGrowthSubmission.is_test.is_(False))
    at = now()
    pending = iq.filter(ClientGrowthInvitation.status == INVITE_ACTIVE).filter(
        (ClientGrowthInvitation.expires_at.is_(None)) | (ClientGrowthInvitation.expires_at > at)).count()
    return {"pending_invitations": pending,
            "submitted": sq.count(),
            "awaiting_review": sq.filter(ClientGrowthSubmission.status == SUB_SUBMITTED).count(),
            "under_review": sq.filter(ClientGrowthSubmission.status == SUB_UNDER_REVIEW).count(),
            "proposals_prepared": sq.filter(ClientGrowthSubmission.status == SUB_PROPOSAL_PREPARED).count(),
            "revoked": iq.filter(ClientGrowthInvitation.status == INVITE_REVOKED).count()}


def cleanup_test_records(db: Session, actor: User, bso_id: str) -> Dict[str, int]:
    """Remove ONLY synthetic verification records: rows flagged is_test and
    prospects whose source_system marks them as a synthetic test."""
    from app.models.models import Notification
    subs = db.query(ClientGrowthSubmission).filter(ClientGrowthSubmission.brand_sales_org_id == bso_id,
                                                   ClientGrowthSubmission.is_test.is_(True)).all()
    sub_ids = [s.id for s in subs]
    n_notes = 0
    for sid in sub_ids:
        n_notes += (db.query(Notification).filter(Notification.link == "/client-growth?submission=%s" % sid)
                    .delete(synchronize_session=False))
    n_tasks = (db.query(ClientGrowthTask).filter(ClientGrowthTask.brand_sales_org_id == bso_id,
                                                 ClientGrowthTask.is_test.is_(True)).delete(synchronize_session=False))
    for s in subs:
        db.delete(s)
    db.flush()
    n_inv = (db.query(ClientGrowthInvitation).filter(ClientGrowthInvitation.brand_sales_org_id == bso_id,
                                                     ClientGrowthInvitation.is_test.is_(True))
             .order_by(ClientGrowthInvitation.created_at.desc()).all())
    for i in n_inv:                 # newest first so replaces_id never dangles
        db.delete(i)
        db.flush()
    opps = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso_id,
                                        Opportunity.source_system == TEST_SOURCE_SYSTEM).all()
    for o in opps:
        db.query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id).delete(synchronize_session=False)
        db.delete(o)
    out = {"submissions": len(subs), "tasks": n_tasks, "invitations": len(n_inv), "notifications": n_notes,
           "prospects": len(opps)}
    _audit(db, actor, bso_id, "client_growth.test_records_removed", "client_growth", bso_id, after=out,
           note="Synthetic verification records only.")
    return out
