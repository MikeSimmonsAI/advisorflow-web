"""
Pipeline Router — Full AI conversation pipeline endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from sqlalchemy import func
from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.models.models import User, Lead, PipelineConversation, Organization
from app.services.pipeline_service import (
    launch_pipeline, get_pipeline_stats, get_ai_forecast
)
from app.routers.audit_log_router import log_action
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.services import lead_scope


def _get_org_ids(db: Session, current_user: User) -> list:
    """Return org IDs to scope queries to.

    THE NEUTRAL OWNER sees all orgs. An owner STANDING INSIDE A CUSTOMER sees
    that customer — role is permission, not scope. See
    lead_scope.god_sees_all_orgs.
    """
    if lead_scope.god_sees_all_orgs(current_user):
        return [str(row[0]) for row in db.query(Organization.id).all()]
    return [str(lead_scope.active_workspace_org_id(current_user, db)
                or current_user.organization_id)]

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


class LaunchRequest(BaseModel):
    lead_ids: list[str]
    lead_type: str = "general"
    tone: str = "warm"
    ai_direction: str = ""
    channel: str = "sms"
    auto_respond: bool = True


class ApproveRequest(BaseModel):
    pipeline_id: str
    message: str
    send: bool = True


class ForecastRequest(BaseModel):
    pass


@router.post("/launch")
def launch(
    req: LaunchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Launch AI pipeline for selected leads."""
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
    ).all()

    if not leads:
        raise HTTPException(status_code=404, detail="No leads found")

    result = launch_pipeline(
        db=db,
        leads=leads,
        advisor=current_user,
        lead_type=req.lead_type,
        tone=req.tone,
        ai_direction=req.ai_direction,
        channel=req.channel,
        auto_respond=req.auto_respond,
        actor=current_user.id,
    )
    _ws = _org_id(db, current_user)
    log_action(db, _ws, current_user.id,
               action="pipeline.launched", target_type="batch",
               target_id=_ws)
    return result


def _is_elevated(user: User, db: Session = None) -> bool:
    """True for a manager IN THE WORKSPACE BEING WORKED IN (is_manager_here).

    This read `users.role`, so an org_admin of A who is only an advisor of B
    saw - and could approve / dismiss - every advisor's pipeline in B, and B's
    real admin (an advisor at home) saw only their own. Without a workspace
    header this is `users.role` exactly as before.
    """
    return lead_scope.is_manager_here(user, db)


def _org_id(db: Session, user: User):
    """The ACTIVE workspace org (X-Workspace-Id backed by a membership, else
    the home column) - the same workspace `_is_elevated` is evaluated in and
    that authorized_lead_query already scopes the lead names to."""
    return lead_scope.active_workspace_org_id(user, db)


@router.get("/stats")
def pipeline_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Pipeline engagement stats for THIS workspace.

    Advisors see only their own; admins see org-wide; the NEUTRAL owner — one
    standing outside every customer — sees all orgs. An owner inside a
    customer sees that customer, because entering a workspace is the act that
    narrows the question. This used to test `role == "god_admin"` alone, which
    put every organization's pipeline on a client's own Reports page under a
    banner reading "God View — All Organizations". See
    lead_scope.god_sees_all_orgs.
    """
    advisor_id = None if _is_elevated(current_user, db) else current_user.id
    is_god = lead_scope.god_sees_all_orgs(current_user)

    if is_god:
        # Aggregate across all orgs
        org_ids = _get_org_ids(db, current_user)
        combined = {
            "total_in_pipeline": 0, "by_stage": {}, "flagged_count": 0, "flagged": [],
            "total_messages_sent": 0, "total_replies_received": 0, "total_booked": 0,
            "ai_auto_sent": 0, "ai_flagged": 0, "is_god_view": True,
        }
        for oid in org_ids:
            s = get_pipeline_stats(db, oid)
            combined["total_in_pipeline"] += s["total_in_pipeline"]
            combined["flagged_count"] += s["flagged_count"]
            combined["flagged"].extend(s.get("flagged", []))
            combined["total_messages_sent"] += s["total_messages_sent"]
            combined["total_replies_received"] += s["total_replies_received"]
            combined["total_booked"] += s["total_booked"]
            combined["ai_auto_sent"] += s["ai_auto_sent"]
            combined["ai_flagged"] += s["ai_flagged"]
            for stage, cnt in s["by_stage"].items():
                combined["by_stage"][stage] = combined["by_stage"].get(stage, 0) + cnt
        combined["flagged"] = combined["flagged"][:10]  # cap at 10
        return combined

    # THE ACTIVE WORKSPACE, NOT THE HOME COLUMN. `users.organization_id` is
    # where a person lives, which is not where they are standing: a member of
    # two customers reading it gets the first one's pipeline inside the second.
    return get_pipeline_stats(
        db,
        lead_scope.active_workspace_org_id(current_user, db)
        or current_user.organization_id,
        advisor_id=advisor_id)


@router.get("/forecast")
def forecast(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """Get AI forecast and alerts for overview dashboard."""
    advisor_id = None if _is_elevated(current_user, db) else current_user.id
    # Use active_workspace_org_id so executive observers get the observed org,
    # not current_user.organization_id which is None for brand executives.
    org_id = lead_scope.active_workspace_org_id(current_user, db)
    return get_ai_forecast(db, org_id, advisor_id=advisor_id)


@router.get("/flagged")
def get_flagged(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get conversations flagged for human review. Advisors see only their own."""
    q = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == _org_id(db, current_user),
        PipelineConversation.flagged == True,
        PipelineConversation.reviewed_at == None,
    )
    if not _is_elevated(current_user, db):
        q = q.filter(PipelineConversation.advisor_id == current_user.id)
    flagged = q.order_by(PipelineConversation.flagged_at.desc()).limit(200).all()

    leads = _authorized_leads_by_id(db, current_user, flagged)
    result = []
    for p in flagged:
        lead = leads.get(p.lead_id)
        result.append({
            "pipeline_id": p.id,
            "lead_id": p.lead_id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
            "lead_phone": lead.phone if lead else None,
            "lead_tier": lead.tier if lead else None,
            "flag_reason": p.flag_reason,
            "flagged_reply": p.flagged_reply_body,
            "suggested_response": p.flagged_suggested_response,
            "flagged_at": p.flagged_at,
            "stage": p.stage,
            "tone": p.tone,
            "lead_type": p.lead_type,
            "messages_sent": p.messages_sent,
            "replies_received": p.replies_received,
        })
    return result


