"""
RATE REQUESTS — the energy work queue (WS4).

A rate request is a LEAD in the acting workspace whose tier is one of the
organization's rate-request stages. There is no second table: the queue is the
lead book asked a narrower question, so a request and the lead it belongs to
can never disagree.

── STATUS MAPPING (presentation name -> the org tier it reads/writes) ─────────

    New            new_inquiry
    In Review      rate_review
    Options Sent   proposal_sent
    Completed      contract_signed
    Booked         DERIVED, not a tier: an OPEN request (New / In Review /
                   Options Sent) whose lead has a booking link that is booked
                   or confirmed with a time on it. It can be filtered on and
                   counted; it cannot be SET, because nothing stores it.

The tiers are validated against THIS organization's configured tiers
(industry_templates.org_lead_tiers). A status whose tier the organization does
not have is reported `available: false` with a null count - a workspace that
never configured a "Proposal Sent" stage has no "Options Sent" requests, and a
zero would claim it had looked.

"Open" is New + In Review + Options Sent, excluding DNC / dead leads and leads
flagged remove_all - the same population the configured view showed.

── RULES ────────────────────────────────────────────────────────────────────

  * Scope is lead_scope.authorized_lead_query: the acting workspace only, and
    an advisor only their own leads. Another tenant's id is a 404.
  * Creating a request is an explicit human action. It creates the Lead with
    tier new_inquiry and the energy custom fields. It records NO consent, sets
    no SMS permission, enrols nothing in a cadence and sends nothing.
  * Assignment is a manager capability, exactly as it is on the lead routes.
  * Every write is audited (audit_log_router.log_action).
  * `next_step` is a fixed rule per status (documented in _NEXT_STEP), not a
    prediction.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_user, require_tenant_or_observer, require_not_observation
from app.models.models import User, Lead, Organization, BookingLink, AuditLogEntry
from app.routers.audit_log_router import log_action
from app.services import lead_scope
from app.services.lead_scope import authorized_lead_query
from app.services.entitlements import require_feature
from app.services.platform_owner import require_tenant_context
from app.services import pipeline_stage  # noqa: F401  (stage clock listener)

router = APIRouter(prefix="/rate-requests", tags=["rate-requests"],
                   dependencies=[Depends(require_feature("leads"))])

# (status key, display label, tier value) — see the module docstring.
STATUSES = [
    ("new", "New", "new_inquiry"),
    ("in_review", "In Review", "rate_review"),
    ("options_sent", "Options Sent", "proposal_sent"),
    ("completed", "Completed", "contract_signed"),
]
OPEN_STATUS_KEYS = ("new", "in_review", "options_sent")
BOOKED_KEY = "booked"
_TIER_BY_STATUS = {k: t for k, _, t in STATUSES}
_STATUS_BY_TIER = {t: k for k, _, t in STATUSES}
_LABEL = {k: l for k, l, _ in STATUSES}
_LABEL[BOOKED_KEY] = "Booked"
_EXCLUDED_STATUSES = ("dnc", "dead")

_NEXT_STEP = {
    "new": "Review request",
    "in_review": "Prepare rate options",
    "options_sent": "Follow up on options",
    "completed": None,
}
_NEXT_STEP_BOOKED = "Hold consultation"

# The energy custom fields a request carries (industry_templates "energy").
CUSTOM_KEYS = ("service_address", "current_supplier", "annual_usage_kwh",
               "contract_end_date", "rate_type", "segment")

SOURCE_CHOICES = ("website", "phone", "email", "referral", "walk_in", "manual", "other")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


from app.services import booking_time as _bkt  # booked_time is local wall time

def _iso(ts):
    return ts.isoformat() + "Z" if ts else None


def _acting_org(db: Session, user: User) -> Organization:
    from app.services.platform_owner import is_platform_pseudo_org
    org_id = lead_scope.active_workspace_org_id(user, db)
    org = (db.query(Organization).filter(Organization.id == org_id).first()
           if org_id and not is_platform_pseudo_org(org_id) else None)
    if org is None:
        raise HTTPException(status_code=409, detail=(
            "No customer organization is selected. Select a workspace first."))
    return org


def _org_tier_values(org) -> List[str]:
    from app.services.industry_templates import org_lead_tiers
    return [t.get("value") for t in org_lead_tiers(org) if t.get("value")]


def _status_table(org) -> List[Dict[str, Any]]:
    tiers = set(_org_tier_values(org))
    return [{"key": k, "label": l, "tier": t, "available": t in tiers}
            for k, l, t in STATUSES]


def _available_tiers(org, keys) -> List[str]:
    tiers = set(_org_tier_values(org))
    return [_TIER_BY_STATUS[k] for k in keys if _TIER_BY_STATUS[k] in tiers]


def _booked_lead_ids_q(db: Session):
    return (db.query(BookingLink.lead_id)
            .filter(BookingLink.status.in_(["booked", "confirmed"]),
                    BookingLink.booked_time.isnot(None)))


def _base_query(db: Session, user: User, request: Optional[Request] = None, *cols):
    q = authorized_lead_query(db, user, *cols, request=request)
    return q.filter(
        or_(Lead.status.is_(None), ~Lead.status.in_(_EXCLUDED_STATUSES)),
        or_(Lead.manual_flag.is_(None), Lead.manual_flag == "bad_email"),
    )


def _parse_custom(raw) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _address(lead: Lead, cf: Dict[str, Any]) -> Optional[str]:
    if cf.get("service_address"):
        return str(cf["service_address"])
    parts = [p for p in (lead.street_address, lead.city, lead.state, lead.zip_code) if p]
    return ", ".join(parts) or None


def _user_names(db: Session, ids) -> Dict[str, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.full_name or u.email for u in db.query(User).filter(User.id.in_(ids)).all()}


def _bookings_for(db: Session, lead_ids) -> Dict[str, BookingLink]:
    lead_ids = [i for i in lead_ids if i]
    if not lead_ids:
        return {}
    out = {}
    for b in (db.query(BookingLink)
              .filter(BookingLink.lead_id.in_(lead_ids),
                      BookingLink.status.in_(["booked", "confirmed"]),
                      BookingLink.booked_time.isnot(None))
              .order_by(BookingLink.booked_time.desc()).all()):
        out.setdefault(b.lead_id, b)
    return out


def _row(lead: Lead, names, bookings, now) -> Dict[str, Any]:
    cf = _parse_custom(lead.custom_fields)
    status = _STATUS_BY_TIER.get(lead.tier)
    booking = bookings.get(lead.id)
    is_booked = bool(booking) and status in OPEN_STATUS_KEYS
    last = max([t for t in (lead.updated_at, lead.last_messaged_at, lead.created_at) if t],
               default=None)
    usage = cf.get("annual_usage_kwh")
    return {
        "id": lead.id,
        "name": f"{lead.first_name or ''} {lead.last_name or ''}".strip() or None,
        "first_name": lead.first_name, "last_name": lead.last_name,
        "email": lead.email, "phone": lead.phone,
        "service_address": _address(lead, cf),
        "current_supplier": cf.get("current_supplier"),
        "annual_usage_kwh": usage if usage not in ("", None) else None,
        "contract_end_date": cf.get("contract_end_date"),
        "rate_type": cf.get("rate_type"),
        "segment": cf.get("segment"),
        "source": lead.source or lead.source_detail or lead.import_list_name or lead.source_file,
        "source_detail": lead.source_detail,
        "tier": lead.tier,
        "status": status,
        "status_label": _LABEL.get(status) if status else None,
        "booked": is_booked,
        "booking": ({"status": booking.status, "booked_time": _bkt.wire_obj(booking),
                     "appointment_type": booking.appt_label} if booking else None),
        "assigned_to_id": lead.assigned_to_id,
        "assigned_to_name": names.get(lead.assigned_to_id),
        "next_step": _NEXT_STEP_BOOKED if is_booked else _NEXT_STEP.get(status),
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
        "last_activity_at": _iso(last),
        "age_days": int((now - lead.created_at).total_seconds() // 86400) if lead.created_at else None,
        "is_test": bool(lead.is_test),
        # Enrollment is its own deliberate step (POST /{id}/enroll), not a status.
        "enrolled_at": _iso(getattr(lead, "enrolled_at", None)),
        "relationship_type": getattr(lead, "relationship_type", None),
    }


# ── list ────────────────────────────────────────────────────────────────────

@router.get("/")
def list_rate_requests(
    request: Request,
    status: str = Query("open", pattern="^(open|all|new|in_review|options_sent|completed|booked)$"),
    search: Optional[str] = Query(None, max_length=120),
    source: Optional[str] = Query(None, max_length=120),
    assigned: Optional[str] = Query(None, max_length=64,
                                    description="'me', 'unassigned' or a user id"),
    date_from: Optional[str] = Query(None, max_length=10),
    date_to: Optional[str] = Query(None, max_length=10),
    days: Optional[int] = Query(None, ge=1, le=3650),
    sort: str = Query("newest", pattern="^(newest|oldest|name|updated)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    org = _acting_org(db, current_user)
    now = _now()
    if status == "open" or status == BOOKED_KEY:
        tiers = _available_tiers(org, OPEN_STATUS_KEYS)
    elif status == "all":
        tiers = _available_tiers(org, [k for k, _, _ in STATUSES])
    else:
        tiers = _available_tiers(org, [status])
    if not tiers:
        return {"items": [], "total": 0, "page": page, "page_size": page_size,
                "status": status, "available": False}

    q = _base_query(db, current_user, request).filter(Lead.tier.in_(tiers))
    if status == BOOKED_KEY:
        q = q.filter(Lead.id.in_(_booked_lead_ids_q(db)))
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"
        conds = [Lead.first_name.ilike(like, escape="\\"), Lead.last_name.ilike(like, escape="\\"),
                 Lead.email.ilike(like, escape="\\"), Lead.phone.ilike(like, escape="\\"),
                 (func.coalesce(Lead.first_name, "") + " "
                  + func.coalesce(Lead.last_name, "")).ilike(like, escape="\\"),
                 Lead.street_address.ilike(like, escape="\\"), Lead.city.ilike(like, escape="\\"),
                 # service address / supplier live in the custom-field JSON text
                 Lead.custom_fields.ilike(like, escape="\\")]
        digits = "".join(ch for ch in search if ch.isdigit())
        if len(digits) >= 3:
            conds.append(Lead.phone.like(f"%{digits}%"))
        q = q.filter(or_(*conds))
    if source:
        q = q.filter(or_(Lead.source == source, Lead.source_detail == source,
                         Lead.import_list_name == source))
    if assigned == "me":
        q = q.filter(Lead.assigned_to_id == current_user.id)
    elif assigned == "unassigned":
        q = q.filter(Lead.assigned_to_id.is_(None))
    elif assigned:
        q = q.filter(Lead.assigned_to_id == assigned)
    if days:
        q = q.filter(Lead.created_at >= now - timedelta(days=days))
    for raw, op in ((date_from, "ge"), (date_to, "lt")):
        if raw:
            try:
                d = datetime.strptime(raw, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail=f"Invalid date: {raw} (YYYY-MM-DD)")
            q = q.filter(Lead.created_at >= d if op == "ge" else Lead.created_at < d + timedelta(days=1))

    total = q.count()
    order = {
        "newest": (Lead.created_at.desc(), Lead.id),
        "oldest": (Lead.created_at.asc(), Lead.id),
        "name": (Lead.last_name.asc(), Lead.first_name.asc(), Lead.id),
        "updated": (Lead.updated_at.desc(), Lead.id),
    }[sort]
    leads = q.order_by(*order).offset((page - 1) * page_size).limit(page_size).all()
    names = _user_names(db, [l.assigned_to_id for l in leads])
    bookings = _bookings_for(db, [l.id for l in leads])
    return {
        "items": [_row(l, names, bookings, now) for l in leads],
        "total": total, "page": page, "page_size": page_size,
        "status": status, "available": True,
    }


# ── summary ─────────────────────────────────────────────────────────────────

@router.get("/summary")
def rate_request_summary(
    request: Request,
    days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    org = _acting_org(db, current_user)
    now = _now()
    table = _status_table(org)
    base = _base_query(db, current_user, request, Lead.tier, func.count(Lead.id))
    by_tier = dict(base.filter(Lead.tier.in_([s["tier"] for s in table if s["available"]]))
                   .group_by(Lead.tier).all()) if any(s["available"] for s in table) else {}

    counts = {s["key"]: (int(by_tier.get(s["tier"], 0)) if s["available"] else None)
              for s in table}
    open_tiers = _available_tiers(org, OPEN_STATUS_KEYS)

    def _open_count(*extra):
        if not open_tiers:
            return None
        return int(_base_query(db, current_user, request, func.count(Lead.id))
                   .filter(Lead.tier.in_(open_tiers), *extra).scalar() or 0)

    counts[BOOKED_KEY] = _open_count(Lead.id.in_(_booked_lead_ids_q(db)))
    return {
        "organization_id": str(org.id),
        "scope": "workspace" if lead_scope.is_manager_here(current_user, db) else "own_leads",
        "statuses": table + [{"key": BOOKED_KEY, "label": "Booked", "tier": None,
                              "available": bool(open_tiers), "derived": True}],
        "counts": counts,
        "open": _open_count(),
        "unassigned": _open_count(Lead.assigned_to_id.is_(None)),
        "stale_7d": _open_count(Lead.updated_at < now - timedelta(days=7)),
        "created_in_period": _open_count(Lead.created_at >= now - timedelta(days=days)),
        "period_days": days,
        "configured": bool(open_tiers),
        "sources": [s for (s,) in (_base_query(db, current_user, request, Lead.source)
                                    .filter(Lead.tier.in_(open_tiers or ["__none__"]),
                                            Lead.source.isnot(None))
                                    .distinct().order_by(Lead.source).limit(50).all())],
        "source_choices": list(SOURCE_CHOICES),
        "definitions": {
            "open": "New + In Review + Options Sent, excluding DNC / dead",
            "booked": ("open requests whose lead has a booked or confirmed booking link "
                       "with a time"),
            "unassigned": "open requests with no assigned owner",
            "stale_7d": "open requests not updated in 7 days",
            "trend": "not reported - no status-history table exists",
        },
        "generated_at": _iso(now),
    }


# ── detail ──────────────────────────────────────────────────────────────────

@router.get("/{lead_id}")
def get_rate_request(
    lead_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_or_observer),
):
    org = _acting_org(db, current_user)
    lead = authorized_lead_query(db, current_user, request=request).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Rate request not found")
    now = _now()
    names = _user_names(db, [lead.assigned_to_id])
    row = _row(lead, names, _bookings_for(db, [lead.id]), now)
    events = (db.query(AuditLogEntry)
              .filter(AuditLogEntry.organization_id == lead.organization_id,
                      AuditLogEntry.target_type == "lead",
                      AuditLogEntry.target_id == lead.id)
              .order_by(AuditLogEntry.created_at.desc()).limit(25).all())
    actors = _user_names(db, [e.actor_user_id for e in events])
    row.update({
        "notes": lead.notes,
        "custom_fields": _parse_custom(lead.custom_fields),
        "statuses": _status_table(org),
        "timeline": [{"action": e.action, "at": _iso(e.created_at),
                      "actor": actors.get(e.actor_user_id),
                      "details": _parse_custom(e.details)} for e in events],
    })
    return row


# ── create ──────────────────────────────────────────────────────────────────

class RateRequestCreate(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    last_name: str = Field("", max_length=120)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=200)
    service_address: Optional[str] = Field(None, max_length=300)
    current_supplier: Optional[str] = Field(None, max_length=120)
    annual_usage_kwh: Optional[float] = Field(None, ge=0, le=1e10)
    contract_end_date: Optional[str] = Field(None, max_length=10)
    rate_type: Optional[str] = Field(None, max_length=40)
    segment: Optional[str] = Field(None, max_length=40)
    source: Optional[str] = Field("manual", max_length=40)
    notes: Optional[str] = Field(None, max_length=4000)
    # omitted -> the creator; "unassigned" -> nobody; a user id -> that person.
    # Anything but "the creator" is a manager capability.
    assign_to: Optional[str] = Field(None, max_length=64)


def _clean_custom(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k in CUSTOM_KEYS:
        v = payload.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if k == "contract_end_date":
            try:
                datetime.strptime(str(v), "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=422, detail="contract_end_date must be YYYY-MM-DD")
        if k == "annual_usage_kwh":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="annual_usage_kwh must be a number")
            if v < 0:
                raise HTTPException(status_code=422, detail="annual_usage_kwh must be >= 0")
            v = int(v) if float(v).is_integer() else v
        out[k] = v.strip() if isinstance(v, str) else v
    return out


def _resolve_assignee(db: Session, user: User, org_id: str, assign_to: Optional[str],
                      request: Optional[Request] = None) -> Optional[str]:
    """None-sentinel handling: returns the user id to store (or None)."""
    if assign_to is None or assign_to == "" or assign_to == user.id:
        return user.id
    if not lead_scope.is_manager_here(user, db, request):
        raise HTTPException(status_code=403,
                            detail="Only a workspace manager can assign a request to someone else.")
    if assign_to == "unassigned":
        return None
    from app.routers.admin_router import _is_workspace_person
    target = db.query(User).filter(User.id == assign_to, User.is_active == True).first()  # noqa: E712
    if not _is_workspace_person(db, org_id, target):
        raise HTTPException(status_code=404, detail="Assignee not found in this workspace.")
    return target.id


@router.post("/", status_code=201, dependencies=[Depends(require_not_observation)])
def create_rate_request(
    payload: RateRequestCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """Log a rate request: creates ONE Lead in the acting workspace at the
    org's New Inquiry stage. No consent, no cadence, no message."""
    from app.services.dedup_service import normalize_phone, normalize_last_name
    from app.services import lead_capacity

    org = _acting_org(db, current_user)
    org_id = str(org.id)
    new_tier = _TIER_BY_STATUS["new"]
    if new_tier not in _org_tier_values(org):
        raise HTTPException(status_code=409, detail=(
            "This workspace's pipeline has no New Inquiry stage, so a rate request "
            "cannot be created here."))
    if not (payload.phone or payload.email or payload.service_address):
        raise HTTPException(status_code=422,
                            detail="Give at least a phone, an email or a service address.")
    custom = _clean_custom(payload.model_dump())
    assignee = _resolve_assignee(db, current_user, org_id, payload.assign_to, request)
    source = (payload.source or "manual").strip().lower().replace(" ", "_")[:40] or "manual"

    phone_normalized = normalize_phone(payload.phone or "") if payload.phone else ""
    last_norm = normalize_last_name(payload.last_name or "")
    dup_of = None
    if phone_normalized and last_norm:
        for existing in db.query(Lead).filter(
                Lead.organization_id == org_id, Lead.phone == phone_normalized,
                Lead.is_duplicate == False,  # noqa: E712
                Lead.duplicate_resolved_at.is_(None)).all():
            if normalize_last_name(existing.last_name or "") == last_norm:
                dup_of = existing.id
                break

    lead_capacity.require_capacity_user_initiated(db, org, adding=1)

    now = datetime.utcnow()
    lead = Lead(
        organization_id=org_id,
        assigned_to_id=assignee,
        first_name=payload.first_name.strip(),
        last_name=(payload.last_name or "").strip(),
        phone=phone_normalized or payload.phone,
        phone_raw=payload.phone,
        email=(payload.email or None),
        tier=new_tier,
        status="new",
        # The channel the record CAN be reached on, as the manual add sets it.
        # This is not permission: sms_consent stays False and allow_sms unset.
        contact_channel="sms" if payload.phone else "email_only",
        source=source,
        source_detail="Rate Request",
        source_file="manual",
        custom_fields=json.dumps(custom) if custom else None,
        notes=payload.notes,
        is_duplicate=bool(dup_of),
        duplicate_of_lead_id=dup_of,
        duplicate_reason="manual_add_phone_last_name" if dup_of else None,
        duplicate_match_field="phone+last_name" if dup_of else None,
        duplicate_match_value=phone_normalized if dup_of else None,
        created_at=now, updated_at=now,
    )
    db.add(lead)
    db.flush()
    try:
        from app.services import master_contacts
        master_contacts.record_lead(db, lead, source=source, source_detail="Rate Request",
                                    ingestion_path="rate_requests_router.create")
    except Exception:  # noqa: BLE001 - retention is best-effort, as on the manual add
        pass
    log_action(db, org_id, current_user.id, action="rate_request.created",
               target_type="lead", target_id=lead.id,
               details={"tier": new_tier, "source": source,
                        "assigned_to_id": assignee, "is_duplicate": bool(dup_of),
                        "custom_fields": sorted(custom)}, commit=False)
    db.commit()
    db.refresh(lead)
    names = _user_names(db, [lead.assigned_to_id])
    return _row(lead, names, {}, _now())


