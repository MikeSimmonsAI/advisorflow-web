"""
Advisor-facing system health status.

Rebuilt per Mike's explicit feedback: the original version only ever
showed a green checkmark or a generic "not connected" with no reason and
no way to act on it. This version adds, for each integration: WHY it's
disconnected (not just that it is), and a settings_path the frontend uses
to deep-link straight to the fix - no more "looks broken, nothing to do
about it."

DELIBERATELY NOT INCLUDED: a "recent send failures" log. Investigated
during this rebuild and confirmed there is currently no actual failure
data to show - Twilio send errors that occur at the API-call level
(bad number, no balance, auth failure) raise an uncaught exception with
no Message row ever created, and there's no Twilio status-callback
webhook to catch async delivery failures after Twilio accepts a message
either. Showing a "recent failures" panel right now would always be
empty, which would look broken in a different way. This needs its own
follow-up to add real error logging on the send path - not done here so
the SMS send flow doesn't get touched mid-Twilio-approval, while that
A2P 10DLC campaign is still pending.
"""

from datetime import datetime
from typing import Optional
import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_tenant_user
from app.models.models import User
from app.services.capabilities import require_capability
from app.services import lead_scope

router = APIRouter(prefix="/health", tags=["health"])


class IntegrationStatus(BaseModel):
    key: str
    title: str
    connected: bool
    reason: Optional[str] = None  # populated only when connected=False - why, in plain language
    settings_path: str  # where the frontend should link to fix this


class AdvisorHealthStatus(BaseModel):
    # Kept for backward compatibility with anything still reading the flat
    # booleans directly, but the frontend should prefer `integrations` below.
    twilio_connected: bool
    google_calendar_connected: bool
    microsoft_365_connected: bool
    last_cadence_run: Optional[datetime] = None

    integrations: list[IntegrationStatus]


def _get_last_cadence_run(db: Session, user: User) -> Optional[datetime]:
    """
    Return the last cadence-job timestamp if the project has explicit run tracking.

    Current AdvisorFlow only tracks per-lead CadenceState timestamps
    (next_touch_due_at, last_touch_sent_at, completed_at). It does not have a
    dedicated job-run ledger/table, and Task 4 explicitly says not to invent one.
    Therefore this returns None until a future scheduler-run table is added.
    """
    return None


def _twilio_status(user: User) -> IntegrationStatus:
    """
    A working send needs all three: twilio_account_sid + auth_token (used
    by get_twilio_client to build the API client) AND twilio_phone_number
    (used as the `from_` field in send_sms). Missing any one of the three
    means sends will fail, so all three are checked, not just the account
    SID like the original version did.
    """
    has_sid = bool(user.twilio_account_sid)
    has_token = bool(user.twilio_auth_token_encrypted)
    has_number = bool(user.twilio_phone_number)

    if has_sid and has_token and has_number:
        return IntegrationStatus(
            key="twilio", title="Twilio SMS", connected=True, settings_path="/settings#twilio",
        )

    missing = []
    if not has_sid:
        missing.append("account SID")
    if not has_token:
        missing.append("auth token")
    if not has_number:
        missing.append("phone number")
    reason = f"Missing {', '.join(missing)} - SMS sends will fail until this is configured."
    return IntegrationStatus(key="twilio", title="Twilio SMS", connected=False, reason=reason, settings_path="/settings#twilio")


def _google_calendar_status(user: User) -> IntegrationStatus:
    if user.google_calendar_connected:
        return IntegrationStatus(
            key="google_calendar", title="Google Calendar", connected=True, settings_path="/settings#google",
        )
    reason = "Google Calendar hasn't been connected yet - bookings won't sync to your calendar until this is done."
    return IntegrationStatus(key="google_calendar", title="Google Calendar", connected=False, reason=reason, settings_path="/settings#google")


def _microsoft_365_status(user: User) -> IntegrationStatus:
    if user.microsoft_365_connected:
        return IntegrationStatus(
            key="microsoft_365", title="Microsoft 365 Email", connected=True, settings_path="/settings#microsoft",
        )
    reason = "Microsoft 365 isn't connected - outbound email will use a generic sender instead of your real mailbox until this is connected."
    return IntegrationStatus(key="microsoft_365", title="Microsoft 365 Email", connected=False, reason=reason, settings_path="/settings#microsoft")


