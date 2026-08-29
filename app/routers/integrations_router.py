"""Machine-facing integration surface.

  GET  /integrations/retell/ping                  prove a key works, change nothing
  POST /integrations/retell/availability          openings for one BRAND advisor
  POST /integrations/retell/book                  take one, having re-checked it

  GET  /integrations/retell/tenant/ping           same, for a customer tenant
  POST /integrations/retell/tenant/availability   openings for one TENANT advisor
  POST /integrations/retell/tenant/book           take one, having re-checked it

THIS IS A BRIDGE, NOT A PRODUCT SURFACE. Six routes, two tenancy trees, one
scope per credential. Every route is gated by a kind-specific dependency; there
is no route here a user JWT can open and no route elsewhere an integration key
can. Two credential systems that cannot be swapped for each other is the point.

THE TWO TREES DO NOT MEET. `/retell/*` requires a `retell` key scoped to a
BrandSalesOrg and writes `SalesAppointment`. `/retell/tenant/*` requires a
`retell_tenant` key scoped to a customer Organization and writes `BookingLink`.
A key of either kind is refused — not filtered, refused, with the same opaque
401 as an unknown key — by every route of the other. One funeral home's voice
agent has no path to another funeral home, and none at all to brand sales.

WHAT IT DELIBERATELY DOES NOT RETURN: no JWT, no calendar provider token, no
other person's meeting titles, no opportunity internals, no lead data, no user
list. A caller sees the advisor it is scoped to, that advisor's free times, and
the appointment it created. Nothing else exists as far as this surface is
concerned.

RATE LIMITED PER CREDENTIAL, not per IP — a vendor's whole fleet shares one
egress address, and a key used from a rotating pool would otherwise have no
bucket at all.
"""

"""
NOTE: no `from __future__ import annotations` here, deliberately. It turns every
annotation into a string, and slowapi's rate-limit decorator rebinds the
function's globals — so FastAPI cannot resolve `AvailabilityIn` back to a class
and the router fails at import. Real annotations, real classes.
"""

import logging
from datetime import datetime, date as date_cls
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.models.integration_models import (
    IntegrationCredential, ACTION_PING, ACTION_AVAILABILITY, ACTION_BOOK,
)
from app.services.integration_auth import (
    require_retell, require_retell_tenant, rate_limit_key,
)
from app.services import retell_bridge as bridge
from app.services import tenant_scheduling as tenant

log = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])

# Generous enough for a live conversation that checks two or three date ranges,
# tight enough that a leaked key cannot be used to mine a calendar at speed.
AVAILABILITY_LIMIT = "30/minute"
# Bookings are rare by nature. A caller hitting this is retrying or is wrong.
BOOK_LIMIT = "10/minute"
PING_LIMIT = "10/minute"


# ── request models ──────────────────────────────────────────────────────────

class AvailabilityIn(BaseModel):
    # Optional: a credential issued for one advisor names them, so a voice agent
    # never has to know a user id at all.
    advisor_id: Optional[str] = Field(
        None, description="Omit to use the integration's default advisor.")
    date_from: date_cls
    date_to: Optional[date_cls] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    timezone: Optional[str] = Field(
        None, description="IANA zone for the spoken times. Defaults to the advisor's.")
    meeting_type: Optional[str] = Field(
        None, description="Meeting type key, e.g. 'discovery'. Sets the duration.")


class BookIn(BaseModel):
    # The caller's own id for this booking attempt. Required — without it a
    # retry cannot be told apart from a second booking.
    external_ref: str = Field(..., min_length=6, max_length=120)
    starts_at: datetime = Field(..., description="UTC instant from an availability slot.")
    advisor_id: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    meeting_type: Optional[str] = None
    timezone: Optional[str] = None
    prospect_name: Optional[str] = None
    prospect_email: Optional[str] = None
    prospect_phone: Optional[str] = None
    prospect_timezone: Optional[str] = None
    opportunity_id: Optional[str] = None
    notes: Optional[str] = None


def _naive_utc(dt: datetime) -> datetime:
    """Every instant in this system is naive UTC. Accept an offset and convert
    it rather than storing a mixture, which is how a meeting ends up an hour
    out and nobody can say why."""
    if dt.tzinfo is not None:
        from datetime import timezone as _tz
        return dt.astimezone(_tz.utc).replace(tzinfo=None)
    return dt


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/retell/ping")
@limiter.limit(PING_LIMIT, key_func=rate_limit_key)
def retell_ping(request: Request,
                cred: IntegrationCredential = Depends(require_retell),
                db: Session = Depends(get_db)):
    """Confirm a key is live and see what it is scoped to. Changes nothing.

    Exists so a key can be verified during setup without booking a meeting into
    somebody's diary to find out.
    """
    org = bridge.brand_for(db, cred)
    advisor = None
    if cred.default_advisor_user_id:
        try:
            advisor = bridge.resolve_advisor(db, cred, None)
        except HTTPException:
            advisor = None
    bridge.audit(db, cred, ACTION_PING, True, 200, "ping")
    db.commit()
    return {
        "success": True,
        "integration": cred.name,
        "brand": org.name,
        "brand_timezone": org.timezone,
        "default_advisor_id": cred.default_advisor_user_id,
        "default_advisor_name": advisor.full_name if advisor else None,
        "advisor_allowlist_size": len(cred.advisor_allowlist()),
        "rate_limit_per_minute": cred.rate_limit_per_minute,
    }


