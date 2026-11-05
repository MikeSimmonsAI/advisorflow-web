"""Human-assisted AI learning: candidates from human replies to handoffs,
reviewed into approved answers the appointment setter may use.

  CANDIDATE  captured automatically when a person answers a handed-off
             family; redacted; never shown to the AI.
  APPROVED   a manager wrote the general question/answer and approved it.
  ACTIVE     offered to the AI for similar questions in its scope.
  RETIRED / REJECTED  never used.

Scope: an answer is the organization's and, by default, its location's. A
manager widens it to every location on purpose. Retrieval never crosses
organizations, never returns a candidate, and never overrides the approved
SCI facts or the setter's rules (the prompt says so; the gates still run).
"""
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.handoff_learning_models import (ACTIVE, APPROVED, CANDIDATE, REJECTED, RETIRED, SCOPE_LOCATION,
                                                SCOPE_PROGRAM, ProgramHandoffLearning, ProgramLearningUse)

MAX_RETRIEVED = 3
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_URL = re.compile(r"https?://\S+")
_LONG_NUM = re.compile(r"\b\d{5,}\b")
_STREET = re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.' ]{2,40}\s(St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Blvd|"
                     r"Ct|Court|Way|Hwy|Highway|Pl|Place|Cir|Circle)\b\.?", re.I)
_WORD = re.compile(r"[a-z']{3,}")
_STOP = set("the and for you your with that this have has are was were from they them what when where which "
            "would could should about there their will can just like been into than then some any our out not "
            "but all how who why her his she him its it's i'm we're you're don't".split())


def redact(text: Optional[str], lead=None) -> str:
    """Remove what identifies a family: email, phone, links, long numbers,
    street addresses and the family's own names."""
    t = text or ""
    t = _EMAIL.sub("[email]", t)
    t = _URL.sub("[link]", t)
    t = _STREET.sub("[address]", t)
    t = _PHONE.sub("[phone]", t)
    t = _LONG_NUM.sub("[number]", t)
    for n in (getattr(lead, "first_name", None), getattr(lead, "last_name", None)) if lead is not None else ():
        n = (n or "").strip()
        if len(n) >= 2:
            t = re.sub(r"\b%s\b" % re.escape(n), "[name]", t, flags=re.I)
    return t.strip()


def personal_data_left(text: str) -> bool:
    return bool(_EMAIL.search(text or "") or _PHONE.search(text or "") or _STREET.search(text or ""))


def capture_candidate(db: Session, prog, lead, conv, resp, human_text: str, *, channel: str, outcome: str,
                      user_id: Optional[str], reason: Optional[str]) -> Optional[ProgramHandoffLearning]:
    """One candidate per answered message (the unique response id makes a
    retried send a no-op). Redacted on the way in. Never shown to the AI."""
    if resp is None or getattr(lead, "is_test", False):
        return None
    existing = db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.response_id == resp.id).first()
    if existing is not None:
        return existing
    ctx = []
    try:
        from app.models.program_models import ProgramConversationTurn
        turns = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == conv.id)
                 .order_by(ProgramConversationTurn.created_at.desc()).limit(6).all()) if conv is not None else []
        for t in reversed(turns):
            if t.text:
                ctx.append({"who": t.role, "text": redact(t.text, lead)[:300]})
    except Exception:                                    # noqa: BLE001
        ctx = []
    row = ProgramHandoffLearning(
        organization_id=prog.organization_id, lead_id=lead.id, conversation_id=getattr(conv, "id", None),
        response_id=resp.id, location_id=resp.location_id, campaign_family=getattr(resp, "campaign_family", None),
        channel=channel, escalation_reason=(reason or "")[:250] or None,
        question=redact(resp.body_excerpt or resp.summary or "", lead)[:1000],
        context=json.dumps(ctx), human_answer=redact(human_text, lead)[:2000], outcome=outcome,
        created_by_user_id=user_id, status=CANDIDATE)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.response_id == resp.id).first()
    return row


class Refused(ValueError):
    pass


def _get(db, org_id, learning_id) -> ProgramHandoffLearning:
    row = (db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.id == learning_id,
                                                   ProgramHandoffLearning.organization_id == org_id).first())
    if row is None:
        raise LookupError("not found")
    return row


