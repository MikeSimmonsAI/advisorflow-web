"""EVOSYS PRO SALES TEAM — who holds which seat, decided in one place.

THE SEATS (top to bottom)
-------------------------
  God Mode       god_admin. Above everyone, every right, always. Nothing here
                 narrows it.
  Owner          brand capability `sales_program_owner` on EvoSys Pro Sales
                 (platform@evosyspro.live and Michael Schlueter). Full view,
                 runs the team. Granted only by God Mode.
  Sr EVP         brand capability `sales_team_executive` + an active sales
                 membership on EvoSys Pro Sales. Leads the team.
  Area Sales Mgr membership role `sales_manager`.
  Rep            membership role `sales_rep`.

The executive grant `deal_builder_executive` (from PR #591) still opens the
full executive Deal Builder; it is treated as full view, nothing more.

One record decides everything: the membership on EvoSys Pro Sales (role,
reports_to, is_active) plus these named grants. Program Access cards, the Deal
Builder views, My Team and commission attribution all read it from here.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     Membership)
from app.services import capabilities as caps
from app.services.sales_access import is_god

PROGRAM_SLUG = "evosyspro-sales"
CAP_OWNER = "sales_program_owner"
CAP_EXECUTIVE = "sales_team_executive"
CAP_EXEC_BUILDER = "deal_builder_executive"

SEAT_GOD, SEAT_OWNER, SEAT_SEVP, SEAT_ASM, SEAT_REP = "god", "owner", "sevp", "asm", "rep"
SEAT_LABELS = {SEAT_GOD: "God Mode", SEAT_OWNER: "Owner",
               SEAT_SEVP: "Sr Executive Vice President",
               SEAT_ASM: "Area Sales Manager", SEAT_REP: "Salesperson"}


def program_brand(db: Session) -> Optional[BrandSalesOrg]:
    return (db.query(BrandSalesOrg)
            .filter(BrandSalesOrg.slug == PROGRAM_SLUG,
                    BrandSalesOrg.is_active.is_(True)).first())


def _has(db: Session, user: User, key: str, brand_id: str) -> bool:
    return brand_id in caps.brands_with_capability(db, user, key)


def membership(db: Session, user: User, brand_id: str) -> Optional[Membership]:
    return (db.query(Membership)
            .filter(Membership.user_id == user.id,
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                    Membership.scope_id == brand_id,
                    Membership.is_active.is_(True),
                    Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP)))
            .first())


def seat(db: Session, user: Optional[User], brand: Optional[BrandSalesOrg] = None) -> Optional[str]:
    """This person's seat in the EvoSys Pro sales program, or None."""
    if user is None:
        return None
    if is_god(user):
        return SEAT_GOD
    brand = brand or program_brand(db)
    if brand is None:
        return None
    if _has(db, user, CAP_OWNER, brand.id):
        return SEAT_OWNER
    m = membership(db, user, brand.id)
    if m is None:
        return None
    if _has(db, user, CAP_EXECUTIVE, brand.id):
        return SEAT_SEVP
    return SEAT_ASM if m.role == ROLE_SALES_MANAGER else SEAT_REP


def is_owner_or_god(db: Session, user: Optional[User]) -> bool:
    return seat(db, user) in (SEAT_GOD, SEAT_OWNER)


def deal_view(db: Session, user: Optional[User]) -> Optional[str]:
    """Which Deal Builder commission view this person gets, or None.

    full  God, owners, and holders of the executive builder grant
    sevp / asm / rep  by seat - and only once onboarding is approved.
    """
    from app.services import deal_builder_commission as dbc
    if user is None:
        return None
    if is_god(user):
        return dbc.VIEW_FULL
    brand = program_brand(db)
    if brand is None:
        return None
    if _has(db, user, CAP_OWNER, brand.id) or _has(db, user, CAP_EXEC_BUILDER, brand.id):
        return dbc.VIEW_FULL
    s = seat(db, user, brand)
    if s is None:
        return None
    if not onboarding_cleared(db, user, brand):
        return None
    return {SEAT_SEVP: dbc.VIEW_SEVP, SEAT_ASM: dbc.VIEW_ASM, SEAT_REP: dbc.VIEW_REP}[s]


def onboarding_cleared(db: Session, user: User, brand: BrandSalesOrg) -> bool:
    """Approved to sell. A person with an onboarding record must be approved;
    see app/services/sales_onboarding.py. (Members who predate onboarding have
    no record and are not blocked by this check.)"""
    try:
        from app.services import sales_onboarding
    except ImportError:  # onboarding not installed yet
        return True
    return sales_onboarding.is_cleared(db, user.id, brand.id)


