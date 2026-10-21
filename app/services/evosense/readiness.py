"""OPPORTUNITY READINESS - where each property stands on the road to a deal,
and the ONE thing a person should do next.

    evidence -> owner -> contact -> conversation -> qualified -> deal

Every step is read from records that already exist (signals and their sources,
owners of record, contact points, permission links and consents, seller facts,
hand-offs, deals, the work queue). Nothing here scores, guesses or fills in a
fact: a step with no evidence says "not yet" and names what is missing.

The scores and thresholds are the strategy's own (scoring.py, the strategy's
`min_opportunity_score`); this module only reads them.

    for_property(db, prop)      -> the checklist + next action for one property
    queue(db, org_id, ...)      -> "act first" list, most advanced and best first
    funnel(db, org_id)          -> where opportunities are getting stuck
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseEngagement, EvoSenseFact,
                                        EvoSenseHandoff, EvoSenseMessage, EvoSenseOwnership,
                                        EvoSenseProperty)
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import signals as SIG

VERSION = "readiness/v1"
DEFAULT_THRESHOLD = 45

DONE, WAITING, TODO, BLOCKED, STOP = "done", "waiting", "todo", "blocked", "stop"

# Properties that are worth a person's time even below threshold: something
# already happened with an owner.
ACTIVE_STATUSES = (C.S_HIGH, C.S_NEEDS_ENRICHMENT, C.S_BUDGET_BLOCKED, C.S_WAITING_DATA,
                   C.S_CONTACT_FOUND, C.S_READY, C.S_OUTREACH, C.S_RESPONDED, C.S_NURTURE,
                   C.S_NEEDS_REVIEW, C.S_NEEDS_YOU)

# Queue order: the step a property is waiting on. A seller who answered beats
# a house whose owner nobody has found yet.
STEP_RANK = {"deal": 0, "qualified": 1, "conversation": 2, "contact": 3, "owner": 4, "evidence": 5}


def _threshold(strategy) -> int:
    return int(getattr(strategy, "min_opportunity_score", None) or DEFAULT_THRESHOLD)


def _step(key: str, label: str, state: str, detail: Optional[str] = None,
          action: Optional[str] = None, who: Optional[str] = None, **extra) -> Dict[str, Any]:
    out = {"key": key, "label": label, "state": state, "detail": detail, "action": action, "who": who}
    out.update(extra)
    return out


def _mask(cp) -> str:
    v = cp.value or ""
    if cp.kind == "phone":
        return "phone ending %s" % v[-4:] if len(v) >= 4 else "phone"
    at = v.find("@")
    return ("email %s***%s" % (v[:1], v[at:])) if at > 0 else "email"


def _open_work(db, org_id: str, prop_id: str) -> List[Dict[str, Any]]:
    from app.models.wholesale_models import EXCEPTION_OPEN_STATUSES, WholesaleWorkException
    rows = (db.query(WholesaleWorkException)
            .filter(WholesaleWorkException.organization_id == org_id,
                    WholesaleWorkException.subject_type == "evosense_property",
                    WholesaleWorkException.subject_id == prop_id,
                    WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES))
            .order_by(WholesaleWorkException.priority.asc()).all())
    return [{"id": r.id, "kind": r.kind, "title": r.title, "status": r.status,
             "assigned_to_id": r.assigned_to_id} for r in rows]


def evidence_step(db, prop, strategy) -> Dict[str, Any]:
    from app.services.evosense import evaluate as EV
    th = _threshold(strategy)
    stacked = EV.stacked_signals(db, prop)
    sigs = []
    for s in stacked:
        if s["freshness"] == SIG.STALE:
            continue
        spec = SIG.CATALOG.get(s["signal_type"], {})
        sigs.append({"type": s["signal_type"], "label": spec.get("label", s["signal_type"]),
                     "sources": s.get("sources") or [], "freshness": s["freshness"],
                     "scored": s["signal_type"] not in SIG.NEVER_SCORED})
    # What the score could not see - stated, never filled in.
    missing = []
    if prop.equity_pct is None:
        missing.append("Equity / mortgage: not known (no free source)")
    if not prop.estimated_value and not prop.appraisal_value:
        missing.append("Value: no appraisal or estimate on file")
    if prop.square_feet is None:
        missing.append("Living area: not on file")
    if prop.bedrooms is None:
        missing.append("Beds / baths: not on file")
    kinds = {s["type"] for s in sigs}
    if "VACANT" not in kinds:
        missing.append("Vacancy: not verified (no public source)")
    score = prop.opportunity_score
    base = {"score": score, "threshold": th, "signals": sigs, "missing": missing}
    if score is None:
        return _step("evidence", "Distress evidence", TODO, "Not enough evidence to score yet.",
                     "Wait for the next county refresh, or add what you know", **base)
    if score < th:
        return _step("evidence", "Distress evidence", STOP,
                     "Scored %s; this search acts at %s." % (score, th),
                     "None needed - a person may still review it", **base)
    return _step("evidence", "Distress evidence", DONE,
                 "Scored %s (search threshold %s) from %d current signal%s."
                 % (score, th, len(sigs), "" if len(sigs) == 1 else "s"), **base)


def owner_step(db, prop) -> Dict[str, Any]:
    from app.services.evosense.ingest import INSTITUTIONAL, OWNER_TYPE_LABELS
    owners = CT.current_owners(db, prop)
    if not owners:
        return _step("owner", "Owner of record", TODO, "No owner of record from any source.",
                     "Find the owner of record (county appraisal district search)", who="staff",
                     owner=None)
    o = owners[0]
    links = (db.query(EvoSenseOwnership)
             .filter(EvoSenseOwnership.organization_id == prop.organization_id,
                     EvoSenseOwnership.property_id == prop.id,
                     EvoSenseOwnership.owner_id == o.id,
                     EvoSenseOwnership.is_current.is_(True)).all())
    conflict = any(l.in_conflict for l in links) or len(owners) > 1
    sources = sorted({l.source for l in links if l.source})
    flags = C.jload(o.review_flags, []) or []
    info = {"owner": {"name": o.display_name, "type": OWNER_TYPE_LABELS.get(o.owner_type, o.owner_type),
                      "mailing": ", ".join(x for x in (o.mailing_street, o.mailing_city,
                                                       " ".join(y for y in (o.mailing_state, o.mailing_zip) if y))
                                           if x) or None,
                      "sources": sources, "flags": flags,
                      "other_owners": [x.display_name for x in owners[1:]]}}
    if o.owner_type in INSTITUTIONAL:
        return _step("owner", "Owner of record", STOP,
                     "Owned by an institution (%s) - not a typical seller." % info["owner"]["type"],
                     "None", **info)
    if o.name_truncated:
        return _step("owner", "Owner of record", TODO,
                     "The county file cut this name at its field width (%s)." % o.display_name,
                     "Confirm the owner's full name (appraisal district record)", who="staff", **info)
    if conflict:
        return _step("owner", "Owner of record", TODO,
                     "Sources disagree about who owns it: %s." % ", ".join(
                         x.display_name for x in owners if x.display_name),
                     "Confirm which owner is current", who="staff", **info)
    if {"ESTATE_INDICATED", "LIFE_ESTATE"} & set(flags):
        return _step("owner", "Owner of record", TODO,
                     "The name of record suggests an estate - who can sell is not settled.",
                     "Find who has authority to sell (heirs / executor)", who="staff", **info)
    return _step("owner", "Owner of record", DONE,
                 "%s, from %s." % (o.display_name, ", ".join(sources) or "a county record"), **info)


def contact_step(db, prop) -> Dict[str, Any]:
    pts = CT.contact_points_for(db, prop)
    active = [(cp, sc) for cp, sc in pts if cp.status == "active"]
    blocked = [cp for cp, _ in pts if cp.status in ("opted_out", "suppressed")]
    wrong = [cp for cp, _ in pts if cp.status in ("wrong_party", "invalid")]
    info = {"contacts": [{"what": _mask(cp), "status": cp.status, "source": cp.source,
                          "confidence": (sc or {}).get("value")} for cp, sc in pts[:6]],
            "contactability": prop.contactability}
    if active:
        cp, sc = active[0]
        return _step("contact", "Way to reach the owner", DONE,
                     "%s (%s, confidence %s)." % (_mask(cp), cp.source, (sc or {}).get("value")), **info)
    if blocked and not active:
        return _step("contact", "Way to reach the owner", STOP,
                     "The owner is on the do-not-contact list.", "Nothing - do not contact", **info)
    # No usable phone/email. Say what a person can do for free, with what we have.
    owners = CT.current_owners(db, prop)
    mail = owners[0].mailing_street if owners else None
    ways = []
    if mail:
        ways.append("Mail the owner at the mailing address on file")
    if prop.street_address:
        ways.append("Door-knock the property (add its ZIP to a canvass route)")
    ways.append("Ask a neighbour or the tenant to pass on your number")
    ways.append("Paid owner lookup - off until it is funded")
    detail = ("Tried and wrong: %s. " % ", ".join(_mask(c) for c in wrong) if wrong else "") + \
        "No phone or email on file."
    return _step("contact", "Way to reach the owner", TODO, detail,
                 "Find a way to reach the owner", who="staff", ways=ways, **info)


def conversation_step(db, prop) -> Dict[str, Any]:
    from app.services.evosense import permission as PM
    pts = CT.contact_points_for(db, prop)
    phones = [cp.value for cp, _ in pts if cp.kind == "phone" and cp.status == "active"]
    perm = PM.status_for(db, prop.organization_id, prop, phones) if phones else {"state": "not_yet"}
    heard = (db.query(func.count(EvoSenseMessage.id))
             .filter(EvoSenseMessage.organization_id == prop.organization_id,
                     EvoSenseMessage.property_id == prop.id,
                     EvoSenseMessage.direction == "inbound").scalar() or 0)
    eng = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == prop.organization_id,
                   EvoSenseEngagement.property_id == prop.id)
           .order_by(EvoSenseEngagement.updated_at.desc()).first())
    info = {"permission": perm.get("state"), "seller_messages": int(heard),
            "engagement": getattr(eng, "status", None), "last_outcome": getattr(eng, "last_outcome", None)}
    if eng is not None and eng.status == "stopped":
        return _step("conversation", "First conversation", STOP,
                     "Stopped: %s." % (eng.last_outcome or eng.blocked_reason or "closed").replace("_", " ").lower(),
                     "Nothing unless the owner comes back", **info)
    if heard:
        return _step("conversation", "First conversation", DONE,
                     "%d message%s from the seller on record." % (heard, "" if heard == 1 else "s"), **info)
    if eng is not None and eng.status == "nurture":
        return _step("conversation", "First conversation", WAITING,
                     "Owner asked for later (%s)." % (eng.nurture_reason or "nurture"),
                     "Follow up on %s" % (eng.nurture_until.strftime("%b %d") if eng.nurture_until else "the set date"),
                     who="staff", **info)
    if perm.get("state") == "link_sent":
        return _step("conversation", "First conversation", WAITING,
                     "Permission link sent; the owner has not answered.",
                     "Call the owner, or wait for the link", who="staff", **info)
    act = ("Call the owner and write down what they say" if perm.get("state") != "on_file"
           else "Call or text the owner (texting permission on file)")
    return _step("conversation", "First conversation", TODO,
                 "Nobody has spoken with the owner yet." +
                 (" Texting permission: %s." % perm.get("state").replace("_", " ") if phones else ""),
                 act, who="staff", **info)


def qualified_step(db, prop) -> Dict[str, Any]:
    facts = (db.query(EvoSenseFact)
             .filter(EvoSenseFact.organization_id == prop.organization_id,
                     EvoSenseFact.property_id == prop.id,
                     EvoSenseFact.superseded.is_(False))
             .order_by(EvoSenseFact.created_at.desc()).all())
    seen, said = set(), []
    for f in facts:
        if f.fact_type in seen:
            continue
        seen.add(f.fact_type)
        # The seller's own words are kept apart from anything worked out.
        said.append({"fact": f.fact_type, "value": f.value, "quote": f.quote,
                     "basis": C.TRUTH_LABELS.get(f.truth_state, f.truth_state)})
    h = (db.query(EvoSenseHandoff)
         .filter(EvoSenseHandoff.organization_id == prop.organization_id,
                 EvoSenseHandoff.property_id == prop.id)
         .order_by(EvoSenseHandoff.created_at.desc()).first())
    info = {"seller_intent": prop.seller_intent, "facts": said,
            "handoff": ({"id": h.id, "status": h.status, "next_action": h.next_action} if h else None)}
    if h is not None and h.status in ("open", "acknowledged", "promoted"):
        return _step("qualified", "Seller qualified", DONE,
                     "Hand-off %s - seller intent %s." % (h.status, prop.seller_intent), **info)
    if h is not None and h.status == "dismissed":
        return _step("qualified", "Seller qualified", STOP, "Hand-off dismissed by a person.", "None", **info)
    if prop.seller_intent == 0:
        return _step("qualified", "Seller qualified", STOP, "The owner said no.", "None", **info)
    if said:
        return _step("qualified", "Seller qualified", TODO,
                     "Seller intent %s - not enough for a hand-off yet." % prop.seller_intent,
                     "Ask about price, timeline and condition", who="staff", **info)
    return _step("qualified", "Seller qualified", TODO, "No seller statements yet.",
                 "Ask about price, timeline and condition", who="staff", **info)


def deal_step(db, prop) -> Dict[str, Any]:
    if prop.promoted_deal_id:
        missing = []
        try:
            from app.services.evosense import economics as ECO
            eco = ECO.preliminary(db, prop)
            if eco.get("arv", {}).get("value") is None:
                missing.append("ARV: no verified comparable sales")
            if eco.get("asking") is None:
                missing.append("Seller's asking price not stated")
            if eco.get("mao") is None:
                missing.append("Offer limit (MAO): not calculated - %s" % (eco.get("blocked") or "inputs missing"))
        except Exception:  # noqa: BLE001 - an explanation must never break the list
            missing.append("Offer inputs could not be read")
        return _step("deal", "Wholesale deal", DONE, "Promoted to a Wholesale deal.",
                     "Get comps and repairs before any offer" if missing else "Work the deal",
                     who="staff", deal_id=prop.promoted_deal_id, missing=missing)
    return _step("deal", "Wholesale deal", TODO, "Not a deal yet.",
                 "Review the hand-off and promote it", who="staff", deal_id=None, missing=[])


def for_property(db, prop, strategy=None) -> Dict[str, Any]:
    from app.services.evosense import evaluate as EV
    strategy = strategy or EV.strategy_for(db, prop)
    steps = [evidence_step(db, prop, strategy), owner_step(db, prop), contact_step(db, prop),
             conversation_step(db, prop), qualified_step(db, prop), deal_step(db, prop)]
    # A seller who already answered / a deal overrides an unfinished earlier
    # step (a person may have reached them without the system's help).
    nxt = None
    for s in steps:
        if s["state"] == STOP:
            nxt = s
            break
        if s["state"] != DONE:
            nxt = s
            break
    if steps[5]["state"] == DONE:
        nxt = steps[5]
    elif steps[4]["state"] == DONE:
        nxt = steps[5]
    stuck = nxt is not None and nxt["state"] == STOP
    return {"version": VERSION, "property_id": prop.id, "address": _addr(prop),
            "county": prop.county, "score": prop.opportunity_score, "status": prop.status,
            "strategy": getattr(strategy, "name", None),
            "steps": steps,
            "next": ({"step": nxt["key"], "label": nxt["label"], "action": nxt["action"],
                      "detail": nxt["detail"], "who": nxt.get("who"), "stopped": stuck}
                     if nxt else None),
            "work": _open_work(db, prop.organization_id, prop.id)}


def _addr(p) -> str:
    return ", ".join(x for x in (p.street_address, p.city, p.zip_code) if x) or "(no address)"


def _next_of(steps) -> Optional[Dict[str, Any]]:
    """for_property's rule, on a (possibly partial) list of steps."""
    nxt = None
    for s in steps:
        if s["state"] == STOP or s["state"] != DONE:
            nxt = s
            break
    return nxt


