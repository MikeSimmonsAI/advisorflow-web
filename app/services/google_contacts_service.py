"""
Google Contacts Service

Handles two-way sync between the app and Google Contacts:
1. Export a lead TO Google Contacts (so advisors have them on their phone)
2. Read contacts FROM Google Contacts. They are NOT written as leads here:
   `google_contacts_csv` turns them into a file that goes through Universal
   Intake (stage -> map -> analyze -> decide), the one canonical importer.

Uses the same Google OAuth refresh token already stored from the Calendar
connection. Requires the contacts scope to have been granted during OAuth.
"""

import csv
import io
import os
import requests
from app.utils.crypto import decrypt_value


PEOPLE_API_BASE = "https://people.googleapis.com/v1"


def _get_access_token(user) -> str:
    """
    Gets a fresh Google access token using the stored refresh token.
    Raises ValueError if the user hasn't connected Google.
    """
    if not user.google_calendar_connected or not user.google_oauth_refresh_token_encrypted:
        raise ValueError("Google account not connected. Please connect Google in Settings first.")

    refresh_token = decrypt_value(user.google_oauth_refresh_token_encrypted)

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


def push_lead_to_google_contacts(db, user, lead) -> dict:
    """
    Creates a Google Contact for this lead in the advisor's Google account.
    """
    access_token = _get_access_token(user)

    contact_body = {
        "names": [{"givenName": lead.first_name or "", "familyName": lead.last_name or ""}],
    }

    if lead.phone:
        contact_body["phoneNumbers"] = [{"value": lead.phone, "type": "mobile"}]

    if lead.email:
        contact_body["emailAddresses"] = [{"value": lead.email, "type": "home"}]

    if lead.tier:
        contact_body["biographies"] = [{
            "value": f"Lead | Tier: {lead.tier} | Status: {lead.status if lead.status else 'new'}",
            "contentType": "TEXT_PLAIN"
        }]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        f"{PEOPLE_API_BASE}/people:createContact",
        json=contact_body,
        headers=headers,
    )

    if not resp.ok:
        if resp.status_code == 403:
            raise ValueError(
                "Google Contacts permission not granted. Please reconnect Google in Settings "
                "and allow contact access when prompted."
            )
        raise ValueError(f"Failed to create Google Contact: {resp.text}")

    return resp.json()


def pull_google_contacts(user, max_results: int = 500) -> list[dict]:
    """
    Pulls contacts from Google Contacts and returns them as rows ready
    for import_leads_from_rows.
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
                "and allow contact access when prompted."
            )
        raise ValueError(f"Failed to fetch Google Contacts: {resp.text}")

    connections = resp.json().get("connections", [])

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
            continue

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


# ── Universal Intake source ─────────────────────────────────────────────────
# Google Contacts is a SOURCE CONNECTOR of the canonical importer, never an
# importer of its own. Everything below only READS Google and produces a file;
# matching, dedupe, classification, consent-neutral eligibility, commit and
# rollback all happen in `app.services.intake`.

GOOGLE_CSV_HEADERS = ["Source Record ID", "First Name", "Last Name", "Company", "Job Title",
                      "Email", "Phone", "Mobile Phone", "Google Phone Label", "Notes"]
GOOGLE_MAX_CONTACTS = 25000


def _digits(v: str) -> str:
    return "".join(ch for ch in (v or "") if ch.isdigit() or ch == "+")


def fetch_google_people(user, *, page_size: int = 1000, limit: int = GOOGLE_MAX_CONTACTS,
                        session=None) -> list:
    """Every connection of the signed-in user (paged). Read-only."""
    http = session or requests
    access_token = _get_access_token(user)
    headers = {"Authorization": f"Bearer {access_token}"}
    people, token = [], None
    while True:
        params = {"personFields": "names,phoneNumbers,emailAddresses,organizations,biographies",
                  "pageSize": min(page_size, 1000)}
        if token:
            params["pageToken"] = token
        resp = http.get(f"{PEOPLE_API_BASE}/people/me/connections", params=params,
                        headers=headers, timeout=30)
        if not resp.ok:
            if resp.status_code == 403:
                raise ValueError("Google Contacts permission not granted. Please reconnect Google in "
                                 "Settings and allow contact access when prompted.")
            raise ValueError("Google Contacts could not be read (HTTP %s)." % resp.status_code)
        data = resp.json() or {}
        people.extend(data.get("connections") or [])
        token = data.get("nextPageToken")
        if not token or len(people) >= limit:
            return people[:limit]


def google_people_rows(people: list) -> list:
    """One row per person, in the Universal Intake column vocabulary. The
    Google phone label ("mobile", "home") is what the owner typed into Google:
    it is kept as SOURCE DATA and never treated as a carrier line type."""
    rows = []
    for p in people or []:
        name = (p.get("names") or [{}])[0]
        phones = p.get("phoneNumbers") or []
        emails = p.get("emailAddresses") or []
        org = (p.get("organizations") or [{}])[0]
        first, last = (name.get("givenName") or "").strip(), (name.get("familyName") or "").strip()
        full = (name.get("displayName") or "").strip()
        if not (first or last) and full:
            first = full
        phone = _digits((phones[0] if phones else {}).get("value", ""))
        mobile = next((_digits(x.get("value", "")) for x in phones
                       if (x.get("type") or "").lower() == "mobile"), "")
        email = ((emails[0] if emails else {}).get("value") or "").strip()
        if not (phone or email or first or last):
            continue
        rows.append({
            "Source Record ID": p.get("resourceName") or "",
            "First Name": first, "Last Name": last,
            "Company": (org.get("name") or "").strip(), "Job Title": (org.get("title") or "").strip(),
            "Email": email, "Phone": phone, "Mobile Phone": mobile,
            "Google Phone Label": ((phones[0] if phones else {}).get("type") or ""),
            "Notes": ((p.get("biographies") or [{}])[0].get("value") or "").strip()[:1000],
        })
    return rows


def rows_to_csv(rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=GOOGLE_CSV_HEADERS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def create_intake_batch_from_google(db, user, ctx, *, list_name=None, people=None):
    """Read the user's Google Contacts and stage them as ONE Universal Intake
    batch (status: mapping). Nothing becomes a contact or a lead until the
    operator runs analysis and commits, exactly like an uploaded file."""
    from datetime import datetime as _dt
    from app.services.intake import engine as ENG
    rows = google_people_rows(people if people is not None else fetch_google_people(user))
    if not rows:
        raise ENG.IntakeError("No contacts with a name, phone or email were found in Google Contacts.")
    stamp = _dt.utcnow().strftime("%Y-%m-%d")
    who = getattr(user, "email", None) or "connected Google account"
    return ENG.create_batch(
        db, ctx, content=rows_to_csv(rows), filename="google-contacts-%s.csv" % stamp,
        source="google_contacts", source_detail="Google Contacts of %s" % who,
        list_name=list_name, display_name=list_name or "Google Contacts %s" % stamp)
