"""The public website's server-to-server intake.

WHY THIS EXISTS RATHER THAN A CHANGE TO THE MARKETING SITE.

The EvoSys Pro V8 site posts its forms with PHP, server-side, through one
helper: `evosys_webhook($key, $payload)` reads a URL from a protected config
file and POSTs the form's own answers as JSON. That is already the shape this
platform wants - the browser never learns an AdvisorFlow URL, never carries a
credential, and never names an organization. What was missing was somewhere
for it to post.

So the adapter is here, on the side that can be tested and deployed, and the
site's only change is a URL in a config file it already reads. The alternative
- rewriting three PHP files to speak this platform's schema - would have meant
editing a locked, approved website whose deployed revision cannot be rebuilt
from any source bundle on hand, to fix a problem that is not the website's.

WHAT THE URL PATH SAYS, AND WHAT IT DOES NOT GRANT.

`{platform_slug}` NAMES A BRAND. It is not authorization and it is not a
destination: `public_intake.resolve_public_intake` takes the name and looks up
what an operator CONFIGURED for that brand, then verifies the organization
exists, is active, and belongs to that brand. A caller inventing a slug gets
the same neutral refusal as a caller naming a brand nobody has configured. No
organization id appears in the path, in the payload, or anywhere a browser
could reach.

WHAT A PUBLIC CALLER IS TOLD WHEN SOMETHING IS WRONG: that we cannot take the
submission right now. Not which brands exist, not which are configured, not
why. The operator gets the reason in the log.

NOTHING HERE SENDS ANYTHING. A submission creates or updates one record and
stops. No cadence is started, no SMS or email goes to the person, no AI is
woken. See public_capture.py.
"""

# NO `from __future__ import annotations` HERE. slowapi's rate-limit decorator
# wraps the handler, and FastAPI then resolves the handler's annotations
# against the WRAPPER's module globals - where `SitePayload` does not exist.
# With postponed evaluation the body model becomes an unresolvable string and
# the app fails to import. Real annotation objects survive the wrapping.
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.services import public_capture as pc
from app.services import public_intake
from app.services.dedup_service import normalize_phone

log = logging.getLogger(__name__)

router = APIRouter(prefix="/site-intake", tags=["site-intake"])

# ONE NEUTRAL REFUSAL, USED FOR EVERY REASON A DESTINATION CANNOT BE RESOLVED.
# An unknown brand, an unconfigured brand, a dangling destination and a
# cross-brand misconfiguration are all the same sentence to an anonymous
# poster, because any difference between them is a map of the platform's
# configuration handed out for free.
_REFUSED = "We can't accept this submission right now. Please email us."


class SitePayload(BaseModel):
    """Whatever the marketing form sent.

    `extra="allow"` is deliberate. A marketing site's form fields change
    without anyone telling this platform, and a 422 on an unrecognised field
    would turn a copy edit on a website into a silently lost prospect. Unknown
    keys are kept verbatim on the record rather than rejected or dropped.
    """
    model_config = ConfigDict(extra="allow")

    # The name as the form asked for it, in either shape.
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None

    # What the person typed. V8 calls it `goals` on the demo form and
    # `message` on the support form; both mean the same thing here.
    message: Optional[str] = None
    goals: Optional[str] = None

    # Consent, in either the demo form's shape (`sms_consent` plus
    # `sms_consent_text`) or the opt-in page's (`consent`, `consent_text`,
    # `consent_version`).
    consent: Optional[Any] = None
    consent_text: Optional[str] = None
    consent_version: Optional[str] = None
    sms_consent: Optional[Any] = None
    sms_consent_text: Optional[str] = None

    # Provenance the site already collects.
    source_url: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    submitted_at: Optional[str] = None

    # The visitor's own IP and browser as the SITE saw them. The request this
    # platform receives comes from the web server, not the visitor, so its own
    # client address is the wrong evidence to file.
    ip: Optional[str] = None
    user_agent: Optional[str] = None


# Keys that have a home of their own above and must not be duplicated into the
# free-form extras.
_CLAIMED = {
    "name", "first_name", "last_name", "email", "phone", "company", "industry",
    "message", "goals", "consent", "consent_text", "consent_version",
    "sms_consent", "sms_consent_text", "source_url", "page_url", "referrer",
    "submitted_at", "ip", "user_agent",
}

_TRUE = {"1", "true", "yes", "y", "on", "checked"}