def _queue_row(db, prop, strategy, work, has_handoff: bool) -> Optional[Dict[str, Any]]:
    """The queue's view of one property: the SAME answer as for_property's
    `next`, computed only as far as needed.

    for_property evaluates all six steps; the queue only needs the first one
    that is not done. That is exact unless a later step overrides an earlier
    one, which happens only when a hand-off exists (qualified/deal DONE) or
    the property was promoted (excluded from the queue). So: a property with
    a hand-off gets the full evaluation; every other one stops at its first
    unfinished step. (Production 2026-10-21: 500 full evaluations took 27 s.)"""
    if has_handoff:
        r = for_property(db, prop, strategy)
        r["work"] = work
        return r
    th = _threshold(strategy)
    if prop.opportunity_score is not None and prop.opportunity_score < th:
        return None                                   # evidence STOP: never in the queue
    steps = []
    if prop.opportunity_score is None:
        steps.append(evidence_step(db, prop, strategy))
    else:
        steps.append(_step("evidence", "Distress evidence", DONE, None))
    for fn in (owner_step, contact_step, conversation_step, qualified_step, deal_step):
        if _next_of(steps) is not None:
            break
        steps.append(fn(db, prop))
    nxt = _next_of(steps)
    if nxt is None:
        return None
    return {"version": VERSION, "property_id": prop.id, "address": _addr(prop),
            "county": prop.county, "score": prop.opportunity_score, "status": prop.status,
            "strategy": getattr(strategy, "name", None),
            "next": {"step": nxt["key"], "label": nxt["label"], "action": nxt["action"],
                     "detail": nxt["detail"], "who": nxt.get("who"), "stopped": nxt["state"] == STOP},
            "work": work}


