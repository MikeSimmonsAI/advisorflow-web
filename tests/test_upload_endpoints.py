"""
Tests for the actual HTTP upload endpoints (POST /leads/upload/preview,
POST /leads/upload/confirm) - as opposed to test_import_service.py, which
tests import_leads_from_excel() directly as a Python function and never
exercised the FastAPI request-parsing layer at all.

This gap is exactly how a real bug shipped unnoticed: source_year was
being sent correctly by the frontend as a multipart form field, but the
endpoint had it as a bare `Optional[int] = None` parameter rather than
`Form(...)`. FastAPI treats bare params as query parameters when mixed
with a File(...) upload, so source_year was silently read as None on
every single import, every time, despite the UI showing a "Source year"
input that looked like it was doing something. Found while wiring up
force_new_inquiry, which would have had the identical problem if left
as a bare bool param.
"""

import io
import pandas as pd

from app.models.models import Lead, LeadTier


def _excel_upload_file(rows: list[dict], filename: str = "test.xlsx"):
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    buf.seek(0)
    return {"file": (filename, buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def test_confirm_upload_refuses_an_advisor_without_the_import_capability(client, auth_headers):
    """The import gate is real and stays real.

    The three tests below use import_auth_headers, an advisor who has been
    GRANTED lead_import_stage and lead_import_commit. This one pins the other
    side of that: a plain advisor is refused. Without it, swapping the
    fixture would look like it had quietly removed an authorization check.
    """
    files = _excel_upload_file([
        {"First Name": "Ungranted", "Last Name": "Test", "Phone": "2145550900", "Email": ""},
    ])

    response = client.post("/leads/upload/confirm", files=files, headers=auth_headers)
    assert response.status_code == 403


def test_confirm_upload_persists_source_year_from_form_field(client, db_session, import_auth_headers):
    """
    Regression test for the source_year bug - which has now happened TWICE,
    by two different mechanisms, which is why this test is worth its weight.

    First time: the endpoint declared source_year as a bare `Optional[int]`
    rather than `Form(...)`, so FastAPI read it as a query parameter when
    mixed with a File(...) upload and it arrived as None on every import.

    Second time: the parameter was correctly declared as Form(...) and parsed
    fine - and then the endpoint simply never passed it to the import
    pipeline. stage_batch()'s signature had nowhere to put it. Same visible
    symptom, completely different cause, and the UI showed a working
    "Source year" box throughout both.

    So this test asserts the OUTCOME - the value reaches the persisted lead -
    rather than any particular mechanism, because the mechanism is exactly the
    part that keeps changing underneath it.
    """
    files = _excel_upload_file([
        {"First Name": "Year", "Last Name": "Test", "Phone": "2145550901", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        data={"source_year": "2019"},
        headers=import_auth_headers,
    )

    assert response.status_code == 200
    lead = db_session.query(Lead).filter(Lead.first_name == "Year").first()
    assert lead is not None
    assert lead.source_year == 2019


def test_preview_upload_does_not_persist_but_still_reads_source_year(client, db_session, auth_headers):
    """Preview is a dry run, so nothing should be persisted, but it should still parse source_year without error."""
    files = _excel_upload_file([
        {"First Name": "PreviewYear", "Last Name": "Test", "Phone": "2145550902", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/preview",
        files=files,
        data={"source_year": "2020"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 1
    lead = db_session.query(Lead).filter(Lead.first_name == "PreviewYear").first()
    assert lead is None  # dry run - nothing persisted


def test_confirm_upload_force_new_inquiry_form_field_tags_every_lead(client, db_session, import_auth_headers):
    """force_new_inquiry must work as an actual multipart form field, matching how the frontend sends it."""
    files = _excel_upload_file([
        {"First Name": "ForceOne", "Last Name": "Inquiry", "Phone": "2145550903", "Email": "", "Lead Type": "Pre-Need"},
        {"First Name": "ForceTwo", "Last Name": "Inquiry", "Phone": "2145550904", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        data={"force_new_inquiry": "true"},
        headers=import_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["tier_breakdown"]["new_inquiry"] == 2
    leads = db_session.query(Lead).filter(Lead.last_name == "Inquiry").all()
    assert len(leads) == 2
    assert all(lead.tier == LeadTier.NEW_INQUIRY for lead in leads)


def test_confirm_upload_without_force_new_inquiry_uses_normal_tier_rules(client, db_session, import_auth_headers):
    """Confirms force_new_inquiry defaults to False/off when omitted, not silently always-on."""
    files = _excel_upload_file([
        {"First Name": "NormalRules", "Last Name": "Test", "Phone": "2145550905", "Email": "", "Lead Type": "Pre-Need"},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        headers=import_auth_headers,
    )

    assert response.status_code == 200
    lead = db_session.query(Lead).filter(Lead.first_name == "NormalRules").first()
    assert lead.tier == LeadTier.PRE_NEED


def test_preview_upload_auto_detects_new_inquiry_from_source_column_via_http(client, db_session, auth_headers):
    """Full HTTP-layer check that source-column auto-detection works end to end, not just at the service-function level."""
    files = _excel_upload_file([
        {"First Name": "WebHttp", "Last Name": "Test", "Phone": "2145550906", "Email": "", "Source": "Web Form"},
    ])

    response = client.post(
        "/leads/upload/preview",
        files=files,
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["tier_breakdown"]["new_inquiry"] == 1