def _truthy(value: Any) -> bool:
    """A checkbox, however the form spelled it.

    V8's demo form sends the string "YES" or "NO"; its opt-in page sends
    "YES"; a JSON caller sends a boolean. Anything not recognisably a yes is a
    no - consent is never inferred from an unparseable value.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def _parse_when(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _extras(payload: SitePayload) -> tuple[dict, dict]:
    """(form answers, utm parameters) - everything with no column of its own."""
    extra, utm = {}, {}
    for key, value in (payload.model_extra or {}).items():
        if key in _CLAIMED or value in (None, ""):
            continue
        if key.lower().startswith("utm_") or key.lower() in ("gclid", "fbclid"):
            utm[key.lower()] = value
        else:
            extra[key] = value
    return extra, utm


def _names(payload: SitePayload) -> tuple[str, Optional[str]]:
    first = (payload.first_name or "").strip()
    last = (payload.last_name or "").strip() or None
    if first:
        return first, last
    return pc.split_name(payload.name)


def _destination(db: Session, platform_slug: str, request: Request):
    """The configured brand and organization, or a neutral 503."""
    try:
        return public_intake.resolve_public_intake(
            db, platform_slug=platform_slug,
            origin=request.headers.get("origin") or request.headers.get("referer"))
    except public_intake.IntakeDestinationError as exc:
        log.error("site-intake refused for slug=%r: %s", platform_slug, exc.reason)
        raise HTTPException(status_code=503, detail=_REFUSED)


def _consent(payload: SitePayload, *, required: bool) -> Optional[pc.Consent]:
    """The consent block, exactly as the site presented it.

    `required` is the opt-in page, where the checkbox IS the submission.
    Elsewhere the box is optional and an unticked one is recorded as a plain
    "no" rather than as a missing answer.
    """
    raw = payload.consent if payload.consent is not None else payload.sms_consent
    given = _truthy(raw)
    if not given and not required and raw is None:
        return None
    return pc.Consent(
        given=given,
        # VERBATIM OR NOT AT ALL. The wording is the evidence; a paraphrase of
        # it proves nothing, so an absent one stays absent.
        text=(payload.consent_text or payload.sms_consent_text or None),
        version=payload.consent_version,
        ip=payload.ip,
        user_agent=payload.user_agent,
        page_url=payload.source_url or payload.page_url,
        at=_parse_when(payload.submitted_at),
    )


def _submission(payload: SitePayload, kind: str,
                consent: Optional[pc.Consent]) -> pc.Submission:
    first, last = _names(payload)
    extra, utm = _extras(payload)
    return pc.Submission(
        kind=kind,
        first_name=first,
        last_name=last,
        email=(payload.email or None),
        phone=(payload.phone or None),
        company=(payload.company or None),
        industry=(payload.industry or None),
        message=(payload.message or payload.goals or None),
        page_url=(payload.page_url or payload.source_url or None),
        referrer=payload.referrer,
        utm=utm,
        extra=extra,
        consent=consent,
        ip=payload.ip,
        user_agent=payload.user_agent,
        submitted_at=_parse_when(payload.submitted_at),
    )


def _require_reachable(payload: SitePayload) -> None:
    """A submission nobody can reply to is not a lead, it is a row.

    Refused at the door rather than stored, so a broken form shows up as a
    failing submission the site can see instead of as a workspace filling with
    unreachable records.
    """
    if not (payload.email or "").strip() and not normalize_phone(payload.phone or ""):
        raise HTTPException(
            status_code=422,
            detail="A valid email address or phone number is required.")


@router.post("/{platform_slug}/demo-request", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def site_demo_request(platform_slug: str, payload: SitePayload,
                      request: Request, db: Session = Depends(get_db)):
    """A demo request from a brand's marketing site. Sales intent.

    The optional SMS consent checkbox on this form is recorded as consent
    evidence, but it does not change what the record is: somebody asking for a
    demo has asked to be sold to, and somebody ticking the box has separately
    agreed to be texted. Neither implies the other.
    """
    platform, org = _destination(db, platform_slug, request)
    _require_reachable(payload)
    first, _ = _names(payload)
    if not first:
        raise HTTPException(status_code=422, detail="A name is required.")

    sub = _submission(payload, pc.KIND_DEMO, _consent(payload, required=False))
    result = pc.capture(db, platform=platform, org=org, sub=sub)
    return {"success": True, "action": result["action"],
            "lead_id": result["lead_id"]}


@router.post("/{platform_slug}/sms-optin", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def site_sms_optin(platform_slug: str, payload: SitePayload,
                   request: Request, db: Session = Depends(get_db)):
    """An SMS opt-in. A CONSENT EVENT, NOT A SALES LEAD.

    This establishes messaging eligibility and files the evidence for it. It
    does not set a sales tier, does not set a message track, and does not put
    anybody into a cadence - somebody who agreed to appointment reminders has
    not asked to be sold to, and a platform that reads the two as the same
    thing will start selling on the strength of a compliance checkbox.
    """
    platform, org = _destination(db, platform_slug, request)

    phone = normalize_phone(payload.phone or "")
    if not phone:
        raise HTTPException(status_code=422,
                            detail="A valid mobile phone number is required.")
    consent = _consent(payload, required=True)
    if consent is None or not consent.given:
        raise HTTPException(status_code=400, detail="SMS consent is required.")

    # THE DO-NOT-CONTACT LIST OUTRANKS A NEW OPT-IN FORM. Somebody on it asked
    # to be left alone through a channel this platform trusts more than an
    # anonymous web form claiming otherwise.
    try:
        from app.services.compliance_service import is_phone_suppressed
        if is_phone_suppressed(db, org.id, phone):
            raise HTTPException(
                status_code=409,
                detail="This number is on the do-not-contact list.")
    except HTTPException:
        raise
    except Exception:
        log.exception("suppression check failed during site sms-optin")

    sub = _submission(payload, pc.KIND_SMS_OPTIN, consent)
    result = pc.capture(db, platform=platform, org=org, sub=sub)
    return {"success": True, "action": result["action"],
            "lead_id": result["lead_id"]}


@router.post("/{platform_slug}/waitlist", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def site_waitlist(platform_slug: str, payload: SitePayload,
                  request: Request, db: Session = Depends(get_db)):
    """A waitlist signup. Interest, recorded; not a demo request.

    Kept distinct from the demo request by `source_detail` alone, which is the
    whole point of that column: the two arrive through the same architecture,
    land in the same workspace, and mean different things to whoever opens the
    record.
    """
    platform, org = _destination(db, platform_slug, request)
    _require_reachable(payload)

    sub = _submission(payload, pc.KIND_WAITLIST, _consent(payload, required=False))
    result = pc.capture(db, platform=platform, org=org, sub=sub)
    return {"success": True, "action": result["action"],
            "lead_id": result["lead_id"]}