def _ai_features_status() -> IntegrationStatus:
    """
    Org-wide, not per-advisor - OPENAI_API_KEY is a single environment
    variable shared by every advisor account, not something stored on the
    User record.

    IMPORTANT LIMITATION: this only checks whether a key is PRESENT, not
    whether it's actually working. A configured key that's hitting OpenAI
    rate limits or billing holds (the exact situation Mike has hit, where
    billing hasn't been added at platform.openai.com yet) will still show
    as connected=True here, because there's no live API call made to
    verify it - that would mean spending a real API call just to render
    this status page. The per-feature error messages (e.g. Templates'
    "AI request failed: ...") are the accurate signal for that failure
    mode; this check only catches the "nobody set a key at all" case.

    settings_path intentionally points back at /system-health itself, not
    a settings page - there is no in-app UI for this since it's a
    deployment-level environment variable, not a per-user setting. The
    actual fix is checking the Render environment config / OpenAI billing
    directly, not clicking a button in the app.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return IntegrationStatus(
            key="ai_features", title="AI Features (templates, reply drafts, classification)",
            connected=True, settings_path="/system-health",
        )
    reason = "No OpenAI API key is configured at all. AI template writing, reply drafting, and reply classification fall back to plain/keyword-based behavior until a key is set. Note: a key that IS set but rate-limited or unpaid will still show as connected here - check individual AI features directly for that kind of failure."
    return IntegrationStatus(
        key="ai_features", title="AI Features (templates, reply drafts, classification)",
        connected=False, reason=reason, settings_path="/system-health",
    )


class MySetupStatus(BaseModel):
    """Deliberately NOT AdvisorHealthStatus.

    Reusing that model is how a caller-scoped page grows platform fields back:
    the moment a platform item is added to the shared model it appears here too,
    silently. A separate model means adding a platform fact to this response is
    an edit somebody has to make on purpose.
    """
    integrations: list[IntegrationStatus]
    all_ready: bool


def _messaging_status(db: Session, user: User) -> IntegrationStatus:
    """Can THIS caller's messages actually go out - and nothing else.

    Reports booleans and plain language only. It never returns an Account SID,
    an auth token, a messaging service SID or any fragment of one, because the
    answer the advisor needs is "yes" or "no, ask your administrator", and a
    last-four is still a credential detail an advisor has no business holding.

    It reflects the REAL resolution order in sms_service: the organization's
    credentials first, the caller's own only as a fallback. Checking just the
    user's columns would tell an advisor in a properly configured Restland that
    their SMS is not connected, which is the same class of lie as showing
    CONNECTED for something unverified - it is just pointed the other way.
    """
    org = None
    org_id = lead_scope.active_workspace_org_id(user)
    if org_id:
        from app.models.models import Organization
        org = db.query(Organization).filter(Organization.id == org_id).first()

    org_creds = bool(org and org.org_twilio_account_sid
                     and org.org_twilio_auth_token_encrypted)
    org_sender = bool(org and (org.org_twilio_phone_number
                               or org.twilio_messaging_service_sid))
    own_creds = bool(user.twilio_account_sid and user.twilio_auth_token_encrypted
                     and user.twilio_phone_number)

    if (org_creds and org_sender) or own_creds:
        return IntegrationStatus(
            key="messaging", title="Text messaging", connected=True,
            settings_path="/settings",
        )

    if org_creds and not org_sender:
        reason = ("Your organization's messaging account is set up but has no "
                  "sending number yet. An administrator needs to finish this - "
                  "your texts will not go out until they do.")
    else:
        reason = ("Text messaging is not set up for your organization yet. An "
                  "administrator needs to configure it - your texts will not go "
                  "out until they do.")
    return IntegrationStatus(key="messaging", title="Text messaging",
                             connected=False, reason=reason,
                             settings_path="/settings")


@router.get("/my-setup", response_model=MySetupStatus)
def my_setup(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
) -> MySetupStatus:
    """IS MY OWN SETUP WORKING - the advisor's question, and only that.

    /advisor-status is God-only by default because System Health reads as
    platform operations. That was right, and it took with it the one thing an
    advisor legitimately needs: whether their own texting and calendar work.
    This is that thing, given its own endpoint rather than reopening the page.

    WHAT IT WILL NOT ANSWER, and why each is excluded rather than filtered:

      - the platform OpenAI key. /advisor-status reports whether the
        deployment has one at all. That is a fact about the platform's
        environment, identical for every customer, and no customer's advisor
        is entitled to it.
      - the cadence scheduler's last run. A platform job, not the caller's.
      - any credential value, including a last-four.
      - anything about another user, or about the organization beyond the one
        boolean "your messaging is or is not configured", which the caller
        needs in order to know whether to ask an administrator.

    No capability gate: every authenticated TENANT user may ask about their own
    setup. That is the whole point - an advisor should not need
    `platform_health`, and granting it to make this page work would hand them
    the platform view to fix a personal one.

    `require_tenant_user`, not `get_current_user`: I wrote this with the looser
    dependency first and the gate caught it. A brand-sales identity with no
    customer workspace got a cheerful 200 saying "text messaging is not set up
    for your organization" - a statement about an organization they are not in,
    and the two-context rule says platform access is not workspace access. They
    are refused here rather than answered inaccurately.
    """
    items = [
        _messaging_status(db, current_user),
        _google_calendar_status(current_user),
        _microsoft_365_status(current_user),
    ]
    return MySetupStatus(
        integrations=items,
        all_ready=all(i.connected for i in items),
    )


@router.get("/advisor-status", response_model=AdvisorHealthStatus)
def advisor_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _cap: User = Depends(require_capability("platform_health")),
) -> AdvisorHealthStatus:
    """SYSTEM HEALTH IS NOW GOD-ONLY BY DEFAULT.

    This endpoint returns only the CALLER's own integration flags, so the data
    exposure was never the problem. The problem was the surface: the sidebar
    carried "System Health" under Administration with `adminOnly` and no feature
    key at all, so no entitlement could switch it off and every org_admin on the
    platform had a page that reads as platform operations.

    `platform_health` is registered as DELEGABLE - so an organization can be
    given its own health view by delegating it and naming an administrator,
    with no code change - but nothing is delegated by default, which is what
    "God-only by default" means here.

    WORTH KNOWING: this closes the advisor's own "why is my Twilio not
    connected" page, which is what the module docstring above says this endpoint
    was rebuilt to provide. That path was already narrowing - advisors can no
    longer write their own Twilio credentials either - so the two changes agree
    rather than conflict. If self-service integration status should come back
    for staff, it wants its own small endpoint scoped to the caller, not this
    page reopened.
    """
    twilio = _twilio_status(current_user)
    google = _google_calendar_status(current_user)
    microsoft = _microsoft_365_status(current_user)
    ai = _ai_features_status()

    return AdvisorHealthStatus(
        twilio_connected=twilio.connected,
        google_calendar_connected=google.connected,
        microsoft_365_connected=microsoft.connected,
        last_cadence_run=_get_last_cadence_run(db, current_user),
        integrations=[twilio, google, microsoft, ai],
    )
