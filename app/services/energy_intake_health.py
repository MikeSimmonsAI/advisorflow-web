# -*- coding: utf-8 -*-
"""Manager view: are new Atlantis requests being picked up, and how fast?

Read-only. It counts only what the system recorded and does not estimate:

* unassigned - live leads still in New Rate Requests (tier new_inquiry) with no
  owner. Test records, do-not-contact and dead leads are left out.
* first contact - every website request and every field referral opens one
  first-contact task ("Contact website request" / "Contact field referral").
  "Time to first contact" runs from the moment that task was created to the
  moment a person marked it done. It measures when staff RECORDED the contact,
  not when a call actually happened, and the response says so.

Scope is the caller's lead scope (managers only - the router checks).
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_

from app.models.models import Lead, User
from app.models.work_models import LeadTask
from app.services.agency.common import CLOSED_LEAD_STATUSES

FIRST_CONTACT = {"website": "Website requests", "field_referral": "Field referrals"}
BASIS = ("Time to first contact = from the request's first-contact task being created to a person "
         "marking it done. It reflects when the contact was recorded, not a verified call time. "
         "Test records are excluded.")
# Platform #238 (open): a website request's stored time is taken from the
# visitor's browser and its time-zone offset is dropped, so website request
# ages and "time to first contact" can be off by the visitor's UTC offset.
# Until #238 is fixed and verified these numbers are PROVISIONAL.
TIMING_STATUS = "provisional"
TIMING_NOTE = ("Provisional: website request times can be off by a few hours until a shared "
               "time-stamp fix (Platform #238) is verified. Counts are exact; hours are approximate.")


def _not_test():
    return or_(Lead.is_test.is_(False), Lead.is_test.is_(None))


def _hours(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return max((b - a).total_seconds() / 3600.0, 0.0)


def _rank(vals: List[float], p: float) -> Optional[float]:
    """Nearest-rank percentile of already-sorted values (no interpolation)."""
    if not vals:
        return None
    k = max(int(-(-p * len(vals) // 1)) - 1, 0)          # ceil(p*n) - 1
    return round(vals[min(k, len(vals) - 1)], 1)


def _name(l: Lead) -> str:
    return " ".join(x for x in (l.first_name, l.last_name) if x) or (l.email or l.phone or "Unnamed")


def report(db, *, org_id: str, scoped, now: datetime, days: int) -> Dict[str, Any]:
    since = now - timedelta(days=days)

    unassigned_q = (db.query(Lead)
                    .filter(Lead.organization_id == org_id, Lead.id.in_(scoped), _not_test(),
                            Lead.assigned_to_id.is_(None), Lead.tier == "new_inquiry",
                            or_(Lead.status.is_(None), ~Lead.status.in_(CLOSED_LEAD_STATUSES))))
    oldest = unassigned_q.order_by(Lead.created_at.asc(), Lead.id.asc()).limit(25).all()

    rows = (db.query(LeadTask, Lead)
            .join(Lead, Lead.id == LeadTask.lead_id)
            .filter(LeadTask.organization_id == org_id, LeadTask.source.in_(tuple(FIRST_CONTACT)),
                    LeadTask.created_at >= since, Lead.id.in_(scoped), _not_test())
            .all())

    channels = []
    owners: Dict[Optional[str], Dict[str, int]] = {}
    for src, label in FIRST_CONTACT.items():
        mine = [(t, l) for t, l in rows if t.source == src]
        done = sorted(h for h in (_hours(t.created_at, t.completed_at)
                                  for t, _ in mine if t.status == "done") if h is not None)
        open_ = [(t, l) for t, l in mine if t.status == "open"]
        for t, _ in open_:
            o = owners.setdefault(t.assigned_to_id, {"open": 0, "overdue": 0})
            o["open"] += 1
            if t.due_at is not None and t.due_at < now:
                o["overdue"] += 1
        oldest_open = max((_hours(t.created_at, now) or 0.0 for t, _ in open_), default=None)
        channels.append({
            "source": src, "label": label,
            "requests": sum(1 for t, _ in mine if t.status != "cancelled"),   # cancelled shown separately
            "contacted": len(done), "open": len(open_),
            "overdue": sum(1 for t, _ in open_ if t.due_at is not None and t.due_at < now),
            "cancelled": sum(1 for t, _ in mine if t.status == "cancelled"),
            "median_hours": _rank(done, 0.5), "p90_hours": _rank(done, 0.9),
            "oldest_open_hours": round(oldest_open, 1) if oldest_open is not None else None,
        })

    ids = [i for i in owners if i]
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
    by_owner = sorted(({"owner_id": k, "owner": names.get(k) if k else "Unassigned", **v}
                       for k, v in owners.items()), key=lambda r: (-r["overdue"], -r["open"], r["owner"] or ""))

    return {
        "days": days, "since": since.isoformat() + "Z", "as_of": now.isoformat() + "Z",
        "unassigned": {
            "count": unassigned_q.count(),
            "oldest": [{"lead_id": l.id, "name": _name(l), "source": l.source,
                        "created_at": l.created_at.isoformat() + "Z" if l.created_at else None,
                        "age_hours": round(_hours(l.created_at, now), 1) if l.created_at else None,
                        "link": "/leads/%s" % l.id} for l in oldest],
        },
        "first_contact": channels,
        "open_by_owner": by_owner,
        "basis": BASIS,
        "timing_status": TIMING_STATUS,
        "timing_note": TIMING_NOTE,
    }
