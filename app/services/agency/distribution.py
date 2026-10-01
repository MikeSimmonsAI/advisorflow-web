"""EXPLAINABLE DISTRIBUTION + ASSIGNMENT / ACCEPTANCE / ESCALATION.

Every reason a candidate is given is read from a stored record (agent profile,
lead.state, prospect profile, counts of assigned leads / open offers, assignment
history). A factor whose data is not stored is reported in
`unavailable_factors` instead of being guessed. "Similar-lead conversion
history" is never computed here, so it is always reported unavailable.

Thresholds (acceptance timeout, workload cap, escalation user) come from the
org's agency_distribution_configs row - nothing agency-specific is hard-coded.
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agency_models import AgencyAssignment
from app.models.models import Lead, User
from app.services.agency.common import (CLOSED_LEAD_STATUSES, NEED_LABELS, agent_profiles,
                                        config_dict, get_config, jdump, jload, now, iso,
                                        org_agents, profile_for)

OPEN_OFFER_STATES = ("offered",)


def active_counts(db: Session, org_id: str) -> Dict[str, int]:
    """Assigned open leads + pending offers per agent. Traceable to rows."""
    counts: Dict[str, int] = {}
    q = (db.query(Lead.assigned_to_id, func.count(Lead.id))
         .filter(Lead.organization_id == org_id, Lead.assigned_to_id.isnot(None),
                 ~func.coalesce(Lead.status, "new").in_(CLOSED_LEAD_STATUSES))
         .group_by(Lead.assigned_to_id))
    for uid, n in q:
        counts[uid] = counts.get(uid, 0) + int(n)
    q2 = (db.query(AgencyAssignment.agent_user_id, func.count(AgencyAssignment.id))
          .filter(AgencyAssignment.organization_id == org_id,
                  AgencyAssignment.state.in_(OPEN_OFFER_STATES))
          .group_by(AgencyAssignment.agent_user_id))
    for uid, n in q2:
        if uid:
            counts[uid] = counts.get(uid, 0) + int(n)
    return counts


def recommend(db: Session, org_id: str, lead: Lead) -> Dict[str, Any]:
    cfg = config_dict(get_config(db, org_id))
    factors = set(cfg["factors_enabled"])
    profile = profile_for(db, org_id, lead.id)
    needs = jload(profile.need_categories, []) if profile else []
    state = (lead.state or "").strip().upper() or None
    profiles = agent_profiles(db, org_id)
    counts = active_counts(db, org_id)
    prior = {a.agent_user_id: a.state for a in db.query(AgencyAssignment).filter(
        AgencyAssignment.organization_id == org_id, AgencyAssignment.lead_id == lead.id,
        AgencyAssignment.state.in_(("declined", "timed_out")))}

    unavailable: List[str] = ["similar-lead conversion history (not tracked)"]
    if "jurisdiction" in factors and not state:
        unavailable.append("jurisdiction (prospect state not recorded)")
    if "specialization" in factors and not needs:
        unavailable.append("specialization match (prospect need categories not recorded)")
    no_response = []

    candidates = []
    for u in org_agents(db, org_id):
        p = profiles.get(u.id)
        if p is None:
            continue  # not configured as an agency agent
        reasons, blockers, score = [], [], 0.0
        if not u.is_active or not p.active:
            blockers.append("agent inactive")
        juris = [j.upper() for j in jload(p.jurisdictions, [])]
        specs = jload(p.specializations, [])
        if "jurisdiction" in factors and state:
            if not juris:
                blockers.append("no licensed jurisdictions configured")
            elif state in juris:
                reasons.append("licensed jurisdiction list includes %s" % state)
                score += 30
            else:
                blockers.append("jurisdiction list does not include %s" % state)
        if "specialization" in factors and needs:
            match = [n for n in needs if n in specs]
            if match:
                reasons.append("%s specialization" % ", ".join(NEED_LABELS.get(m, m) for m in match))
                score += 25 * len(match)
        if "availability" in factors:
            if p.available:
                reasons.append("marked available")
                score += 10
            else:
                blockers.append("marked unavailable")
        cap = p.max_active or cfg["max_active_per_agent"]
        active = counts.get(u.id, 0)
        pct = int(round(100.0 * active / cap)) if cap else None
        if "workload" in factors and cap:
            if active >= cap:
                blockers.append("at capacity (%d of %d active)" % (active, cap))
            else:
                reasons.append("%d%% workload (%d of %d active)" % (pct, active, cap))
                score += max(0, 100 - pct) * 0.3
        if "response_performance" in factors:
            if p.avg_response_minutes is None:
                no_response.append(u.full_name)
            else:
                reasons.append("recorded average response %d min" % round(p.avg_response_minutes))
                score += max(0.0, 20 - p.avg_response_minutes / 6.0)
        if "prior_decline" in factors and u.id in prior:
            blockers.append("previously %s this prospect" % prior[u.id].replace("_", " "))
        candidates.append({"agent_id": u.id, "name": u.full_name, "eligible": not blockers,
                           "score": round(score, 1), "reasons": reasons, "blockers": blockers,
                           "workload_pct": pct, "is_demo": bool(p.is_demo)})
    if no_response:
        unavailable.append("response performance (no recorded history for: %s)" % ", ".join(no_response))
    candidates.sort(key=lambda c: (not c["eligible"], -c["score"], c["name"] or ""))
    best = next((c for c in candidates if c["eligible"]), None)
    rec = ({"agent_id": best["agent_id"], "name": best["name"], "score": best["score"],
            "reasons": best["reasons"]} if best else None)
    return {"recommended": rec, "candidates": candidates, "unavailable_factors": unavailable}


# ── assignment lifecycle ────────────────────────────────────────────────────

def _hist(a: AgencyAssignment, event: str, by: Optional[str], note: Optional[str] = None):
    h = jload(a.history, [])
    h.append({"at": iso(now()), "event": event, "by": by, "note": note})
    a.history = jdump(h)


def _audit(db, org_id, actor_id, action, a: AgencyAssignment, details=None):
    from app.routers.audit_log_router import log_action
    if not actor_id:
        return  # audit_log_entries.actor_user_id is a users FK; callers pass the acting user
    log_action(db, org_id, actor_id, action, "agency_assignment", a.id,
               details=dict(details or {}, lead_id=a.lead_id, agent_user_id=a.agent_user_id,
                            state=a.state), commit=False)


def current_assignment(db: Session, org_id: str, lead_id: str) -> Optional[AgencyAssignment]:
    return (db.query(AgencyAssignment)
            .filter(AgencyAssignment.organization_id == org_id, AgencyAssignment.lead_id == lead_id)
            .order_by(AgencyAssignment.created_at.desc(), AgencyAssignment.attempt.desc()).first())


def offer(db: Session, org_id: str, lead: Lead, agent: User, actor_id: Optional[str],
          note: Optional[str] = None, reasons: Optional[List[str]] = None,
          attempt: int = 1, is_demo: bool = False) -> AgencyAssignment:
    cfg = get_config(db, org_id)
    t = now()
    # any still-open offer for this lead is superseded (recorded, not deleted)
    for old in db.query(AgencyAssignment).filter(
            AgencyAssignment.organization_id == org_id, AgencyAssignment.lead_id == lead.id,
            AgencyAssignment.state == "offered"):
        old.state = "declined"
        old.decline_reason = "superseded by a new offer"
        old.responded_at = t
        _hist(old, "superseded", actor_id)
    a = AgencyAssignment(organization_id=org_id, lead_id=lead.id, agent_user_id=agent.id,
                         state="offered", offered_at=t,
                         expires_at=t + timedelta(minutes=int(cfg.acceptance_timeout_minutes or 30)),
                         note=note, offered_by=actor_id or "system", attempt=attempt,
                         reasons=jdump(reasons or []), is_demo=is_demo, created_at=t)
    _hist(a, "offered", actor_id, note)
    db.add(a)
    db.flush()
    _audit(db, org_id, actor_id, "agency.assignment.offered", a, {"attempt": attempt})
    return a


def accept(db: Session, org_id: str, a: AgencyAssignment, actor_id: str) -> AgencyAssignment:
    a.state = "accepted"
    a.responded_at = now()
    _hist(a, "accepted", actor_id)
    lead = db.query(Lead).filter(Lead.organization_id == org_id, Lead.id == a.lead_id).first()
    if lead is not None:
        lead.assigned_to_id = a.agent_user_id
    _audit(db, org_id, actor_id, "agency.assignment.accepted", a)
    return a


def _next_or_escalate(db: Session, org_id: str, a: AgencyAssignment, actor_id: Optional[str]):
    lead = db.query(Lead).filter(Lead.organization_id == org_id, Lead.id == a.lead_id).first()
    if lead is None:
        return None
    rec = recommend(db, org_id, lead)["recommended"]
    if rec:
        agent = db.query(User).filter(User.id == rec["agent_id"], User.organization_id == org_id).first()
        return offer(db, org_id, lead, agent, actor_id,
                     note="automatic reassignment after %s" % a.state.replace("_", " "),
                     reasons=rec["reasons"], attempt=(a.attempt or 1) + 1, is_demo=a.is_demo)
    cfg = get_config(db, org_id)
    t = now()
    esc = AgencyAssignment(organization_id=org_id, lead_id=lead.id,
                           agent_user_id=cfg.escalation_user_id, state="escalated",
                           offered_at=t, offered_by=actor_id or "system",
                           attempt=(a.attempt or 1) + 1,
                           note=("no eligible agent; escalated to manager" if cfg.escalation_user_id
                                 else "no eligible agent and no escalation manager configured"),
                           reasons=jdump([]), is_demo=a.is_demo, created_at=t)
    _hist(esc, "escalated", actor_id or "system", esc.note)
    db.add(esc)
    db.flush()
    _audit(db, org_id, actor_id, "agency.assignment.escalated", esc)
    return esc


def decline(db: Session, org_id: str, a: AgencyAssignment, actor_id: str, reason: Optional[str]):
    a.state = "declined"
    a.responded_at = now()
    a.decline_reason = reason
    _hist(a, "declined", actor_id, reason)
    _audit(db, org_id, actor_id, "agency.assignment.declined", a, {"reason": reason})
    db.flush()
    return _next_or_escalate(db, org_id, a, actor_id)


def sweep(db: Session, org_id: str, actor_id: Optional[str], at=None) -> Dict[str, Any]:
    t = at or now()
    expired = (db.query(AgencyAssignment)
               .filter(AgencyAssignment.organization_id == org_id,
                       AgencyAssignment.state == "offered",
                       AgencyAssignment.expires_at.isnot(None),
                       AgencyAssignment.expires_at < t).all())
    out = []
    for a in expired:
        a.state = "timed_out"
        a.responded_at = t
        _hist(a, "timed_out", actor_id or "system")
        _audit(db, org_id, actor_id, "agency.assignment.timed_out", a)
        db.flush()
        nxt = _next_or_escalate(db, org_id, a, actor_id)
        out.append({"timed_out_id": a.id, "lead_id": a.lead_id,
                    "next": ({"assignment_id": nxt.id, "state": nxt.state,
                              "agent_user_id": nxt.agent_user_id} if nxt else None)})
    return {"timed_out": len(out), "results": out}


def assignment_row(a: AgencyAssignment, names, lead_names=None) -> Dict[str, Any]:
    return {"id": a.id, "prospect": {"id": a.lead_id, "name": (lead_names or {}).get(a.lead_id)},
            "agent": {"id": a.agent_user_id, "name": names.get(a.agent_user_id)} if a.agent_user_id else None,
            "state": a.state, "offered_at": iso(a.offered_at), "expires_at": iso(a.expires_at),
            "responded_at": iso(a.responded_at), "decline_reason": a.decline_reason,
            "note": a.note, "attempt": a.attempt, "reasons": jload(a.reasons, []),
            "history": jload(a.history, []), "is_demo": bool(a.is_demo)}
