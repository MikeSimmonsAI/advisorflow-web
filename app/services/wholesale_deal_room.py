"""PRIVATE BUYER DEAL ROOM - invitations, investor accounts, listings and
exclusive review. One platform, two experiences (#415).

REUSED, NOT REBUILT
    login / sessions / passwords   users + session_service (role "investor")
    buyer, entities, buy boxes     wholesale_buyers (+ #413 evidence tables)
    what an investor may SEE       wholesale_publication.buyer_room_payload (whitelist)
    match score                    wholesale_matching v2
    interest / offers              wholesale_buyer_outreach (the disposition desk reads it)
    files                          wholesale_files (25 MB, sniffed types, DB/S3)
    signatures                     EvoSys e-sign (evosys_esign) + approved templates
    staff alerts                   wholesale_notify (bell) + the work queue

SECURITY MODEL
    * An investor login has NO workspace membership (users.organization_id is
      NULL) and is fenced in deps.get_current_user to /investor/* and a few
      /auth/* paths. Every /investor endpoint resolves the investor's own
      account and buyer from the session - never from an id in the request.
    * A listing is visible only if it is published, in date, its content was
      approved for investors (deal.buyer_room_published), the investor is in
      its audience, the sandbox flags agree, and no OTHER buyer holds an active
      exclusive review. Anything else is a 404, never a 403.
    * Exclusive review is a temporary VISIBILITY hold, approved by a person,
      with real server timestamps. One active hold per listing is enforced by a
      partial unique index; expiry is applied on every read (no scheduler is
      trusted to have run).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.models import Organization, User
from app.models.wholesale_investor_models import (HOLD_ACTIVE, HOLD_DECLINED, HOLD_ENDED, HOLD_REQUESTED,
                                                  INVITE_ACCEPTED, INVITE_EXPIRED, INVITE_PENDING, INVITE_REVOKED,
                                                  LISTING_STATES, VISIBLE_STATES, DealListing, ExclusiveHold,
                                                  DealRoomSave, InvestorAccount, InvestorEntityRole, InvestorInvite)
from app.models.wholesale_models import (WholesaleBuyBox, WholesaleBuyer, WholesaleBuyerOutreach, WholesaleDeal,
                                         WholesaleDocument, WholesaleFile, WholesaleProperty)

INVITE_DAYS = 7
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")
HOLD_PRESETS = {"24h": 24 * 60, "48h": 48 * 60, "72h": 72 * 60, "7d": 7 * 24 * 60}
HOLD_MAX_MINUTES = 30 * 24 * 60
HOLD_NOTICE = ("Exclusive review is a temporary hold on Deal Room visibility so you can evaluate this "
               "opportunity. It is not an option, a contract or a right to purchase.")
STAFF_ROLES = ("org_admin", "manager", "god_admin", "super_admin")


def _now() -> datetime:
    return datetime.utcnow()


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def _j(v, default):
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def _iso(v):
    return v.isoformat() + "Z" if isinstance(v, datetime) else (v.isoformat() if v else None)


def public_base() -> str:
    return (os.environ.get("PUBLIC_APP_URL") or os.environ.get("FRONTEND_URL") or "https://app.evosyspro.live").rstrip("/")


def log(db, org_id, action, summary, user=None, deal_id=None, details=None):
    from app.services import wholesale_service as svc
    svc.log_event(db, org_id, action, actor_type="user" if user is not None else "system",
                  actor_user_id=getattr(user, "id", None), actor_label=None if user is not None else "Deal Room",
                  deal_id=deal_id, summary=summary[:250], details=details or {})


def notify_staff(db, org_id: str, message: str, link: str, assignee_id: Optional[str] = None,
                 event: Optional[str] = None) -> int:
    from app.models.models import NotificationType
    from app.services import wholesale_deal_room_setup as SET
    from app.services import wholesale_notify
    if event and not SET.notify_on(db, org_id, event):
        return 0
    return wholesale_notify.notify(db, org_id, kind=NotificationType.DEAL_ROOM, message=message, link=link,
                                   assignee_id=assignee_id)


# ═══════════════════════════════ invitations ════════════════════════════════

def _brand_name(db, org_id: str) -> str:
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return getattr(org, "name", None) or "Deal Room"


def _send_invite_email(db, inv: InvestorInvite, token: str) -> str:
    from app.services import email_service
    url = "%s/investors/invite/%s" % (public_base(), token)   # /investor/:token is the Buyer Room link
    org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
    if inv.is_test or inv.email.lower().rsplit(".", 1)[-1] in ("invalid", "test", "example", "localhost"):
        return "not_sent: practice address (link shown to staff only)"
    html = ("<p>Hello %s,</p><p>%s has invited you to a private Deal Room to review investment "
            "opportunities selected for you.</p><p><a href='%s'>Accept your invitation</a></p>"
            "<p>This link is for you only and expires in %d days.</p>" % (
                _esc(inv.name), _esc(_brand_name(db, inv.organization_id)), url, INVITE_DAYS))
    try:
        r = email_service.send_email_via_provider(to_email=inv.email, subject="Your private Deal Room invitation",
                                                  body_html=html, org=org, message_type="deal_room_invite")
        return "delivered" if (r and r.get("success")) else "not_sent: %s" % ((r or {}).get("error") or "provider refused")[:120]
    except Exception as exc:  # noqa: BLE001 - recorded, never raised
        return "not_sent: %s" % str(exc)[:120]


def _esc(s) -> str:
    import html as _h
    return _h.escape(str(s or ""))


def invite_json(inv: InvestorInvite) -> Dict[str, Any]:
    return {"id": inv.id, "name": inv.name, "email": inv.email, "company": inv.company, "buyer_id": inv.buyer_id,
            "status": inv.status, "expires_at": _iso(inv.expires_at), "send_count": inv.send_count,
            "last_sent_at": _iso(inv.last_sent_at), "delivery": inv.last_delivery,
            "accepted_at": _iso(inv.accepted_at), "owner_user_id": inv.owner_user_id, "is_test": bool(inv.is_test),
            "created_at": _iso(inv.created_at)}


def _expire_invites(db, org_id: str) -> None:
    for inv in db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id,
                                               InvestorInvite.status == INVITE_PENDING,
                                               InvestorInvite.expires_at < _now()).all():
        inv.status, inv.token_hash = INVITE_EXPIRED, None


def create_invite(db, org_id: str, user, *, name: str, email: str, company: Optional[str] = None,
                  buyer_id: Optional[str] = None, owner_user_id: Optional[str] = None,
                  is_test: bool = False) -> Tuple[InvestorInvite, str]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="Enter the investor's name.")
    buyer = None
    if buyer_id:
        buyer = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                                WholesaleBuyer.organization_id == org_id).first()
        if buyer is None:
            raise HTTPException(status_code=404, detail="Buyer not found")
        if buyer.do_not_contact:
            raise HTTPException(status_code=409, detail="This buyer asked not to be contacted.")
        is_test = bool(buyer.is_test)
    from app.services import wholesale_deal_room_setup as SET
    SET.require_active(db, org_id, bool(is_test), "sending a real invitation")
    staff = db.query(User).filter(User.email == email).first()
    if staff is not None and staff.role != "investor":
        raise HTTPException(status_code=409, detail="That email belongs to a staff login. Investors need their "
                                                    "own email address.")
    _expire_invites(db, org_id)
    existing = db.query(InvestorInvite).filter(InvestorInvite.organization_id == org_id,
                                               InvestorInvite.email == email,
                                               InvestorInvite.status == INVITE_PENDING).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="An invitation to %s is already pending - resend it instead."
                                                    % email)
    token = secrets.token_urlsafe(32)
    inv = InvestorInvite(organization_id=org_id, buyer_id=getattr(buyer, "id", None), name=name.strip()[:120],
                         email=email, company=(company or "").strip()[:160] or None,
                         owner_user_id=owner_user_id or getattr(user, "id", None),
                         invited_by_id=getattr(user, "id", None), token_hash=_sha(token), status=INVITE_PENDING,
                         expires_at=_now() + timedelta(days=INVITE_DAYS), is_test=bool(is_test))
    db.add(inv)
    db.flush()
    inv.last_delivery = _send_invite_email(db, inv, token)
    inv.send_count, inv.last_sent_at = 1, _now()
    log(db, org_id, "deal_room.invite_sent", "Deal Room invitation for %s (%s)" % (inv.name, inv.last_delivery), user)
    return inv, token


def resend_invite(db, org_id: str, invite_id: str, user) -> Tuple[InvestorInvite, str]:
    inv = _invite(db, org_id, invite_id)
    if inv.status not in (INVITE_PENDING, INVITE_EXPIRED):
        raise HTTPException(status_code=409, detail="This invitation was %s." % inv.status)
    token = secrets.token_urlsafe(32)                 # the old link stops working
    inv.token_hash, inv.status, inv.expires_at = _sha(token), INVITE_PENDING, _now() + timedelta(days=INVITE_DAYS)
    inv.last_delivery = _send_invite_email(db, inv, token)
    inv.send_count, inv.last_sent_at = (inv.send_count or 0) + 1, _now()
    log(db, org_id, "deal_room.invite_resent", "Invitation resent to %s" % inv.name, user)
    return inv, token


def revoke_invite(db, org_id: str, invite_id: str, user) -> InvestorInvite:
    inv = _invite(db, org_id, invite_id)
    if inv.status == INVITE_ACCEPTED:
        raise HTTPException(status_code=409, detail="Already accepted - disable the investor's access instead.")
    inv.status, inv.token_hash, inv.revoked_at, inv.revoked_by_id = INVITE_REVOKED, None, _now(), user.id
    log(db, org_id, "deal_room.invite_revoked", "Invitation to %s revoked" % inv.name, user)
    return inv


def _invite(db, org_id, invite_id) -> InvestorInvite:
    inv = db.query(InvestorInvite).filter(InvestorInvite.id == invite_id,
                                          InvestorInvite.organization_id == org_id).first()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invitation not found")
    return inv


def _by_token(db, token: str) -> InvestorInvite:
    if not token or len(token) < 20 or len(token) > 120:
        raise HTTPException(status_code=404, detail="This invitation link is not valid.")
    inv = db.query(InvestorInvite).filter(InvestorInvite.token_hash == _sha(token)).first()
    if inv is None:
        raise HTTPException(status_code=404, detail="This invitation link is not valid or was replaced by a newer one.")
    if inv.status == INVITE_PENDING and inv.expires_at < _now():
        inv.status, inv.token_hash = INVITE_EXPIRED, None
        db.commit()
        raise HTTPException(status_code=410, detail="This invitation has expired. Ask for a new one.")
    if inv.status != INVITE_PENDING:
        raise HTTPException(status_code=410, detail="This invitation is no longer active.")
    return inv


def invite_preview(db, token: str) -> Dict[str, Any]:
    inv = _by_token(db, token)
    u = db.query(User).filter(User.email == inv.email).first()
    need = "register" if u is None else ("sign_in" if u.role == "investor" else "use_other_email")
    name, _, dom = inv.email.partition("@")
    return {"name": inv.name, "email_masked": name[:2] + "***@" + dom, "company": inv.company,
            "organization": _brand_name(db, inv.organization_id), "expires_at": _iso(inv.expires_at), "next": need}


def _password_ok(pw: str) -> Optional[str]:
    if len(pw or "") < 10:
        return "Use at least 10 characters."
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Use letters and at least one number."
    return None


def accept_invite(db, token: str, *, password: str, full_name: Optional[str] = None,
                  kind: Optional[str] = None) -> Tuple[User, InvestorAccount]:
    """Accept: create the investor login (or link an existing investor login
    after its password is proven), tie it to ONE buyer record, verify the email
    (the emailed link proves control of it), and burn the token."""
    from app.services.auth_service import hash_password, verify_password
    inv = _by_token(db, token)
    user = db.query(User).filter(User.email == inv.email).first()
    if user is not None and user.role != "investor":
        raise HTTPException(status_code=409, detail="That email belongs to a staff login. Ask for an invitation "
                                                    "to a different email address.")
    if user is None:
        problem = _password_ok(password)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        user = User(email=inv.email, full_name=(full_name or inv.name).strip()[:120], role="investor",
                    organization_id=None, password_hash=hash_password(password), must_change_password=False,
                    is_active=True)
        db.add(user)
        db.flush()
    else:
        if not verify_password(password or "", user.password_hash or ""):
            raise HTTPException(status_code=401, detail="That password does not match your existing Deal Room login.")
    acct = db.query(InvestorAccount).filter(InvestorAccount.organization_id == inv.organization_id,
                                            InvestorAccount.user_id == user.id).first()
    buyer = _link_buyer(db, inv)
    if acct is None:
        acct = InvestorAccount(organization_id=inv.organization_id, user_id=user.id, buyer_id=buyer.id,
                               invite_id=inv.id, kind=kind if kind in ("individual", "business") else None,
                               legal_name=(full_name or inv.name).strip()[:160], company_legal_name=inv.company,
                               assigned_user_id=inv.owner_user_id, is_test=bool(inv.is_test),
                               channels=json.dumps(["portal"]))
        db.add(acct)
    acct.email_verified_at = _now()
    acct.status = "active"
    inv.status, inv.token_hash, inv.accepted_at, inv.accepted_user_id = INVITE_ACCEPTED, None, _now(), user.id
    inv.buyer_id = buyer.id
    db.flush()
    log(db, inv.organization_id, "deal_room.invite_accepted", "%s accepted the Deal Room invitation" % inv.name,
        details={"buyer_id": buyer.id})
    notify_staff(db, inv.organization_id, "%s joined your Deal Room" % inv.name,
                 "/wholesale/deal-room?view=investors&buyer=%s" % buyer.id, assignee_id=inv.owner_user_id,
                 event="registered")
    return user, acct


def _link_buyer(db, inv: InvestorInvite) -> WholesaleBuyer:
    """ONE buyer record: the invited one, else an exact email match in this
    workspace, else a new buyer (source deal_room)."""
    if inv.buyer_id:
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == inv.buyer_id,
                                            WholesaleBuyer.organization_id == inv.organization_id).first()
        if b is not None:
            if not b.email:
                b.email = inv.email
            return b
    b = (db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == inv.organization_id,
                                         WholesaleBuyer.email == inv.email,
                                         WholesaleBuyer.is_test.is_(bool(inv.is_test))).first())
    if b is not None:
        return b
    b = WholesaleBuyer(organization_id=inv.organization_id,
                       entity_type="company" if inv.company else "person",
                       company_name=inv.company, contact_name=inv.name, email=inv.email,
                       preferred_channel="email", source="deal_room", is_test=bool(inv.is_test),
                       source_detail="Joined the Deal Room by invitation")
    db.add(b)
    db.flush()
    return b


# ═══════════════════════════════ investor login ═════════════════════════════

def investor_accounts(db, user) -> List[InvestorAccount]:
    return db.query(InvestorAccount).filter(InvestorAccount.user_id == user.id,
                                            InvestorAccount.status == "active").all()


def resolve_account(db, user, account_id: Optional[str] = None) -> InvestorAccount:
    if getattr(user, "role", None) != "investor":
        raise HTTPException(status_code=403, detail="This area is for Deal Room investors.")
    accts = investor_accounts(db, user)
    if account_id:
        accts = [a for a in accts if a.id == account_id]
    if not accts:
        raise HTTPException(status_code=403, detail="Your Deal Room access is not active.")
    a = accts[0]
    a.last_seen_at = _now()
    return a


def buyer_of(db, acct: InvestorAccount) -> WholesaleBuyer:
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == acct.buyer_id,
                                        WholesaleBuyer.organization_id == acct.organization_id).first()
    if b is None:
        raise HTTPException(status_code=403, detail="Your Deal Room access is not linked to a buyer record.")
    return b


# ═══════════════════════════════ identity / entities ════════════════════════

IDENTITY_FIELDS = ("kind", "legal_name", "preferred_name", "phone", "company_legal_name", "dba", "entity_type",
                   "business_address", "position")


def set_identity(db, acct: InvestorAccount, data: Dict[str, Any]) -> InvestorAccount:
    b = buyer_of(db, acct)
    for k in IDENTITY_FIELDS:
        if k in data and data[k] is not None:
            v = str(data[k]).strip()[:200]
            if k == "kind" and v not in ("individual", "business"):
                raise HTTPException(status_code=400, detail="Choose individual or business.")
            setattr(acct, k, v or None)
    if "channels" in data and isinstance(data["channels"], list):
        acct.channels = json.dumps([c for c in data["channels"] if c in ("portal", "email", "phone", "sms")])
    need = ["kind", "legal_name"] + (["company_legal_name"] if acct.kind == "business" else [])
    if all(getattr(acct, k) for k in need):
        acct.identity_completed_at = acct.identity_completed_at or _now()
    # the buyer record only GAINS what was empty - staff-entered values are never overwritten
    if acct.legal_name and not b.contact_name:
        b.contact_name = acct.legal_name
    if acct.kind == "business" and acct.company_legal_name and not b.company_name:
        b.company_name = acct.company_legal_name
        b.entity_type = "company"
    if acct.phone and not b.phone:
        b.phone = acct.phone
    if acct.kind == "business" and acct.company_legal_name:
        add_entity(db, acct, {"legal_name": acct.company_legal_name, "position": acct.position,
                              "authority": "signer"})
    db.flush()
    return acct


def add_entity(db, acct: InvestorAccount, data: Dict[str, Any]) -> Dict[str, Any]:
    """An investor names a buying entity. If that exact name is another buyer
    in this workspace, a person reviews the link; nothing merges by itself."""
    from app.services import wholesale_buyer_evidence as EVD
    name = (data.get("legal_name") or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Enter the entity's legal name.")
    b = buyer_of(db, acct)
    other = EVD.Index(db, acct.organization_id, is_test=bool(b.is_test)).exact(name)
    if other and other != b.id and other != b.linked_to_buyer_id:
        e = EVD.ensure_entity(db, acct.organization_id, other, name, status="needs_review", source="deal_room",
                              reason="%s says they buy as %s, which matches another buyer record - confirm before "
                                     "linking" % (acct.legal_name or "An investor", name), linked_buyer_id=b.id)
        ent = EVD.ensure_entity(db, acct.organization_id, b.id, name, status="needs_review", source="deal_room",
                                reason="Stated in the Deal Room; the name matches another buyer record")
    else:
        ent = EVD.ensure_entity(db, acct.organization_id, b.id, name, status="confirmed", source="deal_room",
                                reason="Stated by the investor in the Deal Room")
        e = ent
    role = db.query(InvestorEntityRole).filter(InvestorEntityRole.account_id == acct.id,
                                               InvestorEntityRole.entity_id == ent.id).first()
    if role is None:
        role = InvestorEntityRole(organization_id=acct.organization_id, account_id=acct.id, entity_id=ent.id)
        db.add(role)
    role.position = (data.get("position") or role.position or "")[:120] or None
    auth = data.get("authority") or role.authority or "representative"
    role.authority = auth if auth in ("signer", "representative", "viewer") else "representative"
    db.flush()
    return {"entity_id": ent.id, "status": ent.status, "review": e.status == "needs_review"}


def profile(db, acct: InvestorAccount) -> Dict[str, Any]:
    from app.models.wholesale_buyer_engine_models import WholesaleBuyerEntity
    b = buyer_of(db, acct)
    roles = db.query(InvestorEntityRole).filter(InvestorEntityRole.account_id == acct.id).all()
    ents = {e.id: e for e in db.query(WholesaleBuyerEntity).filter(
        WholesaleBuyerEntity.id.in_([r.entity_id for r in roles] or [""])).all()}
    reps = (db.query(InvestorAccount, User).join(User, User.id == InvestorAccount.user_id)
            .filter(InvestorAccount.organization_id == acct.organization_id, InvestorAccount.buyer_id == b.id,
                    InvestorAccount.status == "active").all())
    boxes = [x for x in db.query(WholesaleBuyBox).filter(WholesaleBuyBox.buyer_id == b.id,
                                                         WholesaleBuyBox.organization_id == acct.organization_id,
                                                         WholesaleBuyBox.is_active.is_(True)).all()]
    return {
        "account": {k: getattr(acct, k) for k in IDENTITY_FIELDS} | {
            "id": acct.id, "channels": _j(acct.channels, ["portal"]), "email_verified": bool(acct.email_verified_at),
            "identity_complete": bool(acct.identity_completed_at), "welcome_done": bool(acct.welcome_completed_at),
            "is_test": bool(acct.is_test)},
        "organization": _brand_name(db, acct.organization_id),
        "entities": [{"id": r.entity_id, "name": getattr(ents.get(r.entity_id), "legal_name", None),
                      "status": getattr(ents.get(r.entity_id), "status", None), "position": r.position,
                      "authority": r.authority} for r in roles],
        "representatives": [{"name": a.preferred_name or a.legal_name or u.full_name, "you": a.id == acct.id}
                            for a, u in reps],
        "buy_boxes": [box_view(x) for x in boxes],
        "funding": {"stated": b.stated_funding, "documented": False if not (b.proof_of_funds_on_file or b.cash_verified)
                    else True, "pof_expires": _iso(b.proof_of_funds_expires) if b.proof_of_funds_expires else None},
        "buying_status": b.stated_buying_status,
    }


# ═══════════════════════════════ buy boxes (investor view) ══════════════════

def _label_for(box, field: str, value) -> str:
    if value in (None, "", [], {}):
        return "UNKNOWN"
    src = (box.source or "").lower()
    if src == "county_records":
        return "SYSTEM INFERRED"
    if box.confirmed_at or src in ("stated", "ai_conversation", "staff", "file"):
        return "BUYER STATED"
    return "BUYER STATED"


BOX_FIELDS = ("property_types", "strategies", "states", "counties", "cities", "zips", "min_price", "max_price",
              "min_units", "max_units", "min_acres", "max_acres", "rehab_tolerance", "min_cap_rate")


def box_view(x: WholesaleBuyBox) -> Dict[str, Any]:
    from app.services.wholesale_buyer_engine import box_segments
    vals = {}
    for f in BOX_FIELDS:
        v = getattr(x, f)
        if f in ("property_types", "strategies", "states", "counties", "cities", "zips"):
            v = _j(v, [])
        elif v is not None and not isinstance(v, (int, str)):
            v = float(v)
        vals[f] = {"value": v, "label": _label_for(x, f, v)}
    return {"id": x.id, "label": x.label, "source": x.source, "confirmed_at": _iso(x.confirmed_at),
            "fields": vals, "segments": {k: sorted(v) for k, v in box_segments(x).items()},
            "criteria": _j(x.criteria, {}),
            "note": "Funding is never part of a buy box: a stated budget is not verified funding."}


def save_box(db, acct: InvestorAccount, data: Dict[str, Any], box_id: Optional[str] = None) -> Dict[str, Any]:
    """The buyer states or corrects their own criteria. Saving IS their
    confirmation; an inferred box they correct becomes a stated one."""
    b = buyer_of(db, acct)
    if box_id:
        x = db.query(WholesaleBuyBox).filter(WholesaleBuyBox.id == box_id, WholesaleBuyBox.buyer_id == b.id,
                                             WholesaleBuyBox.organization_id == acct.organization_id).first()
        if x is None:
            raise HTTPException(status_code=404, detail="Buy box not found")
    else:
        x = WholesaleBuyBox(organization_id=acct.organization_id, buyer_id=b.id, is_active=True)
        db.add(x)
    x.label = (data.get("label") or x.label or "My buy box")[:80]
    for f in BOX_FIELDS:
        if f not in data:
            continue
        v = data[f]
        if f in ("property_types", "strategies", "states", "counties", "cities", "zips"):
            v = [str(s).strip() for s in (v or []) if str(s).strip()][:60]
            setattr(x, f, json.dumps(v) if v else None)
        elif f == "rehab_tolerance":
            x.rehab_tolerance = v if v in ("light", "moderate", "heavy", "full_gut") else None
        else:
            try:
                setattr(x, f, None if v in (None, "") else float(v))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="%s must be a number" % f.replace("_", " "))
    if "criteria" in data and isinstance(data["criteria"], dict):
        allowed = {k: data["criteria"][k] for k in ("asset_classes", "zoning", "utilities_required", "min_occupancy",
                                                    "min_noi", "sf_profile", "land_types", "commercial_types",
                                                    "portfolio", "exclusions", "custom", "min_cash_flow",
                                                    "timeline", "preferences") if k in data["criteria"]}
        if "min_cash_flow" in allowed and allowed["min_cash_flow"] not in (None, ""):
            try:
                allowed["min_cash_flow"] = max(0.0, float(allowed["min_cash_flow"]))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="minimum cash flow must be a number")
        if "timeline" in allowed and allowed["timeline"] not in (None, "", "0_30", "30_60", "60_90", "90_plus"):
            raise HTTPException(status_code=400, detail="timeline must be 0_30, 30_60, 60_90 or 90_plus")
        if isinstance(allowed.get("preferences"), str):
            allowed["preferences"] = allowed["preferences"].strip()[:500] or None
        x.criteria = json.dumps({**_j(x.criteria, {}), **allowed})
    for lo, hi in (("min_price", "max_price"), ("min_units", "max_units"), ("min_acres", "max_acres")):
        a, c = getattr(x, lo), getattr(x, hi)
        if a is not None and c is not None and float(a) > float(c):
            raise HTTPException(status_code=400, detail="%s is above %s" % (lo.replace("_", " "), hi.replace("_", " ")))
    for f in ("min_units", "max_units"):
        if getattr(x, f) is not None:
            setattr(x, f, int(getattr(x, f)))
    x.source, x.confirmed_at = "stated", _now()
    x.confirmed_by = "Buyer (Deal Room)"
    db.flush()
    return box_view(x)


# ═══════════════════════════════ listings & holds ═══════════════════════════

def sweep(db, org_id: str) -> int:
    """Apply time: end holds past their deadline, expire listings past theirs.
    Idempotent and called on every read, so nothing depends on a scheduler."""
    now, n = _now(), 0
    for h in db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == org_id,
                                            ExclusiveHold.status == HOLD_ACTIVE, ExclusiveHold.ends_at <= now).all():
        h.status, h.ended_at, h.end_reason = HOLD_ENDED, now, "expired"
        lst = db.query(DealListing).filter(DealListing.id == h.listing_id).first()
        if lst is not None and lst.state == "exclusive_review":
            lst.state = "published"
        log(db, org_id, "deal_room.hold_expired", "Exclusive review ended (time expired) - deal back on the board",
            deal_id=h.deal_id, details={"hold_id": h.id})
        notify_staff(db, org_id, "Exclusive review ended for %s - the deal is visible to investors again"
                     % (_buyer_name(db, h.buyer_id) or "an investor"), "/wholesale/deal-room?view=holds",
                     event="hold_request")
        n += 1
    for lst in db.query(DealListing).filter(DealListing.organization_id == org_id,
                                            DealListing.state.in_(("published", "approved")),
                                            DealListing.expires_at.isnot(None), DealListing.expires_at <= now).all():
        lst.state, lst.ended_at, lst.end_reason = "expired", now, "expiry date reached"
        n += 1
    if n:
        db.flush()
    return n


def listing_for_deal(db, org_id: str, deal_id: str, create: bool = False, user=None) -> Optional[DealListing]:
    lst = db.query(DealListing).filter(DealListing.organization_id == org_id, DealListing.deal_id == deal_id).first()
    if lst is None and create:
        deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id, WholesaleDeal.organization_id == org_id).first()
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        from app.services import wholesale_deal_room_setup as SET
        st = SET.effective(db, org_id)
        lst = DealListing(organization_id=org_id, deal_id=deal_id, state="draft", is_test=bool(deal.is_test),
                          audience=st.get("default_audience") or "matched",
                          min_match_score=int(st.get("default_min_match") or 60))
        db.add(lst)
        db.flush()
        log(db, org_id, "deal_room.listing_created", "Deal Room listing drafted", user, deal_id=deal_id)
    return lst


TRANSITIONS = {
    "approve": (("draft",), "approved"),
    "publish": (("approved", "expired", "withdrawn"), "published"),
    "withdraw": (("draft", "approved", "published", "exclusive_review"), "withdrawn"),
    "close": (("draft", "approved", "published", "exclusive_review", "expired", "withdrawn"), "closed"),
    "redraft": (("approved", "withdrawn", "expired"), "draft"),
}


def update_listing(db, org_id: str, lst: DealListing, data: Dict[str, Any], user) -> DealListing:
    if "audience" in data:
        if data["audience"] not in ("matched", "selected", "all"):
            raise HTTPException(status_code=400, detail="audience must be matched, selected or all")
        lst.audience = data["audience"]
    if "buyer_ids" in data:
        ids = [str(i) for i in (data["buyer_ids"] or [])]
        valid = {b.id for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                                               WholesaleBuyer.id.in_(ids or [""])).all()}
        lst.buyer_ids = json.dumps(sorted(valid))
    if "min_match_score" in data and data["min_match_score"] is not None:
        lst.min_match_score = max(0, min(100, int(data["min_match_score"])))
    for k in ("publish_at", "expires_at"):
        if k in data:
            v = data[k]
            try:
                setattr(lst, k, datetime.fromisoformat(str(v).replace("Z", "")[:19]) if v else None)
            except ValueError:
                raise HTTPException(status_code=400, detail="%s must be a date-time" % k)
    if "notify_on_publish" in data:
        lst.notify_on_publish = bool(data["notify_on_publish"])
    db.flush()
    return lst


def transition(db, org_id: str, lst: DealListing, action: str, user) -> DealListing:
    if action not in TRANSITIONS:
        raise HTTPException(status_code=400, detail="Unknown action")
    allowed, target = TRANSITIONS[action]
    sweep(db, org_id)
    if lst.state not in allowed:
        raise HTTPException(status_code=409, detail="A %s listing cannot be %s." % (lst.state, target))
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    if action in ("approve", "publish") and not _serving(db, org_id, deal):
        raise HTTPException(status_code=409, detail="The investor package for this deal is not approved and "
                                                    "published (or it changed after approval). Open the deal's "
                                                    "Investor package, send it for approval and publish it first.")
    if action == "publish":
        from app.services import wholesale_deal_room_setup as SET
        SET.require_active(db, org_id, bool(lst.is_test), "publishing a real deal")
        days = SET.effective(db, org_id).get("default_publish_days")
        if not lst.expires_at and days:
            lst.expires_at = _now() + timedelta(days=int(days))
    if action == "publish" and lst.expires_at and lst.expires_at <= _now():
        raise HTTPException(status_code=409, detail="The expiry date has passed - set a new one first.")
    now = _now()
    if action == "approve":
        lst.approved_by_id, lst.approved_at = user.id, now
    if action == "publish":
        lst.published_by_id, lst.published_at = user.id, now
        lst.publish_at = lst.publish_at or now
    if action in ("withdraw", "close"):
        lst.ended_at, lst.end_reason = now, action
        for h in db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst.id,
                                                ExclusiveHold.status.in_((HOLD_ACTIVE, HOLD_REQUESTED))).all():
            h.status, h.ended_at, h.end_reason = (HOLD_ENDED if h.status == HOLD_ACTIVE else HOLD_DECLINED), now, \
                ("withdrawn" if action == "withdraw" else "closed")
    lst.state = target
    db.flush()
    log(db, org_id, "deal_room.listing_%s" % action, "Deal Room listing %s" % target, user, deal_id=lst.deal_id)
    return lst


def _serving(db, org_id: str, deal) -> bool:
    """The package rule every investor-facing route follows: published AND
    exactly what was last approved (wholesale_deal_package.serving)."""
    if not deal.buyer_room_published:
        return False
    try:
        from app.services import wholesale_deal_package as PKG
        return PKG.serving(db, org_id, deal)
    except Exception:  # noqa: BLE001 - fail closed
        return False


def active_hold(db, listing_id: str) -> Optional[ExclusiveHold]:
    return db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == listing_id,
                                          ExclusiveHold.status == HOLD_ACTIVE).first()


def _eligible(db, lst: DealListing, deal: WholesaleDeal, b: WholesaleBuyer, *, match: Optional[int]) -> bool:
    if lst.state not in VISIBLE_STATES or not _serving(db, lst.organization_id, deal):
        return False
    now = _now()
    if lst.publish_at and lst.publish_at > now or (lst.expires_at and lst.expires_at <= now):
        return False
    if bool(lst.is_test) != bool(b.is_test) or not b.is_active:
        return False
    if lst.audience == "selected":
        return b.id in _j(lst.buyer_ids, [])
    if lst.audience == "matched":
        return match is not None and match >= (lst.min_match_score or 0)
    return True


def _match_for(db, deal, b) -> Dict[str, Any]:
    from app.services import wholesale_matching as M
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    r = M.match_deal_to_buyers(deal, prop, [b], asking_price=deal.buyer_room_asking_price)
    if not r:
        return {"score": None, "factors": [], "disqualified": False}
    r = r[0]
    box = r.get("buy_box")
    missing = M.score_buy_box(deal, prop, box, deal.buyer_room_asking_price)["missing"] if box is not None else []
    return {"score": None if r["disqualified"] else r["score"], "disqualified": r["disqualified"],
            "reason": r["disqualified_reason"], "missing": missing,
            "factors": [{"matched": f.get("matched"), "detail": f.get("detail"), "dimension": f.get("dimension")}
                        for f in r["factors"]
                        if f.get("weight") or f.get("unknown")]}


def visible_listings(db, acct: InvestorAccount) -> List[Tuple[DealListing, WholesaleDeal, Dict[str, Any], Optional[ExclusiveHold]]]:
    sweep(db, acct.organization_id)
    b = buyer_of(db, acct)
    out = []
    rows = (db.query(DealListing, WholesaleDeal).join(WholesaleDeal, WholesaleDeal.id == DealListing.deal_id)
            .filter(DealListing.organization_id == acct.organization_id,
                    DealListing.state.in_(VISIBLE_STATES)).all())
    for lst, deal in rows:
        m = _match_for(db, deal, b)
        if not _eligible(db, lst, deal, b, match=m["score"]):
            continue
        hold = active_hold(db, lst.id)
        if hold is not None and hold.buyer_id != b.id:
            continue                                   # reserved for another investor: not shown, not reachable
        out.append((lst, deal, m, hold))
    return out


def get_visible(db, acct: InvestorAccount, listing_id: str):
    for lst, deal, m, hold in visible_listings(db, acct):
        if lst.id == listing_id:
            return lst, deal, m, hold
    raise HTTPException(status_code=404, detail="Opportunity not found")


def _hold_view(h: Optional[ExclusiveHold], mine: bool) -> Optional[Dict[str, Any]]:
    if h is None:
        return None
    left = max(0, int((h.ends_at - _now()).total_seconds())) if h.ends_at else None
    return {"status": h.status, "yours": mine, "starts_at": _iso(h.starts_at), "ends_at": _iso(h.ends_at),
            "seconds_left": left, "notice": HOLD_NOTICE}


def _my_outreach(db, deal, b) -> Optional[WholesaleBuyerOutreach]:
    return (db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.deal_id == deal.id,
                                                    WholesaleBuyerOutreach.buyer_id == b.id)
            .order_by(WholesaleBuyerOutreach.created_at.desc()).first())


def opportunity_card(db, acct, lst, deal, m, hold) -> Dict[str, Any]:
    from app.services import wholesale_publication as P
    b = buyer_of(db, acct)
    pay = P.buyer_room_payload(db, deal, outreach=_my_outreach(db, deal, b))
    photos = pay.get("photos") or []
    req = db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst.id, ExclusiveHold.buyer_id == b.id,
                                         ExclusiveHold.status == HOLD_REQUESTED).first()
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    return {"id": lst.id, "state": lst.state, "address": pay.get("property"),
            "category": getattr(prop, "property_category", None) or getattr(prop, "property_type", None),
            "facts": {k: getattr(prop, k, None) for k in ("bedrooms", "bathrooms", "square_feet", "year_built",
                                                          "unit_count", "acreage", "building_sqft")} if prop else {},
            "cover_photo": photos[0]["id"] if photos else None, "photo_count": len(photos),
            "asking_price": pay.get("asking_price"), "summary": pay.get("summary"),
            "match": {"score": m["score"], "missing": m.get("missing", [])[:3],
                      # WHY it matches: the names of the investor's own criteria this listing meets (no deal internals)
                      "why": [f["dimension"] for f in m.get("factors", []) if f.get("matched") and f.get("dimension")][:4]},
            "hold": _hold_view(hold, True) if hold is not None else None,
            "hold_requested": bool(req), "you": pay.get("you"), "expires_at": _iso(lst.expires_at),
            "published_at": _iso(lst.published_at), "is_test": bool(lst.is_test),
            "saved": db.query(DealRoomSave).filter(DealRoomSave.account_id == acct.id,
                                                   DealRoomSave.listing_id == lst.id).first() is not None,
            "arv": pay.get("arv"), "estimated_repairs": pay.get("estimated_repairs")}


def save_listing(db, acct, listing_id: str, saved: bool) -> Dict[str, Any]:
    lst, _deal, _m, _h = get_visible(db, acct, listing_id)
    row = db.query(DealRoomSave).filter(DealRoomSave.account_id == acct.id, DealRoomSave.listing_id == lst.id).first()
    if saved and row is None:
        db.add(DealRoomSave(organization_id=acct.organization_id, account_id=acct.id, listing_id=lst.id))
    elif not saved and row is not None:
        db.delete(row)
    db.flush()
    return {"saved": saved}


def my_offers(db, acct) -> List[Dict[str, Any]]:
    """The investor's OWN responses (interest, offers, passes) - still-visible deals
    link to their page; anything no longer shared shows only what they sent."""
    b = buyer_of(db, acct)
    visible = {deal.id: lst.id for lst, deal, _m, _h in visible_listings(db, acct)}
    rows = (db.query(WholesaleBuyerOutreach, WholesaleProperty)
            .join(WholesaleDeal, WholesaleDeal.id == WholesaleBuyerOutreach.deal_id)
            .outerjoin(WholesaleProperty, WholesaleProperty.id == WholesaleDeal.property_id)
            .filter(WholesaleBuyerOutreach.organization_id == acct.organization_id,
                    WholesaleBuyerOutreach.buyer_id == b.id, WholesaleBuyerOutreach.channel == "deal_room")
            .order_by(WholesaleBuyerOutreach.replied_at.desc()).all())
    out = []
    for o, p in rows:
        lid = visible.get(o.deal_id)
        out.append({"listing_id": lid, "still_available": lid is not None, "status": o.status,
                    "offer": float(o.offer_amount) if o.offer_amount is not None else None,
                    "financing": o.offer_financing, "note": o.response_note, "at": _iso(o.replied_at),
                    "address": (", ".join(x for x in (getattr(p, "street_address", None), getattr(p, "city", None)) if x)
                                if lid else "No longer shared")})
    return out


def my_holds(db, acct) -> List[Dict[str, Any]]:
    sweep(db, acct.organization_id)
    b = buyer_of(db, acct)
    visible = {lst.id: deal for lst, deal, _m, _h in visible_listings(db, acct)}
    out = []
    for h in (db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == acct.organization_id,
                                             ExclusiveHold.buyer_id == b.id)
              .order_by(ExclusiveHold.requested_at.desc()).all()):
        deal = visible.get(h.listing_id)
        prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first() if deal else None
        out.append({"id": h.id, "listing_id": h.listing_id if deal else None, "status": h.status,
                    "requested_at": _iso(h.requested_at), "starts_at": _iso(h.starts_at), "ends_at": _iso(h.ends_at),
                    "seconds_left": max(0, int((h.ends_at - _now()).total_seconds()))
                    if h.ends_at and h.status == HOLD_ACTIVE else None,
                    "end_reason": h.end_reason, "note": h.request_note,
                    "address": ", ".join(x for x in (getattr(prop, "street_address", None), getattr(prop, "city", None))
                                         if x) or ("No longer shared" if not deal else None)})
    return out


def opportunity_detail(db, acct, listing_id: str) -> Dict[str, Any]:
    from app.services import wholesale_publication as P
    lst, deal, m, hold = get_visible(db, acct, listing_id)
    b = buyer_of(db, acct)
    pay = P.buyer_room_payload(db, deal, outreach=_my_outreach(db, deal, b))
    pay.pop("deal_id", None)
    hidden = set(_j(lst.hidden_document_ids, []))
    pay["documents"] = [d for d in (pay.get("documents") or []) if d.get("id") not in hidden]
    card = opportunity_card(db, acct, lst, deal, m, hold)
    leak = P.leaked_keys(pay, P.FORBIDDEN_TO_BUYER)
    if leak:                                         # belt and braces: never serve a forbidden key
        raise HTTPException(status_code=500, detail="Withheld: publication boundary check failed.")
    sigs = [d for d in investor_documents(db, acct) if d.get("deal_id") == deal.id]
    return {**card, "room": pay, "match": m, "hold_notice": HOLD_NOTICE, "signatures": sigs,
            "can_submit": hold is None or hold.buyer_id == b.id}


def submit_interest(db, acct, listing_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Interest / offer / pass from the Deal Room lands on the EXISTING buyer
    board (wholesale_buyer_outreach), so disposition sees it where it always did."""
    from app.services.wholesale_analysis import money
    lst, deal, m, hold = get_visible(db, acct, listing_id)
    b = buyer_of(db, acct)
    if hold is not None and hold.buyer_id != b.id:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    kind = data.get("kind")
    if kind not in ("interested", "offer", "pass"):
        raise HTTPException(status_code=400, detail="kind must be interested, offer or pass")
    amount = money(data.get("amount")) if kind == "offer" else None
    if kind == "offer" and (amount is None or amount <= 0):
        raise HTTPException(status_code=400, detail="Enter your offer amount.")
    row = _my_outreach(db, deal, b)
    if row is None:
        row = WholesaleBuyerOutreach(organization_id=acct.organization_id, deal_id=deal.id, buyer_id=b.id,
                                     channel="deal_room", status="queued", subject="Deal Room")
        db.add(row)
    row.status = {"interested": "interested", "offer": "offer_submitted", "pass": "passed"}[kind]
    row.replied_at = _now()
    if amount is not None:
        row.offer_amount = amount
    fin = data.get("financing")
    if fin in ("cash", "hard_money", "private", "conventional", "mixed"):
        row.offer_financing = fin
    row.response_note = (data.get("note") or "")[:1000] or row.response_note
    row.respondent_name = acct.preferred_name or acct.legal_name
    b.last_interaction_at = _now()
    db.flush()
    log(db, acct.organization_id, "deal_room.%s" % kind, "%s: %s%s" % (
        b.company_name or b.contact_name, kind, (" $%s" % format(int(amount), ",")) if amount else ""),
        deal_id=deal.id, details={"buyer_id": b.id, "outreach_id": row.id})
    notify_staff(db, acct.organization_id, "Deal Room: %s - %s" % (b.company_name or b.contact_name, kind),
                 "/wholesale/deal-room?view=listings", assignee_id=acct.assigned_user_id, event="offer")
    return {"status": row.status, "offer": float(amount) if amount else None}


