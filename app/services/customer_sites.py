"""Publishing a customer's public page, and turning what it collects into a
lead in that customer's workspace.

TWO JOBS, AND NOTHING BETWEEN THEM.

  * `publish` writes the page. Staff only, size-capped, slug-validated.
  * `record_inquiry` translates one form submission into a
    `public_capture.Submission` and files it against the owning organization.

The second one is the reason this module exists rather than another copy of
the intake code. `public_capture.capture` already knows how to find-or-create
a person, write provenance on every arrival, record consent as evidence
rather than as a note, respect plan capacity and keep the master contact
ledger in the same transaction. None of that is re-implemented here; this
module only answers the question `capture` does not: which organization.

THE SOURCE IS THE CUSTOMER, NOT THE BRAND. `capture` labels a lead with the
name of the thing the visitor believes they contacted. On the brand's own
marketing site that is the brand. On a customer's own website it is the
customer - somebody filling in a form on an energy retailer's site contacted
that retailer, and recording it as "EvoSys Pro Website" would be false in the
one field a seller reads first.

NOTHING IS SENT. Same rule as every other public arrival path in this
codebase: a form submission creates a record and starts no outreach. No
cadence, no SMS, no email, no AI conversation, no notification. Outreach
begins when a person decides it does.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.customer_site_models import (FORM_KIND_INQUIRY, FORM_KINDS,
                                             CustomerSite)
from app.models.models import Organization
from app.services import public_capture as pc
from app.services.dedup_service import normalize_phone

log = logging.getLogger(__name__)

# Same ceiling the demo-site publisher uses. A public page that needs more
# than this is carrying assets that belong on a CDN.
MAX_HTML_BYTES = 2 * 1024 * 1024

_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# Paths the platform already answers on, plus the words a slug would make
# ambiguous. A customer site must never be able to shadow one of them.
RESERVED_SLUGS = {
    "api", "app", "admin", "auth", "god", "health", "version", "ping",
    "docs", "redoc", "openapi", "static", "assets", "demo", "book", "survey",
    "site", "sites", "launch", "sales", "leads", "calendar", "intake",
    "privacy", "privacy-policy", "terms", "terms-of-service", "support",
    "branding", "settings", "public", "public-booking", "site-intake",
}

# Form fields that have a column of their own and must not be repeated into
# the free-form extras blob.
_CLAIMED = {
    "name", "first_name", "last_name", "email", "phone", "company", "industry",
    "message", "goals", "consent", "consent_text", "consent_version",
    "sms_consent", "sms_consent_text", "page_url", "source_url", "referrer",
    "ip", "user_agent", "submitted_at", "form_kind",
    # Anti-spam fields the page carries and the record must never keep.
    "website_url", "form_started_at",
}

_TRUE = {"1", "true", "yes", "y", "on", "checked"}


def slug_error(slug: str) -> Optional[str]:
    """Why this slug cannot be used, or None if it can."""
    value = (slug or "").strip().lower()
    if not _SLUG_OK.match(value):
        return ("A slug is 3-64 characters, lowercase letters, digits and "
                "hyphens, starting and ending with a letter or digit.")
    if value in RESERVED_SLUGS:
        return "That slug is reserved by the platform."
    return None


def public_url(base_url: Optional[str], slug: str) -> Optional[str]:
    """The address to hand the customer, or None if the brand has no site host.

    RETURNS None RATHER THAN A RELATIVE PATH. An empty base used to produce
    "/site/<slug>", which reads as a URL, gets copied into an email, and
    resolves against whatever host the reader happens to be on. There is no
    useful absolute address to give without a configured host, and saying so
    is the only honest answer.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    return "%s/site/%s" % (base, slug)


def site_base_url(platform) -> Optional[str]:
    """The host this brand serves customer pages from. NOT the app host.

    THE ONE PLACE THAT ANSWERS THIS, because it was answered in two and both
    were wrong in the same way: the publisher script and the god API each read
    `platform.app_base_url`, which is where the React app lives. Its router
    answers every unknown path with the SPA, so a customer site URL built from
    it returned 200 and rendered "That page doesn't exist" — a broken address
    that looked like a working one, printed as the address to hand a customer.

    No fallback to `app_base_url`. Producing that URL is the defect.
    """
    if platform is None:
        return None
    value = (getattr(platform, "sites_base_url", None) or "").strip().rstrip("/")
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return "https://" + value


