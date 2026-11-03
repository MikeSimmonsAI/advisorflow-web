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

from app.models.models import Lead


def _excel_upload_file(rows: list[dict], filename: str = "test.xlsx"):
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    buf.seek(0)
    return {"file": (filename, buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def test_confirm_upload_refuses_an_advisor_without_the_import_capability(client, auth_headers):
    """The import gate is real and stays real.

    The retirement test below uses import_auth_headers, an advisor who has been
    GRANTED lead_import_stage and lead_import_commit. This one pins the other
    side of that: a plain advisor is refused. Without it, swapping the
    fixture would look like it had quietly removed an authorization check.
    """
    files = _excel_upload_file([
        {"First Name": "Ungranted", "Last Name": "Test", "Phone": "2145550900", "Email": ""},
    ])

    response = client.post("/leads/upload/confirm", files=files, headers=auth_headers)
    assert response.status_code == 403


def test_old_lead_upload_is_retired_and_writes_nothing(client, db_session, import_auth_headers):
    """#470 Stage D: POST /leads/upload/preview and /confirm are retired in
    favour of Import Center (/imports/start). An authorised caller gets a 410
    naming the replacement; nothing is read into leads or staged."""
    from app.models.import_models import ImportBatch
    before = db_session.query(Lead).count(), db_session.query(ImportBatch).count()
    for path in ("/leads/upload/preview", "/leads/upload/confirm"):
        files = _excel_upload_file([{"First Name": "Retired", "Last Name": "Path", "Phone": "2145550901", "Email": ""}])
        r = client.post(path, files=files, data={"source_year": "2019"}, headers=import_auth_headers)
        assert r.status_code == 410, (path, r.status_code, r.text[:200])
        assert "Import Center" in r.json()["detail"]
    assert (db_session.query(Lead).count(), db_session.query(ImportBatch).count()) == before