def request_hold(db, acct, listing_id: str, note: Optional[str]) -> Dict[str, Any]:
    from app.services import wholesale_exceptions as EX
    lst, deal, m, hold = get_visible(db, acct, listing_id)
    b = buyer_of(db, acct)
    if hold is not None:
        raise HTTPException(status_code=409, detail="This opportunity is already in an exclusive review%s."
                                                    % (" for you" if hold.buyer_id == b.id else ""))
    from app.services import wholesale_deal_room_setup as SET
    rules = SET.hold_rules(db, acct.organization_id)
    if rules["require_note"] and not (note or "").strip():
        raise HTTPException(status_code=400, detail="Tell the team briefly why you need the exclusive review.")
    if rules["require_pof"] and not profile(db, acct)["funding"]["documented"]:
        raise HTTPException(status_code=409, detail="Upload a current proof of funds first - the team accepts it "
                                                    "before approving exclusive reviews.")
    mine = db.query(ExclusiveHold).filter(ExclusiveHold.organization_id == acct.organization_id,
                                          ExclusiveHold.buyer_id == b.id, ExclusiveHold.listing_id != lst.id,
                                          ExclusiveHold.status.in_((HOLD_ACTIVE, HOLD_REQUESTED))).count()
    if mine >= rules["max_per_investor"]:
        raise HTTPException(status_code=409, detail="You can have %d exclusive review%s open at a time."
                                                    % (rules["max_per_investor"], "" if rules["max_per_investor"] == 1 else "s"))
    req = db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst.id, ExclusiveHold.buyer_id == b.id,
                                         ExclusiveHold.status == HOLD_REQUESTED).first()
    if req is None:
        req = ExclusiveHold(organization_id=acct.organization_id, listing_id=lst.id, deal_id=deal.id, buyer_id=b.id,
                            account_id=acct.id, status=HOLD_REQUESTED, request_note=(note or "")[:1000] or None,
                            is_test=bool(lst.is_test))
        db.add(req)
        db.flush()
    EX.raise_exception(db, acct.organization_id, kind="deal_room_hold_request", subject_type="deal",
                       subject_id=deal.id, title="Exclusive review requested by %s" % (b.company_name or b.contact_name),
                       detail=note, priority=15, source="deal_room", is_test=bool(lst.is_test))
    notify_staff(db, acct.organization_id, "Exclusive review requested: %s" % (b.company_name or b.contact_name),
                 "/wholesale/deal-room?view=holds", assignee_id=acct.assigned_user_id, event="hold_request")
    log(db, acct.organization_id, "deal_room.hold_requested", "Exclusive review requested", deal_id=deal.id,
        details={"hold_id": req.id, "buyer_id": b.id})
    return {"id": req.id, "status": req.status, "notice": HOLD_NOTICE}


