"""Restland BookIT logins for the team, and the owner's test advisor login
(owner, Nov 26: "we need to get that built in ... before we do anything, I
need a test account so I can see what it looks like on their side").

* create_login: management only. Finds or creates the person's EvoSys account
  for the email on the Team tab (the existing staff identity path - unknown
  password, must set their own), gives it advisor access to the Restland
  workspace, and links it to their team name. Their work, follow-ups and
  6 Blocks are found by that name, so they see exactly their own.
  It sends nothing by itself; send_setup emails the one-time set-password link
  from the platform's sending address, or hands the link back to pass on.
* Nobody on the team gets in until BookIT is opened to the team (core.who).
* The test login: one extra team member ("Test Advisor", not on the sheet,
  never in the rotation) linked to a login of the owner's choosing. It shows
  BookIT exactly as the chosen advisor sees it, VIEW ONLY, even before BookIT
  is opened (core.who + the router's view-only guard).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.services.bookit.core import BookitError, Who, audit, cfg, save_cfg

TEST_NAME = "Test Advisor"


def _member(db: Session, org: str, member_id: str):
    from app.models.program_models import ProgramTeamMember as M
    m = db.query(M).filter(M.organization_id == org, M.id == member_id).first()
    if m is None:
        raise BookitError("Not found.", 404)
    return m


def _account(db: Session, org: str, email: str, full_name: str, actor) -> Any:
    """The person's EvoSys login (found or created) with advisor access to this workspace."""
    from fastapi import HTTPException
    from app.services import sales_staff
    from app.services.workspace_access import grant_workspace_membership
    try:
        email_n = sales_staff.assert_email(email)
        user = sales_staff.find_identity(db, email_n)
        created = user is None
        if created:
            user = sales_staff.create_identity(db, email_n, full_name, actor)
        if getattr(user, "role", None) == "god_admin":
            raise BookitError("That email is an owner login; pick a different one.", 409)
        grant_workspace_membership(db, user.id, org, role="advisor", granted_by=actor.id, commit=False)
    except HTTPException as e:
        raise BookitError(str(e.detail), e.status_code)
    return user, created


def create_login(db: Session, prog, w: Who, actor, member_id: str, email: Optional[str] = None) -> Dict[str, Any]:
    from app.models.program_models import ProgramTeamMember as M
    if not w.manager:
        raise BookitError("Management only.", 403)
    org = prog.organization_id
    m = _member(db, org, member_id)
    email = (email or m.email or "").strip()
    if not email:
        raise BookitError("Add an email for %s on the Team tab first." % m.name)
    user, created = _account(db, org, email, m.name, actor)
    other = db.query(M).filter(M.organization_id == org, M.user_id == user.id, M.id != m.id).first()
    if other is not None:
        raise BookitError("That email's login already belongs to %s." % other.name, 409)
    old = m.user_id
    m.user_id = user.id
    audit(db, org, w, "member", m.id, "login", "user_id", old, user.id)
    db.flush()
    return {"member_id": m.id, "name": m.name, "email": user.email, "new_account": created,
            "has_signed_in": user.last_login_at is not None}


def send_setup(db: Session, prog, w: Who, actor, member_id: str, base_url: str, email_it: bool) -> Dict[str, Any]:
    """A one-time link to set their password (3 days). Emailed, or handed back to pass on."""
    from app.models.models import User
    from app.services import staff_activation
    from app.models.staff_models import PURPOSE_SETUP
    if not w.manager:
        raise BookitError("Management only.", 403)
    m = _member(db, prog.organization_id, member_id)
    user = db.query(User).filter(User.id == m.user_id).first() if m.user_id else None
    if user is None:
        raise BookitError("Create %s's login first." % m.name)
    _row, raw = staff_activation.issue(db, user, actor, purpose=PURPOSE_SETUP)
    url = staff_activation.activation_url(base_url, raw)
    sent = _email(user, url, base_url) if email_it else False
    audit(db, prog.organization_id, w, "member", m.id, "setup_link", "emailed", None, bool(sent))
    db.flush()
    return {"emailed": sent, "to": user.email, "setup_url": None if sent else url}


def _email(user, url: str, base_url: str) -> bool:
    try:
        from app.services.email_service import send_email_via_provider
    except Exception:  # pragma: no cover
        return False
    first = (user.full_name or "").split(" ")[0] or "there"
    html = ("<p>Hi %s,</p><p>Your Restland BookIT login is ready. BookIT is where you'll see your "
            "appointments, follow-ups and File Review 6 Block, all in one place.</p>"
            "<p><a href=\"%s\">Set your password</a> (the link works once and expires in 3 days). "
            "Then sign in at <a href=\"%s/login\">%s/login</a> with this email.</p>"
            "<p>Restland BookIT</p>" % (first, url, base_url.rstrip("/"), base_url.rstrip("/")))
    try:
        r = send_email_via_provider(to_email=user.email, subject="Your Restland BookIT login",
                                    body_html=html, message_type="transactional")
        return bool(r and r.get("success"))
    except Exception:
        return False


# ── the owner's test login ──────────────────────────────────────────────────

def test_login(db: Session, prog) -> Dict[str, Any]:
    from app.models.models import User
    t = cfg(prog).get("test_login") or {}
    user = db.query(User).filter(User.id == t.get("user_id")).first() if t.get("user_id") else None
    return {"email": user.email if user else None, "mirrors": t.get("mirrors"),
            "member_id": t.get("member_id"), "has_signed_in": bool(user and user.last_login_at)}


def set_test_login(db: Session, prog, w: Who, actor, email: Optional[str], mirrors: Optional[str]) -> Dict[str, Any]:
    from app.models.program_models import ProgramTeamMember as M
    from app.services.bookit.core import team_names
    if not w.manager:
        raise BookitError("Management only.", 403)
    org = prog.organization_id
    c = cfg(prog)
    t = dict(c.get("test_login") or {})
    if mirrors is not None:
        if mirrors not in team_names(db, org) or mirrors == TEST_NAME:
            raise BookitError("Pick someone on the team.")
        t["mirrors"] = mirrors
    if email:
        m = db.query(M).filter(M.organization_id == org, M.name == TEST_NAME).first()
        if m is None:
            m = M(organization_id=org, name=TEST_NAME, source="board", active=False)
            db.add(m)
            db.flush()
        from app.models.models import User
        existing = db.query(User).filter(User.email == email.strip().lower()).first()
        if existing is not None and (existing.role in ("god_admin", "super_admin", "org_admin")
                                     or existing.id == actor.id):
            raise BookitError("Use a separate email for the test login (for example yourname+bookittest@gmail.com), "
                              "not one of your real logins.", 409)
        user, _created = _account(db, org, email, "Mike Simmons (BookIT test)", actor)
        other = db.query(M).filter(M.organization_id == org, M.user_id == user.id, M.id != m.id).first()
        if other is not None:
            raise BookitError("That email's login already belongs to %s." % other.name, 409)
        m.user_id = user.id
        t.update({"member_id": m.id, "user_id": user.id})
    if not t.get("member_id"):
        raise BookitError("Give the test login an email first.")
    audit(db, org, w, "test_login", t["member_id"], "update", "test_login", (c.get("test_login") or {}), t)
    c["test_login"] = t
    save_cfg(db, prog, c)
    return test_login(db, prog)
