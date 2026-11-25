"""BookIT core: who is asking, what they may touch, audit and notifications.

AUTHORIZATION (docs/restland-bookit/permissions.md)
* Identity comes from the session (the User), never from the request body.
* A MANAGER is a manager of the Restland program workspace (the same check
  every program screen uses). Managers see the whole team.
* An ADVISOR is a ProgramTeamMember linked to this login (user_id). An
  advisor sees only records assigned to their own team name, and only while
  BookIT is open to the team (`bookit_live`) and their access isn't
  suspended. Until the owner opens it, BookIT is the owner's alone
  (standing order, Nov 21).
* Every read and write goes through `scope()`; a record outside it answers
  404 (not 403) so ids can't be probed.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.orm import Session

from app.services.programs import restland_sheet as RS

MANAGEMENT = "*management*"


class BookitError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def cfg(prog) -> Dict[str, Any]:
    return (json.loads(prog.booking_policy_json or "{}").get("bookit") or {})


def save_cfg(db: Session, prog, c: Dict[str, Any]) -> None:
    pol = json.loads(prog.booking_policy_json or "{}")
    pol["bookit"] = c
    prog.booking_policy_json = json.dumps(pol)
    db.flush()


def is_live(prog) -> bool:
    return bool(cfg(prog).get("live"))


class Who:
    """The caller, decided on the server."""

    def __init__(self, user, manager: bool, name: Optional[str], member_id: Optional[str]):
        self.user_id = user.id
        self.user_name = getattr(user, "full_name", None) or name or ""
        self.manager = manager
        self.name = name                                 # their team name (None for a manager with no seat)
        self.member_id = member_id

    def sees(self, *assignees: Optional[str]) -> bool:
        """May this caller see a record assigned to any of these names?"""
        return self.manager or (self.name is not None and self.name in [a for a in assignees if a])

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name or self.user_name, "manager": self.manager}


def who(db: Session, prog, user, manager: bool) -> Who:
    from app.models.program_models import ProgramTeamMember as M
    m = (db.query(M).filter(M.organization_id == prog.organization_id, M.user_id == user.id).first())
    if manager:
        return Who(user, True, m.name if m else None, m.id if m else None)
    if m is None or not is_live(prog):
        raise BookitError("Restland BookIT isn't open for this login yet.", 403)
    if (cfg(prog).get("suspended") or {}).get(m.id):
        raise BookitError("Your BookIT access is paused. Please talk to your manager.", 403)
    return Who(user, False, m.name, m.id)


def assignee_of_lead(db: Session, org: str, lead_id: str) -> Iterable[str]:
    """Every team name a family is assigned to (appointments, follow-ups, new families)."""
    from app.models.program_models import HandoffSheetRow as R
    return [a for (a,) in db.query(R.assigned_to).filter(R.organization_id == org, R.lead_id == lead_id,
                                                          R.assigned_to.isnot(None)).distinct()]


def require_family(db: Session, prog, w: Who, lead_id: str) -> None:
    if not lead_id:
        raise BookitError("No family was picked.")
    from app.models.models import Lead
    lead = db.query(Lead.id).filter(Lead.id == lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None or not w.sees(*assignee_of_lead(db, prog.organization_id, lead_id),
                                  *_extra_owners(db, prog.organization_id, lead_id)):
        raise BookitError("Not found.", 404)


def _extra_owners(db: Session, org: str, lead_id: str) -> Iterable[str]:
    """Owners through BookIT's own records (a referral contact, an ADD case)."""
    from app.models.bookit_models import BookitAddCase, BookitReferralContact
    out = [a for (a,) in db.query(BookitReferralContact.assigned_advisor).filter(
        BookitReferralContact.organization_id == org, BookitReferralContact.lead_id == lead_id)]
    out += [a for (a,) in db.query(BookitAddCase.assigned_advisor).filter(
        BookitAddCase.organization_id == org, BookitAddCase.contact_lead_id == lead_id)]
    return [a for a in out if a]


def team_names(db: Session, org: str, active_only: bool = False):
    from app.models.program_models import ProgramTeamMember as M
    q = db.query(M.name).filter(M.organization_id == org)
    if active_only:
        q = q.filter(M.active.is_(True))
    return [n for (n,) in q.all()]


# ── audit ───────────────────────────────────────────────────────────────────

def audit(db: Session, org: str, w: Optional[Who], entity: str, entity_id: str, action: str,
          field: Optional[str] = None, old=None, new=None, reason: Optional[str] = None) -> None:
    from app.models.bookit_models import BookitAudit
    db.add(BookitAudit(organization_id=org, entity=entity, entity_id=entity_id, action=action, field=field,
                       old_value=None if old is None else str(old)[:2000],
                       new_value=None if new is None else str(new)[:2000], reason=(reason or None),
                       user_id=w.user_id if w else None, user_name=(w.name or w.user_name) if w else "system",
                       at=datetime.utcnow()))


# ── notifications ───────────────────────────────────────────────────────────

def notify(db: Session, org: str, recipient: str, kind: str, title: str, *, body: Optional[str] = None,
           ref_type: Optional[str] = None, ref_id: Optional[str] = None, dedupe: Optional[str] = None) -> bool:
    """One notification per event (dedupe). Bodies stay short and carry no
    more family detail than the recipient may already see."""
    from app.models.bookit_models import BookitNotification as N
    key = dedupe or "%s:%s:%s:%s" % (kind, recipient, ref_type or "", ref_id or "")
    if db.query(N.id).filter(N.organization_id == org, N.dedupe_key == key).first():
        return False
    db.add(N(organization_id=org, recipient=recipient, kind=kind, title=title[:200], body=(body or "")[:500],
             ref_type=ref_type, ref_id=ref_id, dedupe_key=key[:250], created_at=datetime.utcnow()))
    db.flush()
    return True


def notifications(db: Session, org: str, w: Who, unread_only: bool = False, limit: int = 50):
    from app.models.bookit_models import BookitNotification as N
    mine = [w.name] if w.name else []
    if w.manager:
        mine.append(MANAGEMENT)
    q = db.query(N).filter(N.organization_id == org, N.recipient.in_(mine or ["-"]))
    if unread_only:
        q = q.filter(N.read_at.is_(None))
    rows = q.order_by(N.created_at.desc()).limit(min(max(limit, 1), 200)).all()
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "ref_type": n.ref_type,
             "ref_id": n.ref_id, "for": "management" if n.recipient == MANAGEMENT else "me",
             "at": n.created_at.isoformat() + "Z" if n.created_at else None, "read": n.read_at is not None}
            for n in rows]


def mark_read(db: Session, org: str, w: Who, ids) -> int:
    from app.models.bookit_models import BookitNotification as N
    mine = [w.name] if w.name else []
    if w.manager:
        mine.append(MANAGEMENT)
    n = (db.query(N).filter(N.organization_id == org, N.id.in_(list(ids) or ["-"]), N.recipient.in_(mine or ["-"]),
                            N.read_at.is_(None)).update({N.read_at: datetime.utcnow()}, synchronize_session=False))
    db.flush()
    return n


def sheet_edit(db: Session, org: str, w: Who, tab: str, row_id: str, key: str, value: str) -> None:
    """Send a team field to the sheet + app through the board's road (one road)."""
    from app.models.program_models import HandoffPendingEdit as E
    db.add(E(organization_id=org, row_id=row_id, tab=tab, key=key, value=value, by_user_id=w.user_id,
             by_name=w.name, created_at=datetime.utcnow()))


def kick(prog) -> None:
    RS._last_run.pop(prog.id, None)