# ── update: status / assignment / fields ────────────────────────────────────

class RateRequestUpdate(BaseModel):
    status: Optional[str] = Field(None, max_length=40)
    assign_to: Optional[str] = Field(None, max_length=64)
    fields: Optional[Dict[str, Any]] = None
    notes: Optional[str] = Field(None, max_length=4000)


@router.patch("/{lead_id}", dependencies=[Depends(require_not_observation)])
def update_rate_request(
    lead_id: str,
    payload: RateRequestUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    lead = authorized_lead_query(db, current_user, request=request).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Rate request not found")
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    sent = payload.model_fields_set
    changed = {}

    if "status" in sent and payload.status is not None:
        if payload.status == BOOKED_KEY:
            raise HTTPException(status_code=400, detail=(
                "Booked is derived from the appointment record and cannot be set. "
                "Book an appointment instead."))
        tier = _TIER_BY_STATUS.get(payload.status)
        allowed = _org_tier_values(org) if org else []
        if not tier or tier not in allowed:
            raise HTTPException(status_code=400, detail={
                "message": f"'{payload.status}' is not a rate-request status of this workspace.",
                "valid_statuses": [k for k, _, t in STATUSES if t in allowed]})
        if lead.tier != tier:
            prev = lead.tier
            lead.tier = tier
            changed["status"] = True
            log_action(db, lead.organization_id, current_user.id,
                       action="rate_request.status_changed", target_type="lead",
                       target_id=lead.id, details={"from": prev, "to": tier}, commit=False)

    if "assign_to" in sent and payload.assign_to is not None:
        if not lead_scope.is_manager_here(current_user, db, request):
            lead_scope.log_denial(current_user, "rate request reassignment by non-manager",
                                  lead.id, request)
            raise HTTPException(status_code=403,
                                detail="Only a workspace manager can reassign a request.")
        target = _resolve_assignee(db, current_user, str(lead.organization_id),
                                   payload.assign_to, request)
        if lead.assigned_to_id != target:
            prev = lead.assigned_to_id
            lead.assigned_to_id = target
            changed["assigned"] = True
            log_action(db, lead.organization_id, current_user.id,
                       action="rate_request.assigned", target_type="lead",
                       target_id=lead.id, details={"from": prev, "to": target}, commit=False)

    if ("fields" in sent and payload.fields) or ("notes" in sent and payload.notes is not None):
        before = _parse_custom(lead.custom_fields)
        after = dict(before)
        if payload.fields:
            unknown = set(payload.fields) - set(CUSTOM_KEYS)
            if unknown:
                raise HTTPException(status_code=400,
                                    detail=f"Unknown field(s): {', '.join(sorted(unknown))}")
            cleaned = _clean_custom(payload.fields)
            for k in payload.fields:
                if k in cleaned:
                    after[k] = cleaned[k]
                else:
                    after.pop(k, None)  # an explicit blank clears the field
        notes_changed = "notes" in sent and payload.notes is not None and payload.notes != lead.notes
        if after != before or notes_changed:
            lead.custom_fields = json.dumps(after) if after else None
            if notes_changed:
                lead.notes = payload.notes
            changed["fields"] = True
            log_action(db, lead.organization_id, current_user.id,
                       action="rate_request.updated", target_type="lead", target_id=lead.id,
                       details={"fields": sorted(k for k in set(before) | set(after)
                                                 if before.get(k) != after.get(k)),
                                "notes": bool(notes_changed)}, commit=False)

    if changed:
        lead.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(lead)
    names = _user_names(db, [lead.assigned_to_id])
    out = _row(lead, names, _bookings_for(db, [lead.id]), _now())
    out["changed"] = sorted(changed)
    return out


# ── ENROLLMENT: rate request -> customer (explicit) ─────────────────────────
# The one transition from "shopping for a rate" to "enrolled customer". It is a
# deliberate human action: it moves the lead to the Completed status
# (contract_signed), marks the lead's relationship as customer, records the
# supplier / contract end date the PERSON entered (never estimated), and - only
# when the lead came from a canonical contact - reclassifies that contact as a
# customer. It sends nothing and records no consent.

class EnrollPayload(BaseModel):
    current_supplier: Optional[str] = Field(None, max_length=200)
    contract_end_date: Optional[str] = Field(None, max_length=10)
    rate_type: Optional[str] = Field(None, max_length=100)
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/{lead_id}/enroll", dependencies=[Depends(require_not_observation)])
def enroll_rate_request(
    lead_id: str,
    payload: EnrollPayload,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    lead = authorized_lead_query(db, current_user, request=request).filter(Lead.id == lead_id).first()
    if lead is None:
        raise HTTPException(status_code=404, detail="Rate request not found")
    if lead.status in _EXCLUDED_STATUSES:
        raise HTTPException(status_code=409, detail="A DNC or closed lead cannot be enrolled.")
    org = db.query(Organization).filter(Organization.id == lead.organization_id).first()
    tier = _TIER_BY_STATUS["completed"]
    if tier not in (_org_tier_values(org) if org else []):
        raise HTTPException(status_code=400, detail=(
            "This workspace has no Completed (contract_signed) stage configured."))
    prev_tier = lead.tier
    cf = _parse_custom(lead.custom_fields)
    cf.update(_clean_custom(payload.model_dump(exclude={"note"}, exclude_none=True)))
    if lead.enrolled_at is not None and lead.tier == tier:
        # ALREADY ENROLLED. A double-click or a retried request returns the
        # enrollment that exists; it does not re-stamp the date (which moves the
        # customer between months on the Overview) or write a second audit row.
        out = _row(lead, _user_names(db, [lead.assigned_to_id]), _bookings_for(db, [lead.id]), _now())
        out.update(enrolled=True, already_enrolled=True, enrolled_at=_iso(lead.enrolled_at),
                   relationship_type=lead.relationship_type, contact_reclassified=None)
        return out
    lead.custom_fields = json.dumps(cf) if cf else None
    lead.tier = tier
    lead.relationship_type = "customer"
    # The enrollment DATE (Overview "Enrollments This Month"). Set here and only
    # here; customers enrolled before this column existed stay NULL.
    lead.enrolled_at = datetime.utcnow()
    if payload.note:
        stamp = _now().strftime("%Y-%m-%d")
        lead.notes = ((lead.notes + "\n") if lead.notes else "") + f"[Enrolled {stamp}] {payload.note}"
    contact_reclassified = None
    if lead.org_contact_id:
        from app.models.intake_models import OrgContact, RecordClass
        c = db.query(OrgContact).filter(OrgContact.id == lead.org_contact_id,
                                        OrgContact.organization_id == lead.organization_id).first()
        if c is not None and c.record_class in (RecordClass.CONTACT, RecordClass.LEAD,
                                                RecordClass.PREVIOUS_CUSTOMER, None):
            contact_reclassified = {"from": c.record_class, "to": RecordClass.CUSTOMER}
            c.record_class = RecordClass.CUSTOMER
    lead.updated_at = datetime.utcnow()
    log_action(db, lead.organization_id, current_user.id, action="rate_request.enrolled",
               target_type="lead", target_id=lead.id,
               details={"from": prev_tier, "to": tier,
                        "fields": sorted(k for k in ("current_supplier", "contract_end_date",
                                                     "rate_type") if getattr(payload, k)),
                        "contact": contact_reclassified}, commit=False)
    db.commit()
    db.refresh(lead)
    out = _row(lead, _user_names(db, [lead.assigned_to_id]), _bookings_for(db, [lead.id]), _now())
    out["enrolled"] = True
    out["enrolled_at"] = _iso(lead.enrolled_at)
    out["relationship_type"] = lead.relationship_type
    out["contact_reclassified"] = contact_reclassified
    return out