def queue(db, org_id: str, *, limit: int = 50, include_test: bool = False,
          county: Optional[str] = None, step: Optional[str] = None) -> Dict[str, Any]:
    """The properties that deserve action first, and what to do about each."""
    from app.models.evosense_models import EvoSenseStrategy
    from app.models.wholesale_models import EXCEPTION_OPEN_STATUSES, WholesaleWorkException
    q = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id,
                 EvoSenseProperty.archived_at.is_(None),
                 EvoSenseProperty.promoted_deal_id.is_(None),
                 EvoSenseProperty.status.in_(ACTIVE_STATUSES)))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.is_(False))
    if county:
        q = q.filter(func.lower(EvoSenseProperty.county) == county.lower())
    cands = q.order_by(EvoSenseProperty.opportunity_score.desc().nullslast()).limit(500).all()
    ids = [p.id for p in cands] or ["-"]
    # One query each for what used to be asked per property.
    strategies = {s.id: s for s in db.query(EvoSenseStrategy).filter(EvoSenseStrategy.organization_id == org_id)}
    work: Dict[str, List[Dict[str, Any]]] = {}
    for r in (db.query(WholesaleWorkException)
              .filter(WholesaleWorkException.organization_id == org_id,
                      WholesaleWorkException.subject_type == "evosense_property",
                      WholesaleWorkException.subject_id.in_(ids),
                      WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES))
              .order_by(WholesaleWorkException.priority.asc()).all()):
        work.setdefault(r.subject_id, []).append({"id": r.id, "kind": r.kind, "title": r.title,
                                                  "status": r.status, "assigned_to_id": r.assigned_to_id})
    handoffs = {pid for (pid,) in db.query(EvoSenseHandoff.property_id)
                .filter(EvoSenseHandoff.organization_id == org_id, EvoSenseHandoff.property_id.in_(ids))}
    rows = []
    for p in cands:
        strategy = strategies.get(p.best_strategy_id or p.first_strategy_id)
        r = _queue_row(db, p, strategy, work.get(p.id, []), p.id in handoffs)
        if r is None or r["next"] is None or r["next"]["stopped"]:
            continue
        if step and r["next"]["step"] != step:
            continue
        rows.append(r)
    rows.sort(key=lambda r: (STEP_RANK.get(r["next"]["step"], 9), -(r["score"] or 0)))
    by_step: Dict[str, int] = {}
    for r in rows:
        by_step[r["next"]["step"]] = by_step.get(r["next"]["step"], 0) + 1
    return {"version": VERSION, "total": len(rows), "by_step": by_step,
            "items": rows[:max(1, min(limit, 200))],
            "note": "Most advanced first (a seller who answered before a house nobody has reached), "
                    "then by score. Scores and thresholds are the searches' own."}


