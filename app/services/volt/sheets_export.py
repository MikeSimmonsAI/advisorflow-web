"""Google Sheets mirror of VOLT records (never the source of truth).

Two businesses, two spreadsheets, two fixed tab layouts:
  recruiting -> "Applications"  (one row per APPLICATION id; a person may have several)
  consumer   -> "Lead Inbox"    (one row per deduplicated LEAD id)

Rules:
* Only this module talks to Google, and only from the background worker
  (sheets_outbox.sync_pass) - never inside a request.
* Rows are keyed by the record's immutable id in column A. Row position is a
  cache, never the identity: before writing, the worker reads column A once
  per pass and finds the row by id, so a sorted or filtered sheet is fine.
* Values are written RAW (Google never evaluates them). Text that a
  spreadsheet program could read as a formula (= + - @ tab CR) is prefixed
  with an apostrophe so a CSV downloaded from the Sheet is safe too.
* Dates are written as real date-time numbers in Central time (the sheets'
  time zone), so they sort, filter and chart.
* No credentials in code or logs; errors are reduced to a CATEGORY (no
  message text - Google error bodies can echo cell contents).
"""
from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
CENTRAL = ZoneInfo("America/Chicago")
SCHEMAS = {
    "consumer": (
        "Lead ID", "Received (CT)", "Name", "Phone", "Email", "State", "ZIP",
        "Source", "Campaign", "Status", "Assigned agent", "Consent recorded (CT)",
        "Last updated (CT)", "Follow-up (CT)",
    ),
    "recruiting": (
        "Application ID", "Applied (CT)", "Applicant", "Email", "Phone",
        "Project", "Status", "Rep ID", "Last updated (CT)",
    ),
}
DATE_COLUMNS = {"consumer": (1, 11, 12, 13), "recruiting": (1, 8)}
SPREADSHEET_ENV = {"consumer": "VOLT_CONSUMER_SHEETS_ID", "recruiting": "VOLT_RECRUITING_SHEETS_ID"}
TABS = {"consumer": "Lead Inbox", "recruiting": "Applications"}
MAX_ROWS = 50000
BUSINESS_LABEL = {"consumer": "Consumer Leads", "recruiting": "VOLT Recruiting"}

# Error categories shown on the status panel (never the raw message).
CATEGORY_TEXT = {
    "credential": "Google rejected the service-account credential (missing, malformed or revoked).",
    "permission": "The service account is not allowed to edit this spreadsheet (share it as Editor).",
    "not_found": "The spreadsheet or tab was not found (check the spreadsheet ID and tab name).",
    "rate_limited": "Google rate limit reached; retrying automatically with backoff.",
    "google_unavailable": "Google Sheets was unavailable; retrying automatically.",
    "network": "Could not reach Google; retrying automatically.",
    "sheet_layout": "The tab's header row does not match the expected columns; nothing was written.",
    "not_configured": "The spreadsheet ID or credential is not configured on the server.",
    "data": "This record could not be converted to a row.",
    "unknown": "Unexpected error; retrying automatically.",
}
RETRYABLE = {"rate_limited", "google_unavailable", "network", "unknown"}


class SheetsError(Exception):
    """Raised with a category only."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def categorize(exc: BaseException) -> str:
    if isinstance(exc, SheetsError):
        return exc.category
    status = getattr(getattr(exc, "resp", None), "status", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status is not None:
        if status == 401:
            return "credential"
        if status == 403:
            return "permission"
        if status in (400, 404):
            return "not_found"
        if status == 429:
            return "rate_limited"
        if status >= 500:
            return "google_unavailable"
        return "unknown"
    name = type(exc).__name__
    if name in ("RefreshError", "DefaultCredentialsError", "MalformedError") or isinstance(exc, (json.JSONDecodeError, KeyError)):
        return "credential"
    if isinstance(exc, (socket.timeout, TimeoutError, ConnectionError, OSError)):
        return "network"
    return "unknown"


def safe(value: Any) -> str:
    """Text that stays text in Google Sheets and in any CSV downloaded from it."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()[:3000]
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def safe_phone(value: Any) -> str:
    """'+1 (555) 234-0100' would need an apostrophe to stay text; a phone
    number never needs the '+' for these US-only businesses, so drop it."""
    text = str(value or "").strip()
    if re.fullmatch(r"\+[\d\s().-]{7,25}", text):
        text = text[1:].strip()
    return safe(text)