def decide_hold(db, org_id: str, hold_id: str, user, *, decision: str, preset: Optional[str] = None,
                minutes: Optional[int] = None, note: Optional[str] = None) -> ExclusiveHold:
    """approve | decline | extend | release - atomic: the listing row is locked
    and the database refuses a second active hold on the same listing."""
    h = db.query(ExclusiveHold).filter(ExclusiveHold.id == hold_id, ExclusiveHold.organization_id == org_id).first()
    if h is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if getattr(user, "role", None) not in STAFF_ROLES and not _is_wholesale_manager(db, user):
        raise HTTPException(status_code=403, detail="Only an owner or manager approves exclusive reviews.")
    sweep(db, org_id)
    now = _now()
    lst = (db.query(DealListing).filter(DealListing.id == h.listing_id).with_for_update().first())
    if decision in ("approve", "extend"):
        from app.services import wholesale_deal_room_setup as SET
        rules = SET.hold_rules(db, org_id)
        if preset and rules["presets"] and preset not in rules["presets"]:
            raise HTTPException(status_code=400, detail="%s is not an allowed length in your Deal Room settings." % preset)
        if not preset and minutes and not rules["allow_custom"]:
            raise HTTPException(status_code=400, detail="Custom lengths are switched off in your Deal Room settings.")
        mins = HOLD_PRESETS.get(preset or "") or (int(minutes) if minutes else None)
        if not mins or mins <= 0 or mins > HOLD_MAX_MINUTES:
            raise HTTPException(status_code=400, detail="Choose 24h, 48h, 72h, 7d or a custom duration up to 30 days.")
    if decision == "approve":
        if h.status != HOLD_REQUESTED:
            raise HTTPException(status_code=409, detail="This request was already %s." % h.status)
        if lst is None or lst.state != "published":
            raise HTTPException(status_code=409, detail="The listing is not on the board (state: %s)."
                                                        % getattr(lst, "state", "missing"))
        if active_hold(db, lst.id) is not None:
            raise HTTPException(status_code=409, detail="Another exclusive review is already active on this deal.")
        h.status, h.decided_by_id, h.decided_at = HOLD_ACTIVE, user.id, now
        h.duration_minutes, h.starts_at, h.ends_at = mins, now, now + timedelta(minutes=mins)
        h.decision_note = (note or "")[:1000] or None
        lst.state = "exclusive_review"
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Another exclusive review was approved at the same moment.")
        what = "approved for %s" % _dur(mins)
    elif decision == "decline":
        if h.status != HOLD_REQUESTED:
            raise HTTPException(status_code=409, detail="This request was already %s." % h.status)
        h.status, h.decided_by_id, h.decided_at = HOLD_DECLINED, user.id, now
        h.decision_note = (note or "")[:1000] or None
        what = "declined"
    elif decision == "extend":
        if h.status != HOLD_ACTIVE:
            raise HTTPException(status_code=409, detail="Only an active review can be extended.")
        h.ends_at = h.ends_at + timedelta(minutes=mins)
        h.duration_minutes = (h.duration_minutes or 0) + mins
        h.extended_count = (h.extended_count or 0) + 1
        what = "extended by %s" % _dur(mins)
    elif decision == "release":
        if h.status != HOLD_ACTIVE:
            raise HTTPException(status_code=409, detail="Only an active review can be released.")
        h.status, h.ended_at, h.end_reason = HOLD_ENDED, now, "released"
        if lst is not None and lst.state == "exclusive_review":
            lst.state = "published"
        what = "released early"
    else:
        raise HTTPException(status_code=400, detail="decision must be approve, decline, extend or release")
    db.flush()
    log(db, org_id, "deal_room.hold_%s" % decision, "Exclusive review %s" % what, user, deal_id=h.deal_id,
        details={"hold_id": h.id, "buyer_id": h.buyer_id, "ends_at": _iso(h.ends_at)})
    return h


