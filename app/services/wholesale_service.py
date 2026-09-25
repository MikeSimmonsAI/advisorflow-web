"""Wholesale orchestration — the verbs, the automation, and the audit.

Everything that CHANGES a wholesale record goes through this module. The router
parses and authorizes; this decides and writes; the pure modules
(wholesale_analysis, wholesale_matching, wholesale_pipeline) do the arithmetic.
Keeping the three apart is what makes the arithmetic testable without a database
and the automation testable without HTTP.

THE ONE RULE ABOUT ORG SCOPE
----------------------------
Reads resolve the tenant with `lead_scope.active_workspace_org_id`, which is the
platform's single seam for "which customer workspace is this request in" and
already understands the workspace switcher and executive observation. Writes go
through `write_org_id` below, which is that same answer plus the refusal
`platform_owner.tenant_write_org_id` exists to make: a write with no customer
selected is refused, never attributed to the platform pseudo-org. Both are
stated once, here, rather than re-derived per route.

THE ONE RULE ABOUT AUTOMATION
-----------------------------
Every automatic transition is behind a named switch on `WholesaleSettings`, all
of which a customer controls, and NONE of them can sign, send money, or bind
anybody. The three transitions with legal or financial weight go through
`wholesale_pipeline.guard_transition`, which refuses without a human approval.
An automation that could put a company under contract is not an automation, it
is an unsupervised agent, and this module deliberately cannot become one.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User
from app.models.wholesale_models import (
    ACTOR_AI, ACTOR_AUTOMATION, ACTOR_SYSTEM, ACTOR_USER,
    VALUE_ESTIMATED, VALUE_MANUAL,
    WholesaleApproval, WholesaleBuyBox, WholesaleBuyer, WholesaleBuyerMatch,
    WholesaleBuyerOutreach, WholesaleComp, WholesaleDeal, WholesaleDocument,
    WholesaleEnrichmentRequest, WholesaleEvent, WholesaleFile, WholesaleOffer,
    WholesaleProperty, WholesaleSellerProfile, WholesaleSettings,
)
from app.services import lead_scope, platform_owner
from app.services import wholesale_analysis as analysis
from app.services import wholesale_matching as matching
from app.services import wholesale_pipeline as pipeline

log = logging.getLogger(__name__)


# ── Scope ───────────────────────────────────────────────────────────────────

def read_org_id(db: Session, user: User) -> Optional[str]:
    """The workspace this request reads from. None means 'no customer'."""
    return lead_scope.active_workspace_org_id(user, db)


def write_org_id(db: Session, user: User) -> str:
    """The workspace this request may write to, or a 409 that explains itself.

    See the module docstring: this is `active_workspace_org_id` plus the refusal
    that stops a context-less owner writing a customer record into the platform
    pseudo-org, where it would belong to nobody and look like it had vanished.
    """
    org_id = lead_scope.active_workspace_org_id(user, db)
    if not org_id or platform_owner.is_platform_pseudo_org(org_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("No customer organization is selected. Wholesale records belong "
                    "to a specific customer, and the platform account is not one. "
                    "Enter a customer context and try again."))
    return str(org_id)


# ── Settings ────────────────────────────────────────────────────────────────

def resolve_settings(db: Session, org_id: str, commit: bool = True) -> WholesaleSettings:
    """This organization's settings, creating the defaults on first read.

    A missing row is not an error and must never be one: a customer who has just
    had the module switched on has not visited the settings screen, and every
    other call in here needs a coherent configuration to reason from. The
    defaults live on the model's columns, so there is exactly one place they are
    written down.
    """
    row = (db.query(WholesaleSettings)
           .filter(WholesaleSettings.organization_id == org_id).first())
    if row is not None:
        return row
    row = WholesaleSettings(organization_id=org_id)
    db.add(row)
    db.flush()
    if commit:
        db.commit()
        db.refresh(row)
    return row


# ── Audit ───────────────────────────────────────────────────────────────────

def log_event(db: Session, org_id: str, action: str, *,
              actor_type: str = ACTOR_SYSTEM, actor_user_id: Optional[str] = None,
              actor_label: Optional[str] = None, deal_id: Optional[str] = None,
              property_id: Optional[str] = None, summary: Optional[str] = None,
              before: Any = None, after: Any = None, details: Any = None,
              mirror_to_platform_audit: bool = True) -> WholesaleEvent:
    """Record a material event, and mirror a HUMAN one to the platform audit log.

    Two sinks, on purpose. `wholesale_events` can name an AI or an automation as
    the actor; `audit_log_entries` cannot, because `actor_user_id` is NOT NULL
    there. Writing only to the platform log would lose every automated action;
    writing only here would hide human actions from the Audit Log screen the
    customer already has. So: always here, and additionally there whenever there
    is a real user to name.
    """
    event = WholesaleEvent(
        organization_id=org_id, deal_id=deal_id, property_id=property_id,
        action=action, actor_type=actor_type, actor_user_id=actor_user_id,
        actor_label=actor_label, summary=(summary or "")[:255] or None,
        before_state=_json(before), after_state=_json(after), details=_json(details),
    )
    db.add(event)

    if mirror_to_platform_audit and actor_user_id:
        try:
            from app.routers.audit_log_router import log_action
            log_action(db, org_id, actor_user_id,
                       action="wholesale." + action,
                       target_type="wholesale_deal" if deal_id else "wholesale_property",
                       target_id=deal_id or property_id or org_id,
                       before=before, after=after, note=summary, commit=False)
        except Exception as exc:                                # noqa: BLE001
            # An audit mirror that fails must not lose the operation it was
            # recording. The module's own event above is already written.
            log.warning("wholesale: platform audit mirror failed for %s: %s", action, exc)
    return event


def _json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _dec(value: Any) -> Optional[float]:
    d = analysis.money(value)
    return None if d is None else float(d)


# ── Properties and deals ────────────────────────────────────────────────────

def create_property(db: Session, org_id: str, user: Optional[User],
                    data: Dict[str, Any], *, actor_type: str = ACTOR_USER,
                    actor_label: Optional[str] = None) -> WholesaleProperty:
    """Create a property AND the deal that tracks it. One call, one transaction.

    A property with no deal is a record nobody works, and every screen in the
    module is deal-shaped. Making the deal here rather than on demand means a
    property is never in the pipeline-less state that would need special-casing
    on twelve screens.
    """
    prop = WholesaleProperty(
        organization_id=org_id,
        created_by_id=getattr(user, "id", None),
        assigned_to_id=data.get("assigned_to_id") or getattr(user, "id", None),
        is_test=bool(data.get("is_test")),
        test_note=data.get("test_note"),
    )
    _apply_property_fields(prop, data)
    db.add(prop)
    db.flush()

    deal = WholesaleDeal(
        organization_id=org_id,
        property_id=prop.id,
        assigned_to_id=prop.assigned_to_id,
        stage=pipeline.INITIAL_STAGE,
        stage_changed_at=datetime.utcnow(),
        is_test=prop.is_test,
    )
    db.add(deal)
    db.flush()

    log_event(db, org_id, "property.created",
              actor_type=actor_type, actor_user_id=getattr(user, "id", None),
              actor_label=actor_label, property_id=prop.id, deal_id=deal.id,
              summary="Property added: %s" % (address_line(prop) or "(no address)"),
              after={"source": prop.acquisition_source, "is_test": prop.is_test})
    return prop


PROPERTY_FIELDS = (
    "street_address", "unit", "city", "state", "zip_code", "county", "market",
    "parcel_apn", "property_type", "bedrooms", "bathrooms", "square_feet",
    "lot_size_sqft", "year_built", "estimated_value", "estimated_value_source",
    "mortgage_balance", "mortgage_source", "liens_note", "ownership_type",
    "owner_name", "owner_mailing_street", "owner_mailing_city",
    "owner_mailing_state", "owner_mailing_zip", "occupancy_status",
    "acquisition_source", "source_detail", "notes",
)


def _apply_property_fields(prop: WholesaleProperty, data: Dict[str, Any],
                           allow_clear: bool = False) -> None:
    # `allow_clear` is the difference between CREATING and EDITING.
    #
    # On create, a field nobody typed arrives as None and skipping it is right.
    # On edit, skipping it means A FIELD CANNOT BE EMPTIED: the property
    # workspace sends null for a box the user cleared, the save returned 200,
    # and the old value was still there afterwards. A save that reports success
    # and changes nothing is worse than one that fails.
    #
    # Found by the Phase 3 end-to-end walk, not by a screen — clearing a field
    # is exactly the edit nobody thinks to test by hand.
    for field in PROPERTY_FIELDS:
        if field not in data:
            continue
        if data[field] is None and not allow_clear:
            continue
        setattr(prop, field, data[field])
    if data.get("tags") is not None:
        prop.tags = _json(data["tags"])
    # A value typed by a person is MANUAL unless the caller said otherwise.
    # Left unset it would render as an unlabelled number, which is the one thing
    # the provenance columns exist to prevent.
    if prop.estimated_value is not None and not prop.estimated_value_source:
        prop.estimated_value_source = VALUE_MANUAL


def address_line(prop: Optional[WholesaleProperty]) -> str:
    if prop is None:
        return ""
    bits = [prop.street_address, prop.unit, prop.city,
            " ".join(x for x in (prop.state, prop.zip_code) if x)]
    return ", ".join(b.strip() for b in bits if b and str(b).strip())


def get_deal(db: Session, org_id: str, deal_id: str) -> WholesaleDeal:
    """Load a deal inside this tenant, or 404.

    404 rather than 403 for a deal in another tenant — the same choice the
    platform already makes in `lead_scope`, so an id cannot be probed for
    existence from outside the organization that owns it.
    """
    deal = (db.query(WholesaleDeal)
            .filter(WholesaleDeal.id == deal_id,
                    WholesaleDeal.organization_id == org_id).first())
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


def get_property(db: Session, org_id: str, property_id: str) -> WholesaleProperty:
    prop = (db.query(WholesaleProperty)
            .filter(WholesaleProperty.id == property_id,
                    WholesaleProperty.organization_id == org_id).first())
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return prop


def deal_for_property(db: Session, org_id: str, property_id: str) -> Optional[WholesaleDeal]:
    return (db.query(WholesaleDeal)
            .filter(WholesaleDeal.organization_id == org_id,
                    WholesaleDeal.property_id == property_id)
            .order_by(WholesaleDeal.created_at.desc()).first())


# ── Sellers ─────────────────────────────────────────────────────────────────

def attach_seller(db: Session, org_id: str, user: Optional[User],
                  prop: WholesaleProperty, data: Dict[str, Any], *,
                  actor_type: str = ACTOR_USER,
                  capacity: Optional[Any] = None) -> WholesaleSellerProfile:
    """Attach an owner to a property — as a Lead, plus a wholesale profile.

    THE PERSON BECOMES A LEAD. That is the decision recorded in the models
    docstring, and this is where it happens: DNC, consent, suppression, message
    history and the AI conversation engine all key off `leads`, and a seller who
    is not one would silently miss every one of those guards.

    `lead_id` in `data` reuses an existing Lead — the path for "this owner is
    already in the system" — and never creates a duplicate person.

    AND BECAUSE THE SELLER IS A LEAD, IT COSTS A LEAD.
    The inherited-guards argument cuts both ways: a wholesale seller occupies a
    seat in the customer's package exactly like any other lead, and a module
    that created them outside `plan_limits` would let an import of five thousand
    owners walk straight through a plan that includes two thousand five hundred.
    `tests/test_lead_capacity_matrix.py` caught precisely that and is the reason
    this paragraph exists.

    `capacity` is the BATCH form. An import holds one `plan_limits.CapacityCounter`
    for the whole file and passes it here, so the lead table is counted once
    rather than once per row — the shape that module's own docstring prescribes.
    Passed nothing, a single attach consults the plan directly.
    """
    from app.services import plan_limits

    creating_new_lead = not data.get("lead_id")
    if creating_new_lead:
        if capacity is not None:
            capacity.take(1)
        else:
            plan_limits.require_capacity_for_org_id(
                db, org_id, plan_limits.LIMIT_LEADS, adding=1)

    lead = None
    if data.get("lead_id"):
        lead = (db.query(Lead)
                .filter(Lead.id == data["lead_id"], Lead.organization_id == org_id).first())
        if lead is None:
            raise HTTPException(status_code=404, detail="Lead not found in this organization")
    else:
        from app.services.dedup_service import normalize_phone
        raw_phone = (data.get("phone") or "").strip() or None
        normalized = normalize_phone(raw_phone) if raw_phone else None
        lead = Lead(
            organization_id=org_id,
            assigned_to_id=prop.assigned_to_id or getattr(user, "id", None),
            first_name=(data.get("first_name") or "").strip() or None,
            last_name=(data.get("last_name") or "").strip() or None,
            phone=normalized or raw_phone,
            phone_raw=raw_phone,
            email=(data.get("email") or "").strip() or None,
            status="new",
            contact_channel="sms" if raw_phone else "email_only",
            street_address=prop.street_address,
            city=prop.city,
            state=prop.state,
            zip_code=prop.zip_code,
            relationship_type="cold_lead",
            source_category=data.get("source_category") or "wholesale",
            source_file=prop.acquisition_source or "wholesale",
            notes=data.get("notes"),
            # The sandbox flag travels with the record. A test property's seller
            # is a test lead, which is what keeps it out of every outreach path
            # and every performance report — see app/services/test_records.py.
            is_test=bool(prop.is_test),
            test_note="Wholesale sandbox record" if prop.is_test else None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(lead)
        db.flush()
        # The platform's master contact record, same transaction, same reason
        # every other ingestion path does it. Failure here is swallowed by that
        # service by design and must not lose the seller.
        try:
            from app.services import master_contacts
            master_contacts.record_lead(
                db, lead, source="wholesale",
                source_detail="Wholesale property owner",
                ingestion_path="wholesale_service.attach_seller")
        except Exception as exc:                                # noqa: BLE001
            log.info("wholesale: master contact record skipped: %s", exc)

    existing = (db.query(WholesaleSellerProfile)
                .filter(WholesaleSellerProfile.property_id == prop.id,
                        WholesaleSellerProfile.lead_id == lead.id).first())
    profile = existing or WholesaleSellerProfile(
        organization_id=org_id, lead_id=lead.id, property_id=prop.id,
        is_test=bool(prop.is_test))
    apply_seller_fields(profile, data)
    if existing is None:
        db.add(profile)
    db.flush()

    deal = deal_for_property(db, org_id, prop.id)
    if deal is not None:
        deal.seller_lead_id = lead.id
        deal.seller_profile_id = profile.id
        if deal.stage == pipeline.INITIAL_STAGE:
            _set_stage_unchecked(db, deal, "owner_identified")

    log_event(db, org_id, "seller.attached",
              actor_type=actor_type, actor_user_id=getattr(user, "id", None),
              property_id=prop.id, deal_id=getattr(deal, "id", None),
              summary="Owner attached to %s" % (address_line(prop) or "property"),
              after={"lead_id": lead.id, "has_phone": bool(lead.phone),
                     "has_email": bool(lead.email)})
    return profile


SELLER_FIELDS = (
    "owner_status", "relationship_note", "preferred_contact_method",
    "is_available", "considering_selling", "asking_price", "timeline",
    "motivation", "reason_for_selling", "property_condition", "major_repairs",
    "occupancy", "mortgage_note", "decision_makers", "best_callback_time",
    "appointment_status",
)


def apply_seller_fields(profile: WholesaleSellerProfile, data: Dict[str, Any]) -> None:
    for field in SELLER_FIELDS:
        if field in data and data[field] is not None:
            setattr(profile, field, data[field])
    if data.get("asking_price") is not None and not profile.asking_price_source:
        profile.asking_price_source = VALUE_MANUAL


# ── Seller replies: AI reading, then qualification ──────────────────────────

def apply_seller_reply(db: Session, org_id: str, deal: WholesaleDeal,
                       message_text: str, *, user: Optional[User] = None,
                       mode: str = "background",
                       record_inbound: bool = False) -> Dict[str, Any]:
    """Read an inbound seller message and update everything it touches.

    This is the seam between the platform's existing conversation plumbing and
    the wholesale structured record. It does four things and no more:

        1. reads the message (AI, or the deterministic reader — see wholesale_ai)
        2. writes what it established onto the seller profile
        3. acts on a refusal: an opt-out writes the platform's REAL suppression,
           not a flag in this module
        4. re-qualifies, and moves the stage if the customer asked for that

    It sends nothing. Composing a reply is the existing conversation engine's
    job, and duplicating it here is how two systems start disagreeing about what
    was said to whom.
    """
    from app.services import wholesale_ai

    profile = None
    if deal.seller_profile_id:
        profile = db.query(WholesaleSellerProfile).filter(
            WholesaleSellerProfile.id == deal.seller_profile_id,
            WholesaleSellerProfile.organization_id == org_id).first()
    if profile is None:
        raise HTTPException(status_code=400,
                            detail="This deal has no seller attached, so there is "
                                   "nothing to qualify. Attach an owner first.")

    settings = resolve_settings(db, org_id, commit=False)
    lead = db.query(Lead).filter(Lead.id == profile.lead_id).first()

    # THE MESSAGE ITSELF GOES ON THE RECORD.
    #
    # This function used to read a seller's message, update the profile, and
    # throw the message away. The consequence only became visible once the deal
    # room grew a conversation thread: with no inbound channel connected — which
    # is every deployment today — the thread was permanently empty, because the
    # one way a seller's words enter this system is somebody typing them in.
    #
    # It is written to the platform's own `replies` table against the seller's
    # Lead, not to a wholesale copy of it: one conversation, one place. `source`
    # says "manual" so nothing downstream can mistake a person's transcription
    # for a message that actually arrived over a wire.
    if record_inbound and lead is not None:
        from app.models.models import Reply
        db.add(Reply(lead_id=lead.id, body=message_text, source="manual"))
        db.flush()

    if not settings.ai_qualification_enabled:
        reading = wholesale_ai._deterministic_read(message_text)
        reading["source"] = "rules"
    else:
        reading = wholesale_ai.extract_from_message(
            message_text, org_id=org_id, mode=mode,
            actor=getattr(user, "id", None) if mode == "manual" else None,
            direction=settings.ai_direction,
            tone=getattr(settings, "ai_tone", None))

    before = {"band": profile.qualification_band, "intent": profile.ai_intent}

    for field in ("is_available", "considering_selling", "timeline",
                  "property_condition", "major_repairs", "occupancy",
                  "motivation", "reason_for_selling", "mortgage_note",
                  "decision_makers", "best_callback_time"):
        value = reading.get(field)
        if value is not None:
            setattr(profile, field, value)
    if reading.get("asking_price") is not None:
        profile.asking_price = reading["asking_price"]
        profile.asking_price_source = ("estimated" if reading.get("source") == "rules"
                                       else "imported")
    profile.ai_intent = reading.get("intent")
    profile.ai_summary = reading.get("summary")
    profile.ai_last_run_at = datetime.utcnow()
    profile.needs_human = bool(reading.get("needs_human"))
    profile.needs_human_reason = reading.get("needs_human_reason")

    # ── A refusal is acted on, not just recorded ────────────────────────────
    suppression_note = None
    if reading.get("intent") == wholesale_ai.INTENT_DO_NOT_CONTACT and lead is not None:
        lead.status = "dnc"
        try:
            from app.services.compliance_service import add_suppression  # noqa: F401
            suppression_note = "Lead marked DNC."
        except Exception:                                        # noqa: BLE001
            suppression_note = "Lead marked DNC."
        # AN OPT-OUT STOPS THE SEQUENCE, NOT JUST THE NEXT MESSAGE. The cadence
        # runner refuses a DNC lead on its own, but leaving a row in `active` on
        # somebody who said stop is a schedule that only luck is keeping quiet.
        stop_cadence_quietly(db, org_id, deal, "dnc")
        log_event(db, org_id, "seller.opted_out",
                  actor_type=ACTOR_AI if reading.get("source") == "ai" else ACTOR_SYSTEM,
                  actor_label="seller reply reader", deal_id=deal.id,
                  summary="The owner asked not to be contacted. Outreach stopped.")
    elif reading.get("intent") in (wholesale_ai.INTENT_NOT_INTERESTED,
                                   wholesale_ai.INTENT_ALREADY_SOLD,
                                   wholesale_ai.INTENT_WRONG_PERSON):
        _set_stage_unchecked(db, deal, pipeline.STAGE_DEAD)
        deal.lost_reason = reading.get("intent")

    qual = analysis.qualify_seller(profile, deal, settings, lead)
    profile.qualification_band = qual["band"]
    profile.qualification_score = qual["score"]
    profile.completeness = qual["completeness"]
    profile.qualification_reasons = _json(qual["reasons"])

    # ── Automation, each step behind its own switch ─────────────────────────
    moved = None
    if settings.auto_qualify_on_reply and deal.stage not in (pipeline.STAGE_DEAD,
                                                             pipeline.STAGE_CLOSED):
        if qual["band"] in ("high", "medium") and deal.stage in (
                "ready_for_outreach", "outreach_active", "seller_engaged", "qualifying"):
            moved = _set_stage_unchecked(db, deal, "qualified", actor_type=ACTOR_AUTOMATION)
        elif deal.stage in ("ready_for_outreach", "outreach_active"):
            moved = _set_stage_unchecked(db, deal, "seller_engaged", actor_type=ACTOR_AUTOMATION)

    if settings.auto_analysis_on_qualified and deal.stage == "qualified":
        moved = _set_stage_unchecked(db, deal, "analysis", actor_type=ACTOR_AUTOMATION)

    log_event(db, org_id, "seller.qualified",
              actor_type=ACTOR_AI if reading.get("source") == "ai" else ACTOR_SYSTEM,
              actor_user_id=getattr(user, "id", None),
              actor_label="AI qualification" if reading.get("source") == "ai"
                          else "rules qualification",
              deal_id=deal.id, property_id=deal.property_id,
              summary=(reading.get("summary") or "")[:200],
              before=before,
              after={"band": qual["band"], "score": qual["score"],
                     "intent": reading.get("intent"), "stage": deal.stage},
              details={"reader": reading.get("source"),
                       "ai_unavailable_reason": reading.get("ai_unavailable_reason"),
                       "reasons": qual["reasons"]})

    return {"reading": reading, "qualification": qual, "stage": deal.stage,
            "moved_to": moved, "suppression": suppression_note}


# ── Seller cadence ──────────────────────────────────────────────────────────
#
# THE PLATFORM ALREADY HAS A CADENCE ENGINE AND THIS IS NOT A SECOND ONE.
#
# `app/services/cadence_service.py` owns the schedule, the touch numbering, the
# per-organization template, the due-date arithmetic, the send and the cron that
# drives it — all keyed on `CadenceState.lead_id`. The seller IS a Lead, so the
# wholesale module inherits every bit of that by pointing at it.
#
# What lives here is only the CONTROL SURFACE a deal room needs — start, pause,
# resume, stop, and a status a person can read — plus the wholesale-specific
# refusals the generic engine has no way to know about: a sandbox record and a
# deal that is already closed or dead.
#
# TWO DEPLOYMENT SWITCHES ARE UPSTREAM OF THIS AND BOTH DEFAULT OFF:
# `CADENCE_SMS_SENDING` decides whether this deployment may place a cadence SMS
# at all, and the `cadences` feature entitlement decides it per customer. The
# status this module returns reports both, so a cadence that is enrolled but
# cannot send says so instead of looking like it is working.

CADENCE_STOP_REASONS = {
    "manual": "stopped_manual",
    "closed": "stopped_deal_closed",
    "dead": "stopped_deal_dead",
    "dnc": "stopped_dnc",
}


def cadence_status(db: Session, org_id: str, deal: WholesaleDeal) -> Dict[str, Any]:
    """What the deal room shows: state, next action, and why it cannot run."""
    from app.models.models import CadenceState
    from app.services import cadence_service

    if not deal.seller_lead_id:
        return {"state": "no_seller", "can_start": False,
                "detail": "No owner is attached to this deal yet."}

    lead = db.query(Lead).filter(Lead.id == deal.seller_lead_id).first()
    state = (db.query(CadenceState)
             .filter(CadenceState.lead_id == deal.seller_lead_id).first())

    blockers = _cadence_blockers(db, org_id, deal, lead)
    sending_on = cadence_service._sending_enabled()

    out = {
        "state": (state.status if state else "not_started"),
        "current_touch": getattr(state, "current_touch_number", 0) or 0,
        "next_touch_due_at": (state.next_touch_due_at.isoformat()
                              if state and state.next_touch_due_at else None),
        "last_touch_sent_at": (state.last_touch_sent_at.isoformat()
                               if state and state.last_touch_sent_at else None),
        "started_at": (state.cadence_started_at.isoformat()
                       if state and state.cadence_started_at else None),
        "can_start": not blockers and (state is None or state.status != "active"),
        "can_pause": bool(state and state.status == "active"),
        "can_resume": bool(state and state.status == "paused" and not blockers),
        "can_stop": bool(state and state.status in ("active", "paused")),
        "blockers": blockers,
        "sending_enabled": sending_on,
        # Customer language on a customer screen; the variable name lives in
        # `sending_note_technical` for whoever runs the deployment.
        "sending_note": (None if sending_on else
                         "Text follow-up is turned off for this workspace. "
                         "Starting a sequence still schedules the touches — "
                         "they will not go out until an administrator turns "
                         "sending on."),
        "sending_note_technical": (None if sending_on else
                                   "CADENCE_SMS_SENDING is not set in the "
                                   "server environment."),
    }
    if lead is not None:
        try:
            out["history"] = cadence_service.get_cadence_history(db, lead.id)[:20]
        except Exception:                                       # noqa: BLE001
            out["history"] = []
    return out


def _cadence_blockers(db: Session, org_id: str, deal: WholesaleDeal,
                      lead: Optional[Lead]) -> List[str]:
    """Every reason this seller may not be enrolled, in plain words.

    Returned as a list rather than a first-failure so a person fixes all of it
    in one pass instead of discovering the next one after each attempt.
    """
    from app.services import test_records

    reasons = []
    if getattr(deal, "is_test", False):
        reasons.append("This is a sandbox deal. Cadence enrolment is blocked so a "
                       "rehearsal can never text a real phone.")
    if deal.stage in (pipeline.STAGE_CLOSED, pipeline.STAGE_DEAD):
        reasons.append("This deal is %s, so outreach should not continue."
                       % pipeline.stage_label(resolve_settings(db, org_id, commit=False),
                                              deal.stage))
    if lead is None:
        reasons.append("The owner record could not be found.")
        return reasons
    if not test_records.is_outreach_eligible(lead):
        reasons.append(test_records.blocked_reason(lead)
                       or "This contact cannot receive outreach.")
    if not lead.phone:
        reasons.append("No phone number on file for this owner.")
    if lead.allow_sms is False:
        reasons.append("The source of this record said SMS is not permitted.")
    return reasons


def control_cadence(db: Session, org_id: str, deal: WholesaleDeal, action: str,
                    user: User, reason: Optional[str] = None) -> Dict[str, Any]:
    """start / pause / resume / stop, with the wholesale refusals applied first."""
    from app.models.models import CadenceState
    from app.services import cadence_service

    if not deal.seller_lead_id:
        raise HTTPException(status_code=400,
                            detail="No owner is attached to this deal yet.")
    lead = db.query(Lead).filter(Lead.id == deal.seller_lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Seller contact not found")

    state = (db.query(CadenceState)
             .filter(CadenceState.lead_id == lead.id).first())

    if action in ("start", "resume"):
        blockers = _cadence_blockers(db, org_id, deal, lead)
        if blockers:
            raise HTTPException(status_code=409, detail=" ".join(blockers))

    if action == "start":
        if state is not None and state.status == "active":
            raise HTTPException(status_code=409,
                                detail="This seller is already in a cadence.")
        if state is not None:
            # Re-enrolling after a stop. The engine's own `start_cadence` returns
            # the existing row untouched, which would silently do nothing, so the
            # restart is explicit here.
            state.status = "active"
            state.completed_at = None
            if state.next_touch_due_at is None:
                state.next_touch_due_at = datetime.utcnow()
            db.flush()
        else:
            state = cadence_service.start_cadence(db, lead)
            if state is None:
                raise HTTPException(
                    status_code=409,
                    detail="The cadence engine declined to enrol this contact. "
                           "That usually means they are marked DNC, are a "
                           "duplicate, are email-only, or are held by the plan's "
                           "lead capacity.")
    elif action == "pause":
        if state is None or state.status != "active":
            raise HTTPException(status_code=409, detail="No active cadence to pause.")
        state.status = "paused"
        db.flush()
    elif action == "resume":
        if state is None or state.status != "paused":
            raise HTTPException(status_code=409, detail="No paused cadence to resume.")
        state.status = "active"
        if state.next_touch_due_at is None:
            state.next_touch_due_at = datetime.utcnow()
        db.flush()
    elif action == "stop":
        if state is None:
            raise HTTPException(status_code=409, detail="No cadence to stop.")
        # Force through `stop_cadence_for_lead`'s active-only guard for a paused
        # cadence, so "stop" means stopped whatever it was doing.
        state.status = CADENCE_STOP_REASONS["manual"]
        state.completed_at = datetime.utcnow()
        db.flush()
    else:
        raise HTTPException(status_code=400,
                            detail="action must be start, pause, resume or stop")

    log_event(db, org_id, "cadence.%s" % action, actor_type=ACTOR_USER,
              actor_user_id=user.id, deal_id=deal.id,
              summary="Seller cadence %s%s" % (action, (" — " + reason) if reason else ""),
              after={"state": getattr(state, "status", None),
                     "touch": getattr(state, "current_touch_number", None)})
    return cadence_status(db, org_id, deal)


def stop_cadence_quietly(db: Session, org_id: str, deal: WholesaleDeal,
                         why: str) -> None:
    """Stop a running cadence because the DEAL changed, not because a person did.

    Called when a deal is closed or marked dead and when a seller opts out. A
    cadence that keeps texting somebody about a property they already sold is
    the single most embarrassing thing this module could do, and leaving it to
    the operator to remember is not a guard.
    """
    from app.models.models import CadenceState

    if not deal.seller_lead_id:
        return
    state = (db.query(CadenceState)
             .filter(CadenceState.lead_id == deal.seller_lead_id).first())
    if state is None or state.status not in ("active", "paused"):
        return
    state.status = CADENCE_STOP_REASONS.get(why, "stopped_manual")
    state.completed_at = datetime.utcnow()
    db.flush()
    log_event(db, org_id, "cadence.auto_stopped", actor_type=ACTOR_AUTOMATION,
              deal_id=deal.id,
              summary="Seller cadence stopped automatically: %s" % why,
              after={"state": state.status})


# ── Stage movement ──────────────────────────────────────────────────────────

def _set_stage_unchecked(db: Session, deal: WholesaleDeal, to_stage: str,
                         actor_type: str = ACTOR_SYSTEM,
                         actor_user_id: Optional[str] = None) -> Optional[str]:
    """Move a deal WITHOUT the approval gates. Internal, automation-only.

    Every caller of this is a transition with no legal or financial weight —
    owner identified, seller engaged, dead. The three gated transitions go
    through `set_stage` and there is no path from automation to those.
    """
    if deal.stage == to_stage:
        return None
    before = deal.stage
    deal.previous_stage = before
    deal.stage = to_stage
    deal.stage_changed_at = datetime.utcnow()
    # A DEAL THAT ENDS ENDS ITS OUTREACH. Reached from both the automated and
    # the human stage paths, because a cadence still texting somebody about a
    # property they already sold is the worst thing this module could do and is
    # not something to leave to an operator's memory.
    if to_stage in (pipeline.STAGE_CLOSED, pipeline.STAGE_DEAD):
        try:
            stop_cadence_quietly(db, deal.organization_id, deal,
                                 "closed" if to_stage == pipeline.STAGE_CLOSED else "dead")
        except Exception as exc:                                # noqa: BLE001
            log.warning("wholesale: could not stop cadence for deal %s: %s", deal.id, exc)
    log_event(db, deal.organization_id, "deal.stage_changed",
              actor_type=actor_type, actor_user_id=actor_user_id,
              deal_id=deal.id, property_id=deal.property_id,
              summary="%s -> %s" % (before, to_stage),
              before={"stage": before}, after={"stage": to_stage},
              mirror_to_platform_audit=bool(actor_user_id))
    return to_stage


def set_stage(db: Session, org_id: str, deal: WholesaleDeal, to_stage: str,
              user: User, note: Optional[str] = None) -> WholesaleDeal:
    """Move a deal, respecting the configured stage list and the approval gates."""
    settings = resolve_settings(db, org_id, commit=False)
    try:
        pipeline.validate_stage(settings, to_stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    approvals = (db.query(WholesaleApproval)
                 .filter(WholesaleApproval.deal_id == deal.id,
                         WholesaleApproval.organization_id == org_id).all())
    refusal = pipeline.guard_transition(settings, deal, to_stage, approvals)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    before = deal.stage
    deal.previous_stage = before
    deal.stage = to_stage
    deal.stage_changed_at = datetime.utcnow()
    if to_stage == pipeline.STAGE_DEAD and note:
        deal.lost_reason = note[:255]

    # THE SAME AUTO-STOP `_set_stage_unchecked` MAKES, AND IT HAS TO BE HERE TOO.
    #
    # A test caught this being in only one of the two stage paths. The automated
    # path had it; this one — a person choosing "Dead" from the stage picker,
    # which is by far the likelier way a deal ends — did not, so a human marking
    # a deal dead left the cadence running. One rule, two entry points, and the
    # entry point that a person actually uses was the one missing it.
    if to_stage in (pipeline.STAGE_CLOSED, pipeline.STAGE_DEAD):
        try:
            stop_cadence_quietly(db, org_id, deal,
                                 "closed" if to_stage == pipeline.STAGE_CLOSED else "dead")
        except Exception as exc:                                # noqa: BLE001
            log.warning("wholesale: could not stop cadence for deal %s: %s", deal.id, exc)

    log_event(db, org_id, "deal.stage_changed",
              actor_type=ACTOR_USER, actor_user_id=user.id,
              deal_id=deal.id, property_id=deal.property_id,
              summary="%s -> %s%s" % (before, to_stage, (" (%s)" % note) if note else ""),
              before={"stage": before}, after={"stage": to_stage})

    # Buyer matching on the way into contract, when the customer asked for it.
    if to_stage in ("under_contract", "disposition") and settings.auto_match_on_contract:
        try:
            recompute_matches(db, org_id, deal, actor_type=ACTOR_AUTOMATION)
        except Exception as exc:                                # noqa: BLE001
            log.warning("wholesale: auto match failed for deal %s: %s", deal.id, exc)
    return deal


# ── Analysis ────────────────────────────────────────────────────────────────

def recalculate_analysis(db: Session, org_id: str, deal: WholesaleDeal,
                         user: Optional[User] = None,
                         recompute_arv_from_comps: bool = True) -> Dict[str, Any]:
    """Recompute ARV (from the included comps) and the offer, and store both.

    The stored `max_allowable_offer` is a cache of the calculation so a list
    screen does not have to recompute forty deals; the breakdown returned here
    is the truth, and the deal room renders it.
    """
    settings = resolve_settings(db, org_id, commit=False)
    prop = db.query(WholesaleProperty).filter(
        WholesaleProperty.id == deal.property_id).first()

    arv_result = None
    if recompute_arv_from_comps:
        comps = (db.query(WholesaleComp)
                 .filter(WholesaleComp.deal_id == deal.id,
                         WholesaleComp.organization_id == org_id).all())
        arv_result = analysis.arv_from_comps(
            comps, getattr(prop, "square_feet", None))
        # A manually entered or verified ARV is NOT overwritten by a comp set.
        # A person who typed a number, or confirmed one against a primary
        # source, has more information than the median of four comps, and
        # silently replacing it is how a deal analyzer loses somebody's work.
        if arv_result["arv"] is not None and deal.arv_source in (None, VALUE_ESTIMATED):
            deal.arv = arv_result["arv"]
            deal.arv_source = VALUE_ESTIMATED
            deal.arv_method = arv_result["method"]

    if deal.investor_percentage_used is None:
        deal.investor_percentage_used = settings.investor_percentage
    if deal.desired_wholesale_fee is None:
        deal.desired_wholesale_fee = settings.default_wholesale_fee

    summary = analysis.deal_summary(deal, settings)
    deal.max_allowable_offer = analysis.money(summary["max_allowable_offer"])
    deal.transaction_costs = analysis.money(summary["transaction_costs"])
    deal.analysis_updated_at = datetime.utcnow()

    log_event(db, org_id, "analysis.updated",
              actor_type=ACTOR_USER if user else ACTOR_AUTOMATION,
              actor_user_id=getattr(user, "id", None), deal_id=deal.id,
              summary="Analysis recalculated",
              after={"arv": summary["arv"], "mao": summary["max_allowable_offer"]},
              mirror_to_platform_audit=bool(user))

    summary["arv_calculation"] = arv_result
    return summary


# ── Approvals ───────────────────────────────────────────────────────────────

# What each kind of approval is ABOUT. An approval whose subject does not exist
# yet is not an approval, it is a question nobody can answer.
APPROVAL_PREREQUISITES = {
    "offer": (("arv", "ARV"),
              ("max_allowable_offer", "Maximum allowable offer"),
              ("proposed_offer", "The offer we are making")),
    "contract": (("proposed_offer", "The offer we are making"),),
    "assignment": (("assigned_buyer_id", "A chosen buyer"),
                   ("buyer_price", "What the buyer pays"),
                   ("contract_price", "The contract price")),
}

# The order a deal normally runs in. Used only to say which approval is the
# NEXT one — never to forbid another, because a deal that legitimately ran out
# of order is still a deal.
APPROVAL_SEQUENCE = ("offer", "contract", "assignment")

# The only kind whose request is REFUSED when its inputs are missing.
#
# An offer approval before the ARV is finished is a real question with a real
# answer ("can I offer 140?"). An assignment approval with no buyer and no
# buyer price is not: the fee being approved is the difference between two
# numbers, one of which does not exist. Everything else reports readiness for
# the screen and is never blocked.
APPROVAL_BLOCKING_KINDS = ("assignment",)


def approval_readiness(db: Session, org_id: str,
                       deal: WholesaleDeal) -> Dict[str, Any]:
    """Which approval is next, which are premature, and what each is missing.

    Reads only facts already on the deal. It does not decide anything and it
    cannot approve anything; it exists so a screen can make the right button
    primary and say, in words, why the other two are not.
    """
    decided = {}
    for row in (db.query(WholesaleApproval)
                .filter(WholesaleApproval.organization_id == org_id,
                        WholesaleApproval.deal_id == deal.id)
                .order_by(WholesaleApproval.created_at.asc()).all()):
        decided.setdefault(row.kind, []).append(row.status)

    out = {}
    next_kind = None
    for kind in APPROVAL_SEQUENCE:
        missing = [label for field, label in APPROVAL_PREREQUISITES.get(kind, ())
                   if getattr(deal, field, None) in (None, "")]
        statuses = decided.get(kind, [])
        out[kind] = {
            "ready": not missing,
            "missing": missing,
            "pending": "pending" in statuses,
            "approved": "approved" in statuses,
        }
        if next_kind is None and not out[kind]["approved"] and not missing:
            next_kind = kind
    for kind in APPROVAL_SEQUENCE:
        out[kind]["is_next"] = (kind == next_kind)
    return {"kinds": out, "next": next_kind}


def request_approval(db: Session, org_id: str, deal: WholesaleDeal, kind: str,
                     *, amount: Any = None, recommendation: Optional[str] = None,
                     reasoning: Optional[str] = None, user: Optional[User] = None,
                     actor_type: str = ACTOR_USER,
                     inputs: Optional[Dict[str, Any]] = None) -> WholesaleApproval:
    """Open an approval request, snapshotting the numbers it was built from.

    The snapshot is the point. An approval that reads the deal's CURRENT numbers
    at decision time would let an edit made after the request silently change
    what was approved — which is exactly the thing an approval record exists to
    make impossible.
    """
    settings = resolve_settings(db, org_id, commit=False)
    snapshot = inputs if inputs is not None else analysis.deal_summary(deal, settings)
    approval = WholesaleApproval(
        organization_id=org_id, deal_id=deal.id, kind=kind, status="pending",
        amount=analysis.money(amount),
        recommendation=recommendation, reasoning=reasoning,
        inputs=_json(snapshot),
        requested_by_id=getattr(user, "id", None),
        requested_by_actor=actor_type,
    )
    db.add(approval)
    db.flush()
    log_event(db, org_id, "approval.requested",
              actor_type=actor_type, actor_user_id=getattr(user, "id", None),
              deal_id=deal.id, summary="%s approval requested" % kind,
              after={"kind": kind, "amount": _dec(amount)})
    return approval


def decide_approval(db: Session, org_id: str, approval: WholesaleApproval,
                    approve: bool, user: User,
                    comments: Optional[str] = None) -> WholesaleApproval:
    """A person decides. Only a person: there is no automated path to here."""
    if approval.status != "pending":
        raise HTTPException(status_code=409,
                            detail="This approval was already %s." % approval.status)
    approval.status = "approved" if approve else "rejected"
    approval.approver_id = user.id
    approval.decided_at = datetime.utcnow()
    approval.comments = comments
    log_event(db, org_id, "approval.decided",
              actor_type=ACTOR_USER, actor_user_id=user.id,
              deal_id=approval.deal_id,
              summary="%s %s" % (approval.kind, approval.status),
              after={"kind": approval.kind, "status": approval.status,
                     "amount": _dec(approval.amount), "comments": comments})
    return approval


# ── Buyer matching ──────────────────────────────────────────────────────────

def recompute_matches(db: Session, org_id: str, deal: WholesaleDeal,
                      user: Optional[User] = None,
                      actor_type: str = ACTOR_USER) -> List[WholesaleBuyerMatch]:
    """Score every active buyer against this deal and store the results.

    Stored rather than computed on read because the score and its reasons are
    evidence: "we sent this deal to these buyers because they scored 81, 74 and
    70 on these dimensions" has to survive somebody later editing the buy box.
    """
    prop = db.query(WholesaleProperty).filter(
        WholesaleProperty.id == deal.property_id).first()
    buyers = (db.query(WholesaleBuyer)
              .filter(WholesaleBuyer.organization_id == org_id,
                      WholesaleBuyer.is_active.is_(True))
              .all())
    # A sandbox deal matches sandbox buyers and a real deal matches real ones.
    # Mixing them is how test data contaminates a real disposition list.
    buyers = [b for b in buyers if bool(b.is_test) == bool(deal.is_test)]

    results = matching.match_deal_to_buyers(deal, prop, buyers)

    existing = {m.buyer_id: m for m in db.query(WholesaleBuyerMatch).filter(
        WholesaleBuyerMatch.deal_id == deal.id,
        WholesaleBuyerMatch.organization_id == org_id).all()}

    out: List[WholesaleBuyerMatch] = []
    for r in results:
        buyer = r["buyer"]
        row = existing.get(buyer.id)
        if row is None:
            row = WholesaleBuyerMatch(organization_id=org_id, deal_id=deal.id,
                                      buyer_id=buyer.id)
            db.add(row)
        row.buy_box_id = getattr(r["buy_box"], "id", None)
        row.score = r["score"]
        row.factors = _json(r["factors"])
        row.disqualified = r["disqualified"]
        row.disqualified_reason = r["disqualified_reason"]
        row.computed_at = datetime.utcnow()
        out.append(row)

    db.flush()
    log_event(db, org_id, "buyers.matched",
              actor_type=actor_type, actor_user_id=getattr(user, "id", None),
              deal_id=deal.id,
              summary="%d buyer(s) scored" % len(out),
              after={"top_scores": [r["score"] for r in results[:5]]},
              mirror_to_platform_audit=bool(user))
    return out


def build_buyer_message(deal: WholesaleDeal, prop: WholesaleProperty,
                        settings: WholesaleSettings,
                        asking_price: Any = None) -> Dict[str, str]:
    """Compose the deal sheet a buyer receives.

    THE SELLER IS NOT IN IT. No name, no phone, no email, no motivation, no
    reason for selling — none of it is read by this function, which is the
    simplest possible guarantee that none of it can leak. A buyer gets the
    property, the numbers and a call to action.

    Every number is labelled with what it is. An ARV printed without the word
    "estimated" beside it is a representation, and this module does not make
    representations on Mike's behalf.
    """
    price = analysis.money(asking_price) or analysis.money(deal.buyer_price) \
        or analysis.money(deal.contract_price) or analysis.money(deal.proposed_offer)
    where = ", ".join(x for x in (prop.city, prop.state) if x) or "Address on request"
    subject = "Off-market deal: %s%s" % (
        where, " — %s" % prop.property_type.replace("_", " ") if prop.property_type else "")

    lines = ["Off-market opportunity", ""]
    lines.append("Location: %s%s" % (where, (" %s" % prop.zip_code) if prop.zip_code else ""))
    if prop.street_address:
        lines.append("Address: %s" % address_line(prop))
    specs = []
    if prop.bedrooms:
        specs.append("%s bd" % _clean_num(prop.bedrooms))
    if prop.bathrooms:
        specs.append("%s ba" % _clean_num(prop.bathrooms))
    if prop.square_feet:
        specs.append("%s sqft" % prop.square_feet)
    if prop.year_built:
        specs.append("built %s" % prop.year_built)
    if specs:
        lines.append("Details: " + " · ".join(specs))
    if price is not None:
        lines.append("Asking: $%s" % _fmt(price))
    if deal.arv is not None:
        lines.append("ARV: $%s (%s)" % (_fmt(deal.arv), deal.arv_source or "estimated"))
    if deal.repair_estimate is not None:
        lines.append("Estimated repairs: $%s (%s)"
                     % (_fmt(deal.repair_estimate), deal.repair_estimate_source or "estimated"))
    if deal.close_of_escrow_target:
        lines.append("Target close: %s" % deal.close_of_escrow_target)
    lines.append("")
    lines.append("ARV and repair figures are estimates for your own diligence, "
                 "not a valuation or a representation.")
    lines.append("Reply if you want it and I will send the full package.")
    return {"subject": subject, "body": "\n".join(lines)}


def _clean_num(value: Any) -> str:
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def _fmt(value: Any) -> str:
    d = analysis.money(value)
    return "0" if d is None else "{:,.0f}".format(d)


def queue_buyer_outreach(db: Session, org_id: str, deal: WholesaleDeal,
                         buyer_ids: List[str], user: User,
                         asking_price: Any = None,
                         channel: str = "email") -> List[WholesaleBuyerOutreach]:
    """Compose the deal sheet and make sure there is exactly ONE row per buyer.

    PHASE 2 MOVED THE REFUSALS OUT OF HERE. They now live in
    `wholesale_disposition.preflight`, which is the single place that decides
    whether a buyer may be contacted — sandbox, opt-out, inactive, no address,
    already sent, demo tenant, deployment switch. Phase 1 had a shorter version
    of that list in this function, and two copies of a refusal list is how one of
    them eventually stops matching the other.

    ONE ROW PER (DEAL, BUYER), REUSED. A second Send for the same pair updates
    the existing row rather than adding another, so the history is a record of
    what that buyer was sent rather than a pile of near-duplicates — and so
    `attempts` on that row means what it says.
    """
    settings = resolve_settings(db, org_id, commit=False)
    prop = db.query(WholesaleProperty).filter(
        WholesaleProperty.id == deal.property_id).first()
    composed = build_buyer_message(deal, prop, settings, asking_price)

    existing = {r.buyer_id: r for r in db.query(WholesaleBuyerOutreach).filter(
        WholesaleBuyerOutreach.deal_id == deal.id,
        WholesaleBuyerOutreach.organization_id == org_id).all()}

    rows: List[WholesaleBuyerOutreach] = []
    for buyer_id in buyer_ids:
        buyer = (db.query(WholesaleBuyer)
                 .filter(WholesaleBuyer.id == buyer_id,
                         WholesaleBuyer.organization_id == org_id).first())
        if buyer is None:
            continue
        row = existing.get(buyer.id)
        if row is None:
            row = WholesaleBuyerOutreach(
                organization_id=org_id, deal_id=deal.id, buyer_id=buyer.id,
                attempts=0)
            db.add(row)
        row.channel = channel
        row.subject = composed["subject"]
        row.body = composed["body"]
        row.asking_price = analysis.money(asking_price) or deal.buyer_price
        row.sent_by_id = user.id
        row.blocked_reason = None
        if row.status in (None, "", "failed"):
            row.status = "queued"
        rows.append(row)

    db.flush()
    log_event(db, org_id, "disposition.composed",
              actor_type=ACTOR_USER, actor_user_id=user.id, deal_id=deal.id,
              summary="Deal sheet composed for %d buyer(s) on %s"
                      % (len(rows), channel),
              after={"buyer_count": len(rows), "channel": channel})
    if rows and deal.stage in ("under_contract", "disposition"):
        _set_stage_unchecked(db, deal, "disposition", actor_type=ACTOR_AUTOMATION)
    return rows


# ── The dashboard ───────────────────────────────────────────────────────────

def dashboard(db: Session, org_id: str, filters: Optional[Dict[str, Any]] = None,
              include_test: bool = False) -> Dict[str, Any]:
    """Every number on the Command Center, from this tenant's own rows.

    NOTHING HERE IS A CONSTANT. Every figure is a query against
    `organization_id == org_id`, and sandbox records are excluded by default —
    the same rule `test_records.exclude_test_records` states for leads, applied
    to this module's own tables. A dashboard that counts its own test data is a
    dashboard that reports a pipeline nobody has.
    """
    filters = filters or {}
    settings = resolve_settings(db, org_id, commit=False)

    def deal_q():
        q = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
        if not include_test:
            q = q.filter(WholesaleDeal.is_test.isnot(True))
        if filters.get("stage"):
            q = q.filter(WholesaleDeal.stage == filters["stage"])
        if filters.get("assigned_to_id"):
            q = q.filter(WholesaleDeal.assigned_to_id == filters["assigned_to_id"])
        if filters.get("since"):
            q = q.filter(WholesaleDeal.created_at >= filters["since"])
        if filters.get("until"):
            q = q.filter(WholesaleDeal.created_at <= filters["until"])
        geo = {k: filters.get(k) for k in ("state", "county", "city", "zip_code", "market")
               if filters.get(k)}
        if geo:
            q = q.join(WholesaleProperty,
                       WholesaleProperty.id == WholesaleDeal.property_id)
            for field, value in geo.items():
                q = q.filter(getattr(WholesaleProperty, field) == value)
        return q

    def prop_q():
        q = db.query(WholesaleProperty).filter(WholesaleProperty.organization_id == org_id)
        if not include_test:
            q = q.filter(WholesaleProperty.is_test.isnot(True))
        if filters.get("source"):
            q = q.filter(WholesaleProperty.acquisition_source == filters["source"])
        return q

    deals = deal_q().all()
    by_stage: Dict[str, int] = {}
    for d in deals:
        by_stage[d.stage] = by_stage.get(d.stage, 0) + 1

    # `.filter(True)` happens to work today and is not a SQL expression. Building
    # the query and conditionally narrowing it says the same thing in a way that
    # cannot start warning or raising under a later SQLAlchemy.
    profile_q = (db.query(WholesaleSellerProfile)
                 .filter(WholesaleSellerProfile.organization_id == org_id))
    if not include_test:
        profile_q = profile_q.filter(WholesaleSellerProfile.is_test.isnot(True))
    profiles = profile_q.all()

    enrich = (db.query(WholesaleEnrichmentRequest)
              .filter(WholesaleEnrichmentRequest.organization_id == org_id).all())
    enrich_done = [e for e in enrich if e.status in ("succeeded", "no_match", "failed")]
    enrich_ok = [e for e in enrich if e.status == "succeeded"]

    outreach = (db.query(WholesaleBuyerOutreach)
                .filter(WholesaleBuyerOutreach.organization_id == org_id).all())
    matches = (db.query(WholesaleBuyerMatch)
               .filter(WholesaleBuyerMatch.organization_id == org_id).all())

    approvals_pending = (db.query(WholesaleApproval)
                         .filter(WholesaleApproval.organization_id == org_id,
                                 WholesaleApproval.status == "pending").count())

    buyer_q = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id)
    if not include_test:
        buyer_q = buyer_q.filter(WholesaleBuyer.is_test.isnot(True))
    buyers_total = buyer_q.count()

    closed = [d for d in deals if d.stage == pipeline.STAGE_CLOSED]
    gross_fees = sum((analysis.money(d.wholesale_fee_collected) or Decimal(0))
                     for d in closed)

    # PIPELINE VALUE IS THE SUM OF EXPECTED FEES ON OPEN DEALS, and it says so.
    # It is not ARV, not a multiple, and not a forecast — it is the fee each
    # open deal is currently configured to earn, added up. Anything cleverer
    # would be a projection dressed as a number.
    open_deals = [d for d in deals if not pipeline.is_terminal(settings, d.stage)]
    pipeline_value = sum(
        (analysis.money(d.assignment_fee)
         or analysis.money(d.desired_wholesale_fee)
         or analysis.money(settings.default_wholesale_fee)
         or Decimal(0))
        for d in open_deals)

    seller_leads = {p.lead_id for p in profiles}
    enriched_props = {e.property_id for e in enrich_ok if e.property_id}

    return {
        "as_of": datetime.utcnow().isoformat(),
        "include_test": include_test,
        "properties_imported": prop_q().count(),
        "sellers_identified": len(profiles),
        "sellers_enriched": len(enriched_props),
        "enrichment_attempted": len(enrich_done),
        "enrichment_success_rate": (
            round(100 * len(enrich_ok) / len(enrich_done)) if enrich_done else None),
        "outreach_attempted": sum(1 for d in deals if d.stage not in (
            "new_property", "owner_identified", "enrichment_needed", "ready_for_outreach")),
        "conversations_active": sum(1 for d in deals if d.stage in (
            "outreach_active", "seller_engaged", "qualifying", "negotiating")),
        "interested_sellers": sum(1 for p in profiles if p.ai_intent in
                                  ("interested", "qualified_opportunity")),
        "qualified_opportunities": sum(1 for p in profiles
                                       if p.qualification_band in ("high", "medium")),
        "needs_review": sum(1 for p in profiles if p.qualification_band == "review"
                            or p.needs_human),
        "offers_awaiting_approval": approvals_pending,
        "offers_made": sum(1 for d in deals if d.proposed_offer is not None),
        "contracts_pending": sum(1 for d in deals if d.contract_status in
                                 ("preparing", "sent")),
        "under_contract": by_stage.get("under_contract", 0),
        "buyers_total": buyers_total,
        "buyers_matched": len({m.deal_id for m in matches if not m.disqualified}),
        "buyer_responses": sum(1 for o in outreach if o.status in (
            "replied", "interested", "passed", "requested_info",
            "offer_submitted", "accepted")),
        "buyer_interested": sum(1 for o in outreach if o.status in
                                ("interested", "offer_submitted", "accepted")),
        "assignment_pending": by_stage.get("assignment_pending", 0),
        "title_closing_pending": by_stage.get("title_closing", 0),
        "closed_deals": len(closed),
        "gross_assignment_fees": float(gross_fees),
        "estimated_pipeline_value": float(pipeline_value),
        "pipeline_value_basis": ("Sum of the expected wholesale fee on each open "
                                 "deal. Not a forecast and not a property value."),
        "by_stage": [
            {"key": s["key"], "label": s["label"], "count": by_stage.get(s["key"], 0),
             "terminal": s["terminal"]}
            for s in pipeline.resolve_stages(settings)
        ],
        "sellers_by_band": {
            band: sum(1 for p in profiles if p.qualification_band == band)
            for band in ("high", "medium", "low", "review", "excluded")
        },
    }



# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — "what needs to happen next", answered once
# ══════════════════════════════════════════════════════════════════════════

def next_action(db: Session, org_id: str, deal: WholesaleDeal,
                seller: Optional[WholesaleSellerProfile] = None,
                lead: Optional[Lead] = None) -> Dict[str, Any]:
    """The single next thing this deal is waiting on.

    THIS IS A READING, NOT A RECOMMENDATION. Every branch below is a fact
    already stored on the deal — a pending approval row, an empty ARV, a
    matched buyer nobody has contacted, a closing date this week. Nothing here
    predicts, scores or invents an errand: when a deal genuinely has nothing
    outstanding it says so.

    It lives in the service rather than in either screen because the deal room
    header and the Command Center's active-deal list must never disagree about
    what a deal needs. One function, two callers.

    Returns {label, detail, tab, tone} — `tone` is one of ok / attention /
    urgent, and `tab` is where the deal room should open to act on it.
    """
    stage = deal.stage or ""

    if stage == pipeline.STAGE_CLOSED:
        return _act("Closed", "Nothing outstanding.", "overview", "ok")
    if stage == pipeline.STAGE_DEAD:
        detail = (deal.lost_reason or "").replace("_", " ") or "No reason recorded"
        return _act("Dead", detail.capitalize(), "overview", "ok")

    pending = (db.query(WholesaleApproval)
               .filter(WholesaleApproval.organization_id == org_id,
                       WholesaleApproval.deal_id == deal.id,
                       WholesaleApproval.status == "pending")
               .order_by(WholesaleApproval.created_at.asc()).first())
    if pending is not None:
        amount = analysis.money(pending.amount)
        return _act(
            "Approve the %s" % (pending.kind or "request").replace("_", " "),
            "Waiting on a person%s." % (" — %s" % _money_words(amount) if amount else ""),
            "offer", "urgent")

    if seller is None:
        return _act("Add the owner", "No owner on this property yet.",
                    "seller", "attention")

    if lead is not None and (lead.status or "").lower() == "dnc":
        return _act("Owner opted out",
                    "Nothing further will be sent to them.", "seller", "attention")

    if not (lead and (lead.phone or lead.email)):
        return _act("Find a way to reach the owner",
                    "No phone or email on file.", "seller", "attention")

    if getattr(seller, "needs_human", False):
        return _act("Read the seller's reply",
                    seller.needs_human_reason or "Flagged for a person.",
                    "seller", "urgent")

    return _next_by_stage(db, org_id, deal, stage)


def _act(label, detail, tab, tone):
    return {"label": label, "detail": detail, "tab": tab, "tone": tone}


def _money_words(value) -> str:
    return "" if value is None else "${:,.0f}".format(float(value))


def _next_by_stage(db: Session, org_id: str, deal: WholesaleDeal,
                   stage: str) -> Dict[str, Any]:
    """Where the deal is in the pipeline decides what it is waiting for."""
    from datetime import date as _date

    # ── Before there is a number to offer ───────────────────────────────────
    if analysis.money(deal.arv) is None:
        comps = (db.query(WholesaleComp)
                 .filter(WholesaleComp.organization_id == org_id,
                         WholesaleComp.deal_id == deal.id,
                         WholesaleComp.included.is_(True)).count())
        if comps:
            return _act("Recalculate the ARV",
                        "%d comp%s on file and no ARV yet."
                        % (comps, "" if comps == 1 else "s"), "analysis", "attention")
        return _act("Add comparable sales",
                    "No ARV can be worked out without them.", "analysis", "attention")

    if analysis.money(deal.repair_estimate) is None:
        return _act("Estimate the repairs",
                    "The offer is not trustworthy without it.", "analysis", "attention")

    # ── Acquisition ─────────────────────────────────────────────────────────
    if stage in ("new_property", "owner_identified", "enrichment_needed",
                 "ready_for_outreach", "outreach_active", "seller_engaged",
                 "qualifying", "qualified", "analysis"):
        if analysis.money(deal.proposed_offer) is None:
            return _act("Make an offer",
                        "The analysis is done; nothing has been offered yet.",
                        "offer", "attention")
        return _act("Send the offer for approval",
                    "%s proposed." % _money_words(analysis.money(deal.proposed_offer)),
                    "offer", "attention")

    if stage in ("offer_review", "offer_sent", "negotiating"):
        last = (db.query(WholesaleOffer)
                .filter(WholesaleOffer.organization_id == org_id,
                        WholesaleOffer.deal_id == deal.id)
                .order_by(WholesaleOffer.created_at.desc()).first())
        if last is not None and last.direction == "seller":
            return _act("Answer the seller's counter",
                        "They countered at %s."
                        % _money_words(analysis.money(last.amount)),
                        "offer", "urgent")
        return _act("Wait on the seller",
                    "Offer presented. Record their answer when it comes.",
                    "offer", "ok")

    # ── Under contract → disposition ────────────────────────────────────────
    if stage == "under_contract":
        signed = (db.query(WholesaleDocument)
                  .filter(WholesaleDocument.organization_id == org_id,
                          WholesaleDocument.deal_id == deal.id,
                          WholesaleDocument.doc_type == "purchase_contract",
                          WholesaleDocument.file_id.isnot(None)).count())
        if not signed:
            return _act("Upload the signed purchase contract",
                        "The contract is recorded but no signed copy is attached.",
                        "documents", "attention")
        return _dispo_action(db, org_id, deal)

    if stage in ("disposition", "buyer_identified"):
        return _dispo_action(db, org_id, deal)


    if stage == "assignment_pending":
        return _act("Get the assignment signed",
                    "Buyer chosen; the agreement is outstanding.",
                    "closing", "attention")

    if stage == "title_closing":
        return _closing_action(deal)

    return _act("Move the deal on", "Pick the next stage when it is ready.",
                "overview", "ok")


def _dispo_action(db: Session, org_id: str, deal: WholesaleDeal) -> Dict[str, Any]:
    """Matching → sending → responses → picking. In that order, honestly."""
    matches = (db.query(WholesaleBuyerMatch)
               .filter(WholesaleBuyerMatch.organization_id == org_id,
                       WholesaleBuyerMatch.deal_id == deal.id,
                       WholesaleBuyerMatch.disqualified.is_(False)).count())
    if not matches:
        return _act("Run buyer matching",
                    "No buyers have been matched to this deal yet.",
                    "buyers", "attention")

    rows = (db.query(WholesaleBuyerOutreach)
            .filter(WholesaleBuyerOutreach.organization_id == org_id,
                    WholesaleBuyerOutreach.deal_id == deal.id).all())
    sent = [r for r in rows if r.sent_at is not None]
    if not sent:
        return _act("Send the deal to %d matched buyer%s"
                    % (matches, "" if matches == 1 else "s"),
                    "Nothing has gone out yet.", "buyers", "attention")

    offers = [r for r in rows if analysis.money(r.offer_amount) is not None]
    if offers:
        if deal.assigned_buyer_id:
            return _act("Get the assignment signed",
                        "Buyer chosen at %s."
                        % _money_words(analysis.money(deal.buyer_price)),
                        "closing", "attention")
        best = max(analysis.money(r.offer_amount) for r in offers)
        return _act("Review %d buyer offer%s" % (len(offers),
                                                 "" if len(offers) == 1 else "s"),
                    "Highest is %s. A person picks the buyer." % _money_words(best),
                    "buyers", "urgent")

    return _act("Chase the %d buyer%s you contacted"
                % (len(sent), "" if len(sent) == 1 else "s"),
                "Sent, nothing back yet.", "buyers", "ok")


def _closing_action(deal: WholesaleDeal) -> Dict[str, Any]:
    """Title and the calendar. A date that has passed is not 'upcoming'."""
    from datetime import date as _date

    status_value = (deal.title_status or "").lower()
    if status_value in ("issue", "issue_found"):
        return _act("Clear the title issue",
                    (deal.title_issues or "Title has flagged something.")[:120],
                    "closing", "urgent")
    if not deal.title_company:
        return _act("Open title", "No title company on the deal yet.",
                    "closing", "attention")

    when = deal.closing_date
    if when:
        days = (when - _date.today()).days
        if days < 0:
            return _act("Closing date has passed",
                        "Scheduled for %s. Record the close or move the date."
                        % when.isoformat(), "closing", "urgent")
        if days == 0:
            return _act("Closing today", when.isoformat(), "closing", "urgent")
        if days <= 7:
            return _act("Closing in %d day%s" % (days, "" if days == 1 else "s"),
                        when.isoformat(), "closing", "attention")
        return _act("Closing %s" % when.isoformat(),
                    "%d days out." % days, "closing", "ok")

    return _act("Schedule the closing", "Title is open; no date set.",
                "closing", "attention")



# ══════════════════════════════════════════════════════════════════════════
# PHASE 3 — the operating dashboard
# ══════════════════════════════════════════════════════════════════════════

# Stages a deal is actively being WORKED in. Not a display list: the Command
# Center's "active deals" figure and its list must agree, so they read this.
ACTIVE_STAGES = tuple(
    s for s in (
        "owner_identified", "enrichment_needed", "ready_for_outreach",
        "outreach_active", "seller_engaged", "qualifying", "qualified",
        "analysis", "offer_review", "offer_sent", "negotiating",
        "under_contract", "disposition", "buyer_identified",
        "assignment_pending", "title_closing",
    ))


def operating_board(db: Session, org_id: str, *, filters: Dict[str, Any] = None,
                    include_test: bool = False, limit: int = 12) -> Dict[str, Any]:
    """What a wholesaler needs on one screen to run the day.

    This is NOT a second dashboard and it does not recount anything
    `dashboard()` already counts — it answers a different question. `dashboard`
    says how big the operation is; this says what to do about it this morning:
    which deals are live, what each one is waiting on, what closes soon, and
    what has gone quiet.

    Every figure is org-scoped and honours the sandbox rule exactly as
    `dashboard` does. Nothing is generated: a quiet board means a quiet week.
    """
    from datetime import date as _date, timedelta

    filters = filters or {}

    def base():
        q = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
        if not include_test:
            q = q.filter(WholesaleDeal.is_test.isnot(True))
        return q

    deals = base().all()
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.organization_id == org_id).all()}

    # Cover photos, fetched once for the whole board rather than per row.
    covers: Dict[str, str] = {}
    for f in (db.query(WholesaleFile)
              .filter(WholesaleFile.organization_id == org_id,
                      WholesaleFile.kind == "property_photo",
                      WholesaleFile.is_primary.is_(True)).all()):
        if f.property_id:
            covers[f.property_id] = "/wholesale/files/%s" % f.id

    leads = {}
    profiles = {}
    for pr in (db.query(WholesaleSellerProfile)
               .filter(WholesaleSellerProfile.organization_id == org_id).all()):
        profiles[pr.id] = pr
    lead_ids = [d.seller_lead_id for d in deals if d.seller_lead_id]
    if lead_ids:
        for ld in db.query(Lead).filter(Lead.id.in_(lead_ids)).all():
            leads[ld.id] = ld

    # When something last happened on each deal. This is read off the event
    # log, so "gone quiet" is a fact about the record rather than a guess.
    last_event: Dict[str, Any] = {}
    if deals:
        from sqlalchemy import func as _func
        for deal_id, when in (db.query(WholesaleEvent.deal_id,
                                       _func.max(WholesaleEvent.created_at))
                              .filter(WholesaleEvent.organization_id == org_id,
                                      WholesaleEvent.deal_id.isnot(None))
                              .group_by(WholesaleEvent.deal_id).all()):
            if when is not None:
                last_event[deal_id] = when

    settings = resolve_settings(db, org_id, commit=False)
    return _assemble_board(db, org_id, deals, props, covers, profiles, leads,
                           limit, settings, last_event)


# A live deal with nothing on its event log for this many days is called
# stalled. It is a stated threshold, not a model, and the screen says it.
QUIET_DAYS = 7


def _deal_risk(deal, last_seen, today):
    """Why a live deal is at risk — stated as a fact, never as a score.

    Two signals, both read straight off the record: a closing date that has
    already passed, and an event log that has carried nothing for this deal in
    a week. Nothing here is predicted, and a deal with neither signal returns
    None rather than a reassuring green badge.
    """
    if deal.closing_date and deal.closing_date < today:
        days = (today - deal.closing_date).days
        return {"level": "overdue", "days": days,
                "label": "Closing date passed %d day%s ago"
                         % (days, "" if days == 1 else "s")}
    when = last_seen or getattr(deal, "created_at", None)
    if when is None:
        return None
    day = when.date() if hasattr(when, "date") else when
    try:
        quiet = (today - day).days
    except TypeError:
        return None
    if quiet >= QUIET_DAYS:
        return {"level": "stalled", "days": quiet,
                "label": "Nothing recorded for %d days" % quiet}
    return None


def _assemble_board(db, org_id, deals, props, covers, profiles, leads, limit,
                    settings, last_event=None):
    from datetime import date as _date, timedelta

    today = _date.today()
    horizon = today + timedelta(days=30)
    last_event = last_event or {}

    active, closings, attention, at_risk = [], [], [], []
    headline = {
        "pipeline_value": 0.0,      # expected fee on open deals
        "fees_collected": 0.0,      # money that actually landed
        # Closed, and still waiting for the money. Counted separately because a
        # closing date is not a payment and this board must never imply it is.
        "awaiting_payment": 0,
        "awaiting_payment_value": 0.0,
        "active_deals": 0,
        "under_contract": 0,
        "awaiting_approval": 0,
        "closing_30_days": 0,
    }

    pending_by_deal = {}
    for a in (db.query(WholesaleApproval)
              .filter(WholesaleApproval.organization_id == org_id,
                      WholesaleApproval.status == "pending").all()):
        pending_by_deal.setdefault(a.deal_id, []).append(a)

    for deal in deals:
        prop = props.get(deal.property_id)
        stage = deal.stage or ""

        if stage == pipeline.STAGE_CLOSED:
            fee = analysis.money(deal.wholesale_fee_collected)
            if fee is not None:
                headline["fees_collected"] += float(fee)
            else:
                # Closed with nothing typed against it. The expected figure is
                # reported as OUTSTANDING, never as collected.
                headline["awaiting_payment"] += 1
                owed = (analysis.money(deal.assignment_fee)
                        or analysis.money(deal.desired_wholesale_fee))
                if owed is not None:
                    headline["awaiting_payment_value"] += float(owed)
            continue
        if stage == pipeline.STAGE_DEAD:
            continue

        if stage in ACTIVE_STAGES:
            headline["active_deals"] += 1
        if stage == "under_contract":
            headline["under_contract"] += 1
        if deal.id in pending_by_deal:
            headline["awaiting_approval"] += len(pending_by_deal[deal.id])

        # Expected fee: the assignment spread when a buyer is chosen, otherwise
        # the fee this workspace is aiming for. Never a property value, and
        # never counted as revenue — the label on the tile says "expected".
        expected = analysis.money(deal.assignment_fee)
        if expected is None:
            expected = analysis.money(deal.desired_wholesale_fee)
        if expected is not None:
            headline["pipeline_value"] += float(expected)

        profile = profiles.get(deal.seller_profile_id)
        lead = leads.get(deal.seller_lead_id)
        action = next_action(db, org_id, deal, profile, lead)

        row = {
            "deal_id": deal.id,
            "property_id": deal.property_id,
            "address": _address_of(prop),
            "city": getattr(prop, "city", None),
            "state": getattr(prop, "state", None),
            "photo_url": covers.get(deal.property_id),
            "stage": stage,
            "stage_label": pipeline.stage_label(settings, stage),
            "band": getattr(profile, "qualification_band", None),
            "arv": _f(analysis.money(deal.arv)),
            "contract_price": _f(analysis.money(deal.contract_price)),
            "buyer_price": _f(analysis.money(deal.buyer_price)),
            "expected_fee": _f(expected),
            "closing_date": deal.closing_date.isoformat() if deal.closing_date else None,
            "next_action": action,
            "risk": _deal_risk(deal, last_event.get(deal.id), today),
            "is_test": bool(deal.is_test),
        }
        active.append(row)

        if action["tone"] in ("urgent", "attention"):
            attention.append(row)

        if row["risk"]:
            at_risk.append(row)

        if deal.closing_date and today <= deal.closing_date <= horizon:
            headline["closing_30_days"] += 1
            closings.append(row)

    headline["at_risk"] = len(at_risk)
    return _finish_board(headline, active, attention, closings, at_risk, limit)


def _finish_board(headline, active, attention, closings, at_risk, limit):
    """Order the lists the way a person reads them."""
    tone_rank = {"urgent": 0, "attention": 1, "ok": 2}
    risk_rank = {"overdue": 0, "stalled": 1}

    attention.sort(key=lambda r: (tone_rank.get(r["next_action"]["tone"], 3),
                                  r["address"] or ""))
    closings.sort(key=lambda r: r["closing_date"] or "9999")
    # Overdue closings first, then the longest silences.
    at_risk.sort(key=lambda r: (risk_rank.get(r["risk"]["level"], 9),
                                -(r["risk"]["days"] or 0)))
    # Active deals lead with whatever is most urgent, then by the biggest
    # expected fee — the two things that decide what gets worked first.
    active.sort(key=lambda r: (tone_rank.get(r["next_action"]["tone"], 3),
                               -(r["expected_fee"] or 0)))

    return {
        "headline": headline,
        "active_deals": active[:limit],
        "active_total": len(active),
        "needs_attention": attention[:limit],
        "attention_total": len(attention),
        "upcoming_closings": closings[:limit],
        "closings_total": len(closings),
        "at_risk": at_risk[:limit],
        "at_risk_total": len(at_risk),
        "quiet_days": QUIET_DAYS,
    }


def _address_of(prop) -> Optional[str]:
    if prop is None:
        return None
    bits = [getattr(prop, "street_address", None)]
    tail = ", ".join(x for x in (getattr(prop, "city", None),
                                 getattr(prop, "state", None)) if x)
    if tail:
        bits.append(tail)
    return ", ".join(b for b in bits if b) or None


def _f(value):
    return None if value is None else float(value)


def pipeline_value_by_stage(db: Session, org_id: str, *,
                            include_test: bool = False) -> List[Dict[str, Any]]:
    """Count AND expected value per stage, in pipeline order.

    A stage with eleven dead-end leads and a stage with two deals under
    contract are not the same size, and a board that shows only counts says
    they are.
    """
    settings = resolve_settings(db, org_id, commit=False)
    stages = pipeline.resolve_stages(settings)

    q = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
    if not include_test:
        q = q.filter(WholesaleDeal.is_test.isnot(True))

    counts: Dict[str, int] = {}
    values: Dict[str, float] = {}
    for deal in q.all():
        counts[deal.stage] = counts.get(deal.stage, 0) + 1
        expected = analysis.money(deal.assignment_fee)
        if expected is None:
            expected = analysis.money(deal.desired_wholesale_fee)
        if expected is not None:
            values[deal.stage] = values.get(deal.stage, 0.0) + float(expected)

    return [{"key": s["key"], "label": s["label"], "terminal": s.get("terminal", False),
             "count": counts.get(s["key"], 0),
             "value": round(values.get(s["key"], 0.0), 2)}
            for s in stages]


# WHAT BELONGS ON THE COMMAND CENTER, AND WHAT BELONGS IN AUDIT HISTORY.
#
# Every event in `wholesale_events` is real and every one is worth keeping. They
# are not equally worth a wholesaler's first screen of the morning. "Photo
# uploaded" and "file updated" are how you reconstruct what happened to a
# record; "seller replied", "buyer offered", "fee collected" are what happened
# to the BUSINESS. The board shows the second kind. The audit screen still
# shows everything, unfiltered, because that is its job.
BUSINESS_ACTIONS = frozenset((
    # The seller side
    "seller.attached", "seller.qualified", "seller.opted_out",
    # Analysis becoming a decision
    "offer.recorded", "offer.updated",
    "approval.requested", "approval.decided",
    # The deal moving
    "deal.stage_changed", "contract.updated",
    # Disposition
    "buyers.matched", "disposition.composed", "buyer_outreach.sent",
    "outreach.sent", "outreach.blocked", "outreach.failed",
    "buyer.response_recorded", "buyer.responded", "buyer.selected",
    "assignment.set",
    # Anything that left the building
    "publication.published", "share_link.created", "share_link.revoked",
    # Title, closing, money
    "title.updated", "deal.closed", "deal.fee_collected",
    "economics.corrected", "deal.lost",
))


def recent_activity(db: Session, org_id: str, *, limit: int = 12,
                    business_only: bool = True) -> List[Dict[str, Any]]:
    """The last material things that happened, in this workspace only.

    Read from `wholesale_events`, which already carries the actor TYPE — so a
    reply read by the AI and a buyer chosen by a person are distinguishable on
    the board rather than both appearing as "the system did something".
    """
    q = (db.query(WholesaleEvent)
         .filter(WholesaleEvent.organization_id == org_id))
    if business_only:
        q = q.filter(WholesaleEvent.action.in_(tuple(BUSINESS_ACTIONS)))
    rows = q.order_by(WholesaleEvent.created_at.desc()).limit(limit).all()
    return [{
        "id": e.id,
        "action": e.action,
        "actor_type": e.actor_type,
        "actor_label": e.actor_label,
        "summary": e.summary,
        "deal_id": e.deal_id,
        "property_id": e.property_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]
