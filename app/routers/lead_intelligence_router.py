"""LEAD INTELLIGENCE CONTROL CENTER API — god_admin only.

Every route is `Depends(require_god)`. Nothing here is a customer surface.

TRUTHFULNESS RULE: every number returned is a count over real rows. Where the
data model cannot answer a question, the field is null and a `notes` entry says
why — never an estimate, never a made-up trend.

WHAT IS COUNTED WHERE
  master_pool   the master lead database (via god_master_router.master_pool_summary) — every person every
                organization has ever held (cross-tenant, read-only here).
  prospects     app/models/lead_intel_models.py — businesses discovered by the
                scraper, qualified by app/services/qualification.py, waiting to
                be routed by a person.

Routing (POST /route) is the only write that reaches a tenant, and it writes a
STAGED Universal Intake batch in the destination organization — see
app/services/lead_routing.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.lead_intel_models import (LeadIntelProspect, LeadIntelRoutingRule,
                                          LeadIntelScrapeJob)
from app.models.models import Organization, User
from app.routers.god_master_router import master_pool_summary
from app.services import lead_routing as LR
from app.services import qualification as Q

router = APIRouter(prefix="/god/lead-intelligence", tags=["Lead Intelligence"])


def _org_names(db: Session, ids) -> Dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {o.id: o.name for o in db.query(Organization).filter(Organization.id.in_(ids)).all()}


def _since(days: Optional[int]) -> Optional[datetime]:
    return datetime.utcnow() - timedelta(days=days) if days else None


def _prospect_q(db: Session, since: Optional[datetime]):
    q = db.query(LeadIntelProspect)
    if since is not None:
        q = q.filter(LeadIntelProspect.created_at >= since)
    return q


def _iso(d):
    return d.isoformat() if d else None


# ── summary ─────────────────────────────────────────────────────────────────

@router.get("/summary")
def summary(days: Optional[int] = Query(None, ge=1, le=3650),
            god: User = Depends(require_god), db: Session = Depends(get_db)):
    since = _since(days)
    notes: List[str] = []

    # Master pool — all-time, production records only (synthetic hidden, as the
    # master browser does by default). Counted by the master router's own helper
    # so the master tables stay referenced by exactly one router.
    master = master_pool_summary(db)
    notes.append("Master pool totals are all-time and exclude QA/test records. Master "
                 "contacts are qualified inside each organization by its own engine "
                 "and rules, so no cross-tenant qualification distribution is reported "
                 "for them.")

    # Prospect pool
    pq = _prospect_q(db, since)
    total = pq.count()
    by_stage = {s: int(c) for s, c in pq.with_entities(LeadIntelProspect.stage,
                                                       func.count(LeadIntelProspect.id))
                .group_by(LeadIntelProspect.stage).all()}
    qualified_q = pq.filter(LeadIntelProspect.stage == "qualified")
    by_bucket = {b: 0 for b in Q.BUCKETS}
    for b, c in (pq.filter(LeadIntelProspect.stage.in_(("qualified", "suppressed")))
                 .with_entities(LeadIntelProspect.bucket, func.count(LeadIntelProspect.id))
                 .group_by(LeadIntelProspect.bucket).all()):
        if b in by_bucket:
            by_bucket[b] = int(c)
    by_priority = {p: 0 for p in Q.PRIORITIES}
    for p, c in (qualified_q.filter(LeadIntelProspect.priority.isnot(None))
                 .with_entities(LeadIntelProspect.priority, func.count(LeadIntelProspect.id))
                 .group_by(LeadIntelProspect.priority).all()):
        by_priority[p] = int(c)
    routed = pq.filter(LeadIntelProspect.routed_org_id.isnot(None)).count()
    routed_orgs = (pq.filter(LeadIntelProspect.routed_org_id.isnot(None))
                   .with_entities(func.count(func.distinct(LeadIntelProspect.routed_org_id)))
                   .scalar() or 0)
    in_review = (qualified_q.filter(LeadIntelProspect.bucket == Q.REVIEW,
                                    LeadIntelProspect.routed_org_id.is_(None)).count())
    awaiting_routing = (qualified_q.filter(LeadIntelProspect.bucket.in_((Q.READY, Q.REVIEW)),
                                           LeadIntelProspect.routed_org_id.is_(None)).count())
    prospect_sources = [{"source": s or "unknown", "count": int(c)} for s, c in
                        pq.with_entities(LeadIntelProspect.source, func.count(LeadIntelProspect.id))
                        .group_by(LeadIntelProspect.source)
                        .order_by(func.count(LeadIntelProspect.id).desc()).all()]
    industries = [{"industry": s or "unspecified", "count": int(c)} for s, c in
                  pq.with_entities(LeadIntelProspect.industry, func.count(LeadIntelProspect.id))
                  .group_by(LeadIntelProspect.industry)
                  .order_by(func.count(LeadIntelProspect.id).desc()).all()]

    # Recent activity: scrape jobs and routing events, both real rows.
    jq = db.query(LeadIntelScrapeJob)
    if since is not None:
        jq = jq.filter(LeadIntelScrapeJob.created_at >= since)
    jobs = jq.order_by(LeadIntelScrapeJob.created_at.desc()).limit(10).all()
    route_rows = (pq.filter(LeadIntelProspect.routed_batch_id.isnot(None))
                  .with_entities(LeadIntelProspect.routed_batch_id,
                                 LeadIntelProspect.routed_org_id,
                                 func.count(LeadIntelProspect.id),
                                 func.max(LeadIntelProspect.routed_at))
                  .group_by(LeadIntelProspect.routed_batch_id, LeadIntelProspect.routed_org_id)
                  .order_by(func.max(LeadIntelProspect.routed_at).desc()).limit(10).all())
    names = _org_names(db, [j.destination_org_id for j in jobs] + [r[1] for r in route_rows])
    activity = []
    for j in jobs:
        activity.append({"type": "scrape", "at": _iso(j.created_at),
                         "label": "Lead search: %s" % (j.effective_query or j.query or j.industry or "—"),
                         "detail": "%d found, %d staged%s" % (
                             j.result_count or 0, j.staged_count or 0,
                             (", %d imported" % j.imported_count) if j.imported_count else ""),
                         "organization_name": names.get(j.destination_org_id)})
    for batch_id, org_id, n, at in route_rows:
        activity.append({"type": "route", "at": _iso(at),
                         "label": "Routed %d prospect%s to %s" % (n, "" if n == 1 else "s",
                                                                  names.get(org_id) or "organization"),
                         "detail": "Staged for review in Universal Intake",
                         "organization_name": names.get(org_id), "batch_id": batch_id})
    activity.sort(key=lambda a: a["at"] or "", reverse=True)

    recent = (pq.order_by(LeadIntelProspect.created_at.desc()).limit(8).all())
    rnames = _org_names(db, [p.routed_org_id for p in recent] + [p.destination_org_id for p in recent])

    if total == 0:
        notes.append("No prospects have been staged from the Lead Scraper yet.")

    return {
        "window_days": days,
        "master_pool": {**master, "qualification": None},
        "prospects": {"total": total, "by_stage": by_stage, "by_bucket": by_bucket,
                      "by_priority": by_priority, "in_review": in_review,
                      "awaiting_routing": awaiting_routing, "routed": routed,
                      "routed_organizations": int(routed_orgs),
                      "suppressed": by_stage.get("suppressed", 0),
                      "duplicates": by_stage.get("duplicate", 0),
                      "sources": prospect_sources, "industries": industries},
        "thresholds": LR.thresholds(),
        "recent_activity": activity[:12],
        "recent_prospects": [LR.prospect_dict(p, rnames) for p in recent],
        # Trends need a previous-period comparison nobody has asked for yet;
        # reported as unavailable rather than invented.
        "trends": None,
        "notes": notes,
    }


# ── qualification config ────────────────────────────────────────────────────

_CONFIG_NOTE = ("Read-only. Thresholds are defined in app/services/qualification.py "
                "(HIGH_THRESHOLD, MEDIUM_THRESHOLD) and there is no settings store for "
                "them; changing them is a reviewed code change, so a band cannot be "
                "moved from a screen without its gates being re-run.")


@router.get("/qualification-config")
def get_qualification_config(god: User = Depends(require_god)):
    t = LR.thresholds()
    return {
        "editable": False,
        "note": _CONFIG_NOTE,
        "thresholds": t,
        "bands": [
            {"priority": Q.HIGH, "min": t["high_min"], "max": None,
             "label": "HIGH: %d+" % t["high_min"]},
            {"priority": Q.MEDIUM, "min": t["medium_min"], "max": t["medium_max"],
             "label": "MEDIUM: %d–%d" % (t["medium_min"], t["medium_max"])},
            {"priority": Q.LOW, "min": None, "max": t["low_max"],
             "label": "LOW: below %d" % t["medium_min"]},
        ],
        "buckets": [
            {"bucket": Q.READY, "label": "READY",
             "meaning": "The server can defend contacting this one on at least one channel."},
            {"bucket": Q.REVIEW, "label": "REVIEW",
             "meaning": "A person should look first (e.g. a role address, no name). Not a refusal."},
            {"bucket": Q.EXCLUDED, "label": "EXCLUDED",
             "meaning": "Must not be contacted: DNC, suppressed, invalid or missing channel, "
                        "no SMS consent. Excluded prospects cannot be routed."},
        ],
        "channels": list(LR.QUALIFY_CHANNELS),
        "channel_policy": ("Each prospect is qualified per channel by qualify_one; the headline "
                           "bucket is the best channel. SMS is always EXCLUDED for scraped "
                           "businesses because consent is never inferred from a phone number."),
        "diagnostic_path": "/god/diagnostics/qualification",
    }


@router.put("/qualification-config")
def put_qualification_config(payload: Dict[str, Any] = None,
                             god: User = Depends(require_god)):
    raise HTTPException(status_code=409, detail=_CONFIG_NOTE)


# ── pipeline ────────────────────────────────────────────────────────────────

@router.get("/pipeline")
def pipeline(days: Optional[int] = Query(None, ge=1, le=3650),
             god: User = Depends(require_god), db: Session = Depends(get_db)):
    since = _since(days)
    pq = _prospect_q(db, since)
    discovered = pq.count()
    invalid = pq.filter(LeadIntelProspect.stage == "invalid").count()
    duplicate = pq.filter(LeadIntelProspect.stage == "duplicate").count()
    suppressed = pq.filter(LeadIntelProspect.stage == "suppressed").count()
    qualified = pq.filter(LeadIntelProspect.stage == "qualified",
                          LeadIntelProspect.bucket.in_((Q.READY, Q.REVIEW))).count()
    excluded = pq.filter(LeadIntelProspect.stage == "qualified",
                         LeadIntelProspect.bucket == Q.EXCLUDED).count()
    routed = pq.filter(LeadIntelProspect.routed_org_id.isnot(None)).count()
    jq = db.query(func.coalesce(func.sum(LeadIntelScrapeJob.result_count), 0))
    if since is not None:
        jq = jq.filter(LeadIntelScrapeJob.created_at >= since)
    provider_results = int(jq.scalar() or 0)
    normalized = discovered - invalid
    deduped = normalized - duplicate
    stages = [
        {"key": "discovered", "label": "Discovered", "count": discovered},
        {"key": "normalized", "label": "Normalized", "count": normalized},
        {"key": "deduped", "label": "Deduplicated", "count": deduped},
        {"key": "suppressed", "label": "Suppressed", "count": suppressed},
        {"key": "qualified", "label": "Qualified", "count": qualified},
        {"key": "routed", "label": "Routed", "count": routed},
    ]
    return {"window_days": days, "stages": stages,
            "removed": {"invalid": invalid, "duplicate": duplicate,
                        "suppressed": suppressed, "excluded": excluded},
            "provider_results": provider_results,
            "note": ("Counts are prospects staged into the Lead Intelligence pool. "
                     "`provider_results` is what searches returned, including results "
                     "that were never staged. Routed prospects are staged in the "
                     "destination organization's Universal Intake for its review.")}


# ── prospects (the Lead Intelligence browser) ───────────────────────────────

@router.get("/prospects")
def list_prospects(
    search: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    industry: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    score_min: Optional[int] = Query(None),
    score_max: Optional[int] = Query(None),
    routed: Optional[bool] = Query(None),
    destination_org_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    god: User = Depends(require_god), db: Session = Depends(get_db),
):
    q = db.query(LeadIntelProspect)
    if search:
        # LIKE wildcards in the operator's text are literals, not patterns
        # (same escaping as leads_query_router).
        term = (search.strip().lower().replace("\\", "\\\\")
                .replace("%", "\\%").replace("_", "\\_"))
        like = "%" + term + "%"
        clauses = [func.lower(func.coalesce(col, "")).like(like, escape="\\")
                   for col in (LeadIntelProspect.name, LeadIntelProspect.email,
                               LeadIntelProspect.website, LeadIntelProspect.address)]
        digits = "".join(ch for ch in search if ch.isdigit())
        if len(digits) >= 4:
            clauses.append(LeadIntelProspect.phone_e164.like("%%%s%%" % digits))
        q = q.filter(or_(*clauses))
    if bucket:
        q = q.filter(LeadIntelProspect.bucket == bucket)
    if priority:
        q = q.filter(LeadIntelProspect.priority == priority)
    if stage:
        q = q.filter(LeadIntelProspect.stage == stage)
    if source:
        q = q.filter(LeadIntelProspect.source == source)
    if industry:
        q = q.filter(LeadIntelProspect.industry == industry)
    if state:
        q = q.filter(func.upper(LeadIntelProspect.state) == state.strip().upper())
    if city:
        q = q.filter(func.lower(LeadIntelProspect.city) == city.strip().lower())
    if score_min is not None:
        q = q.filter(LeadIntelProspect.score >= score_min)
    if score_max is not None:
        q = q.filter(LeadIntelProspect.score <= score_max)
    if routed is True:
        q = q.filter(LeadIntelProspect.routed_org_id.isnot(None))
    elif routed is False:
        q = q.filter(LeadIntelProspect.routed_org_id.is_(None))
    if destination_org_id:
        q = q.filter(or_(LeadIntelProspect.destination_org_id == destination_org_id,
                         LeadIntelProspect.routed_org_id == destination_org_id))
    if job_id:
        q = q.filter(LeadIntelProspect.job_id == job_id)
    total = q.count()
    rows = (q.order_by(LeadIntelProspect.created_at.desc(), LeadIntelProspect.id.desc())
            .offset(skip).limit(limit).all())
    names = _org_names(db, [p.destination_org_id for p in rows] + [p.routed_org_id for p in rows])
    return {"total": total, "skip": skip, "limit": limit,
            "rows": [LR.prospect_dict(p, names) for p in rows]}


@router.get("/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100),
              god: User = Depends(require_god), db: Session = Depends(get_db)):
    jobs = (db.query(LeadIntelScrapeJob).order_by(LeadIntelScrapeJob.created_at.desc())
            .limit(limit).all())
    names = _org_names(db, [j.destination_org_id for j in jobs])
    return {"jobs": [{
        "id": j.id, "provider": j.provider, "industry": j.industry, "query": j.query,
        "effective_query": j.effective_query, "location": j.location,
        "radius_meters": j.radius_meters, "max_results": j.max_results,
        "destination_org_id": j.destination_org_id,
        "destination_org_name": names.get(j.destination_org_id),
        "status": j.status, "result_count": j.result_count, "staged_count": j.staged_count,
        "imported_count": j.imported_count, "created_at": _iso(j.created_at),
    } for j in jobs]}


@router.get("/industries")
def industries(god: User = Depends(require_god)):
    return {"industries": [{"key": k, "label": LR.INDUSTRY_LABELS.get(k, k),
                            "terms": v} for k, v in LR.INDUSTRY_TERMS.items()]}


# ── routing rules ───────────────────────────────────────────────────────────

class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    destination_org_id: str
    is_active: bool = True
    sort_order: int = 100
    match_industry: Optional[str] = None
    match_state: Optional[str] = None
    match_city: Optional[str] = None
    min_score: Optional[int] = None
    allowed_buckets: List[str] = Field(default_factory=lambda: [Q.READY])


def _validate_rule(db: Session, body: RuleIn):
    if body.destination_org_id == LR.GOD_PLATFORM_ORG_ID or not db.query(Organization.id).filter(
            Organization.id == body.destination_org_id).first():
        raise HTTPException(status_code=404, detail="Destination organization not found.")
    buckets = [b for b in body.allowed_buckets if b]
    if not buckets or any(b not in (Q.READY, Q.REVIEW) for b in buckets):
        raise HTTPException(status_code=400,
                            detail="allowed_buckets may contain only READY_TO_SEND and "
                                   "REVIEW_REQUIRED. Excluded prospects are never routable.")
    return ",".join(dict.fromkeys(buckets))


def _rule_dict(r: LeadIntelRoutingRule, names=None) -> dict:
    names = names or {}
    return {"id": r.id, "name": r.name, "is_active": r.is_active, "sort_order": r.sort_order,
            "match_industry": r.match_industry, "match_state": r.match_state,
            "match_city": r.match_city, "min_score": r.min_score,
            "allowed_buckets": [b for b in (r.allowed_buckets or "").split(",") if b],
            "destination_org_id": r.destination_org_id,
            "destination_org_name": names.get(r.destination_org_id),
            "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)}


@router.get("/routing-rules")
def list_rules(god: User = Depends(require_god), db: Session = Depends(get_db)):
    rules = (db.query(LeadIntelRoutingRule)
             .order_by(LeadIntelRoutingRule.sort_order.asc(),
                       LeadIntelRoutingRule.created_at.asc()).all())
    names = _org_names(db, [r.destination_org_id for r in rules])
    return {"rules": [_rule_dict(r, names) for r in rules],
            "note": "Rules only SUGGEST a destination. Routing always requires an explicit action."}


@router.post("/routing-rules", status_code=201)
def create_rule(body: RuleIn, god: User = Depends(require_god), db: Session = Depends(get_db)):
    buckets = _validate_rule(db, body)
    r = LeadIntelRoutingRule(name=body.name.strip(), destination_org_id=body.destination_org_id,
                             is_active=body.is_active, sort_order=body.sort_order,
                             match_industry=body.match_industry or None,
                             match_state=(body.match_state or "").upper() or None,
                             match_city=body.match_city or None, min_score=body.min_score,
                             allowed_buckets=buckets, created_by_id=god.id)
    db.add(r)
    db.commit()
    return _rule_dict(r, _org_names(db, [r.destination_org_id]))


@router.put("/routing-rules/{rule_id}")
def update_rule(rule_id: str, body: RuleIn, god: User = Depends(require_god),
                db: Session = Depends(get_db)):
    r = db.query(LeadIntelRoutingRule).filter(LeadIntelRoutingRule.id == rule_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    r.allowed_buckets = _validate_rule(db, body)
    r.name = body.name.strip()
    r.destination_org_id = body.destination_org_id
    r.is_active = body.is_active
    r.sort_order = body.sort_order
    r.match_industry = body.match_industry or None
    r.match_state = (body.match_state or "").upper() or None
    r.match_city = body.match_city or None
    r.min_score = body.min_score
    db.commit()
    return _rule_dict(r, _org_names(db, [r.destination_org_id]))


@router.delete("/routing-rules/{rule_id}")
def delete_rule(rule_id: str, god: User = Depends(require_god), db: Session = Depends(get_db)):
    r = db.query(LeadIntelRoutingRule).filter(LeadIntelRoutingRule.id == rule_id).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(r)
    db.commit()
    return {"deleted": rule_id}


# ── routing ─────────────────────────────────────────────────────────────────

class RoutePreviewIn(BaseModel):
    prospect_ids: List[str] = Field(..., max_length=500)


class RouteIn(BaseModel):
    prospect_ids: List[str] = Field(..., min_length=1, max_length=500)
    destination_org_id: Optional[str] = None
    rule_id: Optional[str] = None


@router.post("/route/preview")
def route_preview(body: RoutePreviewIn, god: User = Depends(require_god),
                  db: Session = Depends(get_db)):
    rows = db.query(LeadIntelProspect).filter(LeadIntelProspect.id.in_(body.prospect_ids)).all()
    sug = LR.suggest(db, rows)
    names = _org_names(db, [s["destination_org_id"] for s in sug])
    for s in sug:
        s["destination_org_name"] = names.get(s["destination_org_id"])
    return {"suggestions": sug, "note": "Preview only — nothing has been routed."}


@router.post("/route")
def route(body: RouteIn, god: User = Depends(require_god), db: Session = Depends(get_db)):
    dest = body.destination_org_id
    if body.rule_id:
        rule = db.query(LeadIntelRoutingRule).filter(LeadIntelRoutingRule.id == body.rule_id).first()
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found.")
        dest = dest or rule.destination_org_id
    if not dest:
        raise HTTPException(status_code=400, detail="destination_org_id is required.")
    try:
        return LR.route(db, body.prospect_ids, dest, god, rule_id=body.rule_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except LR.RoutingError as e:
        raise HTTPException(status_code=400, detail=str(e))