def _dur(mins: int) -> str:
    if mins % 1440 == 0 and mins >= 2880:
        return "%d days" % (mins // 1440)
    if mins % 60 == 0:
        return "%d hours" % (mins // 60)
    return "%d minutes" % mins


def _is_wholesale_manager(db, user) -> bool:
    try:
        from app.services import wholesale_team as T
        return T.role_of(db, user) == "manager"
    except Exception:  # noqa: BLE001
        return False


def _buyer_name(db, buyer_id):
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id).first()
    return getattr(b, "company_name", None) or getattr(b, "contact_name", None)


def hold_json(db, h: ExclusiveHold) -> Dict[str, Any]:
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == h.buyer_id).first()
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    prop = (db.query(WholesaleProperty).join(WholesaleDeal, WholesaleDeal.property_id == WholesaleProperty.id)
            .filter(WholesaleDeal.id == h.deal_id).first())
    return {"id": h.id, "listing_id": h.listing_id, "deal_id": h.deal_id, "buyer_id": h.buyer_id,
            "address": ", ".join(x for x in (getattr(prop, "street_address", None), getattr(prop, "city", None)) if x) or None,
            "buyer_name": getattr(b, "company_name", None) or getattr(b, "contact_name", None),
            "status": h.status, "request_note": h.request_note, "requested_at": _iso(h.requested_at),
            "decided_at": _iso(h.decided_at), "starts_at": _iso(h.starts_at), "ends_at": _iso(h.ends_at),
            "duration_minutes": h.duration_minutes, "end_reason": h.end_reason, "extended_count": h.extended_count,
            "is_test": bool(h.is_test),
            "seconds_left": max(0, int((h.ends_at - _now()).total_seconds())) if h.ends_at and h.status == HOLD_ACTIVE
            else None}