def funnel(db, org_id: str, *, include_test: bool = False) -> Dict[str, Any]:
    """Where opportunities stop. Every number is counted from records; a
    stage the system has no record type for says so instead of showing 0."""
    from app.models.wholesale_models import (EXCEPTION_OPEN_STATUSES, WholesaleBuyerMatch,
                                             WholesaleDeal, WholesaleWorkException)
    P = EvoSenseProperty
    base = db.query(P).filter(P.organization_id == org_id, P.archived_at.is_(None))
    if not include_test:
        base = base.filter(P.is_test.is_(False))
    discovered = base.count()
    # "Worth reviewing" uses each property's own search threshold.
    from app.models.evosense_models import EvoSenseStrategy
    th_by = {sid: _threshold(st) for sid, st in
             ((x.id, x) for x in db.query(EvoSenseStrategy).filter(EvoSenseStrategy.organization_id == org_id))}
    worth = 0
    for _pid, score, sid in base.filter(P.opportunity_score.isnot(None)).with_entities(
            P.id, P.opportunity_score, P.best_strategy_id).all():
        worth += int((score or 0) >= th_by.get(sid, DEFAULT_THRESHOLD))
    ids = base.with_entities(P.id).subquery()
    owner_known = (db.query(func.count(func.distinct(EvoSenseOwnership.property_id)))
                   .filter(EvoSenseOwnership.organization_id == org_id,
                           EvoSenseOwnership.property_id.in_(ids.select()),
                           EvoSenseOwnership.is_current.is_(True),
                           EvoSenseOwnership.in_conflict.is_(False)).scalar() or 0)
    contact = base.filter(P.contactability.in_(("CONTACT_DATA_FOUND", "CONTACTABLE_SMS",
                                                 "CONTACTABLE_EMAIL", "CONTACTABLE_OTHER"))).count()
    investigations = (db.query(func.count(WholesaleWorkException.id))
                      .filter(WholesaleWorkException.organization_id == org_id,
                              WholesaleWorkException.kind == "verify_contact",
                              WholesaleWorkException.subject_type == "evosense_property",
                              WholesaleWorkException.subject_id.in_(ids.select()),
                              WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES)).scalar() or 0)
    talked = (db.query(func.count(func.distinct(EvoSenseMessage.property_id)))
              .filter(EvoSenseMessage.organization_id == org_id,
                      EvoSenseMessage.property_id.in_(ids.select()),
                      EvoSenseMessage.direction == "inbound").scalar() or 0)
    handoffs = (db.query(func.count(func.distinct(EvoSenseHandoff.property_id)))
                .filter(EvoSenseHandoff.organization_id == org_id,
                        EvoSenseHandoff.property_id.in_(ids.select())).scalar() or 0)
    deals = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id)
    if not include_test:
        deals = deals.filter(WholesaleDeal.is_test.isnot(True))
    deal_n = deals.count()
    matched = (db.query(func.count(func.distinct(WholesaleBuyerMatch.deal_id)))
               .filter(WholesaleBuyerMatch.organization_id == org_id,
                       WholesaleBuyerMatch.deal_id.in_(deals.with_entities(WholesaleDeal.id).subquery().select()))
               .scalar() or 0)
    closed = deals.filter(WholesaleDeal.stage == "closed").count()
    stages = [
        {"key": "discovered", "label": "Properties discovered", "count": discovered},
        {"key": "worth_review", "label": "At or above their search's threshold", "count": worth},
        {"key": "owner_known", "label": "Owner of record known (no conflict)", "count": owner_known},
        {"key": "contact", "label": "Phone or email on file", "count": contact},
        {"key": "investigating", "label": "Open contact investigations", "count": investigations},
        {"key": "talked", "label": "Sellers who replied / were heard", "count": talked},
        {"key": "handoff", "label": "Qualified hand-offs", "count": handoffs},
        {"key": "deals", "label": "Wholesale deals", "count": deal_n},
        {"key": "matched", "label": "Deals matched to buyers", "count": matched},
        {"key": "closed", "label": "Closed", "count": closed},
        {"key": "appointments", "label": "Seller appointments", "count": None,
         "unavailable": "Field appointments are counted in Wholesale > Appointments, not per property"},
    ]
    biggest = None
    flow = [s for s in stages if s["count"] is not None and s["key"] not in ("investigating", "owner_known")]
    for a, b in zip(flow, flow[1:]):
        if a["count"] and (biggest is None or (a["count"] - b["count"]) / a["count"] >
                           (biggest["from_count"] - biggest["to_count"]) / max(1, biggest["from_count"])):
            if a["key"] == "discovered":
                continue          # most discoveries are SUPPOSED to stay below threshold
            biggest = {"from": a["label"], "to": b["label"], "from_count": a["count"], "to_count": b["count"]}
    return {"version": VERSION, "stages": stages, "stuck_at": biggest,
            "as_of": datetime.utcnow().isoformat() + "Z"}