def central_serial(value: Optional[datetime]):
    """A stored UTC datetime -> a Sheets date-time number in Central time."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(CENTRAL).replace(tzinfo=None)
    delta = local - datetime(1899, 12, 30)
    return round(delta.days + delta.seconds / 86400.0, 8)


def consumer_row(lead: Any, assigned_name: str, status_label: str, source_label: str) -> List[Any]:
    if bool(getattr(lead, "is_test", False)):
        raise SheetsError("data")          # test leads never reach a real Sheet
    return [
        safe(lead.id), central_serial(lead.created_at),
        safe(" ".join(filter(None, (lead.first_name, lead.last_name)))),
        safe_phone(lead.phone), safe(lead.email), safe(lead.state), safe(lead.zip_code),
        safe(source_label), safe(lead.utm_campaign or lead.campaign), safe(status_label),
        safe(assigned_name), central_serial(lead.consent_at),
        central_serial(lead.updated_at), central_serial(lead.next_follow_up_at),
    ]


def recruiting_row(app: Any, rep: Any, project_name: str, email: str, status_label: str, rep_id: str) -> List[Any]:
    if bool(getattr(rep, "is_test", False)):
        raise SheetsError("data")          # test applicants never reach a real Sheet
    return [
        safe(app.id), central_serial(app.submitted_at or app.created_at),
        safe(" ".join(filter(None, (rep.first_name, rep.last_name)))),
        safe(email), safe_phone(rep.phone), safe(project_name), safe(status_label),
        safe(rep_id), central_serial(app.updated_at),
    ]


def spreadsheet_id(business: str) -> str:
    return (os.getenv(SPREADSHEET_ENV[business]) or "").strip()


def configured(business: str) -> bool:
    return bool(spreadsheet_id(business) and (os.getenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON") or "").strip())


def sheet_url(business: str) -> Optional[str]:
    sid = spreadsheet_id(business)
    return "https://docs.google.com/spreadsheets/d/%s/edit" % sid if sid else None


def service_account_email() -> Optional[str]:
    """The identity to share the spreadsheets with (not a secret)."""
    try:
        return json.loads(os.getenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON") or "{}").get("client_email")
    except ValueError:
        return None


def api():
    if not (os.getenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON") or "").strip():
        raise SheetsError("not_configured")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    try:
        info = json.loads(os.environ["VOLT_SHEETS_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except Exception:                                  # noqa: BLE001 - never echo the credential
        raise SheetsError("credential") from None
    import google_auth_httplib2
    import httplib2
    # A hung Google connection must never stall the worker past its lease.
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
    return build("sheets", "v4", http=http, cache_discovery=False)


def column(number: int) -> str:
    out = ""
    while number:
        number, r = divmod(number - 1, 26)
        out = chr(65 + r) + out
    return out


def _range(business: str, a1: str) -> str:
    return "'%s'!%s" % (TABS[business].replace("'", "''"), a1)


def verify(business: str, client=None) -> Dict[str, Any]:
    """Can we authenticate, open the file, find the tab, and is its header
    exactly our layout? Writes nothing."""
    sid = spreadsheet_id(business)
    if not sid:
        raise SheetsError("not_configured")
    client = client or api()
    got = client.spreadsheets().values().get(spreadsheetId=sid, range=_range(business, "A1:%s1" % column(len(SCHEMAS[business])))).execute()
    header = [str(c).strip() for c in (got.get("values") or [[]])[0]]
    if header != list(SCHEMAS[business]):
        raise SheetsError("sheet_layout")
    return {"ok": True, "tab": TABS[business]}


def upsert(business: str, rows: List[List[Any]], client=None) -> Dict[str, int]:
    """Insert-or-update many rows by the id in column A, in one read and at
    most two writes. Duplicate ids inside the batch collapse to the last row.
    A duplicate id already present in the Sheet (made by hand) is left alone
    and only the FIRST occurrence is updated."""
    if business not in SCHEMAS:
        raise ValueError("Invalid business")
    width = len(SCHEMAS[business])
    by_id: Dict[str, List[Any]] = {}
    for row in rows:
        if len(row) != width or not row[0]:
            raise SheetsError("data")
        by_id[str(row[0])] = row
    if not by_id:
        return {"updated": 0, "inserted": 0}
    sid = spreadsheet_id(business)
    if not sid:
        raise SheetsError("not_configured")
    client = client or api()
    values = client.spreadsheets().values()
    verify(business, client)
    got = values.get(spreadsheetId=sid, range=_range(business, "A2:A%d" % (MAX_ROWS + 1))).execute()
    where: Dict[str, int] = {}
    for i, cells in enumerate(got.get("values") or []):
        key = str(cells[0]).strip() if cells else ""
        if key and key not in where:
            where[key] = i + 2
    updates, appends = [], []
    for rid, row in by_id.items():
        if rid in where:
            n = where[rid]
            updates.append({"range": _range(business, "A%d:%s%d" % (n, column(width), n)), "values": [row]})
        else:
            appends.append(row)
    if updates:
        values.batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": updates}).execute()
    if appends:
        # OVERWRITE, not INSERT_ROWS (#630): inserting rows directly under the
        # header copied the header's navy styling onto every new row, dropped
        # the prepared date formats, and made Google shift every dashboard
        # formula (Applications!A2:A became A8:A after six inserts, so the
        # dashboard counted nothing). OVERWRITE fills the next empty,
        # pre-formatted row; Google still adds rows at the end if it must.
        values.append(spreadsheetId=sid, range=_range(business, "A:%s" % column(width)), valueInputOption="RAW",
                      insertDataOption="OVERWRITE", body={"values": appends}).execute()
    return {"updated": len(updates), "inserted": len(appends)}
