"""
Google Contacts Service

Handles two-way sync between the app and Google Contacts:
1. Export a lead TO Google Contacts (so advisors have them on their phone)
2. Import contacts FROM Google Contacts (bulk pull into the app as leads)

Uses the same Google OAuth refresh token already stored from the Calendar
connection - but requires the contacts scope to also have been granted.
If the scope wasn't granted, we direct the user to reconnect with the
additional scope.
"""

import os
import requests
from sqlalchemy.orm import Session
from app.models.models import User, Lead
from app.services.calendar_service import _decrypt_token, get_google_flow


CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts"
PEOPLE_API_BASE = "https://people.googleapis.com/v1"


def _get_access_token(user: User) -> str:
    """
    Gets a fresh Google access token using the stored refresh token.
    Raises ValueError if the user hasn't connected Google or the token
    doesn't have the contacts scope.
    """
    if not user.google_calendar_connected or not user.google_oauth_refresh_token_encrypted:
        raise ValueError("Google account not connected. Please connect Google in Settings first.")

    refresh_token = _decrypt_token(user.google_oauth_refresh_token_encrypted)

    # Exchange refresh token for access token
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })

    if not resp.ok:
        raise ValueError(f"Failed to refresh Google token: {resp.text}")

    data = resp.json()
    if "access_token" not in data:
        raise ValueError("No access token returned from Google.")

    return data["access_token"]


def push_lead_to_google_contacts(db: Session, user: User, lead: Lead) -> dict:
    """
    Creates or updates a Google Contact for this lead.
    Returns the created/updated contact resource name.
    """
    access_token = _get_access_token(user)

    # Build the contact payload
    contact_body = {
        "names": [{"givenName": lead.first_name or "", "familyName": lead.last_name or ""}],
    }

    if lead.phone:
        contact_body["phoneNumbers"] = [{"value": lead.phone, "type": "mobile"}]

    if lead.email:
        contact_body["emailAddresses"] = [{"value": lead.email, "type": "home"}]

    if lead.tier:
        contact_body["biographies"] = [{
            "value": f"BookaBoost Lead | Tier: {lead.tier} | Status: {lead.status.value if lead.status else 'new'}",
            "contentType": "TEXT_PLAIN"
        }]

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    resp = requests.post(
        f"{PEOPLE_API_BASE}/people:createContact",
        json=contact_body,
        headers=headers,
    )

    if not resp.ok:
        if resp.status_code == 403:
            raise ValueError(
                "Google Contacts permission not granted. Please reconnect Google in Settings "
                "to allow contact access."
            )
        raise ValueError(f"Failed to create Google Contact: {resp.text}")

    return resp.json()


def pull_google_contacts(user: User, max_results: int = 500) -> list[dict]:
    """
    Pulls contacts from Google Contacts and returns them in the same
    format as parse_excel_file rows, so they can be fed straight into
    import_leads_from_excel's processing logic.
    """
    access_token = _get_access_token(user)

    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(
        f"{PEOPLE_API_BASE}/people/me/connections",
        params={
            "personFields": "names,phoneNumbers,emailAddresses",
            "pageSize": max_results,
        },
        headers=headers,
    )

    if not resp.ok:
        if resp.status_code == 403:
            raise ValueError(
                "Google Contacts permission not granted. Please reconnect Google in Settings "
                "to allow contact access."
            )
        raise ValueError(f"Failed to fetch Google Contacts: {resp.text}")

    data = resp.json()
    connections = data.get("connections", [])

    rows = []
    for person in connections:
        names = person.get("names", [{}])
        phones = person.get("phoneNumbers", [{}])
        emails = person.get("emailAddresses", [{}])

        first_name = names[0].get("givenName", "") if names else ""
        last_name = names[0].get("familyName", "") if names else ""
        phone = phones[0].get("value", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "") if phones else ""
        email = emails[0].get("value", "") if emails else ""

        if not last_name and not phone and not email:
            continue  # skip contacts with no usable data

        rows.append({
            "first_name": first_name,
            "last_name": last_name,
            "phone": phone,
            "email": email,
            "tier_raw": "",
            "status_reason_raw": "",
            "allow_calls_raw": "",
            "last_action_raw": "",
            "last_contact_date_raw": "",
            "source_raw": "google_contacts",
        })

    return rows
