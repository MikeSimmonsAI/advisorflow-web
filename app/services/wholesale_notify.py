"""Tell the people who work a Wholesale workspace that a seller needs them.

Before this, a public seller inquiry created a property, a deal and a Lead and
told nobody: the records were unassigned, so even the platform's hot-reply
alert (which goes to `lead.assigned_to`) never fired for that seller.

WHO IS TOLD
  1. the configured inquiry assignee (`WholesaleSettings.inquiry_assignee_id`)
     when that is an active user of THIS organization; otherwise
  2. the organization's own workspace admins.
Platform staff (god/super admins) are never recipients: their access to a
tenant is support access, not membership, and a tenant's seller is not theirs
to be paged about.

HOW
  An in-app Notification (the bell), with `link` pointing at the deal. Nothing
  is emailed or texted from here. Creating a notification can never fail the
  request that caused it: it runs in a savepoint and a failure is logged.
"""
from __future__ import annotations

import logging
from typing import List, Optional

log = logging.getLogger(__name__)

TENANT_ADMIN_ROLES = ("org_admin", "admin", "owner")


def recipients(db, org_id: str, assignee_id: Optional[str] = None) -> List[str]:
    from app.models.models import User
    if assignee_id:
        u = (db.query(User).filter(User.id == assignee_id, User.organization_id == org_id,
                                   User.is_active.isnot(False)).first())
        if u is not None:
            return [u.id]
    rows = (db.query(User.id).filter(User.organization_id == org_id,
                                     User.role.in_(TENANT_ADMIN_ROLES),
                                     User.is_active.isnot(False)).all())
    return [r[0] for r in rows]


def notify(db, org_id: str, *, kind, message: str, lead_id: Optional[str] = None,
           link: Optional[str] = None, assignee_id: Optional[str] = None) -> int:
    """Create one bell notification per recipient. Returns how many."""
    from app.models.models import Notification
    try:
        users = recipients(db, org_id, assignee_id)
        if not users:
            return 0
        with db.begin_nested():
            for uid in users:
                db.add(Notification(user_id=uid, lead_id=lead_id, type=kind,
                                    message=message[:1000], link=link))
        return len(users)
    except Exception:  # noqa: BLE001 - a missing badge must never lose an inquiry
        log.exception("wholesale notify failed for org %s", org_id)
        return 0


def deal_link(deal_id: Optional[str]) -> Optional[str]:
    return ("/wholesale/deals/%s" % deal_id) if deal_id else None
