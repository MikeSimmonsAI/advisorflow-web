"""LIST BUILDERS — the one implementation behind every list, the summary counts,
the attention feed and Ask EvoAI. Because the summary calls the SAME functions
with the SAME filters its `link` points at, a count can never disagree with
the list it drills into.

Scope: prospects go through lead_scope.authorized_lead_query (acting workspace;
an advisor sees only their own). Agency tables filter organization_id == the
acting org first; an advisor additionally sees only rows where they are the
agent / recruiter.
"""
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import case, exists, func, or_, select, union_all
from sqlalchemy.orm import Session

from app.models.agency_models import (AgencyApplication, AgencyAppointment, AgencyAssignment,
                                      AgencyCopilotEvent, AgencyPolicy, AgencyProspectProfile,
                                      AgencyRecruit, AgencyRecruitMilestone, AgencyTask)
from app.models.models import Lead, Message, Reply, User
from app.services.agency import applications as appsvc
from app.services.agency.common import (agent_profiles, config_dict, get_config, iso, jload,
                                        lead_is_demo, lead_name, now, org_agents, ref, user_names)
from app.services.lead_scope import authorized_lead_query


class Ctx:
    def __init__(self, db: Session, user: User, org_id: str, manager: bool, request=None):
        self.db, self.user, self.org_id, self.manager, self.request = db, user, org_id, manager, request
        self._cfg = None

    @property
    def cfg(self) -> Dict[str, Any]:
        if self._cfg is None:
            self._cfg = config_dict(get_config(self.db, self.org_id))
        return self._cfg

    def own(self, q, col):
        return q if self.manager else q.filter(col == self.user.id)


def _truthy(v) -> bool:
    return str(v).lower() in ("1", "true", "yes") if v is not None else False


# ── prospects ───────────────────────────────────────────────────────────────

def _latest_assignments(db, org_id, lead_ids) -> Dict[str, AgencyAssignment]:
    out: Dict[str, AgencyAssignment] = {}
    if not lead_ids:
        return out
    rows = (db.query(AgencyAssignment).filter(AgencyAssignment.organization_id == org_id,
                                              AgencyAssignment.lead_id.in_(lead_ids))
            .order_by(AgencyAssignment.created_at, AgencyAssignment.attempt).all())
    for a in rows:
        out[a.lead_id] = a
    return out


def _last_activity(db, lead_ids) -> Dict[str, datetime]:
    out: Dict[str, datetime] = {}
    if not lead_ids:
        return out
    for model, col in ((Message, Message.sent_at), (Reply, Reply.received_at),
                       (AgencyCopilotEvent, AgencyCopilotEvent.created_at)):
        for lid, ts in db.query(model.lead_id, func.max(col)).filter(
                model.lead_id.in_(lead_ids)).group_by(model.lead_id):
            if ts and (lid not in out or ts > out[lid]):
                out[lid] = ts
    return out


def contacted_lead_ids(db, lead_ids) -> set:
    """Real outbound only: a Message row, or a recorded last_contact_date.
    Simulated copilot sends are NOT contact."""
    if not lead_ids:
        return set()
    s = {lid for (lid,) in db.query(Message.lead_id).filter(Message.lead_id.in_(lead_ids)).distinct()}
    s |= {lid for (lid,) in db.query(Lead.id).filter(Lead.id.in_(lead_ids),
                                                     Lead.last_contact_date.isnot(None))}
    return s


def assignment_state(lead: Lead, a: Optional[AgencyAssignment]) -> str:
    if a is not None:
        return a.state
    return "accepted" if lead.assigned_to_id else "unassigned"


def _latest_state_col(org_id):
    """Correlated scalar subquery: the state of the lead's most recent offer
    (same order _latest_assignments uses), or NULL when never offered."""
    return (select(AgencyAssignment.state)
            .where(AgencyAssignment.organization_id == org_id,
                   AgencyAssignment.lead_id == Lead.id)
            .order_by(AgencyAssignment.created_at.desc(), AgencyAssignment.attempt.desc())
            .limit(1).correlate(Lead).scalar_subquery())


