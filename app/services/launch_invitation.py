"""SENDING A CUSTOMER THEIR OWN ONBOARDING.

THE TWO DOORS, AND WHY THEY MUST STAY DIFFERENT
===============================================
`/launch/preview/{organization_id}` is the INTERNAL door. Staff only, read
only, and it exists so somebody can see exactly what a customer will get
BEFORE that customer is contacted. A customer must never receive that URL: it
carries an organization id in the path, it is not their session, and nothing
they did on it would save.

This module is the OTHER door — the real one. It gives a named person an
identity inside the customer's own organization and a one-time link to set
their password, after which `/launch` resolves their workspace from their own
session and writes what they type.

NOTHING HERE IS A SECOND SYSTEM
===============================
Identity comes from `customer_provisioning.add_customer_user`, which finds an
existing person by email before it creates one, so a resend cannot fork a
second account. The link comes from `staff_activation.issue`, which mints a
one-time, expiring, purpose-bound token, stores only a hash, and revokes the
outstanding link when a new one is issued — so a leaked link cannot be rescued
by whoever leaked it. Both already existed and both are used unchanged.

WHAT IT REFUSES TO DO
=====================
It cannot grant control-plane authority. `ROLES` below is the customer-side
set, checked here and checked again by `add_customer_user`; god, brand-sales
and executive roles are not expressible through this path at all.

It does not send the message itself. The operator confirms the recipient, gets
the branded link, and decides how it travels. That is the standing rule for
credential delivery in this codebase (see `customer_activation`'s docstring),
and it is also what keeps an internal action from putting a stranger's address
into a send queue by accident.

STATE IS DERIVED, NEVER DECLARED
================================
`status()` reads records that already exist — the activation row, the person's
own login state, the intake, the submission. There is no "invitation status"
column for somebody to set to a value the system cannot back up, and there is
deliberately no OPENED or DELIVERED: nothing here observes a mailbox, so
claiming either would be inventing evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.implementation_models import Implementation
from app.models.models import Organization, Platform, User
from app.models.staff_models import (
    PURPOSE_SETUP, StaffActivation, STAFF_INVITE_ACCEPTED,
    STAFF_INVITE_PENDING,
)
from app.routers.audit_log_router import log_action
from app.services import customer_provisioning as cp
from app.services import staff_activation as activation
from app.services import workspace_access as _ws

# The customer-side roles, and only those. A control-plane role is not
# expressible through this path — see the module docstring.
ROLES = ("org_admin", "advisor", "viewer")
DEFAULT_ROLE = "org_admin"

# The states this module is willing to claim, in the order they happen.
NOT_SENT = "not_sent"
INVITED = "invited"
ACCOUNT_ACTIVE = "account_active"
IN_PROGRESS = "in_progress"
SUBMITTED = "submitted"
REVIEWED = "reviewed"

STATE_LABELS = {
    NOT_SENT: "Not sent",
    INVITED: "Invited",
    ACCOUNT_ACTIVE: "Account active",
    IN_PROGRESS: "In progress",
    SUBMITTED: "Submitted",
    REVIEWED: "Reviewed",
}


def _people(db: Session, org: Organization) -> List[User]:
    """Everyone with access to this customer, by either route.

    Asks `customer_provisioning` rather than re-querying the legacy column.
    When this filtered on `users.organization_id` alone, sending onboarding to
    a person whose home is elsewhere — a salesperson given org_admin access to
    the customer he sold — left `status()` unable to see the recipient, so the
    screen reported "Not sent" immediately after sending it.
    """
    return cp.customer_people(db, org.id)


def _latest_activation(db: Session, user_ids: List[str]) -> Optional[StaffActivation]:
    if not user_ids:
        return None
    return (db.query(StaffActivation)
            .filter(StaffActivation.user_id.in_(user_ids))
            .order_by(StaffActivation.created_at.desc())
            .first())


def _brand(db: Session, org: Organization) -> Dict[str, Any]:
    """The white-label brand this customer belongs to, from their own row.

    Never a default and never the first brand in the table: a customer invited
    under another brand's name is the leak this platform has already had once.
    """
    plat = (db.query(Platform).filter(Platform.id == org.platform_id).first()
            if org.platform_id else None)
    if plat is None:
        return {"id": None, "name": None, "support_email": None, "known": False}
    return {"id": plat.id, "name": plat.name,
            "support_email": plat.support_email, "known": True}


# ── what would happen, before anything happens ──────────────────────────────

def recipient_preview(db: Session, org: Organization,
                      impl: Implementation) -> Dict[str, Any]:
    """WHO would receive it, and under WHICH brand. Creates nothing.

    The confirmation step exists because the failure this guards against is
    not a technical one: it is sending a real person a real invitation to the
    wrong company's workspace. An operator has to see the address and the
    brand together, and say yes to that exact pair, before anything is minted.
    """
    people = _people(db, org)
    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "brand": _brand(db, org),
        # Everyone who already has an identity here. Offered so the common case
        # — inviting somebody who already exists — cannot accidentally create a
        # second account for the same human.
        "existing_people": [
            {"id": u.id, "name": u.full_name, "email": u.email,
             # The role in THIS customer, not the platform column.
             "role": (_ws.workspace_role(u, db, org.id) or u.role),
             "platform_role": u.role,
             # Their home is elsewhere; they hold this customer additively.
             "is_seconded": (u.organization_id != org.id),
             "has_signed_in": not bool(u.must_change_password)}
            for u in people
        ],
        "roles": list(ROLES),
        "default_role": DEFAULT_ROLE,
        "status": status(db, org, impl),
        # Said explicitly so a screen can show it before the operator commits.
        "will_send_message": False,
        "note": ("Confirm the recipient and the brand. This creates their "
                 "login and a one-time link; it does not email, text or "
                 "otherwise contact them."),
    }


def find_identity(db: Session, org: Organization, email: str) -> Dict[str, Any]:
    """Does this person already exist, and where? Nothing is created by asking."""
    return cp.lookup_identity(db, (email or "").strip().lower(), org.id)


# ── sending ─────────────────────────────────────────────────────────────────

def send(db: Session, org: Organization, impl: Implementation, actor: User, *,
         email: str, full_name: str = "", role: str = DEFAULT_ROLE,
         confirm_email: str = "", base_url: Optional[str] = None,
         location_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Give a named person access to THIS customer's onboarding.

    Idempotent in the way that matters: an email that already has an identity
    here is reused, never duplicated. Issuing again revokes the outstanding
    link rather than adding a second valid one.
    """
    email = (email or "").strip().lower()
    confirm = (confirm_email or "").strip().lower()

    if not email or "@" not in email:
        raise HTTPException(status_code=400,
                            detail="A valid email address is required.")
    # THE CONFIRMATION IS THE POINT OF THE WHOLE ENDPOINT. Without it this is
    # a one-click send to whatever address was on screen.
    if confirm != email:
        raise HTTPException(
            status_code=400,
            detail="Confirm the recipient's email address before sending. "
                   "It must match exactly.")
    if role not in ROLES:
        raise HTTPException(
            status_code=400,
            detail="Onboarding access is a customer role: %s." % ", ".join(ROLES))

    brand = _brand(db, org)
    if not brand["known"]:
        # A customer with no brand would be invited under no name at all, and
        # the link would arrive from nobody. Refuse rather than guess.
        raise HTTPException(
            status_code=409,
            detail="This customer is not attached to a brand yet, so an "
                   "invitation would have no sender identity. Set the "
                   "customer's brand first.")

    before = status(db, org, impl)

    target, created = cp.add_customer_user(
        db, org, actor, email=email, full_name=(full_name or "").strip(),
        role=role, location_ids=list(location_ids or []))

    # WHETHER A SETUP LINK IS THE RIGHT THING TO HAND THIS PERSON.
    #
    # A setup link is how somebody with NO password of their own gets one. For
    # a person who already signs in here — a salesperson, a brand executive,
    # somebody who already administers another customer — minting one is
    # actively harmful in two ways that are easy to miss:
    #
    #   it REVOKES their outstanding links. `issue` supersedes every pending
    #   activation for that user, so onboarding a colleague could quietly
    #   cancel the sales access link they were waiting on.
    #
    #   accepting it REWRITES their password. `staff_activation.accept` sets
    #   password_hash and clears the lockout counters. Sending "set your
    #   password" to somebody who already has one is a password reset wearing
    #   an invitation's clothes.
    #
    # They do not need one. The membership granted above is the access; their
    # existing credentials already work, and `/launch` resolves which
    # customer's intake they get from their own session. So they are sent to
    # the Launch Pad and nothing about their authentication is touched.
    needs_setup = bool(target.must_change_password)
    row, raw = (None, None)
    if needs_setup:
        # THE ONE-TIME LINK. `issue` revokes any outstanding link for this
        # person, so a resend cannot leave two valid ways in.
        row, raw = activation.issue(db, target, actor, purpose=PURPOSE_SETUP)
        url = activation.activation_url(base_url, raw)
    else:
        base = (base_url or "").rstrip("/")
        url = ("%s/launch" % base) if base else "/launch"

    log_action(
        db, org.id, actor.id,
        action="customer_onboarding_invitation_issued",
        target_type="implementation", target_id=impl.id,
        platform_id=org.platform_id,
        before={"status": before["state"]},
        after={"status": INVITED},
        details={
            "recipient_user_id": target.id,
            "identity_created": bool(created),
            "role": role,
            "brand": brand["name"],
            # Stated in the record because the next reader will want to know.
            "message_sent_by_platform": False,
            "send_count": (row.send_count if row is not None else 0),
            "access_path": ("setup_link" if needs_setup else "existing_login"),
            "credentials_touched": bool(needs_setup),
        },
        commit=False,
    )
    db.commit()
    db.refresh(target)

    from app.services import workspace_access
    return {
        "recipient": {"id": target.id, "email": target.email,
                      "name": target.full_name,
                      # The role IN THIS CUSTOMER. `target.role` may be
                      # describing another context entirely — the whole point
                      # of the additive grant.
                      "role": (workspace_access.workspace_role(target, db, org.id)
                               or target.role),
                      "platform_role": target.role},
        "identity_created": bool(created),
        "brand": brand,
        # Either the one-time setup link, or the Launch Pad for somebody who
        # already has credentials. `onboarding_url_is_one_time` says which, so
        # a screen cannot promise "shown once, never retrievable" about a URL
        # that is simply /launch.
        "onboarding_url": url,
        "onboarding_url_is_one_time": bool(needs_setup),
        "access_path": ("setup_link" if needs_setup else "existing_login"),
        "expires_at": (row.expires_at if row is not None else None),
        "send_count": (row.send_count if row is not None else 0),
        "message_sent_by_platform": False,
        "note": ("A one-time link to set their password, then their own "
                 "onboarding."
                 if needs_setup else
                 "This person already signs in to AdvisorFlow. Nothing about "
                 "their password or existing access was changed — they now "
                 "hold %s access to this customer and reach their onboarding "
                 "at /launch with the credentials they already have." % role),
        "status": status(db, org, impl),
    }