def listing_json(db, lst: DealListing) -> Dict[str, Any]:
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == getattr(deal, "property_id", None)).first()
    holds = db.query(ExclusiveHold).filter(ExclusiveHold.listing_id == lst.id).order_by(
        ExclusiveHold.requested_at.desc()).all()
    subs = db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.deal_id == lst.deal_id,
                                                   WholesaleBuyerOutreach.channel == "deal_room").all()
    return {"id": lst.id, "deal_id": lst.deal_id, "state": lst.state, "audience": lst.audience,
            "buyer_ids": _j(lst.buyer_ids, []), "min_match_score": lst.min_match_score,
            "publish_at": _iso(lst.publish_at), "expires_at": _iso(lst.expires_at),
            "approved_at": _iso(lst.approved_at), "published_at": _iso(lst.published_at),
            "content_approved": bool(getattr(deal, "buyer_room_published", False)),
            "address": ", ".join(x for x in (getattr(prop, "street_address", None), getattr(prop, "city", None),
                                             getattr(prop, "zip_code", None)) if x),
            "holds": [hold_json(db, h) for h in holds],
            "submissions": [{"buyer_id": s.buyer_id, "status": s.status, "buyer_name": _buyer_name(db, s.buyer_id),
                             "offer": float(s.offer_amount) if s.offer_amount is not None else None,
                             "at": _iso(s.replied_at)} for s in subs],
            "is_test": bool(lst.is_test)}