# ── MY TEAM: roster, invites, suspend, move, departure ────────────────────────
#
# Rights (all checked here, on the server):
#   God / owner   everyone on EvoSys Pro Sales: invite a Sr EVP, an Area Sales
#                 Manager or a rep; suspend, reactivate, move, record departure;
#                 approve onboarding and open its files.
#   Sr EVP        his own team only (everyone whose reporting line leads to
#                 him): invite Area Sales Managers and reps, suspend,
#                 reactivate, move a rep between his managers. No approval, no
#                 files, no departure, no owners.
#   ASM / rep     no team administration.
# Nobody can promote themselves, and owner appointments stay with God Mode
# (the sales_program_owner grant in God Mode > Pricing & Compensation).

from datetime import datetime as _dt  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from app.models.sales_team_models import SalesDeparture  # noqa: E402

INVITE_SEATS = (SEAT_SEVP, SEAT_ASM, SEAT_REP)


def _all_members(db: Session, brand_id: str, active_only: bool = False) -> List[Membership]:
    q = (db.query(Membership)
         .filter(Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                 Membership.scope_id == brand_id,
                 Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP))))
    if active_only:
        q = q.filter(Membership.is_active.is_(True))
    return q.all()


def team_of(db: Session, brand_id: str, leader_id: str) -> set:
    """Everyone whose reporting line leads to `leader_id` (not including him).
    Cycle-safe."""
    members = _all_members(db, brand_id)
    children: Dict[str, List[str]] = {}
    for m in members:
        if m.reports_to_user_id:
            children.setdefault(m.reports_to_user_id, []).append(m.user_id)
    out, stack = set(), [leader_id]
    while stack:
        cur = stack.pop()
        for c in children.get(cur, []):
            if c not in out and c != leader_id:
                out.add(c)
                stack.append(c)
    return out


def _require_brand(db: Session) -> BrandSalesOrg:
    b = program_brand(db)
    if b is None:
        raise HTTPException(status_code=503, detail="The EvoSys Pro sales organization is not set up.")
    return b


def actor_rights(db: Session, actor: User) -> Dict[str, Any]:
    brand = program_brand(db)
    s = seat(db, actor, brand) if brand else None
    admin = s in (SEAT_GOD, SEAT_OWNER)
    leader = s == SEAT_SEVP and brand is not None and onboarding_cleared(db, actor, brand)
    return {"seat": s, "seat_label": SEAT_LABELS.get(s, ""),
            "can_view_team": admin or leader,
            "can_invite": admin or leader,
            "invite_seats": list(INVITE_SEATS) if admin else ([SEAT_ASM, SEAT_REP] if leader else []),
            "can_approve": admin, "can_open_files": admin,
            "can_record_departure": admin, "is_admin": admin, "is_leader": leader}


def _seat_of_member(db: Session, user: User, m: Membership, brand_id: str) -> str:
    if is_god(user):
        return SEAT_GOD
    if _has(db, user, CAP_OWNER, brand_id):
        return SEAT_OWNER
    if _has(db, user, CAP_EXECUTIVE, brand_id):
        return SEAT_SEVP
    return SEAT_ASM if m.role == ROLE_SALES_MANAGER else SEAT_REP


def roster(db: Session, actor: User) -> Dict[str, Any]:
    from app.services import sales_onboarding as onb_svc
    brand = _require_brand(db)
    rights = actor_rights(db, actor)
    if not rights["can_view_team"]:
        raise HTTPException(status_code=403, detail="My Team is for the owners and the Sr Executive Vice President.")
    members = _all_members(db, brand.id)
    scope = None if rights["is_admin"] else (team_of(db, brand.id, actor.id) | {actor.id})
    departures = {d.user_id: d for d in db.query(SalesDeparture)
                  .filter(SalesDeparture.brand_sales_org_id == brand.id).all()}
    people = []
    for m in members:
        if scope is not None and m.user_id not in scope:
            continue
        u = db.query(User).filter(User.id == m.user_id).first()
        if u is None:
            continue
        st = _seat_of_member(db, u, m, brand.id)
        onb = onb_svc.record(db, u.id, brand.id)
        dep = departures.get(u.id)
        people.append({
            "user_id": u.id, "name": u.full_name or u.email, "email": u.email,
            "seat": st, "seat_label": SEAT_LABELS[st],
            "manager_id": m.reports_to_user_id, "active": bool(m.is_active),
            "departed_at": dep.departed_at.isoformat() if dep else None,
            "onboarding": (onb_svc.checklist(db, onb, with_file_names=rights["can_open_files"])
                           if onb else None),
            "onboarding_id": onb.id if onb else None,
            "is_self": u.id == actor.id,
        })
    order = {SEAT_GOD: -1, SEAT_OWNER: 0, SEAT_SEVP: 1, SEAT_ASM: 2, SEAT_REP: 3}
    people.sort(key=lambda p: (order.get(p["seat"], 9), (p["name"] or "").lower()))
    return {"brand": {"id": brand.id, "name": brand.name}, "rights": rights, "people": people,
            "drive_status": onb_svc.DRIVE_STATUS}


