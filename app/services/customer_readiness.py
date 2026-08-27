"""WHAT IS ACTUALLY CONFIGURED — observed, never asserted.

The provisioning summary is the screen an operator trusts before telling a
customer they are live. So it may only report what it can see.

The rule from the mission, and it is the right rule:

    Do not display CONNECTED / HEALTHY / ACTIVE / SYNCED unless the backend can
    actually verify it. If there is no real source: NOT CONFIGURED / NO SOURCE /
    NOT VERIFIED.

Two words this module will not use, and why:

HEALTHY. Nothing here calls Twilio, Resend, Google or Retell. A stored
credential means somebody typed something into a box, not that it works. Saying
CONFIGURED is honest; saying HEALTHY would be a claim about a live service this
code has not spoken to.

CONNECTED, for anything but a calendar. A calendar connection has a real
`is_connected` flag written by an OAuth callback that actually completed, so
that one word is earned. Twilio and email are credentials at rest, so they get
CONFIGURED.

Everything reports the reason it says what it says, so an operator reading
"NOT_CONFIGURED" can see which field is empty rather than guessing.
"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.location_models import Location
from app.services import entitlements
from app.services.customer_provisioning import (
    ST_CONFIGURED, ST_NOT_CONFIGURED, ST_PARTIAL, ST_NONE, list_locations,
)


def _twilio(db: Session, org: Organization) -> Dict[str, Any]:
    sid = getattr(org, "org_twilio_account_sid", None)
    tok = getattr(org, "org_twilio_auth_token_encrypted", None)
    num = getattr(org, "org_twilio_phone_number", None)
    if sid and tok and num:
        st, why = ST_CONFIGURED, "Account SID, auth token and phone number are stored."
    elif sid or tok or num:
        st, why = ST_PARTIAL, "Some Twilio fields are stored and some are missing."
    else:
        st, why = ST_NOT_CONFIGURED, "No org-level Twilio credentials are stored."
    return {
        "status": st, "reason": why,
        "account_sid_present": bool(sid), "auth_token_present": bool(tok),
        "phone_number": num,
        # Said plainly so no screen can imply otherwise.
        "verified_against_provider": False,
        "note": "Credentials are stored, not tested. Nothing here has called Twilio.",
    }


def _email(db: Session, org: Organization) -> Dict[str, Any]:
    frm = getattr(org, "from_email", None)
    key = getattr(org, "resend_api_key", None)
    if frm and key:
        st, why = ST_CONFIGURED, "A sender address and an org API key are stored."
    elif frm or key:
        st, why = ST_PARTIAL, ("A sender address is set but no org API key, so this "
                               "customer sends on the shared platform key."
                               if frm else
                               "An API key is stored but no sender address is set.")
    else:
        st, why = ST_NOT_CONFIGURED, ("No sender address and no org API key. Outbound "
                                      "email would fall back to the shared platform "
                                      "sender.")
    return {"status": st, "reason": why, "from_email": frm,
            "org_api_key_present": bool(key), "verified_against_provider": False}


def _calendar(db: Session, org: Organization) -> Dict[str, Any]:
    """Real connection state — this one is observed, not inferred."""
    users = db.query(User).filter(User.organization_id == org.id,
                                  User.is_active == True).all()  # noqa: E712
    total = len(users)
    connected = []
    for u in users:
        g = bool(getattr(u, "google_calendar_connected", False))
        m = bool(getattr(u, "microsoft_365_connected", False))
        if g or m:
            connected.append({"user_id": u.id, "name": u.full_name,
                              "google": g, "microsoft": m})
    n = len(connected)
    if total == 0:
        st, why = ST_NONE, "This customer has no active users yet."
    elif n == 0:
        st, why = ST_NOT_CONFIGURED, "No user has connected a calendar."
    elif n < total:
        st, why = ST_PARTIAL, "%d of %d users have connected a calendar." % (n, total)
    else:
        st, why = ST_CONFIGURED, "All %d users have connected a calendar." % total
    return {"status": st, "reason": why, "connected_count": n,
            "user_count": total, "connected": connected}


def _ai(db: Session, org: Organization) -> Dict[str, Any]:
    """There is no per-organization AI configuration in this schema.

    Reported as MISSING rather than dressed up. Every AI call in the codebase
    uses one global OPENAI_API_KEY, so there is nothing customer-specific to
    show and pretending otherwise would put a green tick on a capability that
    does not exist per tenant.
    """
    return {
        "status": ST_NOT_CONFIGURED,
        "reason": "No per-organization AI configuration exists in this schema. AI "
                  "features run on a single platform-wide key.",
        "per_org_config_supported": False,
        "verified_against_provider": False,
    }


def _booking(db: Session, org: Organization) -> Dict[str, Any]:
    locs = db.query(Location).filter(Location.organization_id == org.id,
                                     Location.is_active == True).all()  # noqa: E712
    with_hours = [l for l in locs if l.operating_hours]
    if not locs:
        st, why = ST_NONE, "No locations exist, so there is nowhere to route a booking."
    elif not with_hours:
        st, why = ST_NOT_CONFIGURED, "No location has operating hours configured."
    elif len(with_hours) < len(locs):
        st, why = ST_PARTIAL, ("%d of %d locations have operating hours."
                               % (len(with_hours), len(locs)))
    else:
        st, why = ST_CONFIGURED, "All %d locations have operating hours." % len(locs)
    return {"status": st, "reason": why,
            "location_count": len(locs), "with_hours": len(with_hours)}


def readiness(db: Session, org: Organization) -> Dict[str, Any]:
    """The provisioning summary. Every line is something the backend can see."""
    users = customer_user_counts(db, org.id)
    locs = list_locations(db, org.id)
    feats = entitlements.feature_report(org)

    company_ready = bool(org.name and org.slug and org.platform_id)

    sections = {
        "company": {
            "status": ST_CONFIGURED if company_ready else ST_PARTIAL,
            "reason": ("Name, slug and brand are set." if company_ready else
                       "A customer must have a name, a slug and a brand."),
            "name": org.name, "slug": org.slug, "platform_id": org.platform_id,
            "industry": org.industry, "plan": org.plan,
            "is_active": bool(org.is_active),
        },
        "locations": {
            "status": ST_CONFIGURED if locs else ST_NONE,
            "reason": ("%d location(s)." % len(locs)) if locs
                      else "No locations have been created.",
            "count": len(locs),
            "primary": next((l["name"] for l in locs if l["is_primary"]), None),
        },
        "users": {
            "status": (ST_CONFIGURED if users["active"] else ST_NONE),
            "reason": ("%d active user(s), %d still to accept their invitation."
                       % (users["active"], users["pending"])) if users["active"]
                      else "This customer has no user accounts yet.",
            **users,
        },
        "features": {
            "status": ST_CONFIGURED if feats["enabled_count"] else ST_NONE,
            "reason": ("%d feature(s) enabled." % feats["enabled_count"])
                      if feats["enabled_count"] else "No features are enabled.",
            **feats,
        },
        "communications_sms": _twilio(db, org),
        "communications_email": _email(db, org),
        "calendar": _calendar(db, org),
        "booking": _booking(db, org),
        "ai": _ai(db, org),
        "data": data_state(db, org),
    }

    # What genuinely blocks calling this customer live. Deliberately short: a
    # blocker list that includes everything optional trains people to ignore it.
    blockers: List[str] = []
    if not company_ready:
        blockers.append("The company record is incomplete.")
    if not users["active"]:
        blockers.append("No active user account — nobody can log in.")
    if not locs:
        blockers.append("No location — bookings have nowhere to route.")
    if not feats["enabled_count"]:
        blockers.append("No features are enabled — the customer would see an empty app.")

    warnings: List[str] = []
    if users["pending"]:
        warnings.append("%d invitation(s) not yet accepted." % users["pending"])
    if sections["communications_sms"]["status"] != ST_CONFIGURED:
        warnings.append("SMS is not fully configured.")
    if sections["communications_email"]["status"] != ST_CONFIGURED:
        warnings.append("Email falls back to the shared platform sender.")
    if sections["calendar"]["status"] not in (ST_CONFIGURED,):
        warnings.append("Not every user has connected a calendar.")

    return {
        "organization_id": org.id,
        "name": org.name,
        "sections": sections,
        "blockers": blockers,
        "warnings": warnings,
        "can_activate": not blockers,
    }


def customer_user_counts(db: Session, org_id: str) -> Dict[str, int]:
    users = db.query(User).filter(User.organization_id == org_id).all()
    return {
        "total": len(users),
        "active": sum(1 for u in users if u.is_active),
        # "Pending" means the account exists but has never been signed into.
        # Observed from last_login_at, not from an invitation status somebody
        # forgot to update.
        "pending": sum(1 for u in users if u.is_active and u.last_login_at is None),
        "admins": sum(1 for u in users if u.role == "org_admin" and u.is_active),
    }


def data_state(db: Session, org: Organization) -> Dict[str, Any]:
    from app.models.models import Lead
    total = db.query(Lead).filter(Lead.organization_id == org.id).count()
    test = (db.query(Lead)
            .filter(Lead.organization_id == org.id, Lead.is_test.is_(True)).count())
    real = total - test
    if total == 0:
        st, why = ST_NONE, "No records imported."
    elif real == 0:
        st, why = ST_PARTIAL, "%d record(s), all flagged as test data." % total
    else:
        st, why = ST_CONFIGURED, "%d record(s), %d of them test data." % (total, test)
    return {"status": st, "reason": why, "total": total,
            "test_records": test, "real_records": real}
