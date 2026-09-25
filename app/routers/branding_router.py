"""
Branding Router
----------------
Public endpoint — no auth required.
Returns the correct brand theme config based on the request's Host header.

Used by:
  - Frontend on load (to confirm the hostname-detected theme matches backend config)
  - White-label login pages (to render the correct logo/colors before auth)

This endpoint IS the source of truth. The frontend still detects a hostname
client-side for the very first paint - a fetch cannot beat the first frame, and
a flash of the wrong brand is worse than a bootstrap literal - but it then
fetches this and caches the answer, so every load after the first is driven by
the platform row. See app/services/brand_config.py and frontend/src/theme.js.

GET /branding
  Returns: { brand, displayName, supportEmail, accentColor, bgColor }

CORS: allowed from any origin (public endpoint, no sensitive data)
"""

import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import User, Organization
from app.services import industry_templates

router = APIRouter(prefix="/branding", tags=["branding"])

# The hostname->brand table that used to live here is gone. It was one of four
# unsynchronised copies of the same brand data, none of which read the database,
# and its BookaBoost accent (#2fb6ff) had already drifted from the frontend's
# (#c9973d) with no consumer to notice. app/services/brand_config.py is the one
# resolver now: platform row first, frozen literals only as a fallback for a
# deployment whose columns are not backfilled yet.


@router.get("")
@router.get("/")
def get_branding(request: Request, db: Session = Depends(get_db)):
    """Public, unauthenticated: which brand this hostname is, and how it looks.

    The frontend themes itself from this and caches the answer, so a brand's
    name, colours, logo, favicon and tab title come from its platform row rather
    than from a literal compiled into the bundle.
    """
    from app.services.brand_config import public_payload

    # ══════════════════════════════════════════════════════════════════════
    # THE HOST THAT MATTERS IS THE BROWSER'S, NOT THIS SERVICE'S.
    # ══════════════════════════════════════════════════════════════════════
    #
    # FOUND LIVE, 2026-09-11. This read `Host` only. The frontend is a static
    # site on `app.evosyspro.live` and it calls the API at
    # `advisorflow-backend.onrender.com`, so the Host header this endpoint saw
    # was always the BACKEND's — which contains the substring "advisorflow"
    # and therefore matched the AdvisorFlow platform row. Production returned:
    #
    #     {"brand":"advisorflow","displayName":"AdvisorFlow",
    #      "supportEmail":"mike@simmonsstrong.com", ...}
    #
    # for every brand. `theme.js` caches that answer in localStorage and
    # applies it synchronously on the NEXT load, so an EvoSys Pro customer's
    # app chrome adopted AdvisorFlow's name, accent and support address from
    # their second page load onward. AdvisorFlow is the engine underneath and
    # a customer must never see it; this was it, on every screen.
    #
    # `Origin` IS THE RIGHT SIGNAL AND IT IS NOT CLIENT-CHOSEN. A browser sets
    # Origin on a cross-origin fetch itself, from the page's real address, and
    # page script cannot forge it. That makes it a statement about WHERE THE
    # CUSTOMER ACTUALLY IS, which is exactly the question this endpoint asks —
    # unlike a `?brand=` parameter, which would let anybody pick.
    #
    # `Referer` is the same information with weaker guarantees and is used only
    # when Origin is absent (a plain navigation rather than a fetch). `Host`
    # remains the last resort, so a same-origin deployment behaves exactly as
    # it always did.
    def _host_of(value):
        if not value:
            return ""
        v = str(value).strip()
        if "//" in v:
            v = v.split("//", 1)[1]
        return v.split("/")[0].split(":")[0].strip().lower()

    candidates = [
        _host_of(request.headers.get("origin")),
        _host_of(request.headers.get("referer")),
        _host_of(request.headers.get("host")),
    ]

    slug = os.environ.get("PLATFORM_SLUG", "").strip().lower() or None

    payload = None
    for host in candidates:
        if not host:
            continue
        candidate = public_payload(db, host, slug=None)
        # A host that resolved to a real platform row is an answer. A host
        # that only matched the frozen fallback is not, so the next candidate
        # is tried before settling — which is what stops the backend's own
        # hostname from answering for somebody else's customer.
        if candidate.get("source") == "database":
            payload = candidate
            break
        if payload is None:
            payload = candidate

    if payload is None:
        payload = public_payload(db, "", slug=None)

    # An explicit PLATFORM_SLUG wins only when no host told us anything useful.
    if slug and payload.get("source") == "frozen" and payload.get("brand") != slug:
        payload = public_payload(db, candidates[-1] or "", slug=slug)
    return payload