def _member(db: Session, brand_id: str, user_id: str) -> Membership:
    m = (db.query(Membership)
         .filter(Membership.user_id == user_id, Membership.scope_type == SCOPE_BRAND_SALES_ORG,
                 Membership.scope_id == brand_id,
                 Membership.role.in_((ROLE_SALES_MANAGER, ROLE_SALES_REP))).first())
    if m is None:
        raise HTTPException(status_code=404, detail="That person is not on the EvoSys Pro sales team.")
    return m


def _assert_can_manage(db: Session, actor: User, brand_id: str, target_id: str) -> Dict[str, Any]:
    rights = actor_rights(db, actor)
    if target_id == actor.id:
        raise HTTPException(status_code=403, detail="You cannot change your own seat.")
    target = db.query(User).filter(User.id == target_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")
    tseat = seat(db, target)
    if tseat in (SEAT_GOD, SEAT_OWNER):
        raise HTTPException(status_code=403, detail="Owners are managed in God Mode.")
    if rights["is_admin"]:
        return rights
    if rights["is_leader"] and target_id in team_of(db, brand_id, actor.id):
        return rights
    raise HTTPException(status_code=403, detail="That person is not on your team.")


def invite(db: Session, actor: User, *, email: str, full_name: str, seat_key: str,
           manager_id: Optional[str], base_url: Optional[str]) -> Dict[str, Any]:
    from app.services import sales_onboarding as onb_svc
    from app.services import sales_staff, staff_activation
    from app.models.staff_models import PURPOSE_SETUP
    brand = _require_brand(db)
    rights = actor_rights(db, actor)
    if seat_key not in rights["invite_seats"]:
        raise HTTPException(status_code=403, detail="You can't invite someone to that seat.")
    # Where the new person sits.
    if seat_key == SEAT_SEVP:
        manager_id = None
    elif rights["is_leader"]:
        if seat_key == SEAT_ASM:
            manager_id = actor.id
        else:
            manager_id = manager_id or actor.id
            if manager_id != actor.id and manager_id not in team_of(db, brand.id, actor.id):
                raise HTTPException(status_code=403, detail="Pick a manager on your own team.")
    if seat_key == SEAT_REP and manager_id:
        mgr_user = db.query(User).filter(User.id == manager_id).first()
        mm = membership(db, mgr_user, brand.id) if mgr_user else None
        if mm is None or mm.role != ROLE_SALES_MANAGER:
            raise HTTPException(status_code=422, detail="A rep must report to an Area Sales Manager or the Sr EVP.")

    email_n = sales_staff.assert_email(email)
    user = sales_staff.find_identity(db, email_n)
    created = False
    if user is None:
        user = sales_staff.create_identity(db, email_n, full_name, actor)
        created = True
    if is_god(user) or _has(db, user, CAP_OWNER, brand.id):
        raise HTTPException(status_code=409, detail="That person is an owner; owners are managed in God Mode.")
    role = ROLE_SALES_REP if seat_key == SEAT_REP else ROLE_SALES_MANAGER
    sales_staff.grant_membership(db, user, brand, role, actor, reports_to_user_id=manager_id)
    if seat_key == SEAT_SEVP:
        # Keep every grant they already hold over this brand; add this one.
        keys = caps.brand_grants_for(db, user.id, brand.id)
        caps.set_brand_grants(db, user, brand.id, brand.name, actor,
                              sorted(set(keys) | {CAP_EXECUTIVE}))
    db.query(SalesDeparture).filter(SalesDeparture.brand_sales_org_id == brand.id,
                                    SalesDeparture.user_id == user.id).delete()
    onb_svc.open_for(db, user, brand.id, invited_by=actor.id)

    setup_url = None
    needs_link = created or bool(user.must_change_password)
    if needs_link:
        _row, raw = staff_activation.issue(db, user, actor, brand_sales_org_id=brand.id,
                                           purpose=PURPOSE_SETUP)
        setup_url = staff_activation.activation_url(base_url, raw)
    db.commit()
    sent = _send_invite_email(user, actor, seat_key, setup_url, base_url)
    return {"user_id": user.id, "email": user.email, "seat": seat_key,
            "manager_id": manager_id, "new_account": created,
            "email_sent": sent,
            # Only when the email could not be sent, so the inviter can pass it on.
            "setup_url": None if sent else setup_url}


def _send_invite_email(user: User, actor: User, seat_key: str, setup_url: Optional[str],
                       base_url: Optional[str]) -> bool:
    try:
        from app.services.email_service import send_email_via_provider
    except Exception:  # pragma: no cover
        return False
    base = (base_url or "https://app.evosyspro.live").rstrip("/")
    who = SEAT_LABELS[seat_key]
    if setup_url:
        action = ('<p><a href="%s">Set your password and get started</a>. The link works once '
                  'and expires in 3 days.</p>' % setup_url)
    else:
        action = '<p><a href="%s/login">Sign in</a> with your existing EvoSys Pro login.</p>' % base
    html = ("<p>Hi %s,</p><p>%s invited you to the EvoSys Pro sales team as %s.</p>%s"
            "<p>After you sign in, open <b>Sales Onboarding</b> from Program Access, fill in the "
            "short application and upload your checklist documents. You can start selling "
            "once an owner approves you.</p><p>EvoSys Pro</p>"
            % (user.full_name or "", actor.full_name or "EvoSys Pro", who, action))
    try:
        r = send_email_via_provider(to_email=user.email, subject="You're invited to the EvoSys Pro sales team",
                                    body_html=html, message_type="transactional")
        return bool(r and r.get("success"))
    except Exception:
        return False


def set_suspended(db: Session, actor: User, target_id: str, suspended: bool) -> None:
    from app.services import sales_staff
    brand = _require_brand(db)
    _assert_can_manage(db, actor, brand.id, target_id)
    m = _member(db, brand.id, target_id)
    if not suspended and db.query(SalesDeparture).filter(
            SalesDeparture.brand_sales_org_id == brand.id,
            SalesDeparture.user_id == target_id).first():
        raise HTTPException(status_code=409, detail="This person has left the company. Invite them again instead.")
    sales_staff.set_active(db, m, not suspended, actor)
    db.commit()


def move(db: Session, actor: User, target_id: str, manager_id: str) -> None:
    from app.services import sales_staff
    brand = _require_brand(db)
    rights = _assert_can_manage(db, actor, brand.id, target_id)
    m = _member(db, brand.id, target_id)
    if m.role != ROLE_SALES_REP:
        raise HTTPException(status_code=422, detail="Only reps are moved between managers.")
    if not rights["is_admin"] and manager_id != actor.id and manager_id not in team_of(db, brand.id, actor.id):
        raise HTTPException(status_code=403, detail="Pick a manager on your own team.")
    sales_staff.set_reporting_manager(db, m, manager_id, actor)
    db.commit()


def record_departure(db: Session, actor: User, target_id: str, departed_at: Optional[_dt],
                     note: Optional[str]) -> None:
    from app.services import sales_staff
    from app.routers.audit_log_router import log_action
    brand = _require_brand(db)
    rights = _assert_can_manage(db, actor, brand.id, target_id)
    if not rights["is_admin"]:
        raise HTTPException(status_code=403, detail="Only an owner can record that someone left.")
    m = _member(db, brand.id, target_id)
    when = departed_at or _dt.utcnow()
    dep = (db.query(SalesDeparture).filter(SalesDeparture.brand_sales_org_id == brand.id,
                                           SalesDeparture.user_id == target_id).first())
    if dep is None:
        dep = SalesDeparture(brand_sales_org_id=brand.id, user_id=target_id, departed_at=when,
                             recorded_by=actor.id, note=(note or "")[:500] or None)
        db.add(dep)
    else:
        dep.departed_at = when
    sales_staff.set_active(db, m, False, actor)
    log_action(db, None, actor.id, action="sales_member_departed", target_type="user",
               target_id=target_id, brand_sales_org_id=brand.id,
               after={"departed_at": when.isoformat()}, commit=False)
    db.commit()


def departed_at(db: Session, brand_id: str, user_id: Optional[str]) -> Optional[_dt]:
    if not user_id:
        return None
    d = (db.query(SalesDeparture).filter(SalesDeparture.brand_sales_org_id == brand_id,
                                         SalesDeparture.user_id == user_id).first())
    return d.departed_at if d else None