def _prospect_query(ctx: Ctx, q=None, status=None, intent=None, agent_id=None, unassigned=None,
                    need=None, uncontacted=None, past_response_target=None, assignment=None,
                    lead_id=None, state=None, state_missing=None, need_missing=None,
                    intent_missing=None):
    """Every prospect filter, in SQL. The list, its total and the summary
    counts all come from this one query, so a large book never loads whole."""
    db = ctx.db
    query = authorized_lead_query(db, ctx.user, request=ctx.request).filter(
        Lead.organization_id == ctx.org_id)
    if lead_id:
        query = query.filter(Lead.id == lead_id)
    query = query.outerjoin(AgencyProspectProfile, AgencyProspectProfile.lead_id == Lead.id)
    if q:
        like = "%%%s%%" % q.strip()
        query = query.filter((Lead.first_name.ilike(like)) | (Lead.last_name.ilike(like)) |
                             (Lead.email.ilike(like)) | (Lead.phone.ilike(like)))
    if status:
        query = query.filter(Lead.status == status)
    if intent:
        query = query.filter(AgencyProspectProfile.intent_level == intent)
    if agent_id:
        query = query.filter(Lead.assigned_to_id == agent_id)
    if need:
        query = query.filter(AgencyProspectProfile.need_categories.like('%%"%s"%%' % need))
    if state:
        query = query.filter(func.upper(func.trim(Lead.state)) == state.strip().upper())
    if state_missing:
        query = query.filter(or_(Lead.state.is_(None), func.trim(Lead.state) == ""))
    if need_missing:
        query = query.filter(or_(AgencyProspectProfile.id.is_(None),
                                 AgencyProspectProfile.need_categories.is_(None),
                                 AgencyProspectProfile.need_categories.in_(("", "[]"))))
    if intent_missing:
        query = query.filter(or_(AgencyProspectProfile.id.is_(None),
                                 AgencyProspectProfile.intent_level.is_(None)))
    latest = _latest_state_col(ctx.org_id)
    if unassigned:
        query = query.filter(Lead.assigned_to_id.is_(None),
                             or_(latest.is_(None), ~latest.in_(("offered", "escalated", "accepted"))))
    if assignment:
        effective = func.coalesce(latest, case((Lead.assigned_to_id.isnot(None), "accepted"),
                                               else_="unassigned"))
        query = query.filter(effective == assignment)
    if uncontacted or past_response_target:
        # Real outbound only (a Message row or a recorded last_contact_date);
        # simulated copilot sends are NOT contact. Same rule as contacted_lead_ids.
        query = query.filter(~exists().where(Message.lead_id == Lead.id),
                             Lead.last_contact_date.is_(None))
    if past_response_target:
        cutoff = now() - timedelta(minutes=int(ctx.cfg["response_target_minutes"]))
        query = query.filter(Lead.created_at.isnot(None), Lead.created_at < cutoff)
    return query


def _prospect_rows(ctx: Ctx, rows) -> List[Dict[str, Any]]:
    db = ctx.db
    ids = [l.id for l, _ in rows]
    latest = _latest_assignments(db, ctx.org_id, ids)
    names = user_names(db, [l.assigned_to_id for l, _ in rows])
    act = _last_activity(db, ids)
    out = []
    for l, p in rows:
        out.append({
            "id": l.id, "name": lead_name(l), "email": l.email, "phone": l.phone, "state": l.state,
            "source": l.source, "created_at": iso(l.created_at), "status": l.status,
            "intent_level": p.intent_level if p else None,
            "need_categories": jload(p.need_categories, []) if p else [],
            "assigned_agent": ref(l.assigned_to_id, names),
            "assignment_state": assignment_state(l, latest.get(l.id)),
            "last_activity_at": iso(act.get(l.id)),
            "sms_consent": l.sms_consent if l.sms_consent is not None else None,
            "is_demo": lead_is_demo(l, p),
        })
    return out


def _ordered(query):
    return query.add_entity(AgencyProspectProfile).order_by(Lead.created_at.desc(), Lead.id)


def list_prospects(ctx: Ctx, **filters) -> List[Dict[str, Any]]:
    """Every matching prospect (used by attention / Ask, which need the rows)."""
    return _prospect_rows(ctx, _ordered(_prospect_query(ctx, **filters)).all())


def count_prospects(ctx: Ctx, **filters) -> int:
    return _prospect_query(ctx, **filters).order_by(None).count()


