"""The customer's own public website, and the enquiries it takes.

TWO PUBLIC ROUTES AND A STAFF DOOR.

    GET  /site/{slug}            the page
    POST /site/{slug}/inquiry    what its form collects
    GET  /god/customer-sites/{organization_id}
    PUT  /god/customer-sites/{organization_id}/{slug}
    POST /god/customer-sites/{site_id}/deactivate

WHY THE PAGE IS SERVED HERE AND NOT FROM AN IFRAME. A demo mockup is rendered
inside `<iframe sandbox>` because it is authored during a sale and shown to a
prospect who must not mistake it for a working system. A customer's live
website is the opposite: it IS the working thing, people reach it by typing
the address, and a search engine has to be able to read it. The markup is
written by staff through a god-only route, which is the same trust level as
`fiber_intake_router`'s backend-rendered form that has served public traffic
here for a year.

WHY THE INQUIRY ROUTE SHARES THE PUBLIC INTAKE BUDGET. `PUBLIC_INTAKE_SCOPE`
is one rate-limit budget across every unauthenticated write in the platform,
deliberately. A new public route with a budget of its own would be a new hole
of exactly the size of that budget.

NOTHING GOES OUT. No confirmation email, no internal notice, no SMS, no
notification. The brand's own demo-request form sends two transactional
emails; this one sends none, because the recipient would be a customer's
inbox the customer has not configured and a silent misdelivery is worse than
no notification at all. The enquiry appears in the workspace, which is where
the person working it is already looking.
"""

# NO `from __future__ import annotations` IN THIS FILE. slowapi's rate-limit
# decorator wraps the endpoint with functools.wraps, which does not carry
# `__globals__` across - so with PEP 563 in force FastAPI resolves the
# request-body annotation against slowapi's module namespace and raises
# PydanticUndefinedAnnotation at import time. Real annotations, not strings.

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.deps import get_db, load_org_in_scope, require_god
from app.limiter import limiter
from app.models.customer_site_models import FORM_KIND_INQUIRY, CustomerSite
from app.models.models import Organization, Platform, User
from app.routers.audit_log_router import log_action
from app.services import customer_sites, public_intake

log = logging.getLogger(__name__)

router = APIRouter(tags=["customer-site"])
god_router = APIRouter(prefix="/god/customer-sites", tags=["customer-site-staff"])

# One string for every reason a page cannot be shown. Which customers exist,
# and which of them have a site switched on, is not public information.
_NOT_FOUND = "This page is not available."

# What the visitor is told. Identical for a real submission, a duplicate and a
# honeypot trip, because the difference is not theirs to learn.
_THANKS = "Thanks - we have your request and someone will be in touch."


class InquiryPayload(BaseModel):
    """Whatever the page's form posts.

    `extra="allow"` on purpose: a customer's website asks the questions that
    customer's business needs, and a fixed schema here would mean a field
    could not be added to a form without a deploy. Everything unclaimed is
    kept verbatim in the lead's note and custom fields.
    """
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    message: Optional[str] = None


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


# ── PUBLIC ───────────────────────────────────────────────────────────────────

@router.get("/site/{slug}", response_class=HTMLResponse, include_in_schema=False)
@limiter.limit("120/minute")
def customer_site_page(slug: str, request: Request, db: Session = Depends(get_db)):
    """Serve the published page."""
    site = customer_sites.resolve(db, slug)
    if site is None:
        return HTMLResponse(content=_plain(_NOT_FOUND), status_code=404)
    try:
        customer_sites.note_view(db, site)
    except Exception:  # pragma: no cover - a counter must never 500 a page
        db.rollback()
        log.warning("customer_site: view counter failed for slug=%r", slug)
    return HTMLResponse(content=site.html)