def audience_preview(db, org_id: str, lst: DealListing) -> List[Dict[str, Any]]:
    """Which investors with Deal Room access would see this listing (staff view)."""
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == lst.deal_id).first()
    out = []
    for a in db.query(InvestorAccount).filter(InvestorAccount.organization_id == org_id,
                                              InvestorAccount.status == "active").all():
        b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == a.buyer_id).first()
        if b is None:
            continue
        m = _match_for(db, deal, b)
        probe = DealListing(state="published", audience=lst.audience, buyer_ids=lst.buyer_ids,
                            min_match_score=lst.min_match_score, is_test=lst.is_test)
        ok = _eligible(db, probe, deal, b, match=m["score"]) if deal.buyer_room_published else False
        out.append({"buyer_id": b.id, "name": b.company_name or b.contact_name, "match": m["score"], "eligible": ok})
    return out


# ═══════════════════════════════ files ══════════════════════════════════════

CATEGORIES = ("proof_of_funds", "identity", "entity_documents", "rent_roll", "offer", "other")


def max_file_bytes() -> int:
    from app.services.wholesale_files import MAX_UPLOAD_BYTES
    try:
        mb = float(os.environ.get("DEAL_ROOM_MAX_FILE_MB", "25"))
    except ValueError:
        mb = 25.0
    return int(min(mb * 1048576, MAX_UPLOAD_BYTES))


