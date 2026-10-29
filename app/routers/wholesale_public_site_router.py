"""The public Wholesale website, served by this backend for its own hostname.

    https://wholesale.evosyspro.live/            -> public-site/wholesale/index.html
    https://wholesale.evosyspro.live/buyers.html -> the investor buy-box page
    POST https://wholesale.evosyspro.live/intake.php
                                                 -> a buyer + buy box in EvoSys Wholesale

WHY HERE. The subdomain's DNS already points at this Render service, and the
service already holds its certificate. The site was written for a PHP host
(intake.php stores a private copy, then bridges to
/site-intake/wholesale/{key}/buyer-inquiry). Served from here, the form goes
straight into the same validation and the same `wholesale_buyer_intake.submit`
the PHP bridge would have reached, in-process. The intake key never leaves the
server: the organization is named by WHOLESALE_SITE_ORG_ID and its key is read
from its own WholesaleSettings row, then checked by the same
`resolve_destination` every other public intake uses.

HOST-SCOPED, BOTH WAYS.
  * On the site's hostname ONLY the site exists: its pages, its assets and
    intake.php. Every API path answers the site's 404 page - the platform API
    is not reachable through the public brand's address.
  * On any other hostname the /_wholesale_site/* routes answer 404, so the
    site cannot be fetched or posted to through the API's own address.

WHAT IS SERVED. Only files that ship in public-site/wholesale with a page or
asset extension. The PHP files, build script and handoff notes are never
served (no .php, .py or .md is in the allow-list).

NOTHING GOES OUT. Same contract as the PHP form: contact permission is
required and is not SMS consent; nothing is texted, emailed or called.

MORE THAN ONE WORKSPACE (#272). `WHOLESALE_SITE_HOSTS` is a comma list. A bare
host (the default, wholesale.evosyspro.live) is served exactly as before: the
EvoSys site files, and the buyer form lands in WHOLESALE_SITE_ORG_ID. An entry
written `host=<organization slug or id>` belongs to THAT workspace: it serves
the brand-neutral site in public-site/wholesale-tenant (the workspace's own
public name filled in, never another brand's), and its buyer form lands in that
workspace only, through the same intake key check. No database change: which
host belongs to which workspace is deployment configuration, read per request.
"""
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.services import public_intake

log = logging.getLogger(__name__)

PREFIX = "/_wholesale_site"
SITE_DIR = Path(__file__).resolve().parents[2] / "public-site" / "wholesale"
# The brand-neutral site a second workspace's host serves (see module notes).
TENANT_SITE_DIR = Path(__file__).resolve().parents[2] / "public-site" / "wholesale-tenant"
# Files of the EvoSys site that carry no brand and are shared with tenant hosts.
TENANT_SHARED = ("assets/site.css", "assets/hero-house.svg", "assets/favicon.svg")
DEFAULT_HOSTS = ("wholesale.evosyspro.live",)
SERVED_EXT = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
              ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
              ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
              ".txt": "text/plain; charset=utf-8", ".xml": "application/xml; charset=utf-8"}
_SAFE_PATH = re.compile(r"^(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+$")

router = APIRouter(prefix=PREFIX, include_in_schema=False)


def host_map() -> Dict[str, Optional[str]]:
    """host -> the workspace it belongs to (slug or id), or None for a bare host
    (served as the EvoSys site, organization from WHOLESALE_SITE_ORG_ID)."""
    raw = os.getenv("WHOLESALE_SITE_HOSTS")
    out: Dict[str, Optional[str]] = {}
    for entry in (raw.split(",") if raw else DEFAULT_HOSTS):
        host, _, org = entry.partition("=")
        host = host.strip().lower()
        if host:
            out[host] = org.strip() or None
    return out


def site_hosts() -> set:
    return set(host_map())


def tenant_of(host: str) -> Optional[str]:
    """The workspace a tenant host names, or None (the EvoSys site)."""
    return host_map().get((host or "").lower().split(":")[0])


def _host_of(headers) -> str:
    for k, v in headers:
        if k == b"host":
            return v.decode("latin-1").split(":")[0].strip().lower()
    return ""


def is_site_host(host: str) -> bool:
    return bool(host) and host.lower().split(":")[0] in site_hosts()


def _served_files() -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    if not SITE_DIR.is_dir():
        return out
    for p in SITE_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in SERVED_EXT:
            out[p.relative_to(SITE_DIR).as_posix()] = p
    return out