def _platform_brand(db, org):
    """The presentation of the platform (white-label brand) an organization
    belongs to, or None. Never raises: branding must not break a login."""
    if not getattr(org, "platform_id", None):
        return None
    try:
        from app.models.models import Platform
        from app.services.brand_config import config_for_slug
        row = db.query(Platform).filter(Platform.id == org.platform_id).first()
        if row is None or not row.slug:
            return None
        cfg = config_for_slug(db, row.slug)
        return {"slug": row.slug,
                "display_name": cfg.get("display_name"),
                "short_name": cfg.get("short_name"),
                "logo_url": cfg.get("logo_url"),
                "accent_color": cfg.get("accent_color"),
                "theme": cfg.get("theme_slug") or row.slug}
    except Exception:  # noqa: BLE001
        return None


@router.get("/org")
def get_org_branding(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return the authenticated user's org branding from the database.
    Called by the frontend on login to apply per-org white-label customization.
    Falls back to None values if the org has no custom branding set.
    """
    # THE WORKSPACE BEING WORKED IN, and WHAT IT IS ENTITLED TO.
    #
    # Two defects closed here at once.
    #
    # 1. This read `current_user.organization_id` — the legacy column — so a
    #    person seconded into a second workspace was themed, and now gated, by
    #    their HOME organization rather than the one they had selected.
    #
    # 2. `enabled_features` was never in this payload, and this is the ONLY
    #    call the customer shell makes on load. `Layout.jsx` reads
    #    `branding?.enabled_features ?? null` and treats null as "no
    #    restriction", so the value being absent meant every nav item rendered
    #    for every customer, forever. An organization with no features enabled
    #    was shown Leads, Campaigns, CRM, Users, AI Hub, Cadence, Reports,
    #    Imports, Audit Log and Tier Config — a sidebar of a product it had
    #    not been given. The backend gates are the real enforcement; this is
    #    what stops the app OFFERING what it will then refuse.
    from app.services.lead_scope import active_workspace_org_id, effective_role
    org_id = active_workspace_org_id(current_user, db) or current_user.organization_id
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        return {
            "brand_name": None,
            "brand_logo_url": None,
            "brand_color_primary": None,
            "brand_color_accent": None,
            "favicon_url": None,
            "tagline": None,
            "support_email": None,
            "email_sender_name": None,
            "enabled_features": None,
            "industry": None,
            "workspace_role": None,
            "organization_id": None,
        }
    import json as _json
    features = None
    raw = getattr(org, "enabled_features", None)
    if raw:
        try:
            loaded = _json.loads(raw)
            features = [f for f in loaded if isinstance(f, str)] if isinstance(loaded, list) else []
        except (ValueError, TypeError):
            features = None
    elif raw is not None:
        # An explicit empty list means "no modules", which is not the same
        # thing as "never configured". Preserved, because collapsing the two
        # is how the sidebar came to show everything.
        features = []
    return {
        "brand_name": org.brand_name,
        "brand_logo_url": org.brand_logo_url,
        "brand_color_primary": org.brand_color_primary,
        "brand_color_accent": org.brand_color_accent,
        "favicon_url": getattr(org, "favicon_url", None),
        "tagline": getattr(org, "tagline", None),
        "support_email": getattr(org, "support_email", None),
        "email_sender_name": getattr(org, "email_sender_name", None),
        "enabled_features": features,
        # THE CANONICAL KEY, NOT WHAT SOMEBODY TYPED.
        #
        # `organizations.industry` is stored raw — POST /god/customers writes
        # whatever the caller sent — so this column legitimately holds
        # "Commercial Cleaning", "Energy / Procurement" and "cleaning" for
        # businesses in the same vertical. Every client-side consumer of this
        # field then compares it to a canonical key: labels.js looks up
        # INDUSTRY_MEMBER_LABELS, the sidebar tests `=== 'fiber'`, and
        # verticals/workspaceVertical.js decides whether a workspace has a
        # presentation of its own. Each of those fails SILENTLY on a raw
        # value — the customer gets the platform's generic shell and nothing
        # says why.
        #
        # Resolving here matches what GET /settings/terminology and
        # /org-settings already return, so the browser sees one spelling of an
        # industry rather than two. `normalize` falls back to GENERIC, never
        # to whichever template happens to be first.
        "industry": industry_templates.normalize(getattr(org, "industry", None)),
        # THE ROLE IN THIS WORKSPACE. The browser had only `users.role`, which
        # is one value for a whole human, so an org_admin of one customer was
        # drawn an administrator's sidebar inside every customer they could
        # reach. The server decides this; the client renders it.
        "workspace_role": effective_role(current_user, db),
        "organization_id": org.id,
        # THE BRAND THIS WORKSPACE BELONGS TO (Phase 7.2).
        #
        # The shell used to know its brand ONLY from the browser's hostname:
        # app.evosyspro.live -> EvoSys Pro, and every other host - localhost
        # included - fell through to BookaBoost. An EvoSys Pro customer opened
        # on any host that is not a brand domain was therefore drawn as
        # BookaBoost. The organization already says which platform it belongs
        # to (`organizations.platform_id`); this hands that answer to the
        # browser. The frontend uses it ONLY when the hostname is not itself a
        # brand domain, so a brand domain still decides its own chrome exactly
        # as before and no brand can be shown on another brand's host.
        "platform": _platform_brand(db, org),
    }