@router.post("/approve/{pipeline_id}")
def approve_flagged(
    pipeline_id: str,
    req: ApproveRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Approve and optionally send the suggested response for a flagged conversation."""
    # OWN CONVERSATION ONLY. /pipeline/flagged and /pipeline/conversations both
    # scope the READ to the advisor; these two WRITES did not, so an advisor
    # could clear a colleague's review flag - marking handled a conversation
    # nobody had handled, on a queue that exists specifically for human review.
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    # Owner-only for an advisor IN THIS WORKSPACE - own_records_only's rule,
    # judged on the workspace role (it read users.role, so a home org_admin
    # who is an advisor here passed).
    if lead_scope.effective_role(current_user, db) in lead_scope.OWNER_SCOPED_ROLES:
        pipeline = pipeline.filter(PipelineConversation.advisor_id == current_user.id)
    pipeline = pipeline.first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False

    if req.send:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == pipeline.lead_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        try:
            from app.services.sms_service import send_sms
            # A person reviewed this reply and pressed Send: a one-to-one
            # MANUAL send, stamped as such (app/services/send_source.py).
            # send_sms still runs every one of its own gates - DNC, STOP,
            # consent, quiet hours, suppression, capacity - unchanged.
            from app.services import send_source as _send_source
            send_sms(db=db, lead=lead, advisor=current_user,
                     template=req.message, include_booking_link=False,
                     send_source=_send_source.MANUAL,
                     sent_by_user_id=current_user.id)
            pipeline.messages_sent = (pipeline.messages_sent or 0) + 1
            pipeline.stage = "ai_responding"
            pipeline.last_outbound_at = datetime.utcnow()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("pipeline approve send failed: %s", e)
            raise HTTPException(status_code=500, detail="Failed to send message. Please try again.")

    db.commit()
    log_action(db, pipeline.organization_id, current_user.id,
               action="pipeline.approved", target_type="pipeline", target_id=pipeline_id)
    return {"approved": True, "sent": req.send}


@router.post("/dismiss/{pipeline_id}")
def dismiss_flagged(
    pipeline_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Dismiss a flagged conversation without sending — advisor will handle manually."""
    # Own conversation only - same reason as approve above.
    pipeline = db.query(PipelineConversation).filter(
        PipelineConversation.id == pipeline_id,
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    # Owner-only for an advisor IN THIS WORKSPACE - own_records_only's rule,
    # judged on the workspace role (it read users.role, so a home org_admin
    # who is an advisor here passed).
    if lead_scope.effective_role(current_user, db) in lead_scope.OWNER_SCOPED_ROLES:
        pipeline = pipeline.filter(PipelineConversation.advisor_id == current_user.id)
    pipeline = pipeline.first()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline.reviewed_at = datetime.utcnow()
    pipeline.flagged = False
    db.commit()
    return {"dismissed": True}


def _authorized_leads_by_id(db: Session, user: User, pipelines) -> dict:
    """One scoped query for every lead on the page instead of one per row.
    Leads outside the caller's scope are simply absent (rendered "Unknown",
    exactly as the per-row lookup did)."""
    ids = {p.lead_id for p in pipelines if p.lead_id}
    if not ids:
        return {}
    return {l.id: l for l in authorized_lead_query(db, user).filter(Lead.id.in_(ids)).all()}


@router.get("/conversations")
def get_conversations(
    stage: Optional[str] = None,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    paged: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Get pipeline conversations. Advisors see only their own; admins see org-wide.

    Backward compatible: without `paged` this returns the same bare list of the
    200 most recently updated rows. `paged=true` returns
    {items, total, limit, offset} for server-side paging.
    """
    query = db.query(PipelineConversation).filter(
        PipelineConversation.organization_id == _org_id(db, current_user),
    )
    if not _is_elevated(current_user, db):
        query = query.filter(PipelineConversation.advisor_id == current_user.id)
    if stage:
        query = query.filter(PipelineConversation.stage == stage)

    total = query.count() if paged else None
    pipelines = (query.order_by(PipelineConversation.updated_at.desc(), PipelineConversation.id)
                 .offset(offset).limit(limit).all())

    leads = _authorized_leads_by_id(db, current_user, pipelines)
    result = []
    for p in pipelines:
        lead = leads.get(p.lead_id)
        result.append({
            "pipeline_id": p.id,
            "lead_id": p.lead_id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() if lead else "Unknown",
            "lead_phone": lead.phone if lead else None,
            "lead_tier": lead.tier if lead else None,
            "stage": p.stage,
            "flagged": p.flagged,
            "tone": p.tone,
            "lead_type": p.lead_type,
            "channel": p.channel,
            "messages_sent": p.messages_sent,
            "replies_received": p.replies_received,
            "ai_responses_sent": p.ai_responses_sent,
            "ai_responses_flagged": p.ai_responses_flagged,
            "last_outbound_at": p.last_outbound_at,
            "last_inbound_at": p.last_inbound_at,
            "booked_at": p.booked_at,
            "confirmed_at": p.confirmed_at,
            "created_at": p.created_at,
        })
    if paged:
        return {"items": result, "total": total, "limit": limit, "offset": offset}
    return result



# ═════════════════════════════════════════════════════════════════════════════
# SALES PIPELINE COMMAND CENTER (WS4)
#
# GET /pipeline/summary       KPIs, stage cards, funnel, channel mix, activity
# GET /pipeline/appointments  booked / confirmed appointments for the workspace
#
# THE RULES THESE TWO FOLLOW, because the screen they feed used to show a
# "Projected bookings" figure no model produced:
#   * Scope is the ACTING workspace only (lead_scope.active_workspace_org_id);
#     an advisor sees their own conversations, a manager the workspace's. The
#     neutral platform owner, standing in no customer, gets 409, not zeros.
#   * Internal test records never count (app/services/test_records.py).
#   * A figure the data model cannot state is null - "Not yet available" on
#     screen - never an estimate. `definitions` says what each one counts.
#   * A trend is only reported when the SAME definition can be evaluated over
#     the previous period of equal length. Snapshot counts (a conversation's
#     current stage) have no history table behind them, so they carry none.
# ═════════════════════════════════════════════════════════════════════════════

from datetime import timedelta as _td
from fastapi import Query as _Query

# Stages the platform actually writes to pipeline_conversations.stage, in the
# order a conversation moves through them (pipeline_service,
# ai_conversation_service). `confirmed`, `kept` and `sale` are declared on the
# model but NOTHING writes them - they are reported as untracked below rather
# than as a confident zero.
_STAGE_ORDER = [
    ("outreach_sent", "Outreach Sent"),
    ("replied", "Replied"),
    ("ai_responding", "AI Responding"),
    ("flagged", "Needs Human"),
    ("booking_sent", "Booking Sent"),
    ("booked", "Booked"),
    ("completed", "Sequence Complete"),
    ("stopped", "Stopped"),
    ("dnc", "DNC"),
]
_UNTRACKED_STAGES = [("confirmed", "Confirmed"), ("kept", "Appointment Kept"),
                     ("sale", "Sale / Closed")]
_TERMINAL_STAGES = {"stopped", "dnc", "completed", "sale"}

_ACTIVITY_ACTIONS = {
    "pipeline.launched": "Pipeline launched",
    "pipeline.approved": "Flagged reply approved",
    "lead.stage_moved": "Lead moved to a new stage",
    "lead.marked_lost": "Lead marked lost",
    "lead.reopened": "Lost lead reopened",
    "rate_request.enrolled": "Customer enrolled",
    "lead.create_manual": "Lead added",
    "lead.reassign": "Leads reassigned",
    "rate_request.created": "Rate request created",
    "rate_request.status_changed": "Rate request status changed",
    "rate_request.assigned": "Rate request assigned",
    "rate_request.updated": "Rate request details updated",
}


def _summary_org(db: Session, user: User):
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db)
    org = (db.query(Organization).filter(Organization.id == org_id).first()
           if org_id and not is_platform_pseudo_org(org_id) else None)
    if org is None:
        raise HTTPException(status_code=409, detail=(
            "No customer organization is selected. Select a workspace first."))
    return org


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def _trend_count(cur, prev):
    """Percent change, only when the previous period had something to compare."""
    if cur is None or prev is None or prev == 0:
        return None
    return round(100.0 * (cur - prev) / prev, 1)


def _trend_points(cur, prev):
    if cur is None or prev is None:
        return None
    return round(cur - prev, 1)


def _age_days(ts, now):
    if not ts:
        return None
    return max(0.0, (now - ts).total_seconds() / 86400.0)


@router.get("/summary")
def pipeline_summary(
    days: int = _Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """The Sales Pipeline command center, for the acting workspace only."""
    from app.models.models import BookingLink, Reply, AuditLogEntry
    from app.services import industry_templates

    org = _summary_org(db, current_user)
    org_id = str(org.id)
    is_manager = _is_elevated(current_user, db)
    now = datetime.utcnow()
    start = now - _td(days=days)
    prev_start = start - _td(days=days)

    # EVERYTHING BELOW IS AGGREGATED IN SQL. No conversation row is loaded:
    # stage cards are GROUP BY stage/channel, the period comparisons are
    # conditional sums over created_at >= prev_start.
    from sqlalchemy import case as _case, and_ as _and

    def _scoped(*cols_):
        q_ = (db.query(*cols_)
              .join(Lead, Lead.id == PipelineConversation.lead_id)
              .filter(PipelineConversation.organization_id == org_id,
                      Lead.organization_id == org_id,
                      Lead.is_test.isnot(True)))
        if not is_manager:
            q_ = q_.filter(PipelineConversation.advisor_id == current_user.id)
        return q_

    stage_col = func.coalesce(PipelineConversation.stage, "outreach_sent")
    ts_col = func.coalesce(PipelineConversation.updated_at, PipelineConversation.created_at)
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        epoch = func.strftime("%s", ts_col) * 1.0
    else:
        epoch = func.extract("epoch", ts_col)

    # ── snapshot: where every conversation is right now ─────────────────────
    stage_counts, stage_channels, stage_age = {}, {}, {}
    for st, ch, n in (_scoped(stage_col, PipelineConversation.channel,
                              func.count(PipelineConversation.id))
                      .group_by(stage_col, PipelineConversation.channel).all()):
        n = int(n or 0)
        stage_counts[st] = stage_counts.get(st, 0) + n
        chk = ch or "unknown"
        stage_channels.setdefault(st, {})
        stage_channels[st][chk] = stage_channels[st].get(chk, 0) + n
    now_epoch = (now - datetime(1970, 1, 1)).total_seconds()
    for st, avg_e, min_ts in (_scoped(stage_col, func.avg(epoch), func.min(ts_col))
                              .group_by(stage_col).all()):
        avg_age = (max(0.0, (now_epoch - float(avg_e)) / 86400.0)
                   if avg_e is not None else None)
        if isinstance(min_ts, str):  # sqlite may hand back text for an expression
            try:
                min_ts = datetime.fromisoformat(min_ts)
            except ValueError:
                min_ts = None
        stage_age[st] = (avg_age, _age_days(min_ts, now) if min_ts else None)

    def _stage_card(key, label):
        avg_age, oldest = stage_age.get(key, (None, None))
        return {
            "key": key, "label": label, "count": stage_counts.get(key, 0), "tracked": True,
            "avg_age_days": round(avg_age, 1) if avg_age is not None else None,
            "oldest_age_days": round(oldest, 1) if oldest is not None else None,
            "by_channel": stage_channels.get(key, {}),
            # No stage-history table exists, so "entered this stage in the
            # period" cannot be counted and no trend is claimed.
            "trend_pct": None,
        }

    stages = [_stage_card(k, l) for k, l in _STAGE_ORDER]
    known = {k for k, _ in _STAGE_ORDER}
    for extra in sorted(set(stage_counts) - known - {k for k, _ in _UNTRACKED_STAGES}):
        stages.append(_stage_card(extra, extra.replace("_", " ").title()))
    for k, l in _UNTRACKED_STAGES:
        if stage_counts.get(k):
            stages.append(_stage_card(k, l))
        else:
            stages.append({"key": k, "label": l, "count": None, "tracked": False,
                           "avg_age_days": None, "oldest_age_days": None,
                           "by_channel": {}, "trend_pct": None,
                           "note": "Not recorded by the pipeline yet"})

    total_conversations = sum(stage_counts.values())
    active_count = sum(n for st, n in stage_counts.items() if st not in _TERMINAL_STAGES)
    needs_human = int(_scoped(func.count(PipelineConversation.id))
                      .filter(PipelineConversation.flagged.is_(True),
                              PipelineConversation.reviewed_at.is_(None)).scalar() or 0)
    awaiting_booking = stage_counts.get("booking_sent", 0)
    channels = {}
    for st, chs in stage_channels.items():
        if st in _TERMINAL_STAGES:
            continue
        for ch, n in chs.items():
            channels[ch] = channels.get(ch, 0) + n

    # ── cohorts: conversations started in this period vs the one before ────
    bl_f = [Lead.organization_id == org_id, Lead.is_test.isnot(True)]
    if not is_manager:
        bl_f.append(Lead.assigned_to_id == current_user.id)
    confirmed_lead_ids = (db.query(BookingLink.lead_id)
                          .filter((BookingLink.status == "confirmed")
                                  | BookingLink.confirmed_at.isnot(None)))
    sent_c = func.coalesce(PipelineConversation.messages_sent, 0) > 0
    replied_c = func.coalesce(PipelineConversation.replies_received, 0) > 0
    booked_c = (PipelineConversation.booked_at.isnot(None)
                | (PipelineConversation.stage == "booked"))
    link_c = (PipelineConversation.booking_link_sent_at.isnot(None) | booked_c
              | (PipelineConversation.stage == "booking_sent"))

    def _n(cond):
        return func.sum(_case((cond, 1), else_=0))

    def _cohort(lo, hi):
        r = (_scoped(func.count(PipelineConversation.id), _n(sent_c),
                     _n(_and(sent_c, replied_c)), _n(replied_c), _n(link_c), _n(booked_c),
                     _n(PipelineConversation.lead_id.in_(confirmed_lead_ids)))
             .filter(PipelineConversation.created_at.isnot(None),
                     PipelineConversation.created_at >= lo,
                     PipelineConversation.created_at < hi).one())
        keys = ("started", "sent", "sent_replied", "replied", "link", "booked", "confirmed")
        return {k: int(v or 0) for k, v in zip(keys, r)}

    cur = _cohort(start, now + _td(seconds=1))
    prev = _cohort(prev_start, start)
    reply_cur, reply_prev = _pct(cur["sent_replied"], cur["sent"]), _pct(prev["sent_replied"], prev["sent"])
    conv_cur, conv_prev = _pct(cur["booked"], cur["started"]), _pct(prev["booked"], prev["started"])

    # ── appointments: the booking-link records, not a pipeline stage ───────
    def _confirmed_between(lo, hi):
        return int(db.query(func.count(BookingLink.id))
                   .join(Lead, BookingLink.lead_id == Lead.id)
                   .filter(*bl_f, BookingLink.confirmed_at.isnot(None),
                           BookingLink.confirmed_at >= lo, BookingLink.confirmed_at < hi)
                   .scalar() or 0)

    confirmed_cur = _confirmed_between(start, now + _td(seconds=1))
    confirmed_prev = _confirmed_between(prev_start, start)
    upcoming = int(db.query(func.count(BookingLink.id))
                   .join(Lead, BookingLink.lead_id == Lead.id)
                   .filter(*bl_f, BookingLink.status.in_(["booked", "confirmed"]),
                           BookingLink.booked_time.isnot(None),
                           BookingLink.booked_time >= now)
                   .scalar() or 0)

    # ── funnel: how far the period's conversations actually got ────────────
    funnel_steps = [
        ("started", "Conversations Started", cur["started"]),
        ("outreach_sent", "Outreach Sent", cur["sent"]),
        ("replied", "Replied", cur["replied"]),
        ("booking_sent", "Booking Link Sent", cur["link"]),
        ("booked", "Booked", cur["booked"]),
        ("confirmed", "Confirmed", cur["confirmed"]),
        ("kept", "Appointment Kept", None),
    ]
    base = funnel_steps[0][2]
    funnel = [{"key": k, "label": l, "count": n,
               "pct_of_started": _pct(n, base) if n is not None else None}
              for k, l, n in funnel_steps]

    # ── recent activity: real rows only ────────────────────────────────────
    activity = []
    reply_q = (db.query(Reply.id, Reply.lead_id, Reply.body, Reply.received_at,
                        Reply.source, Lead.first_name, Lead.last_name)
               .join(Lead, Reply.lead_id == Lead.id)
               .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True),
                       Reply.received_at.isnot(None)))
    if not is_manager:
        reply_q = reply_q.filter(Lead.assigned_to_id == current_user.id)
    for rid, lid, body, at, src, fn, ln in (
            reply_q.order_by(Reply.received_at.desc()).limit(10).all()):
        activity.append({
            "kind": "reply", "at": at.isoformat() + "Z" if at else None,
            "title": "Lead replied", "lead_id": lid,
            "lead_name": f"{fn or ''} {ln or ''}".strip() or None,
            "detail": (body or "")[:140], "channel": src,
        })
    audit_q = db.query(AuditLogEntry).filter(
        AuditLogEntry.organization_id == org_id,
        AuditLogEntry.action.in_(list(_ACTIVITY_ACTIONS)))
    if not is_manager:
        audit_q = audit_q.filter(AuditLogEntry.actor_user_id == current_user.id)
    audits = audit_q.order_by(AuditLogEntry.created_at.desc()).limit(10).all()
    lead_ids = [a.target_id for a in audits if a.target_type == "lead" and a.target_id]
    names = {}
    if lead_ids:
        try:
            names = {l.id: f"{l.first_name or ''} {l.last_name or ''}".strip()
                     for l in authorized_lead_query(db, current_user)
                     .filter(Lead.id.in_(lead_ids)).all()}
        except HTTPException:
            names = {}  # a read-only observer role: events without names
    for a in audits:
        in_scope_lead = a.target_type == "lead" and a.target_id in names
        activity.append({
            "kind": "audit", "action": a.action,
            "at": a.created_at.isoformat() + "Z" if a.created_at else None,
            "title": _ACTIVITY_ACTIONS.get(a.action, a.action),
            "lead_id": a.target_id if in_scope_lead else None,
            "lead_name": names.get(a.target_id) if in_scope_lead else None,
            "detail": None, "channel": None,
        })
    activity.sort(key=lambda e: e["at"] or "", reverse=True)

    return {
        "organization_id": org_id,
        "scope": "workspace" if is_manager else "own_conversations",
        "period_days": days,
        "kpis": {
            "active_conversations": {"value": active_count, "trend_pct": None},
            "reply_rate": {"value": reply_cur, "previous": reply_prev,
                           "trend_points": _trend_points(reply_cur, reply_prev)},
            "awaiting_booking": {"value": awaiting_booking, "trend_pct": None},
            "needs_human": {"value": needs_human, "trend_pct": None},
            "confirmed_appointments": {"value": confirmed_cur, "previous": confirmed_prev,
                                       "trend_pct": _trend_count(confirmed_cur, confirmed_prev)},
            "upcoming_appointments": {"value": upcoming, "trend_pct": None},
            "conversion_rate": {"value": conv_cur, "previous": conv_prev,
                                "trend_points": _trend_points(conv_cur, conv_prev)},
            "conversations_started": {"value": cur["started"], "previous": prev["started"],
                                      "trend_pct": _trend_count(cur["started"], prev["started"])},
            # No forecasting model exists; a projection would be invented.
            "projected_bookings": {"value": None, "trend_pct": None},
        },
        "total_conversations": total_conversations,
        "stages": stages,
        "channels_active": channels,
        "funnel": funnel,
        "recent_activity": activity[:12],
        "lead_types": industry_templates.pipeline_lead_types(getattr(org, "industry", None)),
        "definitions": {
            "active_conversations": "pipeline conversations not stopped, DNC, completed or sold",
            "reply_rate": ("of conversations started in the period with at least one "
                           "message sent, the share with at least one reply"),
            "awaiting_booking": "conversations currently at stage booking_sent",
            "needs_human": "conversations flagged for review and not yet reviewed",
            "confirmed_appointments": "booking links confirmed during the period",
            "upcoming_appointments": "booked or confirmed, appointment time in the future",
            "conversion_rate": ("of conversations started in the period, the share that "
                                "reached booked"),
            "projected_bookings": "not available - no forecasting model",
            "trend": "same definition over the previous period of equal length",
            "stage_counts": "current stage of every conversation (snapshot, no history)",
            "kept": "not recorded - no field is written when an appointment is kept",
        },
        "generated_at": now.isoformat() + "Z",
    }


APPOINTMENTS_CAP = 300  # per side: upcoming, and past


@router.get("/appointments")
def pipeline_appointments(
    days: int = _Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    """Booked / confirmed appointments (booking links) for the acting workspace:
    everything upcoming plus the last `days` days. Advisors see their own leads'."""
    from app.models.models import BookingLink

    org = _summary_org(db, current_user)
    org_id = str(org.id)
    now = datetime.utcnow()
    f = [Lead.organization_id == org_id, Lead.is_test.isnot(True),
         BookingLink.status.in_(["booked", "confirmed", "cancelled"]),
         BookingLink.booked_time.isnot(None),
         BookingLink.booked_time >= now - _td(days=days)]
    if not _is_elevated(current_user, db):
        f.append(Lead.assigned_to_id == current_user.id)
    # Upcoming and past are capped SEPARATELY. One ascending query capped at
    # 300 filled up with the oldest past appointments first, so in a busy
    # workspace the upcoming ones - the ones that matter - were the ones cut.
    base = (db.query(BookingLink, Lead.first_name, Lead.last_name, Lead.phone)
            .join(Lead, BookingLink.lead_id == Lead.id).filter(*f))
    upcoming_q = base.filter(BookingLink.booked_time >= now)
    past_q = base.filter(BookingLink.booked_time < now)
    up_rows = upcoming_q.order_by(BookingLink.booked_time.asc()).limit(APPOINTMENTS_CAP).all()
    past_rows = past_q.order_by(BookingLink.booked_time.desc()).limit(APPOINTMENTS_CAP).all()
    rows = list(reversed(past_rows)) + up_rows
    totals = {"upcoming": upcoming_q.count(), "past": past_q.count()}
    advisor_ids = {b.user_id for b, *_ in rows if b.user_id}
    advisors = ({u.id: u.full_name for u in db.query(User).filter(User.id.in_(advisor_ids)).all()}
                if advisor_ids else {})
    return {
        "items": [{
            "id": b.id, "lead_id": b.lead_id,
            "lead_name": f"{fn or ''} {ln or ''}".strip() or None,
            "lead_phone": ph, "status": b.status,
            "booked_time": b.booked_time.isoformat() + "Z" if b.booked_time else None,
            "confirmed_at": b.confirmed_at.isoformat() + "Z" if b.confirmed_at else None,
            "appointment_type": b.appt_label, "advisor_name": advisors.get(b.user_id),
            "upcoming": bool(b.booked_time and b.booked_time >= now),
        } for b, fn, ln, ph in rows],
        "period_days": days,
        "totals": totals,
        "truncated": totals["upcoming"] > len(up_rows) or totals["past"] > len(past_rows),
    }


# ════════════════════════════════════════════════════════════════════════════
# SALES BOARD — the lead book by configurable stage (2026-10-01)
# ════════════════════════════════════════════════════════════════════════════
#
# GET  /pipeline/board                     columns = THIS org's lead tiers
#                                          (industry_templates.org_lead_tiers);
#                                          cards = leads in the caller's scope
# POST /pipeline/board/{lead_id}/stage     move to another configured stage
# POST /pipeline/board/{lead_id}/lost      mark lost; a reason is required
# POST /pipeline/board/{lead_id}/reopen    clear the loss
#
# Every card field is read from a record, never estimated:
#   owner          users.full_name of leads.assigned_to_id
#   age_in_stage   leads.stage_entered_at (stamped on every ORM tier change by
#                  app/services/pipeline_stage.py). NULL for leads that have
#                  not changed stage since that shipped: the card then says
#                  "since <created_at>" with age_basis="created".
#   next_task      earliest open lead_tasks row for the lead (due first)
#   appointment    the next booked/confirmed booking_links row with a time
#                  (else the most recent past one, marked past=True)
#   rate_request   link to the Rate Requests drawer when the lead's tier is a
#                  rate-request status (rate_requests_router.STATUSES)
#   customer       relationship_type == "customer"; enrolled_at or
#                  "date not recorded"
#   loss           pipeline_lost_at / pipeline_lost_reason
#   activity       the lead record (/leads/{id}, Full History tab) plus the
#                  latest real timestamp the lead carries
# Scope: lead_scope.authorized_lead_query - acting workspace only, an advisor
# only their own book. Another tenant's lead id is a 404.

from fastapi import Request as _Request
from sqlalchemy import or_
from pydantic import Field as _Field
from app.deps import require_not_observation as _require_not_observation
from app.services import pipeline_stage as _pipeline_stage  # noqa: F401  (installs the stage clock)

BOARD_CARD_LIMIT = 50
LOSS_REASONS = [
    ("price", "Price / rate not competitive"),
    ("competitor", "Chose another provider"),
    ("no_response", "Stopped responding"),
    ("not_eligible", "Not a fit / not eligible"),
    ("timing", "Bad timing / not now"),
    ("stayed_current", "Stayed with current provider"),
    ("other", "Other"),
]
_LOSS_KEYS = {k for k, _ in LOSS_REASONS}
_LOSS_LABEL = dict(LOSS_REASONS)
_CLOSED_STATUS = "dead"   # the platform's existing "closed / lost" lead status


def _bnow():
    return datetime.utcnow()


def _biso(ts):
    return ts.isoformat() + "Z" if ts else None


def _board_tiers(org):
    from app.services.industry_templates import org_lead_tiers
    return [t for t in org_lead_tiers(org) if t.get("value")]


def _board_card(l, names, tasks, appts, rr_tiers, now):
    created = l.created_at
    if l.stage_entered_at:
        age_basis, since = "stage_entered", l.stage_entered_at
    else:
        age_basis, since = "created", created
    age = None if since is None else max(0, (now - since).days)
    t = tasks.get(l.id)
    a = appts.get(l.id)
    last = max([x for x in (l.updated_at, l.last_messaged_at, l.last_contact_date) if x], default=None)
    is_customer = (l.relationship_type == "customer")
    return {
        "id": l.id,
        "name": " ".join(p for p in (l.first_name, l.last_name) if p) or "Unnamed",
        "link": f"/leads/{l.id}",
        "stage": l.tier,
        "status": l.status,
        "dnc": l.status == "dnc",
        "owner": names.get(l.assigned_to_id),
        "assigned_to_id": l.assigned_to_id,
        "age_in_stage_days": age,
        "age_basis": age_basis,
        "stage_entered_at": _biso(l.stage_entered_at),
        "created_at": _biso(created),
        "next_task": ({"id": t.id, "title": t.title, "due_at": _biso(t.due_at),
                       "overdue": bool(t.due_at and t.due_at < now),
                       "link": f"/leads/{l.id}"} if t else None),
        "appointment": ({"id": a.id, "at": _biso(a.booked_time), "status": a.status,
                         "label": getattr(a, "appt_label", None),
                         "past": bool(a.booked_time and a.booked_time < now)} if a else None),
        "rate_request": ({"status_tier": l.tier, "link": f"/rate-requests?open={l.id}"}
                         if l.tier in rr_tiers else None),
        "customer": ({"enrolled_at": _biso(l.enrolled_at),
                      "label": ("Customer · enrolled " + l.enrolled_at.strftime("%b %d, %Y"))
                               if l.enrolled_at else "Customer · enrollment date not recorded"}
                     if is_customer else None),
        "lost": ({"at": _biso(l.pipeline_lost_at), "reason": l.pipeline_lost_reason,
                  "reason_label": _LOSS_LABEL.get((l.pipeline_lost_reason or "").split(":", 1)[0],
                                                  l.pipeline_lost_reason)}
                 if l.pipeline_lost_at else None),
        "notes_preview": (l.notes or "").strip()[:160] or None,
        "activity": {"last_at": _biso(last), "link": f"/leads/{l.id}?tab=timeline"},
    }


def _board_enrich(db, leads, now):
    from app.models.models import BookingLink
    from app.models.work_models import LeadTask
    ids = [l.id for l in leads]
    tasks, appts = {}, {}
    if ids:
        for t in (db.query(LeadTask).filter(LeadTask.lead_id.in_(ids), LeadTask.status == "open")
                  .order_by(LeadTask.due_at.is_(None), LeadTask.due_at.asc(), LeadTask.created_at.asc()).all()):
            tasks.setdefault(t.lead_id, t)
        rows = (db.query(BookingLink).filter(BookingLink.lead_id.in_(ids),
                                             BookingLink.status.in_(["booked", "confirmed"]),
                                             BookingLink.booked_time.isnot(None))
                .order_by(BookingLink.booked_time.asc()).all())
        for b in rows:          # next upcoming wins
            if b.booked_time >= now:
                appts.setdefault(b.lead_id, b)
        for b in reversed(rows):  # else most recent past
            appts.setdefault(b.lead_id, b)
    owner_ids = list({l.assigned_to_id for l in leads if l.assigned_to_id})
    names = ({u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(owner_ids)).all()}
             if owner_ids else {})
    return names, tasks, appts


def _rr_tiers():
    from app.routers.rate_requests_router import STATUSES as _RR
    return {t for _, _, t in _RR}


@router.get("/board")
def pipeline_board(
    request: _Request,
    owner: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    org = _summary_org(db, current_user)
    now = _bnow()
    tiers = _board_tiers(org)
    values = [t["value"] for t in tiers]
    base = lead_scope.authorized_lead_query(db, current_user, request=request)
    if owner == "me":
        base = base.filter(Lead.assigned_to_id == current_user.id)
    elif owner == "unassigned":
        base = base.filter(Lead.assigned_to_id.is_(None))
    elif owner:
        base = base.filter(Lead.assigned_to_id == owner)
    open_q = base.filter(Lead.pipeline_lost_at.is_(None))
    counts = dict(open_q.filter(Lead.tier.in_(values)).with_entities(Lead.tier, func.count(Lead.id))
                  .group_by(Lead.tier).all()) if values else {}
    picked = {}
    for v in values:
        picked[v] = (open_q.filter(Lead.tier == v)
                     .order_by(Lead.stage_entered_at.is_(None), Lead.stage_entered_at.asc(),
                               Lead.created_at.asc(), Lead.id.asc())
                     .limit(BOARD_CARD_LIMIT).all())
    lost_q = base.filter(Lead.pipeline_lost_at.isnot(None))
    lost_total = lost_q.count()
    lost_rows = lost_q.order_by(Lead.pipeline_lost_at.desc(), Lead.id.asc()).limit(BOARD_CARD_LIMIT).all()
    everything = [l for rows in picked.values() for l in rows] + lost_rows
    names, tasks, appts = _board_enrich(db, everything, now)
    rr = _rr_tiers()
    columns = [{"key": t["value"], "label": t.get("label") or t["value"], "color": t.get("color"),
                "description": t.get("description"),
                "count": counts.get(t["value"], 0),
                "cards": [_board_card(l, names, tasks, appts, rr, now) for l in picked[t["value"]]]}
               for t in tiers]
    unstaged = (open_q.filter(or_(Lead.tier.is_(None), ~Lead.tier.in_(values))).count()
                if values else open_q.count())
    return {
        "organization_id": str(org.id),
        "columns": columns,
        "lost": {"count": lost_total,
                 "cards": [_board_card(l, names, tasks, appts, rr, now) for l in lost_rows]},
        "unstaged_count": unstaged,
        "card_limit": BOARD_CARD_LIMIT,
        "loss_reasons": [{"key": k, "label": v} for k, v in LOSS_REASONS],
        "is_manager": _is_elevated(current_user, db),
        "as_of": _biso(now),
        "rules": {
            "stages": "this organization's configured lead tiers (Settings), in order",
            "age_in_stage": "from leads.stage_entered_at, recorded on every stage change; "
                            "older leads show 'since created' until their next move",
            "lost": "marked by a person on this board with a reason; status becomes 'dead'",
        },
    }


def _board_lead(db, user, request, lead_id):
    lead = (lead_scope.authorized_lead_query(db, user, request=request)
            .filter(Lead.id == lead_id).first())
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


class BoardStageMove(BaseModel):
    stage: str = _Field(..., min_length=1, max_length=100)


class BoardLost(BaseModel):
    reason: str = _Field(..., min_length=1, max_length=40)
    detail: Optional[str] = _Field(None, max_length=500)


def _card_after(db, lead):
    now = _bnow()
    names, tasks, appts = _board_enrich(db, [lead], now)
    return _board_card(lead, names, tasks, appts, _rr_tiers(), now)


@router.post("/board/{lead_id}/stage", dependencies=[Depends(_require_not_observation)])
def board_move_stage(lead_id: str, payload: BoardStageMove, request: _Request,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(require_tenant_user)):
    lead = _board_lead(db, current_user, request, lead_id)
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    values = [t["value"] for t in _board_tiers(org)] if org else []
    if payload.stage not in values:
        raise HTTPException(status_code=400, detail={"message": "Not a stage this organization has configured.",
                                                     "valid_stages": values})
    if lead.pipeline_lost_at:
        raise HTTPException(status_code=409, detail="This lead is marked lost. Reopen it first.")
    prev = lead.tier
    if prev != payload.stage:
        lead.tier = payload.stage          # pipeline_stage listener stamps stage_entered_at
        lead.updated_at = datetime.utcnow()
        log_action(db, lead.organization_id, current_user.id, action="lead.stage_moved",
                   target_type="lead", target_id=lead.id,
                   details={"from": prev, "to": payload.stage, "via": "pipeline_board"}, commit=False)
        db.commit()
        db.refresh(lead)
    return _card_after(db, lead)


@router.post("/board/{lead_id}/lost", dependencies=[Depends(_require_not_observation)])
def board_mark_lost(lead_id: str, payload: BoardLost, request: _Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_tenant_user)):
    lead = _board_lead(db, current_user, request, lead_id)
    if payload.reason not in _LOSS_KEYS:
        raise HTTPException(status_code=400, detail={"message": "Unknown loss reason.",
                                                     "valid_reasons": sorted(_LOSS_KEYS)})
    detail = (payload.detail or "").strip()
    if payload.reason == "other" and not detail:
        raise HTTPException(status_code=400, detail="Describe the reason when choosing Other.")
    prev_status = lead.status
    lead.pipeline_lost_at = datetime.utcnow()
    lead.pipeline_lost_reason = payload.reason + (f": {detail}" if detail else "")
    if lead.status != "dnc":           # DNC is a compliance state; never overwrite it
        lead.status = _CLOSED_STATUS
    stamp = lead.pipeline_lost_at.strftime("%Y-%m-%d")
    lead.notes = ((lead.notes + "\n") if lead.notes else "") + \
        f"[Lost {stamp}] {_LOSS_LABEL[payload.reason]}" + (f" - {detail}" if detail else "")
    lead.updated_at = datetime.utcnow()
    log_action(db, lead.organization_id, current_user.id, action="lead.marked_lost",
               target_type="lead", target_id=lead.id,
               details={"reason": payload.reason, "detail": detail or None,
                        "stage": lead.tier, "prev_status": prev_status}, commit=False)
    db.commit()
    db.refresh(lead)
    return _card_after(db, lead)


@router.post("/board/{lead_id}/reopen", dependencies=[Depends(_require_not_observation)])
def board_reopen(lead_id: str, request: _Request, db: Session = Depends(get_db),
                 current_user: User = Depends(require_tenant_user)):
    lead = _board_lead(db, current_user, request, lead_id)
    if not lead.pipeline_lost_at:
        raise HTTPException(status_code=409, detail="This lead is not marked lost.")
    prev_reason = lead.pipeline_lost_reason
    lead.pipeline_lost_at = None
    lead.pipeline_lost_reason = None
    if lead.status == _CLOSED_STATUS:
        lead.status = "new"
    lead.updated_at = datetime.utcnow()
    log_action(db, lead.organization_id, current_user.id, action="lead.reopened",
               target_type="lead", target_id=lead.id,
               details={"previous_loss_reason": prev_reason}, commit=False)
    db.commit()
    db.refresh(lead)
    return _card_after(db, lead)