def approve(db, org_id, learning_id, user_id, *, question: str, answer: str, scope: str = SCOPE_LOCATION,
            activate: bool = True) -> ProgramHandoffLearning:
    """A manager's general version. Refused if it still carries personal data
    or is empty. `activate` makes it usable at once (APPROVED -> ACTIVE)."""
    row = _get(db, org_id, learning_id)
    if row.status in (RETIRED, REJECTED):
        raise Refused("This answer was %s - capture a new one instead." % row.status)
    q, a = (question or "").strip(), (answer or "").strip()
    if not q or not a:
        raise Refused("Write the general question and the answer the AI may give.")
    if personal_data_left(q) or personal_data_left(a):
        raise Refused("Remove names, phone numbers, emails and addresses - this answer is for every family.")
    if scope not in (SCOPE_LOCATION, SCOPE_PROGRAM):
        raise Refused("scope must be location or program")
    if scope == SCOPE_LOCATION and not row.location_id:
        raise Refused("This question came with no location - approve it for all locations, or not at all.")
    now = datetime.utcnow()
    row.approved_question, row.approved_answer, row.scope = q[:1000], a[:2000], scope
    row.status, row.reviewed_by_user_id, row.reviewed_at = APPROVED, user_id, now
    if activate:
        row.status, row.activated_at = ACTIVE, now
    return row


def set_status(db, org_id, learning_id, user_id, status: str) -> ProgramHandoffLearning:
    row = _get(db, org_id, learning_id)
    now = datetime.utcnow()
    if status == ACTIVE:
        if row.status != APPROVED or not row.approved_answer:
            raise Refused("Only an approved answer can be switched on.")
        row.status, row.activated_at = ACTIVE, now
    elif status == RETIRED:
        if row.status not in (ACTIVE, APPROVED):
            raise Refused("Only an approved or active answer can be retired.")
        row.status, row.retired_at = RETIRED, now
    elif status == REJECTED:
        if row.status != CANDIDATE:
            raise Refused("Only a candidate can be rejected.")
        row.status = REJECTED
    else:
        raise Refused("unknown status")
    row.reviewed_by_user_id, row.reviewed_at = user_id, now
    return row


def _tokens(text: str) -> set:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def retrieve(db, org_id: str, location_id: Optional[str], text: str, limit: int = MAX_RETRIEVED) -> List[Dict]:
    """ACTIVE answers in scope (this organization; this location or
    program-wide) that share meaningful words with the family's message,
    best first. Candidates, approved-but-off, retired and rejected answers
    are never returned."""
    want = _tokens(text)
    if not want:
        return []
    q = db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.organization_id == org_id,
                                                ProgramHandoffLearning.status == ACTIVE)
    out = []
    for r in q.all():
        if r.scope == SCOPE_LOCATION and r.location_id != location_id:
            continue
        have = _tokens(r.approved_question)
        score = len(want & have) / max(1, len(have))
        if score >= 0.34 and want & have:
            out.append((score, r))
    out.sort(key=lambda x: -x[0])
    return [{"id": r.id, "question": r.approved_question, "answer": r.approved_answer} for _s, r in out[:limit]]


def record_use(db, org_id: str, ids: List[str], response_id: Optional[str], action: Optional[str]) -> None:
    for i in ids:
        db.add(ProgramLearningUse(organization_id=org_id, learning_id=i, response_id=response_id, action=action))
        db.query(ProgramHandoffLearning).filter(ProgramHandoffLearning.id == i).update(
            {ProgramHandoffLearning.uses: ProgramHandoffLearning.uses + 1}, synchronize_session=False)


def counts(db, org_id: str) -> Dict:
    from sqlalchemy import func
    by = dict(db.query(ProgramHandoffLearning.status, func.count(ProgramHandoffLearning.id))
              .filter(ProgramHandoffLearning.organization_id == org_id)
              .group_by(ProgramHandoffLearning.status).all())
    uses = db.query(ProgramLearningUse).filter(ProgramLearningUse.organization_id == org_id)
    total = uses.count()
    avoided = uses.filter(ProgramLearningUse.action.notin_(("handoff", "callback"))).count()
    return {**{s: by.get(s, 0) for s in (CANDIDATE, APPROVED, ACTIVE, RETIRED, REJECTED)},
            "uses": total, "uses_without_handoff": avoided}


def view(r: ProgramHandoffLearning, location_name: Optional[str] = None) -> Dict:
    return {"id": r.id, "status": r.status, "scope": r.scope, "location_id": r.location_id,
            "location": location_name, "channel": r.channel, "escalation_reason": r.escalation_reason,
            "question": r.question, "human_answer": r.human_answer, "context": json.loads(r.context or "[]"),
            "outcome": r.outcome, "approved_question": r.approved_question, "approved_answer": r.approved_answer,
            "uses": r.uses or 0, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "reviewed_at": r.reviewed_at.isoformat() + "Z" if r.reviewed_at else None}
