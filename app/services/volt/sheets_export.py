"""Google Sheets projection for VOLT lead records (never the source of truth).

Two independent spreadsheet IDs and tab schemas. No credentials in code, no
outbound calls unless the corresponding integration is configured. Treat Sheet
access as customer-data access; the owner must review Drive sharing first.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
SCHEMAS = {
    "consumer": (
        "Lead ID", "Received (UTC)", "Name", "Phone", "Email", "State", "ZIP",
        "Source", "Campaign", "Status", "Assigned agent", "Consent recorded (UTC)",
        "Last updated (UTC)", "Follow-up (UTC)",
    ),
    "recruiting": (
        "Application ID", "Applied (UTC)", "Applicant", "Email", "Phone",
        "Project", "Status", "Rep serial", "Last updated (UTC)",
    ),
}
SPREADSHEET_ENV = {
    "consumer": "VOLT_CONSUMER_SHEETS_ID",
    "recruiting": "VOLT_RECRUITING_SHEETS_ID",
}
TABS = {"consumer": "Lead Inbox", "recruiting": "Applications"}
MAX_SCAN = 100000


def _safe(value: Any) -> str:
    """Never let an untrusted lead value become a Sheets formula."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()[:3000]
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def _when(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    return _safe(value)


def consumer_row(lead: Any, assigned_name: str = "") -> list[str]:
    if bool(getattr(lead, "is_test", False)):
        raise ValueError("Test leads cannot be exported to a real Google Sheet")
    return [
        _safe(lead.id), _when(lead.created_at),
        _safe(" ".join(filter(None, (lead.first_name, lead.last_name)))),
        _safe(lead.phone), _safe(lead.email), _safe(lead.state),
        _safe(lead.zip_code), _safe(lead.source),
        _safe(lead.utm_campaign or lead.campaign), _safe(lead.status),
        _safe(assigned_name), _when(lead.consent_at),
        _when(lead.updated_at), _when(lead.next_follow_up_at),
    ]


def recruiting_row(app: Any, rep: Any, project_name: str = "", email: str = "") -> list[str]:
    if bool(getattr(rep, "is_test", False)):
        raise ValueError("Test applicants cannot be exported to a real Google Sheet")
    return [
        _safe(app.id), _when(app.submitted_at or app.created_at),
        _safe(" ".join(filter(None, (rep.first_name, rep.last_name)))),
        _safe(email), _safe(rep.phone), _safe(project_name),
        _safe(app.status), _safe(rep.serial), _when(app.updated_at),
    ]


def configured(business: str) -> bool:
    return bool(os.getenv(SPREADSHEET_ENV[business]) and os.getenv("VOLT_SHEETS_SERVICE_ACCOUNT_JSON"))


def _api():
    raw = os.environ["VOLT_SHEETS_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def sync_row(business: str, row: list[str], *, api=None) -> dict[str, Any]:
    """Idempotently insert or update by immutable ID in column A.

    Google Sheets is an external, non-transactional projection. Call only from
    a retryable worker after the database transaction commits, never in the
    request path. One worker per spreadsheet must hold a distributed lock to
    avoid duplicate appends during concurrent runs.
    """
    if business not in SCHEMAS:
        raise ValueError("Invalid business")
    if len(row) != len(SCHEMAS[business]) or not row[0]:
        raise ValueError("Incorrect column count or missing stable record ID")
    spreadsheet_id = os.getenv(SPREADSHEET_ENV[business], "").strip()
    if not spreadsheet_id:
        raise RuntimeError("Google Sheet not configured for " + business)
    if api is None:
        api = _api()

    tab = TABS[business].replace("'", "''")
    values = api.spreadsheets().values()
    first = values.get(spreadsheetId=spreadsheet_id,
                       range=f"'{tab}'!A2:A{MAX_SCAN + 1}").execute()
    ids = [cells[0] if cells else "" for cells in first.get("values", [])]
    normalized = [_safe(item) for item in row]
    if row[0] in ids:
        row_number = ids.index(row[0]) + 2
        rng = f"'{tab}'!A{row_number}:{_column(len(row))}{row_number}"
        values.update(spreadsheetId=spreadsheet_id, range=rng,
                      valueInputOption="RAW", body={"values": [normalized]}).execute()
        return {"action": "updated", "row": row_number}
    values.append(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A:{_column(len(row))}",
                  valueInputOption="RAW", insertDataOption="INSERT_ROWS",
                  body={"values": [normalized]}).execute()
    return {"action": "inserted"}


def _column(number: int) -> str:
    out = ""
    while number:
        number, r = divmod(number - 1, 26)
        out = chr(65 + r) + out
    return out


def initialize_tab(business: str, *, api=None) -> None:
    """Bootstrap headers and polished styling in an EXISTING, access-reviewed file."""
    if business not in SCHEMAS:
        raise ValueError("Invalid business")
    spreadsheet_id = os.environ[SPREADSHEET_ENV[business]]
    api = api or _api()
    ss = api.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets(properties)").execute()
    tab_name = TABS[business]
    match = next((s for s in ss["sheets"] if s["properties"]["title"] == tab_name), None)
    if match is None:
        result = api.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
        ).execute()
        sheet_id = result["replies"][0]["addSheet"]["properties"]["sheetId"]
    else:
        sheet_id = match["properties"]["sheetId"]

    n = len(SCHEMAS[business])
    api.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A1:{_column(n)}1",
        valueInputOption="RAW", body={"values": [list(SCHEMAS[business])]},
    ).execute()
    requests = [
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": n},
            "properties": {"pixelSize": 160}, "fields": "pixelSize"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": {"red": .06, "green": .20, "blue": .32},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 11},
                "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 42}, "fields": "pixelSize"}},
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"addConditionalFormatRule": {"index": 0, "rule": {
            "ranges": [{"sheetId": sheet_id, "startRowIndex": 1}],
            "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
                "values": [{"userEnteredValue": '=$A2<>""'}]},
                "format": {"backgroundColor": {"red": .96, "green": .98, "blue": 1}}}}}},
    ]
    api.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