# ── contact investigations (the work queue's verify_contact items) ──────────

NEEDS_CONTACT = (C.S_HIGH, C.S_NEEDS_ENRICHMENT, C.S_BUDGET_BLOCKED, C.S_WAITING_DATA)
INVESTIGATION_KIND = "verify_contact"


def investigation_priority(score: Optional[int]) -> int:
    """Lower is sooner (the queue's own scale; callbacks are 10). 66 -> 14."""
    return max(11, 35 - max(0, int(score or 0) - DEFAULT_THRESHOLD))


def worth_working(db, prop) -> bool:
    from app.services.evosense import evaluate as EV
    if prop.status in (C.S_NEEDS_YOU, C.S_RESPONDED, C.S_OUTREACH, C.S_READY, C.S_CONTACT_FOUND,
                       C.S_NURTURE, C.S_PROMOTED):
        return True
    if (prop.opportunity_score or 0) >= _threshold(EV.strategy_for(db, prop)):
        return True
    # A house a PERSON put in (typed in, imported list) is worth their look
    # whatever it scores; only bulk county discovery below threshold is not.
    from app.models.evosense_models import EvoSenseObservation
    return db.query(EvoSenseObservation.id).filter(
        EvoSenseObservation.organization_id == prop.organization_id,
        EvoSenseObservation.property_id == prop.id,
        EvoSenseObservation.connector_kind == C.MANUAL).first() is not None


