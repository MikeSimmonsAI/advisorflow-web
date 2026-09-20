"""A workflow screen a customer recognises, over the records the platform
already has.

THE PROBLEM THIS SOLVES. Two customers arrived in the same month wanting
screens the product did not have a name for. One buys electricity and works
"rate requests", "move concierge" and "renewals". One sells commercial
cleaning and works "prospects", "follow-up" and "walkthroughs". Neither is a
new kind of record. Every one of those screens is the same lead table this
platform has always had, asked a different question.

The way that goes wrong is a page per customer: six components, six routers,
six places for the tenancy filter to be written slightly differently, and a
seventh the first time somebody sells to a third vertical. The way it goes
right is one page and one query, and a row that says which question to ask.

SO A VIEW IS DATA. `Organization.workspace_views` holds a JSON list; when it
is NULL the organization inherits its industry's list from
`industry_templates`. A view names a source, a filter, some columns and some
counters. It creates no table, owns no records, and can express nothing the
lead and appointment tables cannot already answer - which is the property that
makes it safe to let configuration decide it.

WHAT A VIEW CANNOT DO, on purpose:

  * It cannot widen scope. Every query is built by `lead_scope`, so a view is
    subject to exactly the tenancy and ownership rules the Leads page is. A
    filter that names another organization's id simply matches nothing.
  * It cannot write. There is no mutation path through a view. A record is
    changed on the record's own screen, where the audit trail already is.
  * It cannot invent a column. `columns` selects from a fixed vocabulary; an
    unknown key is dropped rather than rendered as a blank the reader would
    read as "no value".

A view that only one customer will ever want belongs on that customer's
organization row. Anything the next customer in the same vertical will also
want belongs on the industry template. That split is the whole design.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import (BookingLink, Lead, Organization,
                               PipelineConversation, User)
from app.services import industry_templates, lead_scope

log = logging.getLogger(__name__)


# ── THE VOCABULARY A VIEW MAY USE ────────────────────────────────────────────
# Deliberately closed. A view is written by a person configuring a customer,
# not by a developer, and an open vocabulary would mean every misspelling
# renders as an empty column that reads like missing data.

SOURCE_LEADS = "leads"
SOURCE_APPOINTMENTS = "appointments"
SOURCES = (SOURCE_LEADS, SOURCE_APPOINTMENTS)

LEAD_COLUMNS = {
    "name": "Name",
    "contact": "Contact",
    "tier": "Stage",
    "status": "Status",
    "source": "Source",
    "source_detail": "Form",
    "owner": "Owner",
    "location": "Location",
    "channel": "Channel",
    "temperature": "Engagement",
    "created_at": "Created",
    "updated_at": "Last Activity",
    "last_contact_date": "Last Contact",
    "note": "Latest Note",
}

APPOINTMENT_COLUMNS = {
    "name": "Customer",
    "contact": "Contact",
    "booked_time": "Scheduled",
    "appt_label": "Type",
    "status": "Status",
    "outcome": "Outcome",
    "owner": "Booked By",
    "tier": "Stage",
    "created_at": "Created",
}


# ── WHAT A BOOKING ROW ACTUALLY PROVES ───────────────────────────────────────
#
# `booking_links.status` runs pending -> booked -> confirmed, with expired and
# cancelled as exits. Read plainly, it answers two questions and refuses a
# third:
#
#   pending    A LINK WAS SENT. Nobody has chosen a time. There is no
#              appointment. A screen that counts these as booked visits tells a
#              customer they have a calendar full of site visits that do not
#              exist, which is the defect this constant exists to name.
#   booked     Somebody chose a time. This is an appointment.
#   confirmed  The prospect confirmed that time. Still the same appointment.
#   cancelled  It was called off. It happened as a booking and then stopped
#              being one, so it stays visible and stays labelled.
#   expired    The link lapsed unused. Same class as pending: never an
#              appointment.
#
# The third question — did anybody turn up — a booking link cannot answer. It
# carries no attended flag, no no-show and no completion timestamp, and
# app/services/workforce_intelligence/metrics.py reached the same wall and
# returns UNKNOWN rather than promoting confirmed bookings to completed ones.
BOOKING_SCHEDULED = ("booked", "confirmed")
BOOKING_NEVER_SCHEDULED = ("pending", "expired")

# WHERE "IT HAPPENED" DOES COME FROM, and it is a person, not a clock.
# `pipeline_conversations` carries a lead through booked -> confirmed -> kept
# -> sale and stamps `appointment_kept_at` when somebody records that the visit
# took place. That is evidence. "The booked time has passed" is not: inferring
# attendance from the calendar would hand a business a perfect show rate on
# walkthroughs nobody attended.
#
# Deliberately NOT sales_models.Opportunity. That is the brand's own sales
# pipeline over its customers; this is a customer's pipeline over its
# prospects. Reading one to answer the other crosses a tenancy boundary that
# happens to type-check.
PIPELINE_KEPT_STAGES = ("kept", "sale")

# A filter key that is not here is ignored rather than guessed at. Silently
# matching everything would be worse: a screen that looks filtered and is not.
LEAD_FILTER_KEYS = (
    "tier", "not_tier", "status", "not_status", "source_detail", "source",
    "channel", "temperature", "relationship_type", "assigned", "unassigned",
    "since_days", "stale_days", "has_email", "has_phone",
)
APPOINTMENT_FILTER_KEYS = ("status", "not_status", "since_days", "upcoming",
                           "past", "pipeline_stage", "not_pipeline_stage",
                           "kept")

MAX_ROWS = 500
DEFAULT_ROWS = 100
MAX_VIEWS = 12


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def _clean_view(raw: Any, position: int) -> Optional[Dict[str, Any]]:
    """One configured view, or None if it cannot be trusted to render.

    A malformed view is dropped, not repaired. A screen that renders from a
    half-understood spec is how a customer ends up looking at the wrong
    records without being told.
    """
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key") or "").strip().lower()
    label = str(raw.get("label") or "").strip()
    if not key or not label:
        return None
    if not all(c.isalnum() or c in "-_" for c in key):
        return None

    source = str(raw.get("source") or SOURCE_LEADS).strip().lower()
    if source not in SOURCES:
        return None

    allowed_columns = LEAD_COLUMNS if source == SOURCE_LEADS else APPOINTMENT_COLUMNS
    columns = [c for c in _as_list(raw.get("columns")) if c in allowed_columns]
    if not columns:
        columns = (["name", "contact", "tier", "owner", "updated_at"]
                   if source == SOURCE_LEADS
                   else ["name", "booked_time", "appt_label", "status", "owner"])

    stats = []
    for entry in (raw.get("stats") or []):
        if not isinstance(entry, dict):
            continue
        stat_label = str(entry.get("label") or "").strip()
        if not stat_label:
            continue
        stats.append({"label": stat_label,
                      "filter": entry.get("filter") if isinstance(entry.get("filter"), dict) else {}})
        if len(stats) >= 6:
            break

    return {
        "key": key,
        "label": label,
        "group": str(raw.get("group") or "Workspace").strip() or "Workspace",
        "icon": str(raw.get("icon") or "list").strip() or "list",
        "title": str(raw.get("title") or label).strip(),
        "subtitle": str(raw.get("subtitle") or "").strip() or None,
        "empty": str(raw.get("empty") or "").strip() or None,
        "source": source,
        "filter": raw.get("filter") if isinstance(raw.get("filter"), dict) else {},
        "columns": columns,
        "column_labels": {c: allowed_columns[c] for c in columns},
        "stats": stats,
        "position": position,
    }


def _parse(raw: Any) -> List[Dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            log.warning("workspace_views: organization column is not valid JSON")
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for position, entry in enumerate(raw):
        view = _clean_view(entry, position)
        if view is not None:
            out.append(view)
        if len(out) >= MAX_VIEWS:
            break
    return out


def for_organization(org: Optional[Organization]) -> List[Dict[str, Any]]:
    """The views this organization shows, its own first, its industry's second.

    NULL on the column is not "no views" - it is "nothing said about this
    customer yet", which is the state every organization starts in and the
    state in which inheriting the vertical's defaults is the useful answer. An
    empty list IS a decision, and it is honoured: a customer who has turned
    every view off gets none back.
    """
    if org is None:
        return []
    raw = getattr(org, "workspace_views", None)
    if raw is not None and str(raw).strip() not in ("", "null"):
        return _parse(raw)
    return _parse(industry_templates.workspace_views(getattr(org, "industry", None)))


def find(org: Optional[Organization], key: str) -> Optional[Dict[str, Any]]:
    wanted = (key or "").strip().lower()
    for view in for_organization(org):
        if view["key"] == wanted:
            return view
    return None


# ── APPLYING A FILTER ────────────────────────────────────────────────────────

def _apply_lead_filter(query, spec: Dict[str, Any]):
    spec = {k: v for k, v in (spec or {}).items() if k in LEAD_FILTER_KEYS}
    now = datetime.utcnow()

    values = _as_list(spec.get("tier"))
    if values:
        query = query.filter(Lead.tier.in_(values))
    values = _as_list(spec.get("not_tier"))
    if values:
        query = query.filter((Lead.tier.is_(None)) | (~Lead.tier.in_(values)))
    values = _as_list(spec.get("status"))
    if values:
        query = query.filter(Lead.status.in_(values))
    values = _as_list(spec.get("not_status"))
    if values:
        query = query.filter((Lead.status.is_(None)) | (~Lead.status.in_(values)))
    values = _as_list(spec.get("source_detail"))
    if values:
        query = query.filter(Lead.source_detail.in_(values))
    values = _as_list(spec.get("source"))
    if values:
        query = query.filter(Lead.source.in_(values))
    values = _as_list(spec.get("channel"))
    if values:
        query = query.filter(Lead.contact_channel.in_(values))
    values = _as_list(spec.get("relationship_type"))
    if values:
        query = query.filter(Lead.relationship_type.in_(values))

    if spec.get("unassigned") is True:
        query = query.filter(Lead.assigned_to_id.is_(None))
    elif spec.get("assigned") is True:
        query = query.filter(Lead.assigned_to_id.isnot(None))

    if spec.get("has_email") is True:
        query = query.filter(Lead.email.isnot(None), Lead.email != "")
    if spec.get("has_phone") is True:
        query = query.filter(Lead.phone.isnot(None), Lead.phone != "")

    days = spec.get("since_days")
    if isinstance(days, int) and 0 < days <= 3650:
        query = query.filter(Lead.created_at >= now - timedelta(days=days))
    days = spec.get("stale_days")
    if isinstance(days, int) and 0 < days <= 3650:
        cutoff = now - timedelta(days=days)
        query = query.filter((Lead.updated_at.is_(None)) | (Lead.updated_at <= cutoff))
    return query


def _pipeline_match(*conditions):
    """EXISTS a pipeline conversation for this booking's lead, matching.

    Correlated on the LEAD, because that is the only thing the two tables
    share: `pipeline_conversations` has no booking_link_id. The second
    condition — the conversation's organization matching the lead's — is not
    redundant with the caller's tenancy scope. The scope already guarantees
    which LEADS are visible; this guarantees the EVIDENCE about them came from
    the same tenant, so a mis-set conversation row can contribute to nobody's
    numbers rather than to the wrong customer's.
    """
    return (select(PipelineConversation.id)
            .where(PipelineConversation.lead_id == BookingLink.lead_id,
                   PipelineConversation.organization_id == Lead.organization_id,
                   *conditions)
            .exists())


def _kept_match():
    """The pipeline says somebody recorded that the visit took place."""
    return _pipeline_match(
        (PipelineConversation.appointment_kept_at.isnot(None))
        | (PipelineConversation.stage.in_(PIPELINE_KEPT_STAGES)))


def _apply_appointment_filter(query, spec: Dict[str, Any]):
    spec = {k: v for k, v in (spec or {}).items() if k in APPOINTMENT_FILTER_KEYS}
    now = datetime.utcnow()

    values = _as_list(spec.get("pipeline_stage"))
    if values:
        query = query.filter(_pipeline_match(PipelineConversation.stage.in_(values)))
    values = _as_list(spec.get("not_pipeline_stage"))
    if values:
        query = query.filter(~_pipeline_match(PipelineConversation.stage.in_(values)))

    # Tri-state on purpose. True means "the pipeline says it happened", False
    # means "the pipeline does not say so" — which is an honest queue of visits
    # still owed an outcome, NOT a claim that nobody turned up. Absent means
    # the question was not asked.
    kept = spec.get("kept")
    if kept is True:
        query = query.filter(_kept_match())
    elif kept is False:
        query = query.filter(~_kept_match())

    values = _as_list(spec.get("status"))
    if values:
        query = query.filter(BookingLink.status.in_(values))
    values = _as_list(spec.get("not_status"))
    if values:
        query = query.filter((BookingLink.status.is_(None)) | (~BookingLink.status.in_(values)))
    if spec.get("upcoming") is True:
        query = query.filter(BookingLink.booked_time.isnot(None),
                             BookingLink.booked_time >= now)
    if spec.get("past") is True:
        query = query.filter(BookingLink.booked_time.isnot(None),
                             BookingLink.booked_time < now)
    days = spec.get("since_days")
    if isinstance(days, int) and 0 < days <= 3650:
        query = query.filter(BookingLink.created_at >= now - timedelta(days=days))
    return query


def _base_query(db: Session, user: User, view: Dict[str, Any], request=None):
    """Every view starts from the same authorized query the Leads page uses.

    This is the single most important line in the module. A view never builds
    its own tenancy filter, so a view cannot be written that sees a record its
    author's own Leads page would not show them.
    """
    if view["source"] == SOURCE_LEADS:
        return lead_scope.authorized_lead_query(db, user, request=request)
    # A SUBQUERY, NOT A LIST OF IDS. `authorized_lead_ids` would materialise
    # every lead id in the workspace into Python to filter a handful of
    # appointments - fine at a hundred leads, a memory event at a hundred
    # thousand. The scope is expressed once, as SQL, and the database
    # intersects it.
    in_scope = lead_scope.authorized_lead_query(
        db, user, Lead.id, request=request).scalar_subquery()
    return (db.query(BookingLink, Lead)
            .join(Lead, Lead.id == BookingLink.lead_id)
            .filter(BookingLink.lead_id.in_(in_scope)))


def _owner_names(db: Session, ids) -> Dict[str, str]:
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    rows = db.query(User.id, User.full_name).filter(User.id.in_(wanted)).all()
    return {str(r[0]): (r[1] or "") for r in rows}


def _lead_row(lead: Lead, columns, owners) -> Dict[str, Any]:
    name = " ".join(p for p in [(lead.first_name or "").strip(),
                                (lead.last_name or "").strip()] if p).strip()
    values: Dict[str, Any] = {}
    for column in columns:
        if column == "name":
            values[column] = name or "(no name)"
        elif column == "contact":
            values[column] = {"phone": lead.phone, "email": lead.email}
        elif column == "tier":
            values[column] = lead.tier
        elif column == "status":
            values[column] = lead.status
        elif column == "source":
            values[column] = lead.source
        elif column == "source_detail":
            values[column] = lead.source_detail
        elif column == "owner":
            values[column] = owners.get(str(lead.assigned_to_id or "")) or None
        elif column == "location":
            values[column] = ", ".join(p for p in [(lead.city or "").strip(),
                                                   (lead.state or "").strip()] if p) or None
        elif column == "channel":
            values[column] = lead.contact_channel
        elif column == "temperature":
            raw = getattr(lead, "engagement_temperature", None)
            values[column] = getattr(raw, "value", raw)
        elif column == "created_at":
            values[column] = lead.created_at.isoformat() if lead.created_at else None
        elif column == "updated_at":
            values[column] = lead.updated_at.isoformat() if lead.updated_at else None
        elif column == "last_contact_date":
            raw = lead.last_contact_date
            values[column] = raw.isoformat() if hasattr(raw, "isoformat") else raw
        elif column == "note":
            note = (lead.notes or "").strip()
            values[column] = (note.splitlines()[-1][:160] if note else None)
    return {"id": lead.id, "lead_id": lead.id, "values": values}


def _kept_lead_ids(db: Session, lead_ids) -> set:
    """Which of these leads the pipeline says had their visit take place.

    ONE QUERY FOR THE PAGE, in the shape `_owner_names` above already uses.
    Asking per row would be an N+1 against a table that is only being consulted
    to label a column.
    """
    wanted = sorted({i for i in lead_ids if i})
    if not wanted:
        return set()
    rows = (db.query(PipelineConversation.lead_id)
            .filter(PipelineConversation.lead_id.in_(wanted))
            .filter((PipelineConversation.appointment_kept_at.isnot(None))
                    | (PipelineConversation.stage.in_(PIPELINE_KEPT_STAGES)))
            .all())
    return {r[0] for r in rows}


def _outcome_of(booking: BookingLink, kept: bool, now: datetime) -> str:
    """What this row is allowed to claim, and nothing beyond it.

    Every branch is something the database actually recorded. There is no
    branch that turns a passed booking into an attendance, because no row says
    that: "Awaiting outcome" is the honest name for a visit whose time has gone
    by with nobody having said what happened, and it is a work queue rather
    than a result.
    """
    status = (booking.status or "").strip().lower()
    if status == "cancelled":
        return "Cancelled"
    if status == "expired":
        return "Link expired"
    if status in BOOKING_NEVER_SCHEDULED:
        return "Not booked"
    if kept:
        return "Completed"
    if booking.booked_time is not None and booking.booked_time < now:
        return "Awaiting outcome"
    return "Scheduled"


def _appointment_row(booking: BookingLink, lead: Lead, columns, owners,
                     kept_leads=frozenset(), now=None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    name = " ".join(p for p in [(lead.first_name or "").strip(),
                                (lead.last_name or "").strip()] if p).strip()
    values: Dict[str, Any] = {}
    for column in columns:
        if column == "name":
            values[column] = name or "(no name)"
        elif column == "contact":
            values[column] = {"phone": lead.phone, "email": lead.email}
        elif column == "booked_time":
            values[column] = booking.booked_time.isoformat() if booking.booked_time else None
        elif column == "appt_label":
            values[column] = booking.appt_label
        elif column == "status":
            values[column] = booking.status
        elif column == "outcome":
            values[column] = _outcome_of(booking, lead.id in kept_leads, now)
        elif column == "owner":
            values[column] = owners.get(str(booking.user_id or "")) or None
        elif column == "tier":
            values[column] = lead.tier
        elif column == "created_at":
            values[column] = booking.created_at.isoformat() if booking.created_at else None
    return {"id": booking.id, "lead_id": lead.id, "values": values}


def summary(db: Session, user: User, org: Optional[Organization], request=None) -> List[Dict[str, Any]]:
    """What the navigation needs, and nothing that costs a query to produce."""
    return [{"key": v["key"], "label": v["label"], "group": v["group"],
             "icon": v["icon"], "subtitle": v["subtitle"]}
            for v in for_organization(org)]


def render(db: Session, user: User, view: Dict[str, Any], *,
           limit: int = DEFAULT_ROWS, request=None) -> Dict[str, Any]:
    """The rows and the counters for one view.

    Counters are counted, not estimated, and they are counted through the same
    authorized query as the rows - so the number at the top of the screen and
    the list under it can never disagree about who the reader is.
    """
    limit = max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
    apply_filter = (_apply_lead_filter if view["source"] == SOURCE_LEADS
                    else _apply_appointment_filter)

    scoped = apply_filter(_base_query(db, user, view, request=request), view["filter"])
    total = scoped.count()

    stats = []
    for stat in view["stats"]:
        narrowed = apply_filter(scoped, stat["filter"])
        stats.append({"label": stat["label"], "value": narrowed.count()})

    if view["source"] == SOURCE_LEADS:
        rows = (scoped.order_by(Lead.updated_at.desc(), Lead.created_at.desc())
                .limit(limit).all())
        owners = _owner_names(db, [r.assigned_to_id for r in rows])
        items = [_lead_row(r, view["columns"], owners) for r in rows]
    else:
        rows = (scoped.order_by(BookingLink.booked_time.desc(), BookingLink.created_at.desc())
                .limit(limit).all())
        owners = _owner_names(db, [b.user_id for b, _ in rows])
        kept_leads = (_kept_lead_ids(db, [l.id for _, l in rows])
                      if "outcome" in view["columns"] else frozenset())
        now = datetime.utcnow()
        items = [_appointment_row(b, l, view["columns"], owners,
                                  kept_leads=kept_leads, now=now)
                 for b, l in rows]

    return {
        "view": {k: view[k] for k in
                 ("key", "label", "group", "icon", "title", "subtitle",
                  "empty", "source", "columns", "column_labels")},
        "stats": stats,
        "total": total,
        "returned": len(items),
        "limit": limit,
        "items": items,
    }