# ── where it actually stands ────────────────────────────────────────────────

def status(db: Session, org: Organization,
           impl: Implementation) -> Dict[str, Any]:
    """Derived from records, never from a column somebody set.

    Deliberately has no OPENED, READ or DELIVERED. Nothing in this platform
    observes the recipient's mailbox, so those would be claims with no
    evidence behind them — and an operator who believes a customer has read
    something they have not is worse off than one who knows nothing.
    """
    from app.services import launch_intake

    people = _people(db, org)
    ids = [u.id for u in people]
    row = _latest_activation(db, ids)

    invited_user = None
    if row is not None:
        invited_user = next((u for u in people if u.id == row.user_id), None)

    # Has anybody here actually got a working login?
    signed_in = [u for u in people if not u.must_change_password]

    ov = launch_intake.overview(db, impl.id, org.id)
    sub = launch_intake.latest_submission(db, impl.id, org.id)

    if sub is not None and sub.reviewed_at is not None:
        state = REVIEWED
    elif sub is not None:
        state = SUBMITTED
    elif ov["overall_pct"] > 0:
        state = IN_PROGRESS
    elif signed_in:
        state = ACCOUNT_ACTIVE
    elif row is not None and row.status in (STAFF_INVITE_PENDING,
                                            STAFF_INVITE_ACCEPTED):
        state = INVITED
    else:
        state = NOT_SENT

    return {
        "state": state,
        "label": STATE_LABELS[state],
        "invited_email": invited_user.email if invited_user else None,
        "invited_name": invited_user.full_name if invited_user else None,
        "invited_at": row.created_at if row is not None else None,
        "invite_expires_at": row.expires_at if row is not None else None,
        "invite_status": row.status if row is not None else None,
        "send_count": row.send_count if row is not None else 0,
        "people_count": len(people),
        "accounts_active": len(signed_in),
        "intake_pct": ov["overall_pct"],
        # THE HONEST ABSENCES, said out loud rather than left to be assumed.
        "delivery_evidence": None,
        "opened": None,
        "note": ("Delivery and open tracking are not claimed: this platform "
                 "does not send or observe the message."),
    }