_FILES: Optional[Dict[str, Path]] = None
_TENANT_FILES: Optional[Dict[str, Path]] = None


def served_files() -> Dict[str, Path]:
    global _FILES
    if _FILES is None:
        _FILES = _served_files()
    return _FILES


def tenant_files() -> Dict[str, Path]:
    """The neutral tenant site, plus the brand-free shared assets."""
    global _TENANT_FILES
    if _TENANT_FILES is None:
        out: Dict[str, Path] = {}
        if TENANT_SITE_DIR.is_dir():
            for p in TENANT_SITE_DIR.rglob("*"):
                if p.is_file() and p.suffix.lower() in SERVED_EXT:
                    out[p.relative_to(TENANT_SITE_DIR).as_posix()] = p
        for rel in TENANT_SHARED:
            if rel not in out and (SITE_DIR / rel).is_file():
                out[rel] = SITE_DIR / rel
        _TENANT_FILES = out
    return _TENANT_FILES


class WholesaleSiteHostMiddleware:
    """Raw ASGI (see SecurityHeadersMiddleware in app.main for why). On the
    site's hostname, maps the public URL onto this router and nothing else."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and is_site_host(_host_of(scope.get("headers") or ())):
            new = site_path(scope.get("method", "GET"), scope.get("path") or "/")
            scope = dict(scope, path=new, raw_path=new.encode("ascii"))
        await self.app(scope, receive, send)


def site_path(method: str, path: str) -> str:
    """The internal path a public URL on the site's hostname is served from."""
    method = method.upper()
    rel = path.lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    if method == "POST":
        return PREFIX + "/intake" if rel == "intake.php" else PREFIX + "/not-found"
    if method in ("GET", "HEAD"):
        if _SAFE_PATH.match(rel) and ".." not in rel:
            return PREFIX + "/page/" + rel
    return PREFIX + "/not-found"


def _not_found(request: Optional[Request] = None) -> HTMLResponse:
    if request is not None and tenant_of(request.headers.get("host", "")):
        body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Page not found</title>'
                '<link rel="stylesheet" href="/assets/site.css"></head><body>'
                '<main style="max-width:640px;margin:15vh auto;padding:0 20px;font-family:system-ui,sans-serif">'
                '<h1>Page not found</h1><p>The page you were looking for is not here.</p>'
                '<p><a href="/">Go to the home page</a></p></main></body></html>')
        return HTMLResponse(body, status_code=404, headers={"Cache-Control": "no-store"})
    page = served_files().get("index.html")
    body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Page not found | EvoSys Pro Wholesale</title>'
            '<link rel="stylesheet" href="/assets/site.css"></head><body>'
            '<main style="max-width:640px;margin:15vh auto;padding:0 20px;font-family:system-ui,sans-serif">'
            '<h1>Page not found</h1><p>The page you were looking for is not here.</p>'
            '<p><a href="/">Go to EvoSys Pro Wholesale</a></p></main></body></html>')
    return HTMLResponse(body if page else "Not found", status_code=404,
                        headers={"Cache-Control": "no-store"})


def _require_site_host(request: Request) -> None:
    if not is_site_host(request.headers.get("host", "")):
        raise HTTPException(status_code=404, detail="Not Found")


@router.api_route("/page/{rel:path}", methods=["GET", "HEAD"])
def page(rel: str, request: Request, db: Session = Depends(get_db)):
    _require_site_host(request)
    tenant = tenant_of(request.headers.get("host", ""))
    f = (tenant_files() if tenant else served_files()).get(rel)
    if f is None:
        return _not_found(request)
    ctype = SERVED_EXT[f.suffix.lower()]
    cache = "public, max-age=300" if f.suffix.lower() in (".html", ".txt", ".xml") else "public, max-age=3600"
    if tenant and f.suffix.lower() == ".html":
        return HTMLResponse(_tenant_page(db, tenant, f), headers={"Cache-Control": cache})
    return FileResponse(f, media_type=ctype, headers={"Cache-Control": cache})


