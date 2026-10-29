"""God-granted demo previews - one person, one deal, revocable, audited.

The rules live here so the launcher card, the open route and the God screens
can never disagree about whether a grant is usable:

  * the grant names the caller, is not revoked and has not expired;
  * the caller is an ordinary login (never god/super admin) who still belongs
    to the brand the grant was made for;
  * the deal is still OPEN and pre-won, has no customer workspace, and still
    has a live demo link.

Nothing here creates a membership, a workspace, a platform-login right or a
sales seat. Nothing here ever returns the demo's token or public address.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.demo_site_models import DealPreviewGrant, DemoSite
from app.models.models import Platform, User

PREVIEW_LABEL = "Authorized preview"

# Pre-won stages, the same set the brand's own launcher previews use (#397).
OPEN_STAGES = ("prospect", "contacted", "discovery", "demo_build",
               "demo_proposal", "closing")

# Logins that may never be grantees: they already have root authority, and a
# grant row for them would only muddy the audit trail.
_NOT_GRANTEES = ("god_admin", "super_admin")


class GrantError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _platform_slug(db: Session, platform_id: str) -> Optional[str]:
    row = db.query(Platform.slug).filter(Platform.id == platform_id).first()
    return row[0] if row else None


def user_on_platform(db: Session, user: User, platform_id: str) -> bool:
    """Does this login currently belong to that brand (any real source)?"""
    from app.services.workspace_access import user_authorized_platform_slugs
    slug = _platform_slug(db, platform_id)
    return bool(slug) and slug in user_authorized_platform_slugs(user, db)


def _open_deal(db: Session, opportunity_id: str):
    from app.models.sales_models import Opportunity
    opp = db.query(Opportunity).filter(Opportunity.id == opportunity_id).first()
    if opp is None:
        return None
    if (opp.status or "") != "open" or opp.stage not in OPEN_STAGES:
        return None
    if opp.customer_organization_id is not None:
        return None
    return opp


def live_demo(db: Session, opportunity_id: str, slot: Optional[str] = None,
              now: Optional[datetime] = None) -> Optional[DemoSite]:
    """The deal's live demo - the product walkthrough first, unless a slot is asked for."""
    from app.services import demo_sites as _demos
    now = now or datetime.utcnow()
    rows = (db.query(DemoSite).filter(DemoSite.opportunity_id == opportunity_id)
              .order_by(DemoSite.created_at.desc()).all())
    live = [d for d in rows if d.is_live(now)]
    if slot:
        want = _demos.normalize_slot(slot)
        return next((d for d in live if (d.slot or _demos.DEFAULT_SLOT) == want), None)
    return (next((d for d in live if (d.slot or _demos.DEFAULT_SLOT) == _demos.DEFAULT_SLOT), None)
            or (live[0] if live else None))


def usable(db: Session, grant: Optional[DealPreviewGrant], user: User,
           now: Optional[datetime] = None) -> Optional[Tuple[Any, DemoSite]]:
    """(opportunity, demo) when this caller may open this grant right now, else None."""
    now = now or datetime.utcnow()
    if grant is None or user is None or not getattr(user, "is_active", False):
        return None
    if grant.grantee_user_id != user.id or not grant.is_active(now):
        return None
    if getattr(user, "role", None) in _NOT_GRANTEES:
        return None
    if not user_on_platform(db, user, grant.grantee_platform_id):
        return None
    opp = _open_deal(db, grant.opportunity_id)
    if opp is None:
        return None
    demo = live_demo(db, opp.id, now=now)
    if demo is None:
        return None
    return opp, demo


