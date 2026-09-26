"""Start working an owner — through the platform's EXISTING machinery.

STRATEGY decides WHAT to hunt. CAMPAIGN decides HOW to work it. EvoSense has
no sender of its own: a real (non-test) owner becomes a Lead and is enrolled
in the organization's existing cadence (`cadence_service.start_cadence`),
which applies its own DNC / suppression / capacity / quiet-hours / sending
gates on every touch. EvoSense adds eligibility in front of that, never
instead of it.

SANDBOX. A sandbox owner's Lead is a TEST lead, and the cadence engine
refuses test leads on purpose (app/services/test_records.py). So a sandbox
engagement records its opening message as `delivery = sandbox_simulated` —
written, visible, labelled, and never sent anywhere.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.models.evosense_models import (EvoSenseEngagement, EvoSenseMessage, EvoSenseOwner,
                                        EvoSensePerson)
from app.models.models import Lead
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import eligibility as EL
from app.services.evosense import strategy as ST

# The opening message is a FOLLOW-UP TO THE SELLER'S OWN INQUIRY. EvoSense only
# ever reaches a real owner by SMS when that number holds seller-program consent
# (see eligibility.py + wholesale_sms.py), so there is no cold opener here and
# none may be added: the registered campaign does not cover one.
OPENER = ("{sender}: Thanks for your property inquiry about {address}. Can you confirm "
          "the condition and your preferred timeline to sell? Reply STOP to opt out, "
          "HELP for help.")
FOLLOW_UP = ("{sender}: Following up on {address}. When we spoke you said: "
             "“{context}”. Is now a better time to talk? Reply STOP to opt out.")


def _split_name(full: Optional[str]):
    if not full or full.lower().startswith(("unnamed", "registered agent", "estate of")):
        return None, None
    toks = [t for t in full.replace(",", " ").split() if not (len(t.rstrip(".")) == 1)]
    if not toks:
        return None, None
    return toks[0], (toks[-1] if len(toks) > 1 else None)


def _converge(db, prop, person: EvoSensePerson, cp, lead) -> None:
    """Point EvoSense's person and contact point at the org contact that is
    this person in Universal Intake (convergence step 5). EvoSense keeps the
    owner-of-record relationship; it never becomes a second contact database."""
    try:
        from app.services.intake.contacts import contact_for_lead
        c = contact_for_lead(db, prop.organization_id, lead)
    except Exception:  # noqa: BLE001 - a missing link is a gap, not a failure
        return
    if c is None:
        return
    person.converged_contact_ref = c.id
    if cp is not None:
        cp.converged_contact_ref = c.id


def _capture(db, prop, person: EvoSensePerson, cp, first, last, *, user=None):
    """WHO THIS OWNER IS, through Universal Intake: the one matcher (an owner
    already in the workspace is reused, never duplicated), the org contact, a
    batch that can be rolled back. Returns (lead, created) or (None, False).

    Sandbox owners are synthetic and never enter the contact database. Any
    intake error falls back to the direct path; it is logged, never raised."""
    if prop.is_test:
        return None, False
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == prop.organization_id).first()
    # capture_one commits as it goes; persist the engagement state written so
    # far (eligibility, channel) first so a fallback rollback cannot lose it.
    db.commit()
    try:
        from app.services.intake.capture import capture_one
        cap = capture_one(db, org, {
            "first_name": first, "last_name": last,
            "phone": cp.value if cp.kind == "phone" else None,
            "email": cp.value if cp.kind == "email" else None,
            "street_address": prop.street_address, "city": prop.city, "state": prop.state,
            "zip_code": prop.zip_code},
            source="evosense", source_detail="EvoSense property owner",
            list_name="EvoSense owners", classification="cold_prospect",
            actor_label="EvoSense", external=False, explicit=True, user=user)
        return cap.lead, cap.lead_created
    except Exception as exc:  # noqa: BLE001 - intake must never stop outreach bookkeeping
        db.rollback()
        C.log.exception("evosense: universal intake failed; direct path used")
        try:
            from app.services.wholesale_seller_intake import _safe_error
            C.log_event(db, prop.organization_id, "owner.intake_error", property_id=prop.id,
                        user=user, is_test=prop.is_test,
                        summary="Universal Intake failed; the owner's Lead was created directly",
                        details={"error": _safe_error(exc)})
        except Exception:  # noqa: BLE001
            pass
        return None, False


def ensure_lead(db, prop, person: EvoSensePerson, cp, *, user=None) -> Lead:
    """The seller becomes a Lead (Phase 6 decision) — only now, when worked.
    Costs a lead seat exactly like `wholesale_service.attach_seller`."""
    if person.lead_id:
        lead = db.query(Lead).filter(Lead.id == person.lead_id,
                                     Lead.organization_id == prop.organization_id).first()
        if lead is not None:
            if not person.converged_contact_ref:
                _converge(db, prop, person, cp, lead)
            return lead
    from app.services import plan_limits
    plan_limits.require_capacity_for_org_id(db, prop.organization_id, plan_limits.LIMIT_LEADS, adding=1)
    from app.services.dedup_service import normalize_phone
    first, last = _split_name(person.full_name)
    lead, created = _capture(db, prop, person, cp, first, last, user=user)
    if lead is not None:
        if created:
            lead.source_category = "evosense"
            lead.relationship_type = "cold_lead"
            if not lead.assigned_to_id and user is not None:
                lead.assigned_to_id = user.id
            if not lead.notes:
                lead.notes = "Property owner found by EvoSense (%s)." % (
                    prop.street_address or "property")
        person.lead_id = lead.id
        _converge(db, prop, person, cp, lead)
        return lead
    phone = cp.value if cp.kind == "phone" else None
    lead = Lead(
        organization_id=prop.organization_id,
        assigned_to_id=getattr(user, "id", None),
        first_name=first, last_name=last,
        phone=(normalize_phone(phone) or phone) if phone else None, phone_raw=phone,
        email=cp.value if cp.kind == "email" else None,
        status="new", contact_channel="sms" if phone else "email_only",
        street_address=prop.street_address, city=prop.city, state=prop.state,
        zip_code=prop.zip_code, relationship_type="cold_lead",
        source_category="evosense", source_file="evosense",
        notes="Property owner found by EvoSense (%s)." % (prop.street_address or "property"),
        is_test=bool(prop.is_test),
        test_note="EvoSense sandbox record" if prop.is_test else None,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(lead)
    db.flush()
    try:
        from app.services import master_contacts
        master_contacts.record_lead(db, lead, source="evosense",
                                    source_detail="EvoSense property owner",
                                    ingestion_path="evosense.outreach.ensure_lead")
    except Exception as exc:  # noqa: BLE001
        C.log.info("evosense: master contact record skipped: %s", exc)
    person.lead_id = lead.id
    return lead


def _engagement(db, prop, strategy, cp) -> EvoSenseEngagement:
    db.flush()
    eng = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == prop.organization_id,
                   EvoSenseEngagement.property_id == prop.id,
                   EvoSenseEngagement.status.in_(("pending", "blocked", "active", "responded",
                                                  "nurture", "handed_off")))
           .order_by(EvoSenseEngagement.created_at.desc()).first())
    if eng is None:
        eng = EvoSenseEngagement(organization_id=prop.organization_id, property_id=prop.id,
                                 strategy_id=getattr(strategy, "id", None), status="pending",
                                 is_test=bool(prop.is_test))
        db.add(eng)
    if cp is not None:
        eng.contact_point_id = cp.id
        eng.person_id = cp.person_id
        eng.owner_id = cp.owner_id
    db.flush()
    return eng


def start(db, prop, strategy, *, user=None, channel: str = "sms",
          actor_type: str = C.ACTOR_AUTOMATION) -> Dict[str, Any]:
    from app.services.evosense import evaluate as EV
    cp, _ = CT.best_contact(db, prop)
    eng = _engagement(db, prop, strategy, cp)
    if eng.status in ("active", "responded", "handed_off"):
        return {"started": False, "engagement": eng.id, "reason": "Already %s" % eng.status}
    elig = EL.check(db, prop, cp, strategy, channel=channel, engagement_id=eng.id)
    eng.eligibility = C.jdump(elig)
    eng.channel = channel
    if not elig["eligible"]:
        eng.status = "blocked"
        eng.blocked_reason = elig["primary_block"]
        C.log_event(db, prop.organization_id, "outreach.blocked", property_id=prop.id,
                    strategy_id=getattr(strategy, "id", None), user=user, actor_type=actor_type,
                    is_test=prop.is_test, summary="Outreach refused: %s" % elig["summary"],
                    details={"blocks": elig["blocks"]})
        EV.refresh_status(db, prop, strategy)
        return {"started": False, "engagement": eng.id, "reason": elig["summary"],
                "blocks": elig["blocks"]}

    person = db.query(EvoSensePerson).filter(EvoSensePerson.id == cp.person_id).first()
    owner = db.query(EvoSenseOwner).filter(EvoSenseOwner.id == cp.owner_id).first()
    lead = ensure_lead(db, prop, person, cp, user=user)
    eng.lead_id = lead.id
    first, _ = _split_name(person.full_name)
    resumed = eng.nurture_resumed_at is not None and eng.touches
    ctx = None
    if resumed:
        last_in = (db.query(EvoSenseMessage)
                   .filter(EvoSenseMessage.organization_id == prop.organization_id,
                           EvoSenseMessage.engagement_id == eng.id,
                           EvoSenseMessage.direction == "inbound")
                   .order_by(EvoSenseMessage.created_at.desc()).first())
        ctx = last_in.body if last_in else None
    from app.services import wholesale_sms
    body = (FOLLOW_UP if ctx else OPENER).format(
        first=first or "there",
        sender=wholesale_sms.program_brand(db, prop.organization_id)["brand"],
        address=prop.street_address or "your property",
        context=(ctx or "")[:140])

    if elig["mode"] == "sandbox_simulated":
        delivery, ref = "sandbox_simulated", None
        eng.delivery_mode = "sandbox_simulated"
    else:
        from app.services import cadence_service
        state = cadence_service.start_cadence(db, lead)
        if state is None:
            eng.status = "blocked"
            eng.blocked_reason = "CADENCE REFUSED"
            C.log_event(db, prop.organization_id, "outreach.blocked", property_id=prop.id,
                        user=user, actor_type=actor_type, is_test=prop.is_test,
                        summary="The platform cadence engine refused this lead.")
            EV.refresh_status(db, prop, strategy)
            return {"started": False, "engagement": eng.id,
                    "reason": "The platform cadence engine refused this lead."}
        delivery, ref = "cadence_enrolled", getattr(state, "id", None)
        eng.delivery_mode = "cadence"
        eng.campaign_key = ST.outreach_policy(strategy).get("campaign") if strategy else "platform_cadence"
        body = "Enrolled in the organization's SMS cadence; the cadence engine sends touch 1 " \
               "under its own gates."
    db.add(EvoSenseMessage(organization_id=prop.organization_id, engagement_id=eng.id,
                           property_id=prop.id, direction="outbound", channel=channel,
                           body=body, delivery=delivery, platform_ref=ref))
    eng.status = "active"
    eng.blocked_reason = None
    eng.touches = (eng.touches or 0) + 1
    eng.last_touch_at = C.now()
    owner.last_touch_at = C.now()
    C.log_event(db, prop.organization_id, "outreach.started", property_id=prop.id,
                strategy_id=getattr(strategy, "id", None), user=user, actor_type=actor_type,
                is_test=prop.is_test,
                summary=("Opening SMS written — SANDBOX, simulated, nothing sent"
                         if delivery == "sandbox_simulated" else "Owner enrolled in the SMS cadence"),
                details={"engagement": eng.id, "lead": lead.id, "delivery": delivery})
    EV.refresh_status(db, prop, strategy)
    return {"started": True, "engagement": eng.id, "delivery": delivery, "lead_id": lead.id}