# ── PUBLISHING ───────────────────────────────────────────────────────────────

def publish(db: Session, *, organization: Organization, slug: str, html: str,
            title: Optional[str] = None, form_kind: str = FORM_KIND_INQUIRY,
            consent_text: Optional[str] = None,
            consent_version: Optional[str] = None,
            default_owner_user_id: Optional[str] = None,
            actor_id: Optional[str] = None,
            commit: bool = True) -> CustomerSite:
    """Create or replace the page at `slug`. Raises ValueError on refusal.

    REPUBLISHING KEEPS THE ADDRESS. A demo site mints a new token on every
    publish because a demo link is meant to be retired. A customer's website
    is the opposite: the URL is on their stationery, and a publish that moved
    it would break every link the customer has handed out.
    """
    value = (slug or "").strip().lower()
    problem = slug_error(value)
    if problem:
        raise ValueError(problem)

    body = html or ""
    if not body.strip():
        raise ValueError("The page is empty.")
    size = len(body.encode("utf-8"))
    if size > MAX_HTML_BYTES:
        raise ValueError("The page is %d bytes; the limit is %d."
                         % (size, MAX_HTML_BYTES))

    kind = (form_kind or FORM_KIND_INQUIRY).strip().lower()
    if kind not in FORM_KINDS:
        raise ValueError("Unknown form kind %r." % form_kind)

    # A page that asks for consent without carrying the wording cannot produce
    # usable evidence, so it is refused rather than published half-built.
    if consent_version and not (consent_text or "").strip():
        raise ValueError("A consent version needs the consent wording with it.")

    existing = db.query(CustomerSite).filter(CustomerSite.slug == value).first()
    if existing is not None and existing.organization_id != organization.id:
        raise ValueError("That slug already belongs to another customer.")

    now = datetime.utcnow()
    site = existing or CustomerSite(organization_id=organization.id, slug=value)
    site.title = (title or site.title or organization.name or "").strip()[:200] or None
    site.html = body
    site.form_kind = kind
    site.consent_text = (consent_text or "").strip() or None
    site.consent_version = (consent_version or "").strip() or None
    site.default_owner_user_id = default_owner_user_id or site.default_owner_user_id
    site.is_active = True
    site.published_at = now
    site.published_by = actor_id or site.published_by
    site.updated_at = now
    if existing is None:
        db.add(site)
    if commit:
        db.commit()
        db.refresh(site)
    return site


def resolve(db: Session, slug: str) -> Optional[CustomerSite]:
    """The live page at this slug, or None for every failure mode alike.

    Deactivated, unknown and malformed all answer the same way, so the 404 the
    caller renders cannot be used to learn which customers exist.
    """
    value = (slug or "").strip().lower()
    if not _SLUG_OK.match(value):
        return None
    site = db.query(CustomerSite).filter(CustomerSite.slug == value).first()
    if site is None or not site.is_live():
        return None
    return site


def note_view(db: Session, site: CustomerSite, commit: bool = True) -> None:
    site.view_count = (site.view_count or 0) + 1
    site.last_viewed_at = datetime.utcnow()
    if commit:
        db.commit()


def deactivate(db: Session, site: CustomerSite, commit: bool = True) -> CustomerSite:
    site.is_active = False
    site.updated_at = datetime.utcnow()
    if commit:
        db.commit()
    return site


def out(site: CustomerSite, *, base_url: Optional[str] = None) -> Dict[str, Any]:
    """The row as an API answer. Never carries `html`."""
    return {
        "id": site.id,
        "organization_id": site.organization_id,
        "slug": site.slug,
        "title": site.title,
        "form_kind": site.form_kind,
        "is_active": site.is_active,
        "has_consent_text": bool(site.consent_text),
        "consent_version": site.consent_version,
        "view_count": site.view_count or 0,
        "inquiry_count": site.inquiry_count or 0,
        "last_viewed_at": site.last_viewed_at.isoformat() if site.last_viewed_at else None,
        "last_inquiry_at": site.last_inquiry_at.isoformat() if site.last_inquiry_at else None,
        "published_at": site.published_at.isoformat() if site.published_at else None,
        "url": public_url(base_url, site.slug) if base_url else None,
    }


# ── TAKING AN ENQUIRY ────────────────────────────────────────────────────────

def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE


