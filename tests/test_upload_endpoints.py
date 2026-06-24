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


def test_confirm_upload_persists_source_year_from_form_field(client, db_session, admin_auth_headers):
    """
    Regression test for the source_year bug: previously this value was
    silently discarded no matter what the frontend sent, because the
    endpoint expected it as a query param while the frontend sent it as
    multipart form data.
    """
    files = _excel_upload_file([
        {"First Name": "Year", "Last Name": "Test", "Phone": "2145550901", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        data={"source_year": "2019"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    lead = db_session.query(Lead).filter(Lead.first_name == "Year").first()
    assert lead is not None
    assert lead.source_year == 2019


def test_preview_upload_does_not_persist_but_still_reads_source_year(client, db_session, admin_auth_headers):
    """Preview is a dry run, so nothing should be persisted, but it should still parse source_year without error."""
    files = _excel_upload_file([
        {"First Name": "PreviewYear", "Last Name": "Test", "Phone": "2145550902", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/preview",
        files=files,
        data={"source_year": "2020"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 1
    lead = db_session.query(Lead).filter(Lead.first_name == "PreviewYear").first()
    assert lead is None  # dry run - nothing persisted


def test_confirm_upload_force_new_inquiry_form_field_tags_every_lead(client, db_session, admin_auth_headers):
    """force_new_inquiry must work as an actual multipart form field, matching how the frontend sends it."""
    files = _excel_upload_file([
        {"First Name": "ForceOne", "Last Name": "Inquiry", "Phone": "2145550903", "Email": "", "Lead Type": "Pre-Need"},
        {"First Name": "ForceTwo", "Last Name": "Inquiry", "Phone": "2145550904", "Email": ""},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        data={"force_new_inquiry": "true"},
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["tier_breakdown"]["new_inquiry"] == 2
    leads = db_session.query(Lead).filter(Lead.last_name == "Inquiry").all()
    assert len(leads) == 2
    assert all(lead.tier == LeadTier.NEW_INQUIRY for lead in leads)


def test_confirm_upload_without_force_new_inquiry_uses_normal_tier_rules(client, db_session, admin_auth_headers):
    """Confirms force_new_inquiry defaults to False/off when omitted, not silently always-on."""
    files = _excel_upload_file([
        {"First Name": "NormalRules", "Last Name": "Test", "Phone": "2145550905", "Email": "", "Lead Type": "Pre-Need"},
    ])

    response = client.post(
        "/leads/upload/confirm",
        files=files,
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    lead = db_session.query(Lead).filter(Lead.first_name == "NormalRules").first()
    assert lead.tier == LeadTier.PRE_NEED


def test_preview_upload_auto_detects_new_inquiry_from_source_column_via_http(client, db_session, admin_auth_headers):
    """Full HTTP-layer check that source-column auto-detection works end to end, not just at the service-function level."""
    files = _excel_upload_file([
        {"First Name": "WebHttp", "Last Name": "Test", "Phone": "2145550906", "Email": "", "Source": "Web Form"},
    ])

    response = client.post(
        "/leads/upload/preview",
        files=files,
        headers=admin_auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["tier_breakdown"]["new_inquiry"] == 1


# ---------------------------------------------------------------------------
# Access control - lead import is admin-only by default per Mike's
# explicit request, with a per-advisor override (User.can_import_leads)
# an admin can grant individually rather than it being all-or-nothing.
# ---------------------------------------------------------------------------

def test_plain_advisor_blocked_from_preview_upload_by_default(client, db_session, auth_headers):
    files = _excel_upload_file([
        {"First Name": "Blocked", "Last Name": "Test", "Phone": "2145550950", "Email": ""},
    ])

    response = client.post("/leads/upload/preview", files=files, headers=auth_headers)

    assert response.status_code == 403
    assert "permission" in response.json()["detail"].lower()


def test_plain_advisor_blocked_from_confirm_upload_by_default(client, db_session, auth_headers):
    files = _excel_upload_file([
        {"First Name": "Blocked2", "Last Name": "Test", "Phone": "2145550951", "Email": ""},
    ])

    response = client.post("/leads/upload/confirm", files=files, headers=auth_headers)

    assert response.status_code == 403


def test_advisor_with_can_import_leads_override_is_allowed(client, db_session, sample_advisor, auth_headers):
    sample_advisor.can_import_leads = True
    db_session.commit()

    files = _excel_upload_file([
        {"First Name": "Granted", "Last Name": "Test", "Phone": "2145550952", "Email": ""},
    ])

    response = client.post("/leads/upload/preview", files=files, headers=auth_headers)

    assert response.status_code == 200


def test_org_admin_always_allowed_regardless_of_override_flag(client, db_session, admin_auth_headers):
    """org_admin should never need the can_import_leads override - admins are always allowed."""
    files = _excel_upload_file([
        {"First Name": "AdminAllowed", "Last Name": "Test", "Phone": "2145550953", "Email": ""},
    ])

    response = client.post("/leads/upload/preview", files=files, headers=admin_auth_headers)

    assert response.status_code == 200


def test_advisor_without_override_still_blocked_even_with_phone_set(client, db_session, sample_advisor, auth_headers):
    """can_import_leads defaults to False - confirms the default-deny behavior explicitly, not just absence of an error."""
    assert sample_advisor.can_import_leads is False

    files = _excel_upload_file([
        {"First Name": "StillBlocked", "Last Name": "Test", "Phone": "2145550954", "Email": ""},
    ])

    response = client.post("/leads/upload/confirm", files=files, headers=auth_headers)

    assert response.status_code == 403