def page_prospects(ctx: Ctx, page: int = 1, per_page: int = 50, **filters) -> Dict[str, Any]:
    """One page, filtered/counted/offset in SQL; rows built only for the page."""
    per_page = max(1, min(int(per_page or 50), 200))
    page = max(1, int(page or 1))
    query = _prospect_query(ctx, **filters)
    total = query.order_by(None).count()
    rows = _ordered(query).offset((page - 1) * per_page).limit(per_page).all()
    return {"items": _prospect_rows(ctx, rows), "total": total, "page": page, "per_page": per_page}


# ── conversations ───────────────────────────────────────────────────────────

def page_conversations(ctx: Ctx, page: int = 1, per_page: int = 50, q: Optional[str] = None,
                       awaiting_reply: Optional[bool] = None) -> Dict[str, Any]:
    """Prospects in the acting workspace that HAVE a conversation (a real
    Message, an inbound Reply, or a recorded SIMULATED copilot send), newest
    activity first. Same lead scope as the prospect list; nothing is sent."""
    db = ctx.db
    sims = (select(AgencyCopilotEvent.lead_id.label("lead_id"), AgencyCopilotEvent.created_at.label("at"))
            .where(AgencyCopilotEvent.organization_id == ctx.org_id,
                   AgencyCopilotEvent.kind == "simulated_send"))
    outs = union_all(select(Message.lead_id.label("lead_id"), Message.sent_at.label("at")), sims).subquery()
    last_out = (select(outs.c.lead_id, func.max(outs.c.at).label("at")).group_by(outs.c.lead_id).subquery())
    last_in = (select(Reply.lead_id.label("lead_id"), func.max(Reply.received_at).label("at"))
               .group_by(Reply.lead_id).subquery())
    query = (_prospect_query(ctx, q=q)
             .outerjoin(last_out, last_out.c.lead_id == Lead.id)
             .outerjoin(last_in, last_in.c.lead_id == Lead.id)
             .filter(or_(last_out.c.at.isnot(None), last_in.c.at.isnot(None))))
    if awaiting_reply:
        query = query.filter(last_in.c.at.isnot(None),
                             or_(last_out.c.at.is_(None), last_in.c.at > last_out.c.at))
    last_at = case((last_in.c.at.is_(None), last_out.c.at), (last_out.c.at.is_(None), last_in.c.at),
                   (last_in.c.at > last_out.c.at, last_in.c.at), else_=last_out.c.at)
    per_page = max(1, min(int(per_page or 50), 200))
    page = max(1, int(page or 1))
    total = query.order_by(None).count()
    rows = (query.add_entity(AgencyProspectProfile).add_columns(last_at)
            .order_by(last_at.desc(), Lead.id).offset((page - 1) * per_page).limit(per_page).all())
    items = []
    base = {r["id"]: r for r in _prospect_rows(ctx, [(l, p) for l, p, _ in rows])} if rows else {}
    ids = [l.id for l, _, _ in rows]
    newest: Dict[str, Dict[str, Any]] = {}
    inbound_n: Dict[str, int] = {}

    def offer(lid, d):
        cur = newest.get(lid)
        if cur is None or (d["at"] or "") >= (cur["at"] or ""):
            newest[lid] = d
    if ids:
        for m in db.query(Message).filter(Message.lead_id.in_(ids)):
            offer(m.lead_id, {"direction": "outbound", "channel": "sms", "body": m.body,
                              "at": iso(m.sent_at), "simulated": False})
        for r in db.query(Reply).filter(Reply.lead_id.in_(ids)):
            inbound_n[r.lead_id] = inbound_n.get(r.lead_id, 0) + 1
            offer(r.lead_id, {"direction": "inbound", "channel": r.source or "sms", "body": r.body,
                              "at": iso(r.received_at), "simulated": False})
        for e in db.query(AgencyCopilotEvent).filter(AgencyCopilotEvent.organization_id == ctx.org_id,
                                                     AgencyCopilotEvent.lead_id.in_(ids),
                                                     AgencyCopilotEvent.kind == "simulated_send"):
            offer(e.lead_id, {"direction": "outbound", "channel": "simulated", "body": e.body,
                              "at": iso(e.created_at), "simulated": True})
    for l, _, _ in rows:
        b = base[l.id]
        last_msg = newest.get(l.id)
        if last_msg and last_msg.get("body") and len(last_msg["body"]) > 160:
            last_msg = dict(last_msg, body=last_msg["body"][:157] + "...")
        items.append({"prospect": {"id": l.id, "name": b["name"]}, "assigned_agent": b["assigned_agent"],
                      "intent_level": b["intent_level"], "sms_consent": b["sms_consent"],
                      "last_message": last_msg, "inbound_count": inbound_n.get(l.id, 0),
                      "awaiting_reply": bool(last_msg and last_msg["direction"] == "inbound"),
                      "last_activity_at": last_msg["at"] if last_msg else None,
                      "is_demo": b["is_demo"]})
    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ── applications ────────────────────────────────────────────────────────────