def launcher_entries(db: Session, user: User,
                     skip_opportunity_ids=()) -> List[Dict[str, Any]]:
    """Program Access cards for this caller.

    The payload is deliberately tiny: the grant id, the deal's display name,
    the label and the signed-in path. No token, no public URL, no deal id."""
    now = datetime.utcnow()
    out: List[Dict[str, Any]] = []
    if getattr(user, "role", None) in _NOT_GRANTEES:
        return out
    grants = (db.query(DealPreviewGrant)
                .filter(DealPreviewGrant.grantee_user_id == user.id,
                        DealPreviewGrant.revoked_at.is_(None))
                .order_by(DealPreviewGrant.created_at.asc()).all())
    seen = set(skip_opportunity_ids or ())
    for g in grants:
        if g.opportunity_id in seen:
            continue
        ok = usable(db, g, user, now)
        if ok is None:
            continue
        opp, _demo = ok
        seen.add(g.opportunity_id)
        out.append({
            "type": "preview",
            "grant_id": g.id,
            "title": opp.company_name,
            "label": PREVIEW_LABEL,
            "path": "/previews/%s" % g.id,
        })
    out.sort(key=lambda x: (x["title"] or "").lower())
    return out


def create(db: Session, actor: User, *, opportunity_id: str, grantee_user_id: str,
           platform_id: str, reason: str, expires_at: Optional[datetime] = None,
           now: Optional[datetime] = None) -> DealPreviewGrant:
    """Validate and add a grant. The caller (a God-only route) commits and audits."""
    now = now or datetime.utcnow()
    reason = (reason or "").strip()
    if not reason:
        raise GrantError(422, "Say why this person needs the preview.")
    if expires_at is not None and expires_at <= now:
        raise GrantError(422, "The end date must be in the future.")
    grantee = db.query(User).filter(User.id == grantee_user_id).first()
    if grantee is None or not grantee.is_active:
        raise GrantError(404, "That login was not found or is not active.")
    if grantee.role in _NOT_GRANTEES:
        raise GrantError(422, "God and super admin logins cannot be given a preview.")
    if _platform_slug(db, platform_id) is None:
        raise GrantError(404, "That brand was not found.")
    if not user_on_platform(db, grantee, platform_id):
        raise GrantError(422, "That login does not belong to that brand.")
    opp = _open_deal(db, opportunity_id)
    if opp is None:
        raise GrantError(404, "That deal was not found or is no longer open.")
    if live_demo(db, opp.id, now=now) is None:
        raise GrantError(422, "That deal has no live demo to preview.")
    dup = (db.query(DealPreviewGrant)
             .filter(DealPreviewGrant.grantee_user_id == grantee.id,
                     DealPreviewGrant.opportunity_id == opp.id,
                     DealPreviewGrant.revoked_at.is_(None)).all())
    if any(g.is_active(now) for g in dup):
        raise GrantError(409, "This person already has an active preview of that deal.")
    row = DealPreviewGrant(opportunity_id=opp.id, grantee_user_id=grantee.id,
                           grantee_platform_id=platform_id, granted_by=actor.id,
                           reason=reason, created_at=now, expires_at=expires_at)
    db.add(row)
    db.flush()
    return row


def revoke(db: Session, grant: DealPreviewGrant, actor: User,
           now: Optional[datetime] = None) -> None:
    grant.revoked_at = now or datetime.utcnow()
    grant.revoked_by = actor.id


def out(db: Session, g: DealPreviewGrant) -> Dict[str, Any]:
    """God's view of a grant. Still no token or public address."""
    from app.models.sales_models import Opportunity
    opp = db.query(Opportunity).filter(Opportunity.id == g.opportunity_id).first()
    grantee = db.query(User).filter(User.id == g.grantee_user_id).first()
    return {
        "id": g.id,
        "opportunity_id": g.opportunity_id,
        "company_name": getattr(opp, "company_name", None),
        "grantee_user_id": g.grantee_user_id,
        "grantee_email": getattr(grantee, "email", None),
        "grantee_platform_id": g.grantee_platform_id,
        "granted_by": g.granted_by,
        "reason": g.reason,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "revoked_by": g.revoked_by,
        "active": g.is_active(),
    }
