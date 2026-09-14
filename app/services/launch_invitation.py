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
import logging

from app.routers.audit_log_router import log_action
from app.services import customer_provisioning as cp
from app.services import staff_activation as activation
from app.services import workspace_access as _ws

# The customer-side roles, and only those. A control-plane role is not
# expressible through this path — see the module docstring.
log = logging.getLogger(__name__)

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
            "support_email": plat.support_email,
            "accent_color": (getattr(plat, "invite_accent_color", None)
                             or getattr(plat, "accent_color", None)),
            "known": True}



# ── ACTUALLY SENDING IT ─────────────────────────────────────────────────────

# WHAT "SENT" IS ALLOWED TO MEAN.
#
# The God console reported a customer as "Invited" and Manage Access recorded
# `sales_access_link_issued`, and BOTH were true — but nothing had ever
# attempted an email, there was no delivery receipt, no send event and no
# bounce, because this module only ever generated a link. A reader of those
# two records could not tell the difference between "we emailed them" and "we
# made a link somebody still has to paste into a message", and those are very
# different states to be in with a customer who has gone quiet.
#
# So delivery is now a state with names, and every one of them is honest about
# how far the platform actually got.
DELIVERY_GENERATED = "generated"      # a link exists; nothing was sent
DELIVERY_QUEUED = "queued"            # handed to the provider, no answer yet
DELIVERY_SENT = "sent"                # the provider accepted it
DELIVERY_FAILED = "failed"            # the provider refused it, with a reason
DELIVERY_NOT_ATTEMPTED = "not_attempted"

DELIVERY_LABELS = {
    DELIVERY_GENERATED: "Link generated — not sent",
    DELIVERY_QUEUED: "Queued with the mail provider",
    DELIVERY_SENT: "Sent",
    DELIVERY_FAILED: "Send failed",
    DELIVERY_NOT_ATTEMPTED: "Not attempted",
}


def _invitation_email(brand: Dict[str, Any], org: Organization,
                      recipient_name: str, url: str, one_time: bool) -> str:
    """The branded invitation body. One brand's face, resolved, never typed."""
    name = brand.get("name") or "your account team"
    accent = brand.get("accent_color") or "#1d4ed8"
    greeting = ("Hi %s," % recipient_name) if recipient_name else "Hi,"
    action = ("set your password and start your onboarding"
              if one_time else "sign in and start your onboarding")
    note = ("This link can be used once and then expires."
            if one_time else
            "Use the password you already sign in with.")
    support = brand.get("support_email")
    tail = ("Questions? Reply to this email — it reaches %s." % support
            if support else "")
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;color:#1c2430">'
        '<p style="margin:0 0 4px;font-size:12px;color:#6b7a8c">%s</p>'
        '<div style="height:3px;width:44px;background:%s;margin:0 0 14px"></div>'
        '<h2 style="margin:0 0 12px;font-size:18px">Your %s workspace is ready</h2>'
        '<p style="margin:0 0 12px">%s</p>'
        '<p style="margin:0 0 16px">Use the button below to %s for '
        '<strong>%s</strong>.</p>'
        '<p style="margin:0 0 18px">'
        '<a href="%s" style="display:inline-block;background:%s;color:#fff;'
        'padding:11px 22px;border-radius:6px;text-decoration:none;'
        'font-weight:700">Start onboarding</a></p>'
        '<p style="margin:0 0 8px;font-size:12px;color:#6b7a8c">%s</p>'
        '<p style="margin:0;font-size:12px;color:#6b7a8c">%s</p>'
        '</div>'
    ) % (name, accent, name, greeting, action,
         getattr(org, "name", "your organization"), url, accent, note, tail)


def _deliver(db: Session, org: Organization, brand: Dict[str, Any], *,
             to_email: str, recipient_name: str, url: str,
             one_time: bool) -> Dict[str, Any]:
    """Hand the invitation to the mail provider and report what happened.

    Uses the platform's existing email infrastructure — `email_service.
    send_email_via_provider` with a resolved brand identity — so there is no
    second mail system and no address typed into this file. A brand with no
    verified sender FAILS here with that reason rather than borrowing
    somebody else's address.
    """
    from app.services.email_service import send_email_via_provider
    from app.services import email_identity

    identity = _sending_identity(db, org, brand)
    if not getattr(identity, "from_email", None):
        return {"state": DELIVERY_FAILED, "to": to_email,
                "provider_message_id": None,
                "error": ("No verified sending address is configured for %s. "
                          "Set the brand's support email before sending."
                          % (brand.get("name") or "this brand")),
                "attempted_at": datetime.utcnow()}
    try:
        result = send_email_via_provider(
            to_email,
            "Your %s onboarding" % (brand.get("name") or "workspace"),
            _invitation_email(brand, org, recipient_name, url, one_time),
            org=identity,
            # DECLARED, so the decision is not left to a default. This message
            # contains a one-time setup link; `email_identity` refuses an audit
            # copy for this type and `_record_delivery` below keeps the
            # envelope instead.
            message_type="onboarding_invitation",
            sensitivity=email_identity.SENSITIVE,
            template_id="launch.onboarding_invitation")
    except Exception as exc:                                    # noqa: BLE001
        log.exception("launch invitation: provider raised")
        return {"state": DELIVERY_FAILED, "to": to_email,
                "provider_message_id": None, "error": str(exc)[:300],
                "attempted_at": datetime.utcnow()}
    state = DELIVERY_SENT if result.get("success") else DELIVERY_FAILED
    out = {"state": state, "to": to_email,
           "provider_message_id": (result.get("provider_message_id")
                                   if result.get("success") else None),
           "error": (None if result.get("success")
                     else (result.get("error")
                           or "The mail provider refused it.")[:300]),
           "attempted_at": datetime.utcnow()}
    _record_delivery(db, org, brand, identity, out, subject_template=(
        "launch.onboarding_invitation"))
    return out


