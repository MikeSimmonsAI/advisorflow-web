"""
Google Contacts Sync

Per Mike's explicit request: "I need the leads to sync into Google
Contacts so when I call someone from my phone, their name and number
are already there." Automatic, not advisor-triggered - his own words:
"if I upload a spreadsheet, those contacts need to be able to go into
Google Contacts too," with no separate review/click step.

REUSES the exact same per-advisor OAuth refresh-token pattern already
built for Google Calendar (see calendar_service.py) - same Google
account, same encrypted-refresh-token-on-User-record approach. This is
intentional, not duplicated logic: Calendar and Contacts are two
different Google APIs under the same OAuth umbrella, and an advisor
who's already connected Calendar should not need a second, separate
"Connect Contacts" flow - see SCOPES below, which now requests both.

IMPORTANT SCOPE CHANGE: the original Calendar-only OAuth client
requested scope "https://www.googleapis.com/auth/calendar.events" only.
Contacts access requires the additional
"https://www.googleapis.com/auth/contacts" scope. Any advisor who
already connected Google Calendar before this change will need to
RECONNECT once to grant the new scope - their existing refresh token
does not retroactively cover Contacts access. This is unavoidable; OAuth
scopes are granted at consent time, not expandable after the fact
without the user explicitly re-consenting.

DESIGN: sync is automatic and best-effort, never blocking. A lead must
always be created successfully whether or not Google sync works - if
the advisor hasn't connected Google yet, sync is silently skipped (not
an error); if the Google API call fails for any reason (rate limit,
network, expired token), that failure is logged but never raised back
to the caller. This mirrors the same defensive pattern already used for
notify_reply's email alert and the cadence job's per-organization error
isolation - elsewhere in this codebase, a secondary side-effect failing
never breaks the primary operation.
"""

import os
from sqlalchemy.orm import Session
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.models.models import User, Lead
from app.utils.crypto import decrypt_value

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

# Both Calendar and Contacts scopes requested together now, so a single
# Connect flow covers both - see calendar_service.py's get_oauth_flow,
# which imports SCOPES from here (this module is the single source of
# truth for what's requested, since Contacts sync is the reason the
# scope list grew).
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/contacts",
]


def _get_people_service(advisor: User):
    """
    Mirrors calendar_service.py's _get_calendar_service exactly, just
    against the People API (Google's actual API name for Contacts)
    instead of Calendar - same credential construction, same refresh
    token, same client.
    """
    if not advisor.google_oauth_refresh_token_encrypted:
        raise ValueError(f"Advisor {advisor.full_name} has not connected Google.")

    refresh_token = decrypt_value(advisor.google_oauth_refresh_token_encrypted)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    return build("people", "v1", credentials=creds)


def sync_lead_to_google_contacts(db: Session, lead: Lead) -> dict:
    """
    Creates (or finds and skips, if already synced) a Google Contact for
    one lead, on the assigned advisor's Google account.

    Returns {"success": bool, "skipped_reason": str|None, "error": str|None,
    "contact_resource_name": str|None} - same result-dict shape style as
    send_email_via_provider/send_plain_sms elsewhere in this codebase, so
    callers can check success consistently regardless of which side
    effect they're looking at.

    Deliberately does NOT raise on any failure - see module docstring.
    Callers (import_service.py, leads_router.py's manual-entry endpoint)
    should call this in a try/except anyway as a second layer of
    defense, but this function's own contract is "never raises."
    """
    advisor = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None

    if not advisor:
        return {"success": False, "skipped_reason": "Lead has no assigned advisor.", "error": None, "contact_resource_name": None}

    if not advisor.google_calendar_connected:
        # Reuses the existing google_calendar_connected flag rather than
        # adding a second "google_contacts_connected" flag - both
        # Calendar and Contacts are granted in the same consent screen
        # now (see SCOPES above), so there's only ever one real
        # connected/not-connected state to track per advisor, not two
        # that could drift out of sync with each other.
        return {"success": False, "skipped_reason": "Advisor has not connected Google yet.", "error": None, "contact_resource_name": None}

    if not lead.phone and not lead.email:
        return {"success": False, "skipped_reason": "Lead has no phone or email to sync.", "error": None, "contact_resource_name": None}

    if lead.google_contact_resource_name:
        return {"success": True, "skipped_reason": "Already synced.", "error": None, "contact_resource_name": lead.google_contact_resource_name}

    try:
        service = _get_people_service(advisor)
    except ValueError as e:
        return {"success": False, "skipped_reason": None, "error": str(e), "contact_resource_name": None}

    contact_body = {
        "names": [{"givenName": lead.first_name or "", "familyName": lead.last_name or ""}],
    }
    if lead.phone:
        contact_body["phoneNumbers"] = [{"value": lead.phone, "type": "mobile"}]
    if lead.email:
        contact_body["emailAddresses"] = [{"value": lead.email}]
    # Notes field makes it obvious where this contact came from, if the
    # advisor is ever looking at it directly in their phone/Gmail
    # contacts rather than through AdvisorFlow.
    contact_body["biographies"] = [{"value": "Added via AdvisorFlow", "contentType": "TEXT_PLAIN"}]

    try:
        created = service.people().createContact(body=contact_body).execute()
        resource_name = created.get("resourceName")
        lead.google_contact_resource_name = resource_name
        db.commit()
        return {"success": True, "skipped_reason": None, "error": None, "contact_resource_name": resource_name}
    except Exception as e:
        return {"success": False, "skipped_reason": None, "error": str(e), "contact_resource_name": None}


def sync_leads_to_google_contacts_batch(db: Session, leads: list[Lead]) -> dict:
    """
    Syncs a batch of leads (e.g. right after a bulk Excel import) - calls
    sync_lead_to_google_contacts per lead, in a plain loop rather than
    anything more elaborate, since the People API has no batch-create
    endpoint for contacts the way some Google APIs do.

    Each lead's sync is independently wrapped, so one failure (rate
    limit, a specific lead's data being malformed) never stops the rest
    of the batch from attempting - mirrors the same per-item isolation
    pattern as the cadence job's per-organization error handling.
    """
    succeeded, skipped, failed = 0, 0, 0
    errors = []

    for lead in leads:
        try:
            result = sync_lead_to_google_contacts(db, lead)
        except Exception as e:
            failed += 1
            errors.append(f"Lead {lead.id}: unexpected error - {e}")
            continue

        if result["success"]:
            succeeded += 1
        elif result["skipped_reason"]:
            skipped += 1
        else:
            failed += 1
            if result["error"]:
                errors.append(f"Lead {lead.id}: {result['error']}")

    return {"succeeded": succeeded, "skipped": skipped, "failed": failed, "errors": errors}