def _lead_names(db, org_id, ids):
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {l.id: lead_name(l) for l in db.query(Lead).filter(Lead.organization_id == org_id,
                                                               Lead.id.in_(ids))}


def application_row(ctx: Ctx, a: AgencyApplication, names, lnames) -> Dict[str, Any]:
    stalled = appsvc.is_stalled(a, ctx.cfg["stalled_days"])
    return {"id": a.id, "prospect": {"id": a.lead_id, "name": lnames.get(a.lead_id)},
            "agent": ref(a.agent_user_id, names), "carrier": a.carrier,
            "product_category": a.product_category, "status": a.status,
            "submitted_at": iso(a.submitted_at), "days_in_status": appsvc.days_in_status(a),
            "stalled": stalled, "next_action": appsvc.NEXT_ACTION.get(a.status),
            "allowed_transitions": appsvc.allowed_from(a.status),
            "policy_id": a.policy_id, "is_demo": bool(a.is_demo)}


def list_applications(ctx: Ctx, status=None, agent_id=None, stalled=None, open_only=None,
                      lead_id=None) -> List[Dict[str, Any]]:
    q = ctx.own(ctx.db.query(AgencyApplication).filter(
        AgencyApplication.organization_id == ctx.org_id), AgencyApplication.agent_user_id)
    if status:
        q = q.filter(AgencyApplication.status == status)
    if agent_id:
        q = q.filter(AgencyApplication.agent_user_id == agent_id)
    if lead_id:
        q = q.filter(AgencyApplication.lead_id == lead_id)
    if open_only:
        q = q.filter(~AgencyApplication.status.in_(appsvc.TERMINAL))
    apps = q.order_by(AgencyApplication.created_at.desc(), AgencyApplication.id).all()
    names = user_names(ctx.db, [a.agent_user_id for a in apps])
    lnames = _lead_names(ctx.db, ctx.org_id, [a.lead_id for a in apps])
    rows = [application_row(ctx, a, names, lnames) for a in apps]
    if stalled:
        rows = [r for r in rows if r["stalled"]]
    return rows


# ── appointments ────────────────────────────────────────────────────────────

def list_appointments(ctx: Ctx, range_=None, status=None, agent_id=None, lead_id=None,
                      needs_confirmation=None) -> List[Dict[str, Any]]:
    q = ctx.own(ctx.db.query(AgencyAppointment).filter(
        AgencyAppointment.organization_id == ctx.org_id), AgencyAppointment.agent_user_id)
    t = now()
    if range_ == "today":
        # The AGENCY's today: a 7pm Central appointment is today's, not tomorrow's.
        from app.services import workspace_time as wt
        sod, eod, _tz = wt.day_bounds(ctx.db, ctx.org_id, now=t)
        q = q.filter(AgencyAppointment.starts_at >= sod, AgencyAppointment.starts_at < eod)
    elif range_ == "upcoming":
        q = q.filter(AgencyAppointment.starts_at >= t)
    elif range_ == "past":
        q = q.filter(AgencyAppointment.starts_at < t)
    if status:
        q = q.filter(AgencyAppointment.status == status)
    if agent_id:
        q = q.filter(AgencyAppointment.agent_user_id == agent_id)
    if lead_id:
        q = q.filter(AgencyAppointment.lead_id == lead_id)
    if needs_confirmation:
        q = q.filter(AgencyAppointment.status == "pending", AgencyAppointment.starts_at >= t)
    rows = q.order_by(AgencyAppointment.starts_at, AgencyAppointment.id).all()
    names = user_names(ctx.db, [a.agent_user_id for a in rows])
    lnames = _lead_names(ctx.db, ctx.org_id, [a.lead_id for a in rows])
    return [{"id": a.id, "prospect": {"id": a.lead_id, "name": lnames.get(a.lead_id)},
             "agent": ref(a.agent_user_id, names), "type": a.type, "medium": a.medium,
             "starts_at": iso(a.starts_at), "status": a.status, "notes": a.notes,
             "is_demo": bool(a.is_demo)} for a in rows]