def _tenant_names(db: Session, tenant: str) -> Dict[str, str]:
    """The workspace's own public name and contact, for the neutral pages.
    Nothing from another brand: an unconfigured contact is left out."""
    import html as _h
    from app.models.models import Organization
    from app.models.wholesale_models import WholesaleSettings
    org = (db.query(Organization).filter((Organization.slug == tenant) | (Organization.id == tenant)).first()
           if tenant else None)
    s = (db.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org.id).first()
         if org is not None else None)
    name = (getattr(s, "public_business_name", None) or getattr(org, "brand_name", None)
            or getattr(org, "name", None) or "Our team")
    email = getattr(s, "public_contact_email", None)
    contact = ('<p class="form-note">Questions? <a href="mailto:%s">%s</a></p>' % (_h.escape(email), _h.escape(email))
               if email else "")
    return {"{{BUSINESS_NAME}}": _h.escape(name), "{{CONTACT_LINE}}": contact}


def _tenant_page(db: Session, tenant: str, f: Path) -> str:
    text = f.read_text(encoding="utf-8")
    for k, v in _tenant_names(db, tenant).items():
        text = text.replace(k, v)
    return text


@router.api_route("/not-found", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def not_found(request: Request):
    _require_site_host(request)
    return _not_found(request)


def _reply(code: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"success": code < 300, "message": message, **extra}, status_code=code,
                        headers={"Cache-Control": "no-store"})


def _destination(db: Session, host: str = ""):
    """(org, settings) for this host's workspace - a tenant host's own, else
    WHOLESALE_SITE_ORG_ID exactly as before - through the shared checks."""
    from app.models.models import Organization
    from app.models.wholesale_models import WholesaleSettings
    from app.services import wholesale_seller_intake as intake
    tenant = tenant_of(host)
    if tenant:
        org = (db.query(Organization).filter((Organization.slug == tenant) | (Organization.id == tenant)).first())
        if org is None:
            raise intake.IntakeRefused("site host names no known organization")
        org_id = org.id
    else:
        org_id = (os.getenv("WHOLESALE_SITE_ORG_ID") or "").strip()
    if not org_id:
        raise intake.IntakeRefused("WHOLESALE_SITE_ORG_ID is not set")
    s = db.query(WholesaleSettings).filter(WholesaleSettings.organization_id == org_id).first()
    if s is None or not s.public_intake_key:
        raise intake.IntakeRefused("site organization has no public intake key")
    return intake.resolve_destination(db, s.public_intake_key)


@router.post("/intake")
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT, scope=public_intake.PUBLIC_INTAKE_SCOPE)
async def site_intake(request: Request, db: Session = Depends(get_db)):
    """The buyers.html form (multipart or urlencoded), as intake.php handled it."""
    _require_site_host(request)
    from app.services import wholesale_buyer_intake as buyers
    from app.services import wholesale_seller_intake as intake
    if int(request.headers.get("content-length") or 0) > 20000:
        return _reply(413, "Form is too large.")
    origin = request.headers.get("origin") or ""
    if origin:
        o = re.sub(r"^https?://", "", origin).split("/")[0].split(":")[0].lower()
        if o != request.headers.get("host", "").split(":")[0].lower():
            return _reply(403, "Please submit from this website.")
    try:
        form = await request.form()
    except Exception:
        return _reply(400, "Unable to read the form. Please try again.")
    f = {k: (v if isinstance(v, str) else "") for k, v in form.items()}
    if f.get("company_url"):                                   # honeypot: a bot filled it
        return _reply(200, "Thank you.", reference="RECEIVED")
    if f.get("intent") != "buyer":
        return _reply(422, "Unknown inquiry type.")
    if f.get("contact_permission") != "yes":
        return _reply(422, "Please confirm you agree to be contacted about this inquiry.")
    try:
        org, _settings = _destination(db, request.headers.get("host", ""))
    except intake.IntakeRefused as exc:
        log.error("wholesale site intake refused: %s", exc.reason)
        return _reply(503, "Registration is temporarily unavailable. Please try again later.")
    payload = dict(f, source_url="https://%s/buyers.html" % request.headers.get("host", "").split(":")[0],
                   form_version="buyers-v1-hosted")
    try:
        data = buyers.validate(payload)
    except HTTPException as exc:
        errs = (exc.detail or {}).get("errors") if isinstance(exc.detail, dict) else None
        msg = next(iter(errs.values())) if errs else "Please check the form and try again."
        return _reply(422, msg, errors=errs or {})
    result = buyers.submit(db, org, data)
    return _reply(201, "Received.", reference=result["reference"])