def _record_delivery(db: Session, org: Organization, brand: Dict[str, Any],
                     identity, outcome: Dict[str, Any],
                     subject_template: str) -> None:
    """THE ENVELOPE OF A MESSAGE WE ARE NOT ALLOWED TO COPY.

    A BCC is out of the question here — the body holds a one-time setup link —
    but "we sent it and the provider took it" is exactly the fact nobody could
    retrieve afterwards, which is what made "was Joshua's invitation actually
    delivered?" an archaeology exercise. So the envelope is recorded and the
    contents are not.

    `safe_delivery_record` is given the subject, the addresses and the
    provider's answer; it is never given the body, the link or the token, and
    it scrubs anything URL-shaped that arrives anyway. Best-effort: an audit
    failure must never turn a delivered invitation into an error.
    """
    from app.services import email_identity
    try:
        record = email_identity.safe_delivery_record(
            platform_id=getattr(org, "platform_id", None),
            brand_name=brand.get("name"),
            organization_id=getattr(org, "id", None),
            recipient=outcome.get("to"),
            message_type="onboarding_invitation",
            template_id=subject_template,
            subject="Your %s onboarding" % (brand.get("name") or "workspace"),
            from_email=getattr(identity, "from_email", None),
            sent_at=outcome.get("attempted_at"),
            provider="resend",
            provider_message_id=outcome.get("provider_message_id"),
            delivery_state=outcome.get("state"),
            delivery_error=outcome.get("error"),
        )
        log_action(db, getattr(org, "id", None), None,
                   action="email.credential_delivery",
                   target_type="organization",
                   target_id=getattr(org, "id", None),
                   platform_id=getattr(org, "platform_id", None),
                   details=record,
                   note="Envelope only. No link, token or body is recorded.",
                   commit=False)
    except Exception:                                           # noqa: BLE001
        log.exception("launch invitation: delivery record not written")


class _BrandSender:
    """Duck-type for `send_email_via_provider`, same shape support uses."""

    __slots__ = ("from_email", "reply_to_email", "cc_email",
                 "resend_api_key", "resolved", "from_name", "audit_bcc_email")

    def __init__(self, address, api_key=None, brand_name=None):
        self.from_email = address
        self.reply_to_email = address
        self.cc_email = None
        self.resend_api_key = api_key
        # THE BRAND NAMES ITSELF ON ITS OWN INVITATION. From the brand row
        # that supplied the address, so the two cannot disagree.
        self.from_name = brand_name
        # DELIBERATELY None, AND NOT A MISSING FEATURE.
        #
        # An onboarding invitation carries a one-time setup link: whoever holds
        # the text can set the recipient's password. BCCing it into a shared
        # mailbox would make a single-use credential a standing one, readable
        # by everyone with access to that inbox, long after the customer has
        # signed in. `email_identity.audit_bcc_for` refuses this message type
        # anyway; leaving the attribute empty means two independent things
        # would have to fail before a copy could be taken.
        self.audit_bcc_email = None
        # RESOLVED MEANS "I ASKED THE BRAND". An unresolved brand must not be
        # rescued by the deployment-wide default, which belongs to nobody.
        self.resolved = True


def _sending_identity(db: Session, org: Organization, brand: Dict[str, Any]):
    address = (brand.get("support_email")
               or getattr(org, "from_email", None))
    return _BrandSender(address, getattr(org, "resend_api_key", None),
                        brand_name=brand.get("name"))

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
         location_ids: Optional[List[str]] = None,
         deliver: bool = False) -> Dict[str, Any]:
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

    # THE SEND ITSELF, when the operator asked for one. Attempted BEFORE the
    # audit row is written so the record states what actually happened rather
    # than what was intended.
    delivery = {"state": DELIVERY_GENERATED if not deliver else DELIVERY_NOT_ATTEMPTED,
                "to": None, "provider_message_id": None, "error": None,
                "attempted_at": None}
    if deliver:
        delivery = _deliver(db, org, brand, to_email=target.email,
                            recipient_name=(target.full_name or "").strip(),
                            url=url, one_time=needs_setup)

    log_action(
        db, org.id, actor.id,
        action=("customer_onboarding_invitation_sent" if deliver
                else "customer_onboarding_invitation_issued"),
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
            # It is now the TRUTH OF THIS ATTEMPT rather than a constant: the
            # console reported "Invited" for months on the strength of a link
            # nobody had sent.
            "message_sent_by_platform": bool(
                deliver and delivery["state"] == DELIVERY_SENT),
            "delivery_state": delivery["state"],
            "delivery_error": delivery.get("error"),
            "provider_message_id": delivery.get("provider_message_id"),
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
        "message_sent_by_platform": bool(
            deliver and delivery["state"] == DELIVERY_SENT),
        "delivery": {
            "state": delivery["state"],
            "label": DELIVERY_LABELS.get(delivery["state"], delivery["state"]),
            "to": delivery.get("to"),
            "provider_message_id": delivery.get("provider_message_id"),
            "error": delivery.get("error"),
            "attempted_at": delivery.get("attempted_at"),
        },
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