def _why(db, prop) -> str:
    o = CT.primary_owner(db, prop)
    st = contact_step(db, prop)
    ev = evidence_step(db, prop, None)
    sig = ", ".join(s["label"] for s in ev.get("signals", []) if s.get("scored"))[:300]
    lines = ["Scored %s. Evidence: %s." % (prop.opportunity_score, sig or "see property"),
             "Owner of record: %s%s." % ((o.display_name if o else "unknown"),
                                         (" - mailing " + ", ".join(x for x in (o.mailing_street, o.mailing_city,
                                                                                o.mailing_state) if x))
                                         if o is not None and o.mailing_street else "")]
    if st.get("ways"):
        lines.append("Ways to reach them: " + "; ".join(st["ways"]) + ".")
    lines.append("When you find a number or email, add it on the property page - this item closes itself.")
    return " ".join(lines)[:1500]


def contact_investigation_candidates(db, org_id: str, *, include_test: bool = True, limit: int = 200):
    """(property, why) for every property that passed its threshold and has no
    usable contact. Best score first."""
    q = (db.query(EvoSenseProperty)
         .filter(EvoSenseProperty.organization_id == org_id,
                 EvoSenseProperty.archived_at.is_(None),
                 EvoSenseProperty.promoted_deal_id.is_(None),
                 EvoSenseProperty.status.in_(NEEDS_CONTACT)))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.is_(False))
    out = []
    for p in q.order_by(EvoSenseProperty.opportunity_score.desc().nullslast()).limit(limit).all():
        if not worth_working(db, p):
            continue
        st = contact_step(db, p)
        if st["state"] != TODO:
            continue
        own = owner_step(db, p)
        if own["state"] == STOP:          # institutional: there is nobody to find
            continue
        out.append((p, _why(db, p)))
    return out