def buyer_quota_bytes() -> int:
    try:
        return int(float(os.environ.get("DEAL_ROOM_BUYER_QUOTA_MB", "250")) * 1048576)
    except ValueError:
        return 250 * 1048576


_PDF_BAD = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)\b")
_ZIP_BAD = re.compile(r"(vbaProject\.bin|\.(exe|dll|bat|cmd|com|js|vbs|scr|ps1|jar|msi)$)", re.I)


def content_check(data: bytes, content_type: str) -> Tuple[str, str]:
    """Basic content checks - NOT an antivirus engine (none is connected), and
    the status says so. Blocks active content: scripts or launch actions in
    PDFs, macros or executables inside Office files."""
    import io
    import zipfile
    if content_type == "application/pdf":
        hit = _PDF_BAD.search(data)
        if hit:
            return "blocked", "PDF contains active content (%s)" % hit.group(1).decode()
        return "clean", "Basic content checks passed (no antivirus engine connected)"
    if content_type.startswith("application/vnd.openxmlformats"):
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "blocked", "Not a valid Office file"
        bad = [n for n in names if _ZIP_BAD.search(n)]
        if bad:
            return "blocked", "Office file contains macros or executables (%s)" % bad[0]
        return "clean", "Basic content checks passed (no antivirus engine connected)"
    if content_type == "application/msword":
        return "blocked", "Legacy .doc files can carry macros - send a PDF or .docx"
    return "clean", "Image type verified from its bytes (no antivirus engine connected)"


