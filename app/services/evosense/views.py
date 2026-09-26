"""Read models for the three EvoSense screens.

NO FAKE VALUES. Every number here is counted from rows. Where there is
nothing to count the value is None (the UI shows "—" or a sentence), never
a placeholder, and a ratio with a zero denominator is None, not 0.

Bounded for 100k+ properties: the inbox is paginated on indexed columns
(organization_id + status / opportunity_score), bucket counts are one
GROUP BY, and per-row extras (signals, strategy names) are fetched for the
visible page only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, or_

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseEngagement, EvoSenseEnrichmentDecision,
                                        EvoSenseEvent, EvoSenseFact, EvoSenseFeedback,
                                        EvoSenseHandoff, EvoSenseIdentityReview, EvoSenseMessage,
                                        EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSensePerson, EvoSenseProperty, EvoSenseRun,
                                        EvoSenseScore, EvoSenseSignal, EvoSenseStrategy)
from app.services.evosense import budget as B
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import economics as ECO
from app.services.evosense import eligibility as EL
from app.services.evosense import providers as PV
from app.services.evosense import scoring as SC
from app.services.evosense import signals as SIG
from app.services.evosense import strategy as ST


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _ratio(a, b):
    return None if not b else round(a / b)


def controls_payload(ctl) -> Dict[str, Any]:
    return {k: getattr(ctl, k) for k in ("paused_all", "paused_discovery", "paused_paid_data",
                                         "paused_sms", "paused_email", "paused_voice",
                                         "paused_ai_replies", "org_daily_budget_cents",
                                         "org_monthly_budget_cents", "owner_touch_cap_days",
                                         "last_hunt_status")} | {
        "last_hunt_at": _iso(ctl.last_hunt_at),
        "score_weights": C.jload(getattr(ctl, "score_weights", None), None),
        "default_weights": {k: v["points"] for k, v in SIG.CATALOG.items()},
        "score_version": SC.PO_VERSION}


def sandbox_state(db, org_id) -> Dict[str, Any]:
    sandbox_on = [k for k in PV.SANDBOX_KEYS if PV.config(db, org_id, k).enabled]
    test_props = (db.query(func.count(EvoSenseProperty.id))
                  .filter(EvoSenseProperty.organization_id == org_id,
                          EvoSenseProperty.is_test.is_(True)).scalar() or 0)
    real = [r["key"] for r in PV.source_registry(db, org_id)["sources"]
            if r["connector_kind"] == C.REAL and r["state"] == PV.REG_HEALTHY]
    return {"sandbox_providers_enabled": sandbox_on, "sandbox_properties": int(test_props),
            "real_connectors": real, "banner": (
                "SANDBOX — every discovered property, owner and contact on this screen is synthetic "
                "test data from sandbox adapters. No real vendor is connected; nothing is sent."
                if (sandbox_on or test_props) else None)}


def spend(db, org_id) -> Dict[str, Any]:
    day, month = C.day_key(), C.month_key()
    by_provider = defaultdict(int)
    rows = (db.query(EvoSenseCostEntry.provider_key, EvoSenseCostEntry.connector_kind,
                     func.sum(EvoSenseCostEntry.total_cents), func.count(EvoSenseCostEntry.id))
            .filter(EvoSenseCostEntry.organization_id == org_id,
                    EvoSenseCostEntry.status == "charged",
                    EvoSenseCostEntry.period_month == month)
            .group_by(EvoSenseCostEntry.provider_key, EvoSenseCostEntry.connector_kind).all())
    providers = [{"provider": k, "label": PV.PROVIDERS[k].label if k in PV.PROVIDERS else k,
                  "connector_kind": ck, "cents": int(c or 0), "calls": int(n)} for k, ck, c, n in rows]
    for p in providers:
        by_provider[p["provider"]] += p["cents"]
    today = B.spent(db, org_id, day)
    this_month = B.spent(db, org_id, month)
    found = (db.query(func.count(EvoSenseEnrichmentDecision.id))
             .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                     EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT,
                     EvoSenseEnrichmentDecision.outcome == "found").scalar() or 0)
    handoffs = (db.query(func.count(EvoSenseHandoff.id))
                .filter(EvoSenseHandoff.organization_id == org_id).scalar() or 0)
    all_time = int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
                   .filter(EvoSenseCostEntry.organization_id == org_id,
                           EvoSenseCostEntry.status == "charged").scalar() or 0)
    refunded = int(db.query(func.count(EvoSenseCostEntry.id))
                   .filter(EvoSenseCostEntry.organization_id == org_id,
                           EvoSenseCostEntry.status == "failed_refunded").scalar() or 0)
    strategies = []
    for s in (db.query(EvoSenseStrategy)
              .filter(EvoSenseStrategy.organization_id == org_id,
                      EvoSenseStrategy.status.in_(("active", "paused"))).all()):
        st_today = B.spent(db, org_id, day, s.id)
        strategies.append({"id": s.id, "name": s.name, "status": s.status, "is_test": s.is_test,
                           "today_cents": st_today, "daily_budget_cents": s.daily_budget_cents,
                           "remaining_today_cents": max(0, (s.daily_budget_cents or 0) - st_today),
                           "month_cents": B.spent(db, org_id, month, s.id),
                           "monthly_budget_cents": s.monthly_budget_cents})
    return {"today_cents": today, "month_cents": this_month, "all_time_cents": all_time,
            "by_provider": providers, "strategies": strategies,
            "refunded_calls": refunded,
            "cost_per_contact_found_cents": _ratio(all_time, found),
            "cost_per_handoff_cents": _ratio(all_time, handoffs),
            "contacts_found": int(found), "handoffs": int(handoffs),
            "note": "SANDBOX costs are simulated prices from sandbox adapters; no money moved."
            if any(p["connector_kind"] == C.SANDBOX for p in providers) else None}


def command_center(db, org_id: str, *, hours: int = 24) -> Dict[str, Any]:
    ctl = C.controls(db, org_id)
    since = C.now() - timedelta(hours=hours)
    ev = (db.query(EvoSenseEvent.action, func.count(EvoSenseEvent.id))
          .filter(EvoSenseEvent.organization_id == org_id, EvoSenseEvent.created_at >= since)
          .group_by(EvoSenseEvent.action).all())
    evc = {a: int(n) for a, n in ev}
    runs = (db.query(EvoSenseRun).filter(EvoSenseRun.organization_id == org_id,
                                         EvoSenseRun.started_at >= since)
            .order_by(EvoSenseRun.started_at.desc()).all())
    dec = (db.query(EvoSenseEnrichmentDecision.decision, EvoSenseEnrichmentDecision.outcome,
                    func.count(EvoSenseEnrichmentDecision.id))
           .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                   EvoSenseEnrichmentDecision.capability == C.CONTACT_ENRICHMENT,
                   EvoSenseEnrichmentDecision.created_at >= since)
           .group_by(EvoSenseEnrichmentDecision.decision, EvoSenseEnrichmentDecision.outcome).all())
    dec_budget = sum(n for d, o, n in dec if d == C.D_BUDGET)
    happened = {
        "hunts": len(runs),
        "last_hunt": {"at": _iso(runs[0].finished_at or runs[0].started_at), "status": runs[0].status,
                      "counts": C.jload(runs[0].counts, {})} if runs else None,
        "properties_discovered": evc.get("property.discovered", 0),
        "contacts_found": evc.get("enrichment.found", 0),
        "lookups_without_result": evc.get("enrichment.no_match", 0) + evc.get("enrichment.provider_failed", 0),
        "budget_blocked": int(dec_budget),
        "outreach_started": evc.get("outreach.started", 0),
        "outreach_refused": evc.get("outreach.blocked", 0),
        "replies": evc.get("reply.read", 0),
        "handoffs": evc.get("handoff.opened", 0),
        "nurtured": evc.get("nurture.set", 0),
        "provider_failures": evc.get("provider.failed", 0),
        "identity_reviews": evc.get("identity.review", 0),
    }
    top = (db.query(EvoSenseProperty)
           .filter(EvoSenseProperty.organization_id == org_id,
                   EvoSenseProperty.archived_at.is_(None),
                   EvoSenseProperty.status.in_((C.S_NEEDS_YOU, C.S_READY, C.S_HIGH, C.S_CONTACT_FOUND,
                                                C.S_OUTREACH, C.S_RESPONDED, C.S_WAITING_DATA,
                                                C.S_BUDGET_BLOCKED)))
           .order_by(EvoSenseProperty.seller_intent.desc().nullslast(),
                     EvoSenseProperty.opportunity_score.desc().nullslast())
           .limit(8).all())
    needs = (db.query(EvoSenseHandoff).filter(EvoSenseHandoff.organization_id == org_id,
                                              EvoSenseHandoff.status.in_(("open", "acknowledged")))
             .order_by(EvoSenseHandoff.priority.desc(), EvoSenseHandoff.created_at.asc()).limit(20).all())
    hprops = {p.id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == org_id,
        EvoSenseProperty.id.in_([h.property_id for h in needs] or ["-"])).all()}
    reviews = (db.query(func.count(EvoSenseIdentityReview.id))
               .filter(EvoSenseIdentityReview.organization_id == org_id,
                       EvoSenseIdentityReview.status == "open").scalar() or 0)
    buckets = bucket_counts(db, org_id)
    upcoming = (db.query(EvoSenseEngagement)
                .filter(EvoSenseEngagement.organization_id == org_id,
                        EvoSenseEngagement.status == "nurture")
                .order_by(EvoSenseEngagement.nurture_until.asc()).limit(5).all())
    uprops = {p.id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == org_id,
        EvoSenseProperty.id.in_([e.property_id for e in upcoming] or ["-"])).all()}
    active = (db.query(EvoSenseStrategy)
              .filter(EvoSenseStrategy.organization_id == org_id,
                      EvoSenseStrategy.status == "active").all())
    from app.services.evosense import scheduler as SCH
    from app.services.evosense import conversation as CV
    from app.services.evosense import inbound as INB
    automation = SCH.status(db, org_id)
    nxt = []
    if ctl.paused_all:
        nxt.append("AUTOMATIC HUNTING PAUSED — EvoSense is paused. Seller replies are still saved "
                   "and opt-outs still honoured.")
    elif ctl.paused_discovery:
        nxt.append("AUTOMATIC HUNTING PAUSED — discovery is paused.")
    elif not active:
        nxt.append("No active strategy — EvoSense is not hunting. Build or activate one.")
    for a in automation:
        if a["state"] == "scheduled" and a["next_due_at"]:
            nxt.append({"kind": "hunt", "strategy": a["name"], "cadence": a["cadence"],
                        "next_due_at": a["next_due_at"], "state": "scheduled"})
        elif a["state"] == "running":
            nxt.append({"kind": "hunt", "strategy": a["name"], "state": "running"})
        elif a["state"] == "manual":
            nxt.append({"kind": "hunt", "strategy": a["name"], "state": "manual"})
    if buckets.get(C.S_BUDGET_BLOCKED):
        nxt.append("%s propert%s wait for budget; they are retried on the next hunt after the "
                   "daily budget resets." % (buckets[C.S_BUDGET_BLOCKED],
                                             "y" if buckets[C.S_BUDGET_BLOCKED] == 1 else "ies"))
    for e in upcoming:
        p = uprops.get(e.property_id)
        nxt.append("Nurture follow-up %s: %s" % (e.nurture_until.strftime("%b %d") if e.nurture_until
                                                  else "—", p.street_address if p else "property"))
    last_in = (db.query(EvoSenseMessage.created_at)
               .filter(EvoSenseMessage.organization_id == org_id,
                       EvoSenseMessage.direction == "inbound")
               .order_by(EvoSenseMessage.created_at.desc()).first())
    pending_rows = CV.pending_messages(db, org_id).all()
    held = sum(1 for m in pending_rows if (C.jload(m.reading, {}) or {}).get("pending") == "held")
    routing = INB.open_routing_reviews(db, org_id)
    return {
        "window_hours": hours, "generated_at": _iso(C.now()),
        "automation": {
            "hunting": ("paused" if (ctl.paused_all or ctl.paused_discovery) else
                        "active" if any(a["state"] in ("scheduled", "running") for a in automation) else
                        "manual" if automation else "none"),
            "strategies": automation,
            "inbound": {"state": "active",
                        "note": ("EvoSense is paused: replies are saved and opt-outs honoured; "
                                 "the rest is read when it resumes.") if ctl.paused_all else None,
                        "last_seller_reply_at": _iso(last_in[0]) if last_in else None,
                        "pending_ai_review": len(pending_rows) - held, "held_while_paused": held,
                        "routing_review": len(routing)},
        },
        "routing_reviews": routing,
        "controls": controls_payload(ctl), "sandbox": sandbox_state(db, org_id),
        "happened": happened,
        "found": [row(p, _signal_labels(db, org_id, p.id)) for p in top],
        "spent": spend(db, org_id),
        "needs_you": [dict({"id": h.id, "property_id": h.property_id,
                       "address": hprops[h.property_id].street_address if h.property_id in hprops else None,
                       "city": hprops[h.property_id].city if h.property_id in hprops else None,
                       "seller_intent": hprops[h.property_id].seller_intent if h.property_id in hprops else None,
                       "is_test": h.is_test, "priority": h.priority, "next_action": h.next_action,
                       "reasons": C.jload(h.reasons, []), "status": h.status,
                       "since": _iso(h.created_at)},
                      **_needs_detail(db, org_id, hprops.get(h.property_id), first=(i == 0)))
                      for i, h in enumerate(needs)],
        # Phase 7.2: what the Acquisition Command metrics and rail read. All
        # counted from rows; nothing here is a target or an estimate.
        "totals": _totals(db, org_id, buckets, needs),
        "recent_activity": _recent_activity(db, org_id),
        "identity_reviews_open": int(reviews),
        "approvals_waiting": buckets.get(C.S_NEEDS_ENRICHMENT, 0),
        "next": nxt,
        "buckets": [{"key": k, "label": lbl, "count": buckets.get(k, 0)} for k, lbl in C.BUCKETS],
        "strategies": [{"id": s.id, "name": s.name, "is_test": s.is_test,
                        "pilot": bool(getattr(s, "pilot_mode", False)),
                        "last_hunt_at": _iso(s.last_hunt_at)} for s in active],
    }


def _needs_detail(db, org_id: str, prop, *, first: bool = False) -> Dict[str, Any]:
    """What the Needs You card shows beyond the hand-off itself (Phase 7.2).

    Read-only and derived from the same rows the property page reads: the
    three scores, the signals that stacked, the seller's own latest words, and
    - for the first card only, because it is the one drawn large - the
    preliminary economics. Nothing is estimated here that the property page
    does not already estimate, and every estimate keeps its truth label.
    """
    if prop is None:
        return {}
    from app.services.evosense import evaluate as EV
    out = {"state": prop.state, "zip_code": prop.zip_code, "property_type": prop.property_type,
           "opportunity_score": prop.opportunity_score,
           "contact_confidence": prop.contact_confidence,
           "estimated_value": prop.estimated_value, "equity_pct": prop.equity_pct,
           "occupancy": prop.occupancy, "status_bucket": prop.status}
    try:
        out["signals"] = [s["label"] for s in EV.stacked_signals(db, prop)
                          if s.get("freshness") != SIG.STALE][:6]
    except Exception:  # noqa: BLE001 - a card never fails the page
        out["signals"] = []
    last = (db.query(EvoSenseMessage.body, EvoSenseMessage.created_at)
            .filter(EvoSenseMessage.organization_id == org_id,
                    EvoSenseMessage.property_id == prop.id,
                    EvoSenseMessage.direction == "inbound")
            .order_by(EvoSenseMessage.created_at.desc()).first())
    out["seller_quote"] = last[0] if last else None
    try:
        cp, conf = CT.best_contact(db, prop)
    except Exception:  # noqa: BLE001
        cp, conf = None, None
    if cp is not None:
        person = db.query(EvoSensePerson).filter(EvoSensePerson.id == cp.person_id).first()
        out["contact"] = {"kind": cp.kind, "value": cp.value, "status": cp.status,
                          "name": person.full_name if person else None,
                          "is_test": bool(cp.is_test)}
    out["seller_quote_at"] = _iso(last[1]) if last else None
    if first:
        try:
            eco = ECO.preliminary(db, prop)
            out["economics"] = {"asking": eco.get("asking"), "mao": eco.get("mao"),
                                "spread": eco.get("spread"), "verdict": eco.get("verdict")}
        except Exception:  # noqa: BLE001
            out["economics"] = None
    return out


def _signal_labels(db, org_id: str, property_id: str) -> List[str]:
    types = {t for (t,) in db.query(EvoSenseSignal.signal_type)
             .filter(EvoSenseSignal.organization_id == org_id,
                     EvoSenseSignal.property_id == property_id,
                     EvoSenseSignal.active.is_(True)).all()}
    order = list(SIG.CATALOG)
    return [SIG.CATALOG[t]["label"] for t in sorted(types, key=order.index) if t in SIG.CATALOG]


def _totals(db, org_id: str, buckets: Dict[str, int], needs) -> Dict[str, Any]:
    contacts = (db.query(func.count(EvoSenseProperty.id))
                .filter(EvoSenseProperty.organization_id == org_id,
                        EvoSenseProperty.contact_confidence.isnot(None)).scalar() or 0)
    hunts = (db.query(func.count(EvoSenseRun.id))
             .filter(EvoSenseRun.organization_id == org_id).scalar() or 0)
    return {"properties_evaluated": int(sum(buckets.values())),
            "contacts_found": int(contacts),
            "needs_you": len(needs),
            "in_outreach": int(buckets.get(C.S_OUTREACH, 0) + buckets.get(C.S_RESPONDED, 0)),
            "nurture": int(buckets.get("nurture", 0)),
            "hunts": int(hunts)}


_ACTIVITY_SKIP = ("controls.changed",)


def _recent_activity(db, org_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    rows = (db.query(EvoSenseEvent)
            .filter(EvoSenseEvent.organization_id == org_id,
                    ~EvoSenseEvent.action.in_(_ACTIVITY_SKIP))
            .order_by(EvoSenseEvent.created_at.desc()).limit(limit).all())
    props = {p.id: p for p in db.query(EvoSenseProperty).filter(
        EvoSenseProperty.organization_id == org_id,
        EvoSenseProperty.id.in_([r.property_id for r in rows if r.property_id] or ["-"])).all()}
    return [{"action": r.action, "summary": r.summary, "actor": r.actor_type,
             "property_id": r.property_id,
             "address": props[r.property_id].street_address if r.property_id in props else None,
             "at": _iso(r.created_at)} for r in rows]


def bucket_counts(db, org_id: str, strategy_id: Optional[str] = None) -> Dict[str, int]:
    q = db.query(EvoSenseProperty.status, func.count(EvoSenseProperty.id)).filter(
        EvoSenseProperty.organization_id == org_id, EvoSenseProperty.archived_at.is_(None))
    if strategy_id:
        q = q.filter(EvoSenseProperty.best_strategy_id == strategy_id)
    return {s: int(n) for s, n in q.group_by(EvoSenseProperty.status).all()}


def row(p, signals: Optional[List[str]] = None, strategy_name: Optional[str] = None) -> Dict[str, Any]:
    return {"id": p.id, "address": p.street_address, "unit": p.unit, "city": p.city, "state": p.state,
            "zip_code": p.zip_code, "county": p.county, "property_type": p.property_type,
            "estimated_value": p.estimated_value, "equity_pct": p.equity_pct,
            "opportunity_score": p.opportunity_score, "opportunity_band": SC.band(p.opportunity_score),
            "data_confidence": p.data_confidence, "contact_confidence": p.contact_confidence,
            "seller_intent": p.seller_intent, "signal_count": p.signal_count,
            "signals": signals, "status": p.status, "next_action": p.next_action,
            "next_action_detail": p.next_action_detail, "blocked_reason": p.blocked_reason,
            "has_conflicts": bool(p.has_conflicts), "identity_status": p.identity_status,
            "is_test": bool(p.is_test), "strategy_id": p.best_strategy_id,
            "strategy_name": strategy_name, "discovered_at": _iso(p.discovered_at),
            "promoted_deal_id": p.promoted_deal_id, "parcel_apn": p.parcel_apn,
            "last_observed_at": _iso(p.last_observed_at),
            "archived_at": _iso(getattr(p, "archived_at", None)),
            "archive_reason": getattr(p, "archive_reason", None)}


SORTS = {"opportunity": (EvoSenseProperty.opportunity_score.desc().nullslast(),),
         "intent": (EvoSenseProperty.seller_intent.desc().nullslast(),
                    EvoSenseProperty.opportunity_score.desc().nullslast()),
         "newest": (EvoSenseProperty.discovered_at.desc(),),
         "contact": (EvoSenseProperty.contact_confidence.desc().nullslast(),)}


def inbox(db, org_id: str, *, bucket: Optional[str] = None, strategy_id: Optional[str] = None,
          q: Optional[str] = None, signal: Optional[str] = None, sort: str = "opportunity",
          limit: int = 50, offset: int = 0, county: Optional[str] = None,
          min_score: Optional[int] = None, source: Optional[str] = None,
          archived: bool = False) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    base = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id)
    # A rolled-back pilot is hidden, never deleted; "archived" shows only it.
    base = base.filter(EvoSenseProperty.archived_at.isnot(None) if archived
                       else EvoSenseProperty.archived_at.is_(None))
    if county:
        base = base.filter(func.lower(EvoSenseProperty.county) == county.strip().lower().replace(" county", ""))
    if min_score is not None:
        base = base.filter(EvoSenseProperty.opportunity_score >= int(min_score))
    if source:
        osub = (db.query(EvoSenseObservation.property_id)
                .filter(EvoSenseObservation.organization_id == org_id,
                        EvoSenseObservation.provider_key == source))
        base = base.filter(EvoSenseProperty.id.in_(osub))
    if strategy_id:
        base = base.filter(EvoSenseProperty.best_strategy_id == strategy_id)
    if q:
        like = "%%%s%%" % q.strip()
        base = base.filter(or_(EvoSenseProperty.street_address.ilike(like),
                               EvoSenseProperty.city.ilike(like),
                               EvoSenseProperty.zip_code.ilike(like)))
    if signal:
        sub = (db.query(EvoSenseSignal.property_id)
               .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.signal_type == signal,
                       EvoSenseSignal.active.is_(True)))
        base = base.filter(EvoSenseProperty.id.in_(sub))
    counts_q = base.with_entities(EvoSenseProperty.status, func.count(EvoSenseProperty.id)) \
        .group_by(EvoSenseProperty.status)
    counts = {s: int(n) for s, n in counts_q.all()}
    listq = base.filter(EvoSenseProperty.status == bucket) if bucket else base
    total = counts.get(bucket, 0) if bucket else sum(counts.values())
    items = listq.order_by(*SORTS.get(sort, SORTS["opportunity"])).offset(max(0, offset)).limit(limit).all()
    ids = [p.id for p in items] or ["-"]
    sig = defaultdict(set)
    for pid, stype in (db.query(EvoSenseSignal.property_id, EvoSenseSignal.signal_type)
                       .filter(EvoSenseSignal.organization_id == org_id,
                               EvoSenseSignal.property_id.in_(ids),
                               EvoSenseSignal.active.is_(True)).all()):
        sig[pid].add(stype)
    order = list(SIG.CATALOG)
    names = {s.id: s.name for s in db.query(EvoSenseStrategy).filter(
        EvoSenseStrategy.organization_id == org_id).all()}
    return {"total": total, "limit": limit, "offset": offset,
            "buckets": [{"key": k, "label": lbl, "count": counts.get(k, 0)} for k, lbl in C.BUCKETS],
            "items": [row(p, [SIG.CATALOG[s]["label"] for s in sorted(sig[p.id], key=order.index)
                              if s in SIG.CATALOG],
                          names.get(p.best_strategy_id)) for p in items],
            "strategies": [{"id": k, "name": v} for k, v in names.items()],
            "signal_types": [{"key": k, "label": v["label"]} for k, v in SIG.CATALOG.items()],
            "counties": sorted({c for (c,) in db.query(EvoSenseProperty.county.distinct())
                                .filter(EvoSenseProperty.organization_id == org_id).all() if c}),
            "sources": sorted({k for (k,) in db.query(EvoSenseObservation.provider_key.distinct())
                               .filter(EvoSenseObservation.organization_id == org_id).all() if k}),
            "archived": bool(archived),
            "sandbox": sandbox_state(db, org_id)}


def _score_payload(s: Optional[EvoSenseScore]):
    if s is None:
        return None
    return {"value": s.value, "label": s.label, "version": s.version,
            "factors": C.jload(s.factors, []), "inputs": C.jload(s.inputs, {}),
            "calculated_at": _iso(s.calculated_at)}


def property_detail(db, org_id: str, prop: EvoSenseProperty) -> Dict[str, Any]:
    from app.services.evosense import evaluate as EV
    strategy = EV.strategy_for(db, prop)
    stacked = EV.stacked_signals(db, prop)
    scores = (db.query(EvoSenseScore).filter(EvoSenseScore.organization_id == org_id,
                                             EvoSenseScore.property_id == prop.id)
              .order_by(EvoSenseScore.calculated_at.desc()).all())
    current = {}
    for s in scores:
        if s.is_current and s.subject_type != "contact_point" and s.score_type not in current:
            current[s.score_type] = s
    history = [{"score_type": s.score_type, "value": s.value, "version": s.version,
                "at": _iso(s.calculated_at), "current": s.is_current}
               for s in scores if s.subject_type != "contact_point"][:30]

    owners = []
    links = (db.query(EvoSenseOwnership).filter(EvoSenseOwnership.organization_id == org_id,
                                                EvoSenseOwnership.property_id == prop.id).all())
    for o in CT.current_owners(db, prop) + [
            x for x in db.query(EvoSenseOwner).filter(
                EvoSenseOwner.organization_id == org_id,
                EvoSenseOwner.id.in_([l.owner_id for l in links if not l.is_current] or ["-"])).all()]:
        other_props = (db.query(func.count(EvoSenseOwnership.property_id.distinct()))
                       .filter(EvoSenseOwnership.organization_id == org_id,
                               EvoSenseOwnership.owner_id == o.id,
                               EvoSenseOwnership.is_current.is_(True)).scalar() or 0)
        persons = db.query(EvoSensePerson).filter(EvoSensePerson.organization_id == org_id,
                                                  EvoSensePerson.owner_id == o.id).all()
        owners.append({
            "id": o.id, "name": o.display_name, "owner_type": o.owner_type,
            "resolution": o.resolution,
            "resolution_label": "UNRESOLVED ENTITY" if o.resolution == "unresolved" else "RESOLVED",
            "mailing": ", ".join(x for x in (o.mailing_street, o.mailing_city, o.mailing_state,
                                             o.mailing_zip) if x) or None,
            "properties_owned_here": int(other_props),
            "last_touch_at": _iso(o.last_touch_at), "last_enriched_at": _iso(o.last_enriched_at),
            "sources": sorted({l.source for l in links if l.owner_id == o.id}),
            "current": any(l.is_current for l in links if l.owner_id == o.id),
            "in_conflict": any(l.in_conflict for l in links if l.owner_id == o.id),
            "persons": [{"id": p.id, "name": p.full_name, "role": p.role, "source": p.source,
                         "lead_id": p.lead_id} for p in persons]})

    contacts = []
    for cp, sc in CT.contact_points_for(db, prop):
        person = db.query(EvoSensePerson).filter(EvoSensePerson.id == cp.person_id).first()
        contacts.append({
            "id": cp.id, "kind": cp.kind, "value": cp.value, "person": person.full_name if person else None,
            "role": person.role if person else None, "source": cp.source,
            "connector_kind": cp.connector_kind,
            "connector_label": C.CONNECTOR_LABELS.get(cp.connector_kind or "", cp.connector_kind),
            "line_type": cp.line_type, "validation": cp.validation,
            "agreeing_sources": cp.agreeing_sources, "status": cp.status,
            "status_reason": cp.status_reason, "confidence": sc, "is_test": bool(cp.is_test)})
    best_cp, _ = CT.best_contact(db, prop)
    live = (db.query(EvoSenseEngagement.id)
            .filter(EvoSenseEngagement.organization_id == org_id,
                    EvoSenseEngagement.property_id == prop.id,
                    EvoSenseEngagement.status.in_(EL.LIVE_ENGAGEMENT)).first())
    # Eligibility answers "may EvoSense START working this owner?" — for an
    # owner already in conversation that question does not arise.
    elig = EL.check(db, prop, best_cp, strategy, mark=False) \
        if best_cp is not None and live is None else None

    decisions = (db.query(EvoSenseEnrichmentDecision)
                 .filter(EvoSenseEnrichmentDecision.organization_id == org_id,
                         EvoSenseEnrichmentDecision.property_id == prop.id)
                 .order_by(EvoSenseEnrichmentDecision.created_at.desc()).limit(40).all())
    ledger = (db.query(EvoSenseCostEntry)
              .filter(EvoSenseCostEntry.organization_id == org_id, EvoSenseCostEntry.property_id == prop.id)
              .order_by(EvoSenseCostEntry.created_at.asc()).all())
    engs = (db.query(EvoSenseEngagement).filter(EvoSenseEngagement.organization_id == org_id,
                                                EvoSenseEngagement.property_id == prop.id)
            .order_by(EvoSenseEngagement.created_at.asc()).all())
    msgs = defaultdict(list)
    for m in (db.query(EvoSenseMessage).filter(EvoSenseMessage.organization_id == org_id,
                                               EvoSenseMessage.property_id == prop.id)
              .order_by(EvoSenseMessage.created_at.asc()).all()):
        msgs[m.engagement_id].append({"id": m.id, "direction": m.direction, "channel": m.channel,
                                      "body": m.body, "delivery": m.delivery, "outcome": m.outcome,
                                      "pending": ((C.jload(m.reading, {}) or {}).get("why")
                                                  if m.direction == "inbound" and not m.outcome else None),
                                      "platform_ref": m.platform_ref,
                                      "at": _iso(m.created_at)})
    facts = (db.query(EvoSenseFact).filter(EvoSenseFact.organization_id == org_id,
                                           EvoSenseFact.property_id == prop.id)
             .order_by(EvoSenseFact.created_at.asc()).all())
    handoff = (db.query(EvoSenseHandoff).filter(EvoSenseHandoff.organization_id == org_id,
                                                EvoSenseHandoff.property_id == prop.id)
               .order_by(EvoSenseHandoff.created_at.desc()).first())
    events = (db.query(EvoSenseEvent).filter(EvoSenseEvent.organization_id == org_id,
                                             EvoSenseEvent.property_id == prop.id)
              .order_by(EvoSenseEvent.created_at.desc()).limit(60).all())
    obs = (db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == org_id,
                                                EvoSenseObservation.property_id == prop.id)
           .order_by(EvoSenseObservation.observed_at.asc()).all())
    reviews = (db.query(EvoSenseIdentityReview)
               .filter(EvoSenseIdentityReview.organization_id == org_id,
                       EvoSenseIdentityReview.status == "open",
                       EvoSenseIdentityReview.candidate_property_ids.like('%%"%s"%%' % prop.id)).all())
    feedback = (db.query(EvoSenseFeedback).filter(EvoSenseFeedback.organization_id == org_id,
                                                  EvoSenseFeedback.property_id == prop.id)
                .order_by(EvoSenseFeedback.created_at.desc()).all())
    ranks = C.jload(prop.fact_ranks, {}) or {}
    first_strategy = (db.query(EvoSenseStrategy).filter(EvoSenseStrategy.id == prop.first_strategy_id).first()
                      if prop.first_strategy_id else None)
    return {
        "property": row(prop, strategy_name=getattr(strategy, "name", None)),
        "facts": {
            "estimated_value": {"value": prop.estimated_value, "source": prop.estimated_value_source,
                                "truth": C.TRUTH_LABELS[C.T_PROVIDER] if prop.estimated_value else "MISSING",
                                "at": _iso(prop.estimated_value_at)},
            "mortgage_balance": {"value": prop.mortgage_balance, "source": prop.mortgage_source,
                                 "truth": C.TRUTH_LABELS[C.T_PROVIDER] if prop.mortgage_balance is not None else "MISSING"},
            "equity_pct": {"value": prop.equity_pct, "source": prop.equity_basis,
                           "truth": "SYSTEM ESTIMATE (value − mortgage)" if prop.equity_basis == "computed"
                           else ("PROVIDER REPORTED" if prop.equity_pct is not None else "MISSING")},
            "ownership_years": {"value": prop.ownership_years, "last_sale_date": _iso(prop.last_sale_date),
                                "truth": "PROVIDER REPORTED" if prop.last_sale_date else "MISSING"},
            "occupancy": {"value": prop.occupancy, "source": prop.occupancy_source},
            "physical": {k: (float(getattr(prop, k)) if getattr(prop, k) is not None and k in ("bedrooms", "bathrooms")
                             else getattr(prop, k))
                         for k in ("property_type", "bedrooms", "bathrooms", "square_feet", "year_built",
                                   "parcel_apn", "county")},
            "ranks": ranks,
        },
        "conflicts": C.jload(prop.conflicts, []) or [],
        "identity_reviews": [{"id": r.id, "reason": r.reason, "candidates": C.jload(r.candidate_property_ids, [])}
                             for r in reviews],
        "signals": stacked,
        "scores": {k: _score_payload(current.get(k)) for k in
                   ("property_opportunity", "data_confidence", "seller_intent")},
        "score_history": history,
        "owners": owners,
        "contacts": contacts,
        "eligibility": elig,
        "enrichment": [{"id": d.id, "decision": d.decision, "reasons": C.jload(d.reasons, []),
                        "governor": C.GOVERNOR_LABEL.get(d.decision, d.decision),
                        "capability": d.capability,
                        "provider": d.provider_key, "estimated_cost_cents": d.estimated_cost_cents,
                        "outcome": d.outcome, "by": d.decided_by, "at": _iso(d.created_at)}
                       for d in decisions if (d.capability or C.CONTACT_ENRICHMENT) == C.CONTACT_ENRICHMENT],
        "lookups": [{"id": d.id, "decision": d.decision, "governor": C.GOVERNOR_LABEL.get(d.decision, d.decision),
                     "capability": d.capability, "reasons": C.jload(d.reasons, []),
                     "provider": d.provider_key,
                     "provider_label": PV.PROVIDERS[d.provider_key].label if d.provider_key in PV.PROVIDERS else d.provider_key,
                     "cost_cents": d.estimated_cost_cents or 0, "outcome": d.outcome, "at": _iso(d.created_at)}
                    for d in decisions if (d.capability or C.CONTACT_ENRICHMENT) != C.CONTACT_ENRICHMENT],
        "sms_eligibility": sms_eligibility(db, org_id, prop, contacts),
        "provenance": [{"id": o.id, "provider": o.provider_key,
                        "provider_label": PV.PROVIDERS[o.provider_key].label if o.provider_key in PV.PROVIDERS else o.provider_key,
                        "connector_kind": o.connector_kind,
                        "connector_label": C.CONNECTOR_LABELS.get(o.connector_kind, o.connector_kind),
                        "capability": o.capability, "reference": o.source_reference,
                        "retrieved_at": _iso(o.observed_at), "last_seen_at": _iso(o.last_seen_at),
                        "source_updated_at": _iso(getattr(o, "source_updated_at", None)),
                        "source_url": getattr(o, "source_url", None),
                        "adapter_version": getattr(o, "adapter_version", None),
                        "content_hash": getattr(o, "content_hash", None),
                        "has_raw": bool(getattr(o, "raw_payload", None)),
                        "processing": getattr(o, "processing_status", None),
                        "error": getattr(o, "processing_error", None),
                        "run_id": o.run_id, "match": o.match_type,
                        "evidence": (C.jload(o.payload, {}) or {}).get("evidence")} for o in obs],
        "ledger": [{"id": e.id, "provider": e.provider_key, "connector_kind": e.connector_kind,
                    "capability": e.capability, "operation": e.operation, "cents": e.total_cents,
                    "status": e.status, "at": _iso(e.created_at)} for e in ledger],
        "spent_cents": sum(e.total_cents for e in ledger if e.status == "charged"),
        "engagements": [{"id": e.id, "status": e.status, "channel": e.channel,
                         "delivery_mode": e.delivery_mode, "blocked_reason": e.blocked_reason,
                         "last_outcome": e.last_outcome, "touches": e.touches,
                         "nurture_until": _iso(e.nurture_until), "nurture_reason": e.nurture_reason,
                         "lead_id": e.lead_id, "messages": msgs.get(e.id, [])} for e in engs],
        "seller_facts": [{"id": f.id, "fact_type": f.fact_type, "value": f.value,
                          "truth": C.TRUTH_LABELS.get(f.truth_state, f.truth_state),
                          "quote": f.quote, "message_id": f.message_id, "extracted_by": f.extracted_by,
                          "superseded": f.superseded, "at": _iso(f.created_at)} for f in facts],
        "handoff": {"id": handoff.id, "status": handoff.status, "reasons": C.jload(handoff.reasons, []),
                    "next_action": handoff.next_action, "priority": handoff.priority,
                    "at": _iso(handoff.created_at)} if handoff else None,
        "economics": ECO.preliminary(db, prop),
        "attribution": {"first_strategy": getattr(first_strategy, "name", None),
                        "first_strategy_version": getattr(first_strategy, "version", None),
                        "best_strategy": getattr(strategy, "name", None),
                        "sources": sorted({o.provider_key for o in obs}),
                        "observations": [{"provider": o.provider_key, "connector_kind": o.connector_kind,
                                          "capability": o.capability, "reference": o.source_reference,
                                          "match": o.match_type, "at": _iso(o.observed_at)} for o in obs]},
        "feedback": [{"kind": f.kind, "reason": f.reason, "at": _iso(f.created_at)} for f in feedback],
        "timeline": [{"action": e.action, "summary": e.summary, "actor": e.actor_type,
                      "at": _iso(e.created_at)} for e in events],
        "feedback_kinds": list(C.FEEDBACK_KINDS),
    }


def sms_eligibility(db, org_id: str, prop, contacts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """FOUND PHONE != SMS CONSENT. The Wholesale seller-SMS gate is the only
    authority; this shows its verdict for every phone on the property. A
    property with no phone reads UNKNOWN, never eligible."""
    from app.services import wholesale_sms as WS
    phones = [c for c in contacts if c.get("kind") == "phone"]
    rows = []
    for c in phones:
        try:
            res = WS.check_eligibility(db, org_id, c["value"])
            rows.append({"contact_id": c["id"], "phone": c["value"], "eligible": bool(res["eligible"]),
                         "reasons": res["reasons"], "consent_id": res.get("consent_id")})
        except Exception as exc:  # noqa: BLE001 - unknown means not eligible
            rows.append({"contact_id": c["id"], "phone": c["value"], "eligible": False,
                         "reasons": ["CHECK_FAILED: %s" % type(exc).__name__]})
    if not rows:
        verdict = "UNKNOWN — no phone on file"
    elif any(r["eligible"] for r in rows):
        verdict = "ELIGIBLE (consent of record)"
    else:
        verdict = "NOT ELIGIBLE"
    return {"verdict": verdict, "phones": rows, "program": WS.PROGRAM,
            "rule": "A phone found in public records or by skip trace is never permission to text. "
                    "Only a consent record under the Wholesale seller SMS program is."}


def strategy_metrics(db, org_id: str, strategy: EvoSenseStrategy) -> Dict[str, Any]:
    """Attribution per strategy. A ratio is shown only when its denominator exists."""
    props = (db.query(EvoSenseProperty.status, func.count(EvoSenseProperty.id))
             .filter(EvoSenseProperty.organization_id == org_id,
                     EvoSenseProperty.best_strategy_id == strategy.id)
             .group_by(EvoSenseProperty.status).all())
    counts = {s: int(n) for s, n in props}
    total = sum(counts.values())
    spent = int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
                .filter(EvoSenseCostEntry.organization_id == org_id,
                        EvoSenseCostEntry.strategy_id == strategy.id,
                        EvoSenseCostEntry.status == "charged").scalar() or 0)
    promoted = counts.get(C.S_PROMOTED, 0)
    return {"discovered": total, "by_status": counts, "spent_cents": spent,
            "promoted": promoted, "cost_per_promotion_cents": _ratio(spent, promoted),
            "runs": db.query(func.count(EvoSenseRun.id)).filter(
                EvoSenseRun.organization_id == org_id, EvoSenseRun.strategy_id == strategy.id).scalar() or 0}