# ── policies ────────────────────────────────────────────────────────────────

def _open_tasks_by(db, org_id, col, ids) -> Dict[str, int]:
    if not ids:
        return {}
    return {k: int(n) for k, n in db.query(col, func.count(AgencyTask.id)).filter(
        AgencyTask.organization_id == org_id, col.in_(ids), AgencyTask.status == "open").group_by(col)}


def list_policies(ctx: Ctx, review_due=None, status=None, lead_id=None,
                  review_month=None) -> List[Dict[str, Any]]:
    q = ctx.own(ctx.db.query(AgencyPolicy).filter(AgencyPolicy.organization_id == ctx.org_id),
                AgencyPolicy.agent_user_id)
    if status:
        q = q.filter(AgencyPolicy.status == status)
    if lead_id:
        q = q.filter(AgencyPolicy.lead_id == lead_id)
    if review_due:
        horizon = date.today() + timedelta(days=int(ctx.cfg["review_window_days"]))
        q = q.filter(AgencyPolicy.annual_review_date.isnot(None),
                     AgencyPolicy.annual_review_date <= horizon)
    if review_month:
        # "YYYY-MM": annual_review_date within that calendar month
        from calendar import monthrange
        y, m = (int(x) for x in str(review_month).split("-")[:2])
        q = q.filter(AgencyPolicy.annual_review_date >= date(y, m, 1),
                     AgencyPolicy.annual_review_date <= date(y, m, monthrange(y, m)[1]))
    pols = q.order_by(AgencyPolicy.annual_review_date, AgencyPolicy.id).all()
    names = user_names(ctx.db, [p.agent_user_id for p in pols])
    lnames = _lead_names(ctx.db, ctx.org_id, [p.lead_id for p in pols])
    tasks = _open_tasks_by(ctx.db, ctx.org_id, AgencyTask.policy_id, [p.id for p in pols])
    return [{"id": p.id, "client": {"id": p.lead_id, "name": lnames.get(p.lead_id)},
             "agent": ref(p.agent_user_id, names), "carrier": p.carrier,
             "product_category": p.product_category, "status": p.status,
             "policy_number": p.policy_number, "application_id": p.application_id,
             "effective_date": iso(p.effective_date), "annual_review_date": iso(p.annual_review_date),
             "open_service_tasks": tasks.get(p.id, 0), "is_demo": bool(p.is_demo)} for p in pols]


# ── recruits ────────────────────────────────────────────────────────────────

def list_recruits(ctx: Ctx, stage=None, near_activation=None, milestone_overdue=None, recruit_id=None):
    q = ctx.own(ctx.db.query(AgencyRecruit).filter(AgencyRecruit.organization_id == ctx.org_id),
                AgencyRecruit.recruiter_user_id)
    if recruit_id:
        q = q.filter(AgencyRecruit.id == recruit_id)
    if stage:
        q = q.filter(AgencyRecruit.stage == stage)
    stages = ctx.cfg["recruit_stages"]
    recs = q.order_by(AgencyRecruit.created_at.desc(), AgencyRecruit.id).all()
    ids = [r.id for r in recs]
    ms: Dict[str, List[AgencyRecruitMilestone]] = {}
    if ids:
        for m in ctx.db.query(AgencyRecruitMilestone).filter(
                AgencyRecruitMilestone.organization_id == ctx.org_id,
                AgencyRecruitMilestone.recruit_id.in_(ids)):
            ms.setdefault(m.recruit_id, []).append(m)
    names = user_names(ctx.db, [r.recruiter_user_id for r in recs])
    today = date.today()
    out = []
    for r in recs:
        mlist = ms.get(r.id, [])
        overdue = [m for m in mlist if m.status != "done" and m.due and m.due < today]
        idx = stages.index(r.stage) if r.stage in stages else -1
        near = idx >= 0 and idx >= len(stages) - 3 and r.stage != stages[-1]
        out.append({"id": r.id, "name": r.name, "email": r.email, "phone": r.phone,
                    "jurisdiction": r.jurisdiction, "stage": r.stage,
                    "stage_index": idx, "stage_changed_at": iso(r.stage_changed_at),
                    "recruiter": ref(r.recruiter_user_id, names), "exam_status": r.exam_status,
                    "training_progress_pct": r.training_progress_pct,
                    "milestones_total": len(mlist),
                    "milestones_done": sum(1 for m in mlist if m.status == "done"),
                    "milestones_overdue": len(overdue), "near_activation": near,
                    "is_demo": bool(r.is_demo)})
    if near_activation:
        out = [r for r in out if r["near_activation"]]
    if milestone_overdue:
        out = [r for r in out if r["milestones_overdue"]]
    return out