@router.post("/retell/availability")
@limiter.limit(AVAILABILITY_LIMIT, key_func=rate_limit_key)
def retell_availability(request: Request, body: AvailabilityIn,
                        cred: IntegrationCredential = Depends(require_retell),
                        db: Session = Depends(get_db)):
    """Openings for one advisor, computed by the one scheduling engine."""
    org = bridge.brand_for(db, cred)
    try:
        advisor = bridge.resolve_advisor(db, cred, body.advisor_id)
        mt = bridge.resolve_meeting_type(db, cred, body.meeting_type)
        out = bridge.availability(
            db, cred, advisor, org,
            date_from=body.date_from, date_to=body.date_to,
            duration_minutes=body.duration_minutes,
            timezone=body.timezone, meeting_type=mt)
    except HTTPException as e:
        bridge.audit(db, cred, ACTION_AVAILABILITY, False, e.status_code,
                     str(e.detail), advisor_user_id=body.advisor_id)
        db.commit()
        raise
    bridge.audit(db, cred, ACTION_AVAILABILITY, True, 200,
                 "%d slots %s..%s" % (out["slot_count"], out["date_from"],
                                      out["date_to"]),
                 advisor_user_id=advisor.id)
    db.commit()
    return out


@router.post("/retell/book")
@limiter.limit(BOOK_LIMIT, key_func=rate_limit_key)
def retell_book(request: Request, body: BookIn,
                cred: IntegrationCredential = Depends(require_retell),
                db: Session = Depends(get_db)):
    """Take a slot. Re-checked at this instant, idempotent on `external_ref`."""
    org = bridge.brand_for(db, cred)
    ref = (body.external_ref or "").strip()
    try:
        advisor = bridge.resolve_advisor(db, cred, body.advisor_id)
        mt = bridge.resolve_meeting_type(db, cred, body.meeting_type)
        out = bridge.book(
            db, cred, advisor, org,
            starts_at=_naive_utc(body.starts_at),
            duration_minutes=body.duration_minutes,
            meeting_type=mt, external_ref=ref, timezone=body.timezone,
            prospect_name=body.prospect_name, prospect_email=body.prospect_email,
            prospect_phone=body.prospect_phone,
            prospect_timezone=body.prospect_timezone,
            opportunity_id=body.opportunity_id, notes=body.notes)
    except HTTPException as e:
        # `book` may already have rolled back; the audit row is written on a
        # clean session so a refusal is still recorded.
        try:
            db.rollback()
        except Exception:
            pass
        bridge.audit(db, cred, ACTION_BOOK, False, e.status_code, str(e.detail),
                     advisor_user_id=body.advisor_id, external_ref=None)
        db.commit()
        raise

    if not out.get("idempotent_replay"):
        bridge.audit(db, cred, ACTION_BOOK, True, 201, "booked %s" % out["appointment_id"],
                     advisor_user_id=advisor.id,
                     appointment_id=out["appointment_id"],
                     row=bridge.find_prior_attempt(db, cred, ref))
    db.commit()
    return out


# ── tenant request models ───────────────────────────────────────────────────
#
# Only fields the tenant scheduling services actually consume. There is no
# `organization_id` here and there never will be: the tenant comes from the
# credential, so there is nothing for a caller to widen.

class TenantAvailabilityIn(BaseModel):
    advisor_id: Optional[str] = Field(
        None, description="Omit to use the integration's default advisor.")
    date_from: date_cls
    date_to: Optional[date_cls] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    timezone: Optional[str] = Field(
        None, description="IANA zone for the spoken times. Defaults to the advisor's.")
    appointment_type: Optional[str] = Field(
        None, description="One of the location's configured appointment types.")


class TenantBookIn(BaseModel):
    external_ref: str = Field(..., min_length=6, max_length=120)
    starts_at: datetime = Field(..., description="UTC instant from an availability slot.")
    advisor_id: Optional[str] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    appointment_type: Optional[str] = None
    timezone: Optional[str] = None
    # The family. Either an existing lead_id, or enough to create one.
    lead_id: Optional[str] = None
    family_name: Optional[str] = None
    family_phone: Optional[str] = None
    family_email: Optional[str] = None
    notes: Optional[str] = None
    # WHICH CALL THIS BOOKING BELONGS TO. The provider's own call id, which the
    # agent can send as {{call_id}}. It identifies the lead EvoSys dialled, so
    # a callback number the family speaks is stored against that lead rather
    # than re-keying the appointment onto whichever record happens to carry the
    # spoken number. Optional: when absent the bridge falls back to the most
    # recent call this organization placed, which is the conversation in hand.
    call_id: Optional[str] = None