def close_investigation(db, org_id: str, prop, user, how: str) -> int:
    """A contact arrived (typed in, permission link, door knock): the open
    investigation is done. Recorded as the person's own result."""
    from app.models.wholesale_models import (ACTOR_USER, EXCEPTION_OPEN_STATUSES,
                                             WholesaleWorkException)
    from app.services import wholesale_service as svc
    n = 0
    for ex in (db.query(WholesaleWorkException)
               .filter(WholesaleWorkException.organization_id == org_id,
                       WholesaleWorkException.kind == INVESTIGATION_KIND,
                       WholesaleWorkException.subject_type == "evosense_property",
                       WholesaleWorkException.subject_id == prop.id,
                       WholesaleWorkException.status.in_(EXCEPTION_OPEN_STATUSES)).all()):
        before = ex.status
        ex.status, ex.outcome = "complete", "complete"
        ex.outcome_note = how[:500]
        ex.resolved_at, ex.resolved_by_id = datetime.utcnow(), getattr(user, "id", None)
        svc.log_event(db, org_id, "exception.resolved", actor_type=ACTOR_USER,
                      actor_user_id=getattr(user, "id", None),
                      summary="Contact investigation complete: %s" % ex.title,
                      before={"status": before}, after={"status": "complete", "outcome": "complete"},
                      details={"exception_id": ex.id, "note": ex.outcome_note})
        n += 1
    return n


def reopen_investigation(db, org_id: str, prop, user, why: str):
    """The contact turned out wrong: the property needs a person again now -
    not after the sweep's 30-day cooldown."""
    from app.services import wholesale_exceptions as WX
    if not worth_working(db, prop) or contact_step(db, prop)["state"] != TODO:
        return None
    ex, _ = WX.raise_exception(db, org_id, kind=INVESTIGATION_KIND, subject_type="evosense_property",
                               subject_id=prop.id,
                               title="Find a way to reach the owner of %s" % (prop.street_address or "a property"),
                               detail=("%s %s" % (why, _why(db, prop)))[:1500],
                               priority=investigation_priority(prop.opportunity_score),
                               source="evosense:contact_wrong", user=user, is_test=bool(prop.is_test))
    return ex
