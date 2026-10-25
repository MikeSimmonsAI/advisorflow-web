"""Is the acquisition machine ready to run on its own - and what is in the way?

One read-only answer built from what already exists:
    the controls (start / pause / stop are the existing switches, enforced
    server-side), the channel truth (what each channel can ACTUALLY do), test
    mode (sandbox), the scheduled hunts, contact enrichment, the owners'
    consents of record, and where every property sits in the workflow.

It changes nothing and sends nothing.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func

from app.services.evosense import common as C

HOW = {
    "start": "Controls: turn off 'Pause everything' (and the channel's own pause). Hunts run on each "
             "strategy's schedule; outreach still needs a permitted channel per owner.",
    "pause": "Controls: 'Pause everything' - hunting, lookups and outreach stop at once; replies are still "
             "saved and opt-outs are still honoured.",
    "stop": "Controls: pause the channel (SMS / email / voice) or set a strategy to paused or manual.",
    "test_mode": "Sandbox: synthetic properties and owners; messages are written and labelled, never sent.",
    "human_override": "Any property: open it and act - a person's action always wins; a hand-off pauses "
                      "automated outreach for that owner.",
    "retry": "Replies that could not be read are held and re-read by the scheduler (or the Retry button); "
             "a failed source is retried after its back-off; nothing is guessed.",
}


def readiness(db, org_id: str) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSensePermissionLink, EvoSenseProperty
    from app.services.evosense import capabilities as CAP
    from app.services.evosense import opportunity as OP
    from app.services.evosense import scheduler as SCH
    from app.services.evosense import views as V
    ctl = C.controls(db, org_id)
    channels = V.channel_truth(db, org_id, ctl)
    sandbox = V.sandbox_state(db, org_id)
    live = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                             EvoSenseProperty.archived_at.is_(None),
                                             EvoSenseProperty.is_test.is_(False))
    stages = dict(live.with_entities(EvoSenseProperty.workflow_state, func.count(EvoSenseProperty.id))
                  .group_by(EvoSenseProperty.workflow_state).all())
    nexts = dict(live.with_entities(EvoSenseProperty.next_best_action, func.count(EvoSenseProperty.id))
                 .group_by(EvoSenseProperty.next_best_action).all())
    consents = (db.query(func.count(EvoSensePermissionLink.id))
                .filter(EvoSensePermissionLink.organization_id == org_id,
                        EvoSensePermissionLink.status == "confirmed").scalar() or 0)
    try:
        enrich = CAP.is_operational(db, org_id, C.CONTACT_ENRICHMENT)
    except Exception:  # noqa: BLE001
        enrich = False
    try:
        hunts = SCH.status(db, org_id)
    except Exception:  # noqa: BLE001
        hunts = []
    blockers: List[str] = []
    if ctl.paused_all:
        blockers.append("Everything is paused (Controls).")
    sms = channels.get("paused_sms") or {}
    if not sms.get("operational"):
        blockers.append("SMS: %s" % sms.get("why"))
    if not channels.get("paused_email", {}).get("built"):
        blockers.append("Email outreach is not a channel EvoSense sends on; first contact is a person's call.")
    if not enrich:
        blockers.append("No contact-enrichment provider is operational (paid lookups are off until approved); "
                        "phone numbers come from free records, CSV exports or a person.")
    if not consents:
        blockers.append("No owner has confirmed text permission yet - SMS can only follow a call and a "
                        "permission link.")
    ready_now = stages.get("OUTREACH_QUEUED", 0)
    return {
        "controls": V.controls_payload(ctl), "channels": channels, "sandbox": sandbox,
        "test_mode_available": True, "how": HOW,
        "enrichment_operational": bool(enrich), "consents_confirmed": consents,
        "scheduled_hunts": hunts,
        "workflow": {k or "NOT_ASSESSED": v for k, v in stages.items()},
        "workflow_labels": OP.WORKFLOW_LABELS,
        "next_actions": {(k or "not_assessed"): v for k, v in nexts.items()},
        "next_action_labels": OP.ACTIONS,
        "eligible_for_outreach_now": ready_now,
        "blockers": blockers,
        "can_run_unattended": bool(not ctl.paused_all and sms.get("operational") and ready_now),
        "notice": "Read-only. A public foreclosure notice is not permission to text or auto-dial; outreach "
                  "only reaches owners whose channel is permitted, through the platform's own gates.",
    }


PROPSTREAM = {
    "key": "propstream", "label": "PropStream",
    "state": "CSV IMPORT AVAILABLE",
    "why": "PropStream does not publish a developer API we could find. Export a list (with its skip-traced "
           "phones, if bought there) and import it - the PropStream CSV columns are recognised and every value "
           "is recorded as the export's claim. An API adapter can be added if PropStream provides credentials "
           "and documentation.",
    "cost": "billed by PropStream, outside this platform",
}


def enrichment_queue(db, org_id: str, *, limit: int = 100) -> Dict[str, Any]:
    """Who needs contact data first (the opportunity view's own order), what
    was already tried, and which providers could do it - with their cost and
    what stands in the way. Read-only: nothing is bought from here; a paid
    lookup still goes through the existing per-property approval and budgets."""
    from app.models.evosense_models import EvoSenseEnrichmentDecision, EvoSenseProperty
    from app.services.evosense import capabilities as CAP
    limit = max(1, min(int(limit or 100), 500))
    base = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                             EvoSenseProperty.archived_at.is_(None),
                                             EvoSenseProperty.next_best_action == "find_contact")
    total = base.count()
    props = (base.order_by(EvoSenseProperty.priority_score.desc().nullslast(),
                           EvoSenseProperty.urgency_score.desc().nullslast())
             .limit(limit).all())
    ids = [p.id for p in props] or ["-"]
    last: Dict[str, Any] = {}
    for d in (db.query(EvoSenseEnrichmentDecision)
              .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                      EvoSenseEnrichmentDecision.property_id.in_(ids),
                      EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT)
              .order_by(EvoSenseEnrichmentDecision.created_at.asc()).all()):
        last[d.property_id] = d
    from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership
    owner_of: Dict[str, Any] = {}
    for pid, o in (db.query(EvoSenseOwnership.property_id, EvoSenseOwner)
                   .join(EvoSenseOwner, EvoSenseOwner.id == EvoSenseOwnership.owner_id)
                   .filter(EvoSenseOwnership.organization_id == org_id, EvoSenseOwnership.property_id.in_(ids),
                           EvoSenseOwnership.is_current.is_(True)).all()):
        owner_of.setdefault(pid, o)
    rows = []
    for i, p in enumerate(props, 1):
        d = last.get(p.id)
        owner = owner_of.get(p.id)
        rows.append({"rank": i, "property_id": p.id, "address": p.street_address, "city": p.city,
                     "county": p.county, "priority": p.priority_score, "urgency": p.urgency_score,
                     "sale_deadline": p.sale_deadline.isoformat() if p.sale_deadline else None,
                     "owner_known": owner is not None, "owner_type": getattr(owner, "owner_type", None),
                     "is_test": bool(p.is_test),
                     "last_attempt": ({"decision": d.decision, "outcome": d.outcome, "provider": d.provider_key,
                                       "at": d.created_at.isoformat() + "Z" if d.created_at else None,
                                       "reasons": C.jload(d.reasons, []) or []} if d is not None else None)})
    providers = []
    try:
        for cap in CAP.matrix(db, org_id):
            if C.CONTACT_ENRICHMENT in (cap.get("engine_capabilities") or []):
                for r in cap["providers"]:
                    if any(x.get("provider") == r.get("provider") for x in providers):
                        continue            # one row per provider, whatever capabilities it serves
                    providers.append({k: r.get(k) for k in ("provider", "label", "state", "operational",
                                                            "evaluation_only", "connector_kind", "why",
                                                            "cost", "costs") if k in r})
    except Exception:  # noqa: BLE001
        providers = []
    ctl = C.controls(db, org_id)
    return {"needing_contact": total, "rows": rows, "providers": providers + [PROPSTREAM],
            "paid_lookups_paused": bool(ctl.paused_paid_data or ctl.paused_all),
            "match_rule": "A phone or email is attached only when the vendor's name matches the owner of record "
                          "(matched); a near match is labelled probable; anything else is refused and recorded "
                          "as no verified match.",
            "notice": "Nothing is bought from this list. Each paid lookup still needs its own approval and "
                      "stays inside the strategy and workspace budgets."}