def _parse_when(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def looks_automated(payload: Dict[str, Any]) -> bool:
    """The two checks a public form can make without asking a human anything.

    A filled honeypot, or a submission faster than a person can read the form.
    Both are answered with a cheerful success by the caller: telling a bot it
    was caught is how it learns to stop tripping the check.
    """
    if str(payload.get("website_url") or "").strip():
        return True
    started = payload.get("form_started_at")
    try:
        started_at = float(started)
    except (TypeError, ValueError):
        return False
    if started_at <= 0:
        return False
    elapsed = datetime.utcnow().timestamp() - started_at
    return 0 <= elapsed < 2.0


def reachable(payload: Dict[str, Any]) -> bool:
    """A submission nobody can reply to is a row, not a lead."""
    if str(payload.get("email") or "").strip():
        return True
    return bool(normalize_phone(str(payload.get("phone") or "")))


def _split_extras(payload: Dict[str, Any]) -> Tuple[dict, dict]:
    extra, utm = {}, {}
    for key, value in (payload or {}).items():
        if key in _CLAIMED or value in (None, "", []):
            continue
        low = key.lower()
        if low.startswith("utm_") or low in ("gclid", "fbclid"):
            utm[low] = value
        else:
            extra[key] = value
    return extra, utm


def _names(payload: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    first = str(payload.get("first_name") or "").strip()
    last = str(payload.get("last_name") or "").strip() or None
    if first:
        return first, last
    return pc.split_name(str(payload.get("name") or ""))


def _consent(site: CustomerSite, payload: Dict[str, Any],
             *, required: bool) -> Optional[pc.Consent]:
    raw = payload.get("consent")
    if raw is None:
        raw = payload.get("sms_consent")
    given = _truthy(raw)
    if not given and not required and raw is None:
        return None
    # THE PAGE'S OWN WORDING WINS. What was actually on screen is the row on
    # the site, not whatever the form posted back - a client can lie about the
    # second and cannot change the first.
    text = site.consent_text or payload.get("consent_text") or payload.get("sms_consent_text")
    return pc.Consent(
        given=given,
        text=(str(text).strip() or None) if text else None,
        version=site.consent_version or payload.get("consent_version"),
        ip=payload.get("ip"),
        user_agent=payload.get("user_agent"),
        page_url=payload.get("page_url") or payload.get("source_url"),
        at=_parse_when(payload.get("submitted_at")),
    )


def record_inquiry(db: Session, *, site: CustomerSite, organization: Organization,
                   payload: Dict[str, Any], commit: bool = True) -> Dict[str, Any]:
    """One submission from this page, filed against this page's owner.

    Returns `public_capture.capture`'s answer. The caller decides what the
    visitor is told; nothing in the response is meant to be shown to them.
    """
    kind = site.form_kind or FORM_KIND_INQUIRY
    first, last = _names(payload)
    extra, utm = _split_extras(payload)
    extra.setdefault("site_slug", site.slug)

    submission = pc.Submission(
        kind=kind,
        first_name=first,
        last_name=last,
        email=(str(payload.get("email") or "").strip() or None),
        phone=(str(payload.get("phone") or "").strip() or None),
        company=(str(payload.get("company") or "").strip() or None),
        industry=(str(payload.get("industry") or "").strip() or None),
        message=(str(payload.get("message") or payload.get("goals") or "").strip() or None),
        page_url=(payload.get("page_url") or payload.get("source_url") or None),
        referrer=payload.get("referrer"),
        utm=utm,
        extra=extra,
        consent=_consent(site, payload, required=(kind == "sms_optin")),
        ip=payload.get("ip"),
        user_agent=payload.get("user_agent"),
        submitted_at=_parse_when(payload.get("submitted_at")),
    )

    # `capture` names the lead's source after the thing the visitor believes
    # they contacted. On a customer's own website that is the customer.
    result = pc.capture(db, platform=organization, org=organization,
                        sub=submission, commit=False)

    site.inquiry_count = (site.inquiry_count or 0) + 1
    site.last_inquiry_at = datetime.utcnow()

    if site.default_owner_user_id and result.get("action") == "created":
        from app.models.models import Lead
        lead = db.query(Lead).filter(Lead.id == result["lead_id"]).first()
        if lead is not None and not lead.assigned_to_id:
            lead.assigned_to_id = site.default_owner_user_id

    if commit:
        db.commit()
    return result