@router.post("/site/{slug}/inquiry", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def customer_site_inquiry(slug: str, payload: InquiryPayload, request: Request,
                          db: Session = Depends(get_db)):
    """File one submission against the organization that owns this page."""
    site = customer_sites.resolve(db, slug)
    if site is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    org = (db.query(Organization)
           .filter(Organization.id == site.organization_id).first())
    # A page whose owner has been deactivated stops taking enquiries rather
    # than filing them somewhere a person will never look.
    if org is None or org.is_active is False:
        raise HTTPException(status_code=503, detail=_NOT_FOUND)

    data: Dict[str, Any] = payload.model_dump(exclude_none=True)
    data.setdefault("page_url", request.headers.get("referer"))
    data["ip"] = _client_ip(request)
    data["user_agent"] = (request.headers.get("user-agent") or "")[:400] or None

    if customer_sites.looks_automated(data):
        # Answered as a success on purpose. See the module docstring.
        return {"success": True, "message": _THANKS}

    if not customer_sites.reachable(data):
        raise HTTPException(
            status_code=422,
            detail="A valid email address or phone number is required.")

    try:
        customer_sites.record_inquiry(db, site=site, organization=org, payload=data)
    except Exception:
        db.rollback()
        log.exception("customer_site: inquiry failed for slug=%r", slug)
        raise HTTPException(
            status_code=503,
            detail="We could not record that just now. Please try again shortly.")

    return {"success": True, "message": _THANKS}


def _plain(message: str) -> str:
    return ("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Not available</title></head>"
            "<body style=\"font-family:system-ui,sans-serif;padding:48px;"
            "color:#1b2a3a;background:#f4f8fb\"><p>%s</p></body></html>" % message)


# ── STAFF ────────────────────────────────────────────────────────────────────

class SitePublishBody(BaseModel):
    html: str
    title: Optional[str] = None
    form_kind: str = FORM_KIND_INQUIRY
    consent_text: Optional[str] = None
    consent_version: Optional[str] = None
    default_owner_user_id: Optional[str] = None


def _base_url(db: Session, org: Organization) -> Optional[str]:
    """The host this customer's pages are served from.

    `app_base_url` used to answer this and it is the wrong host: that is where
    the React app is served, and its catch-all route renders the SPA for every
    unknown path — so the address this produced returned 200 and showed "That
    page doesn't exist". See customer_sites.site_base_url.
    """
    if not org.platform_id:
        return None
    platform = db.query(Platform).filter(Platform.id == org.platform_id).first()
    return customer_sites.site_base_url(platform)


@god_router.get("/{organization_id}")
def list_customer_sites(organization_id: str, db: Session = Depends(get_db),
                        current_user: User = Depends(require_god)):
    org = load_org_in_scope(db, current_user, organization_id)
    base = _base_url(db, org)
    rows = (db.query(CustomerSite)
            .filter(CustomerSite.organization_id == org.id)
            .order_by(CustomerSite.created_at.desc()).all())
    return {"organization_id": org.id,
            "organization_name": org.name,
            "sites": [customer_sites.out(s, base_url=base) for s in rows]}


@god_router.put("/{organization_id}/{slug}")
def publish_customer_site(organization_id: str, slug: str,
                          body: SitePublishBody = Body(...),
                          db: Session = Depends(get_db),
                          current_user: User = Depends(require_god)):
    """Publish or replace this customer's page at this address."""
    org = load_org_in_scope(db, current_user, organization_id)
    try:
        site = customer_sites.publish(
            db, organization=org, slug=slug, html=body.html, title=body.title,
            form_kind=body.form_kind, consent_text=body.consent_text,
            consent_version=body.consent_version,
            default_owner_user_id=body.default_owner_user_id,
            actor_id=current_user.id, commit=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log_action(db, org.id, current_user.id, "customer_site.published",
               "customer_site", site.slug,
               details={"bytes": len(body.html.encode("utf-8")),
                        "form_kind": site.form_kind},
               platform_id=org.platform_id, commit=False)
    db.commit()
    db.refresh(site)
    return customer_sites.out(site, base_url=_base_url(db, org))


@god_router.post("/{site_id}/deactivate")
def deactivate_customer_site(site_id: str, db: Session = Depends(get_db),
                             current_user: User = Depends(require_god)):
    site = db.query(CustomerSite).filter(CustomerSite.id == site_id).first()
    if site is None:
        raise HTTPException(status_code=404, detail="No such site.")
    org = load_org_in_scope(db, current_user, site.organization_id)
    customer_sites.deactivate(db, site, commit=False)
    log_action(db, org.id, current_user.id, "customer_site.deactivated",
               "customer_site", site.slug, platform_id=org.platform_id, commit=False)
    db.commit()
    return customer_sites.out(site, base_url=_base_url(db, org))