def store_upload(db, org_id: str, *, buyer: WholesaleBuyer, data: bytes, content_type: str, filename: str,
                 kind: str, category: Optional[str], deal_id: Optional[str], by_account: Optional[InvestorAccount],
                 by_user=None, investor_visible: bool = True) -> WholesaleFile:
    from sqlalchemy import func

    from app.services import wholesale_files as F
    if len(data) > max_file_bytes():
        raise HTTPException(status_code=413, detail="That file is %.1f MB. The Deal Room limit is %d MB."
                                                    % (len(data) / 1048576.0, max_file_bytes() // 1048576))
    used = db.query(func.coalesce(func.sum(WholesaleFile.byte_size), 0)).filter(
        WholesaleFile.organization_id == org_id, WholesaleFile.buyer_id == buyer.id).scalar() or 0
    if used + len(data) > buyer_quota_bytes():
        raise HTTPException(status_code=413, detail="This buyer's file space (%d MB) is full."
                                                    % (buyer_quota_bytes() // 1048576))
    scan, detail = content_check(data, content_type)
    if scan == "blocked":
        log(db, org_id, "deal_room.upload_blocked", "Upload refused: %s" % detail, by_user,
            details={"buyer_id": buyer.id, "filename": filename})
        raise HTTPException(status_code=400, detail="That file was refused: %s." % detail)
    stored = F.put(org_id, kind, data, content_type, db=db)
    f = WholesaleFile(organization_id=org_id, buyer_id=buyer.id, deal_id=deal_id, kind=kind,
                      storage_backend=stored["storage_backend"], storage_key=stored["storage_key"],
                      original_filename=filename, content_type=content_type, byte_size=len(data),
                      checksum=hashlib.sha256(data).hexdigest(), category=(category if category in CATEGORIES
                                                                          else "other"),
                      uploaded_by_investor_id=getattr(by_account, "id", None),
                      uploaded_by_id=getattr(by_user, "id", None), investor_visible=investor_visible,
                      review_status="pending" if by_account is not None else "accepted",
                      scan_status=scan, scan_detail=detail[:250])
    db.add(f)
    db.flush()
    return f


def file_json(f: WholesaleFile) -> Dict[str, Any]:
    return {"id": f.id, "name": f.original_filename, "content_type": f.content_type, "size": f.byte_size,
            "category": f.category, "kind": f.kind, "deal_id": f.deal_id,
            "from_you": bool(f.uploaded_by_investor_id), "review_status": f.review_status,
            "scan_status": f.scan_status, "scan_detail": f.scan_detail, "uploaded_at": _iso(f.created_at)}


def investor_files(db, acct) -> List[WholesaleFile]:
    b = buyer_of(db, acct)
    return (db.query(WholesaleFile).filter(WholesaleFile.organization_id == acct.organization_id,
                                           WholesaleFile.buyer_id == b.id,
                                           WholesaleFile.investor_visible.is_(True))
            .order_by(WholesaleFile.created_at.desc()).all())


def investor_file(db, acct, file_id: str) -> WholesaleFile:
    for f in investor_files(db, acct):
        if f.id == file_id:
            return f
    raise HTTPException(status_code=404, detail="File not found")


# ═══════════════════════════════ signatures ═════════════════════════════════

def investor_documents(db, acct) -> List[Dict[str, Any]]:
    from app.models.esign_models import EsignEnvelope, EsignSigner
    from app.services import evosys_esign as EV
    u = db.query(User).filter(User.id == acct.user_id).first()
    email = (u.email or "").lower()
    b = buyer_of(db, acct)
    rows = (db.query(EsignSigner, EsignEnvelope).join(EsignEnvelope, EsignEnvelope.id == EsignSigner.envelope_id)
            .filter(EsignSigner.organization_id == acct.organization_id, EsignSigner.email == email).all())
    out = []
    for s, env in rows:
        doc = db.query(WholesaleDocument).filter(WholesaleDocument.id == env.document_id).first() if env.document_id else None
        if doc is not None and doc.buyer_id and doc.buyer_id != b.id:
            continue
        signers = EV.signers_of(db, env)
        current = EV._current(signers)
        out.append({"envelope_id": env.id, "document_id": env.document_id, "title": env.title, "status": env.status,
                    "your_role": s.role, "your_status": s.status, "signed_at": _iso(s.signed_at),
                    "your_turn": current is not None and current.id == s.id and env.status == "sent",
                    "parties": [{"role": x.role, "status": x.status} for x in signers],
                    "completed": env.status == "completed", "expires_at": _iso(env.expires_at),
                    "deal_id": env.deal_id, "sha256": env.document_sha256})
    return out


def signing_session(db, acct, envelope_id: str) -> Dict[str, Any]:
    from app.models.esign_models import EsignEnvelope, EsignSigner
    from app.services import evosys_esign as EV
    u = db.query(User).filter(User.id == acct.user_id).first()
    env = db.query(EsignEnvelope).filter(EsignEnvelope.id == envelope_id,
                                         EsignEnvelope.organization_id == acct.organization_id).first()
    if env is None:
        raise HTTPException(status_code=404, detail="Document not found")
    s = db.query(EsignSigner).filter(EsignSigner.envelope_id == env.id,
                                     EsignSigner.email == (u.email or "").lower()).first()
    if s is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if not acct.email_verified_at:
        raise HTTPException(status_code=403, detail="Verify your email first.")
    try:
        token = EV.session_link(db, env, s, verified_by="Deal Room sign-in (email verified %s)"
                                % acct.email_verified_at.date())
    except EV.EsignError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return {"url": "/sign/%s" % token, "token": token}


# ═══════════════════════════════ staff views ════════════════════════════════

def accounts_for_buyer(db, org_id: str, buyer_id: str) -> List[Dict[str, Any]]:
    rows = (db.query(InvestorAccount, User).join(User, User.id == InvestorAccount.user_id)
            .filter(InvestorAccount.organization_id == org_id, InvestorAccount.buyer_id == buyer_id).all())
    return [{"id": a.id, "name": a.preferred_name or a.legal_name or u.full_name, "email": u.email, "status": a.status,
             "kind": a.kind, "company": a.company_legal_name, "identity_complete": bool(a.identity_completed_at),
             "email_verified": bool(a.email_verified_at), "last_seen_at": _iso(a.last_seen_at)} for a, u in rows]


def set_account_status(db, org_id: str, account_id: str, status: str, user) -> InvestorAccount:
    a = db.query(InvestorAccount).filter(InvestorAccount.id == account_id,
                                         InvestorAccount.organization_id == org_id).first()
    if a is None:
        raise HTTPException(status_code=404, detail="Investor not found")
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status must be active or disabled")
    a.status = status
    if status == "disabled":
        from app.services import session_service
        try:
            session_service.revoke_all_for_user(db, a.user_id)
        except Exception:  # noqa: BLE001 - access is refused by the account status anyway
            pass
    log(db, org_id, "deal_room.account_%s" % status, "Deal Room access %s" % status, user)
    return a