# ── tenant routes ───────────────────────────────────────────────────────────

@router.get("/retell/tenant/ping")
@limiter.limit(PING_LIMIT, key_func=rate_limit_key)
def retell_tenant_ping(request: Request,
                       cred: IntegrationCredential = Depends(require_retell_tenant),
                       db: Session = Depends(get_db)):
    """Confirm a tenant key is live and see what it is scoped to. Changes nothing.

    Exists so a key can be verified during setup without booking a visit into a
    real family's afternoon to find out.
    """
    org = tenant.tenant_for(db, cred)
    advisor = None
    if cred.default_advisor_user_id:
        try:
            advisor = tenant.resolve_advisor(db, cred, None)
        except HTTPException:
            advisor = None

    calendar = "none"
    if advisor is not None:
        try:
            from app.services import calendar_providers as reg
            key = reg.resolve_provider_key(db, advisor)
            calendar = key if reg.is_external_calendar(key) else "none"
        except Exception:
            calendar = "unknown"

    tenant.audit(db, cred, ACTION_PING, True, 200, "ping",
                 advisor_user_id=advisor.id if advisor else None)
    db.commit()
    return {
        "success": True,
        "integration": cred.name,
        "organization": org.brand_name or org.name,
        "location": org.org_address or None,
        "default_advisor_id": cred.default_advisor_user_id,
        "default_advisor_name": advisor.full_name if advisor else None,
        # Which calendar this advisor's availability is actually read from.
        # 'none' means no external calendar is connected — their AdvisorFlow
        # bookings and blocks still apply, but nothing outside the app does.
        "advisor_calendar": calendar,
        "advisor_timezone": (tenant.Settings(advisor).timezone if advisor
                             else None),
        "appointment_types": tenant.appointment_types(org),
        "advisor_allowlist_size": len(cred.advisor_allowlist()),
        "rate_limit_per_minute": cred.rate_limit_per_minute,
    }


@router.post("/retell/tenant/availability")
@limiter.limit(AVAILABILITY_LIMIT, key_func=rate_limit_key)
def retell_tenant_availability(request: Request, body: TenantAvailabilityIn,
                               cred: IntegrationCredential = Depends(require_retell_tenant),
                               db: Session = Depends(get_db)):
    """Real openings for one tenant advisor, honouring their own settings."""
    org = tenant.tenant_for(db, cred)
    try:
        advisor = tenant.resolve_advisor(db, cred, body.advisor_id)
        out = tenant.availability(
            db, cred, advisor, org,
            date_from=body.date_from, date_to=body.date_to,
            duration_minutes=body.duration_minutes,
            timezone=body.timezone,
            appointment_type=body.appointment_type)
    except HTTPException as e:
        tenant.audit(db, cred, ACTION_AVAILABILITY, False, e.status_code,
                     str(e.detail), advisor_user_id=body.advisor_id)
        db.commit()
        raise
    tenant.audit(db, cred, ACTION_AVAILABILITY, True, 200,
                 "%d slots %s..%s" % (out["slot_count"], out["date_from"],
                                      out["date_to"]),
                 advisor_user_id=advisor.id)
    db.commit()
    return out


@router.post("/retell/tenant/book")
@limiter.limit(BOOK_LIMIT, key_func=rate_limit_key)
def retell_tenant_book(request: Request, body: TenantBookIn,
                       cred: IntegrationCredential = Depends(require_retell_tenant),
                       db: Session = Depends(get_db)):
    """Book a family in. Re-checked at this instant, idempotent on `external_ref`."""
    org = tenant.tenant_for(db, cred)
    ref = (body.external_ref or "").strip()
    try:
        advisor = tenant.resolve_advisor(db, cred, body.advisor_id)
        out = tenant.book(
            db, cred, advisor, org,
            starts_at_utc=_naive_utc(body.starts_at),
            external_ref=ref,
            duration_minutes=body.duration_minutes,
            appointment_type=body.appointment_type,
            timezone=body.timezone,
            lead_id=body.lead_id,
            family_name=body.family_name,
            family_phone=body.family_phone,
            family_email=body.family_email,
            notes=body.notes,
            call_id=body.call_id)
    except HTTPException as e:
        # `book` may already have rolled back; the audit row is written on a
        # clean session so a refusal is still recorded.
        try:
            db.rollback()
        except Exception:
            pass
        tenant.audit(db, cred, ACTION_BOOK, False, e.status_code, str(e.detail),
                     advisor_user_id=body.advisor_id, external_ref=None)
        db.commit()
        raise

    if not out.get("idempotent_replay"):
        tenant.audit(db, cred, ACTION_BOOK, True, 200,
                     "booked %s" % out["booking_id"],
                     advisor_user_id=advisor.id,
                     booking_link_id=out["booking_id"],
                     lead_id=out.get("lead_id"),
                     row=tenant.find_prior_attempt(db, cred, ref))
    db.commit()
    return out