# ── agents ──────────────────────────────────────────────────────────────────

def list_agents(ctx: Ctx, with_capacity=None, over_workload=None) -> List[Dict[str, Any]]:
    from app.services.agency.distribution import active_counts
    db = ctx.db
    profs = agent_profiles(db, ctx.org_id)
    counts = active_counts(db, ctx.org_id)
    t = now()
    queue = {k: int(n) for k, n in db.query(AgencyAssignment.agent_user_id, func.count(AgencyAssignment.id))
             .filter(AgencyAssignment.organization_id == ctx.org_id, AgencyAssignment.state == "offered")
             .group_by(AgencyAssignment.agent_user_id)}
    appts = {k: int(n) for k, n in db.query(AgencyAppointment.agent_user_id, func.count(AgencyAppointment.id))
             .filter(AgencyAppointment.organization_id == ctx.org_id, AgencyAppointment.starts_at >= t,
                     AgencyAppointment.status.in_(("pending", "confirmed")))
             .group_by(AgencyAppointment.agent_user_id)}
    apps = {k: int(n) for k, n in db.query(AgencyApplication.agent_user_id, func.count(AgencyApplication.id))
            .filter(AgencyApplication.organization_id == ctx.org_id,
                    ~AgencyApplication.status.in_(appsvc.TERMINAL))
            .group_by(AgencyApplication.agent_user_id)}
    out = []
    for u in org_agents(db, ctx.org_id):
        p = profs.get(u.id)
        if p is None or (not ctx.manager and u.id != ctx.user.id):
            continue
        cap = p.max_active or ctx.cfg["max_active_per_agent"]
        n = counts.get(u.id, 0)
        pct = int(round(100.0 * n / cap)) if cap else None
        out.append({"user_id": u.id, "name": u.full_name, "active": bool(u.is_active and p.active),
                    "jurisdictions": jload(p.jurisdictions, []),
                    "specializations": jload(p.specializations, []),
                    "available": bool(p.available), "max_active": cap, "active_count": n,
                    "workload_pct": pct, "queue_count": queue.get(u.id, 0),
                    "appointments_upcoming": appts.get(u.id, 0),
                    "applications_open": apps.get(u.id, 0),
                    "avg_response_minutes": p.avg_response_minutes, "is_demo": bool(p.is_demo)})
    if with_capacity:
        out = [a for a in out if a["active"] and a["available"] and a["active_count"] < a["max_active"]]
    if over_workload:
        out = [a for a in out if a["workload_pct"] is not None
               and a["workload_pct"] >= int(ctx.cfg["workload_alert_pct"])]
    return out


# ── tasks ───────────────────────────────────────────────────────────────────

def task_row(t: AgencyTask) -> Dict[str, Any]:
    return {"id": t.id, "kind": t.kind, "title": t.title, "status": t.status, "due_at": iso(t.due_at),
            "lead_id": t.lead_id, "application_id": t.application_id, "policy_id": t.policy_id,
            "recruit_id": t.recruit_id, "assigned_user_id": t.assigned_user_id,
            "is_demo": bool(t.is_demo)}


def overdue_tasks(ctx: Ctx) -> List[AgencyTask]:
    q = ctx.own(ctx.db.query(AgencyTask).filter(AgencyTask.organization_id == ctx.org_id,
                                                AgencyTask.status == "open",
                                                AgencyTask.due_at.isnot(None),
                                                AgencyTask.due_at < now()),
                AgencyTask.assigned_user_id)
    return q.order_by(AgencyTask.due_at).all()


def open_tasks(ctx: Ctx) -> List[AgencyTask]:
    q = ctx.own(ctx.db.query(AgencyTask).filter(AgencyTask.organization_id == ctx.org_id,
                                                AgencyTask.status == "open"),
                AgencyTask.assigned_user_id)
    return q.order_by(AgencyTask.due_at, AgencyTask.id).all()
