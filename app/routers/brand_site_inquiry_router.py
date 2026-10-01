"""A brand website's two-journey inquiry, through the EXISTING public intake.

WHY A NEW ROUTE AND NOT ONE OF THE FOUR IN site_intake_router.py.

The Max Life public site has two materially different visitors - a family or
individual asking for a planning conversation, and an agent / builder asking
about the opportunity - and neither fits an existing form kind honestly:

  * `demo-request` records "Request Demo" and sends EvoSys Pro-branded demo
    acknowledgement email. A family asking about life insurance has not asked
    for a software demo, and must not be told it did.
  * `waitlist` records "Waitlist". Neither visitor joined a waitlist.

So this is a thin ADAPTER, not a pipeline. Destination resolution, payload
parsing, the reachability rule, the consent block, find-or-create, provenance,
master-contact retention and the shared rate ceiling are all the existing code
in site_intake_router.py and public_capture.py, called as-is. The only thing
decided here is the honest `source_detail` label for the journey.

WHAT IT DELIBERATELY DOES NOT DO.

  * No sales tier, no message track, no cadence, no SMS, no email, no AI.
    The kind used here is not in public_capture.SALES_INTENT, so capture()
    leaves tier/message_track alone; qualification and routing happen in the
    workspace, by a person or the workspace's own rules.
  * Consent is never inferred. An unticked box is filed as a plain "no" in the
    record's answers and leaves every consent column exactly as it was.
  * Contact creation never depends on consent.
  * No organization id, brand name or phone number is named in this file. The
    brand slug in the path is resolved against operator configuration
    (Platform.public_intake_organization_id) exactly as every other public
    intake route does, with the same neutral refusal.
"""

# No `from __future__ import annotations` - see site_intake_router.py: slowapi
# wraps the handler and FastAPI must resolve real annotation objects.
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.routers import site_intake_router as sir
from app.services import public_capture as pc
from app.services import public_intake

log = logging.getLogger(__name__)

router = APIRouter(prefix="/site-intake", tags=["site-intake"])

# Journey -> the label written to Lead.source_detail. A fixed whitelist: the
# browser chooses which of these it is, never what the label says.
JOURNEYS = {
    "family": "Website Inquiry - Families & Individuals",
    "builder": "Website Inquiry - Agents & Builders",
}


@router.post("/{platform_slug}/inquiry", status_code=201)
@limiter.shared_limit(public_intake.PUBLIC_INTAKE_LIMIT,
                      scope=public_intake.PUBLIC_INTAKE_SCOPE)
def site_inquiry(platform_slug: str, payload: sir.SitePayload,
                 request: Request, db: Session = Depends(get_db)):
    """A journey-specific inquiry from a brand's public website."""
    platform, org = sir._destination(db, platform_slug, request)

    journey = str((payload.model_extra or {}).get("journey") or "").strip().lower()
    label = JOURNEYS.get(journey)
    if label is None:
        raise HTTPException(status_code=422,
                            detail="Choose which path this inquiry is for.")

    sir._require_reachable(payload)
    first, _ = sir._names(payload)
    if not first:
        raise HTTPException(status_code=422, detail="A name is required.")

    # The kind IS the label: public_capture falls back to the kind for
    # source_detail and to "Website Submission" for the note heading, and
    # treats any kind outside SALES_INTENT as carrying no sales intent.
    consent = sir._consent(payload, required=False)
    sub = sir._submission(payload, label, consent)
    # Record the answer to the optional box explicitly, ticked or not, so the
    # record shows "no" rather than silence when it was left unticked.
    sub.extra["sms_consent_answer"] = "yes" if (consent and consent.given) else "no"

    result = pc.capture(db, platform=platform, org=org, sub=sub)
    return {"success": True, "action": result["action"],
            "lead_id": result["lead_id"]}
