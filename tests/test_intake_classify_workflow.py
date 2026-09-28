# -*- coding: utf-8 -*-
"""P0 importer: after Classify the wizard goes ON to Review Problems, not back to
Analyze - and the step is persisted server-side, so refresh / Back / the ledger
agree. Re-analysis after classification keeps the file's truth: no duplicated
staged rows, in-file duplicates still found, the email-review row still
flagged, Previous Customer still contact-only, 0 SMS-ready, nothing in the CRM."""
import pytest

from app.models.import_models import ImportStagedRow
from app.models.intake_models import OrgContact
from app.models.models import Lead
from tests.test_universal_intake_api import _headers, _org, _upload, _user


@pytest.fixture(autouse=True)
def _inline(monkeypatch):
    monkeypatch.setenv("INTAKE_INLINE_JOBS", "1")


def _atlantis_like_csv():
    rows = ["First Name,Last Name,Email,Phone,Import Segment"]
    for i in range(19):
        rows.append("P%d,Person,p%d@atlantisco.com,,General Contact Database" % (i, i))
    for i in range(4):                                   # 4 in-file duplicates
        rows.append("P%d,Person,p%d@atlantisco.com,,General Contact Database" % (i, i))
    rows.append("Rae,Role,info@atlantisco.com,,General Contact Database")   # email review
    rows.append("Pat,Former,pat@atlantisco.com,,Previous Customer")
    return ("\n".join(rows) + "\n").encode()


def _get(client, h, bid):
    r = client.get("/intake/batches/%s" % bid, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_classify_moves_on_to_review_and_keeps_everything_true(client, db_session):
    org = _org(db_session, "Atlantis Workflow", "atl-wf")
    h = _headers(db_session, _user(db_session, org, "org_admin", "wf@atl.test"))
    bid = _upload(client, h, _atlantis_like_csv()).json()["id"]
    assert _get(client, h, bid)["workflow"]["step"] == 2                  # map first
    client.post("/intake/batches/%s/analyze" % bid, headers=h)
    first = _get(client, h, bid)
    assert first["workflow"] == {"step": 3, "analyzed": True, "classified": False}
    email_review_before = first["analysis"]["emails"]["review"] + first["analysis"]["emails"]["invalid"]

    # Step 4: apply the classification, then re-analyze (what the button does).
    vals = client.get("/intake/batches/%s/classification-values" % bid, headers=h).json()
    vmap = {v["value"]: v["classification"] for v in vals["values"]}
    vmap["Previous Customer"] = "previous_customer"
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h,
                   json={"classification": {"value_map": vmap, "fallback": "contact"}})
    assert r.status_code == 200, r.text
    assert r.json()["workflow"]["classified"] is True
    assert client.post("/intake/batches/%s/analyze" % bid, headers=h).status_code == 202

    after = _get(client, h, bid)
    # The fix: the persisted next step is Review Problems (5), not Analyze (3).
    assert after["status"] == "ready_for_review"
    assert after["workflow"] == {"step": 5, "analyzed": True, "classified": True}
    # A refresh (a fresh GET) lands on the same step.
    assert _get(client, h, bid)["workflow"]["step"] == 5
    # Classification persisted.
    assert after["classification"]["value_map"]["Previous Customer"] == "previous_customer"
    assert after["classification"]["confirmed_at"]
    a = after["analysis"]
    # Re-analysis rebuilt the staged rows; it did not add to them.
    assert a["total_rows"] == 25
    assert db_session.query(ImportStagedRow).filter(ImportStagedRow.batch_id == bid).count() == 25
    # The file's truth is unchanged by re-analysis.
    assert a["match"]["duplicates_in_file"] == 4 and a["status"].get("duplicate") == 4
    assert a["emails"]["review"] + a["emails"]["invalid"] == email_review_before >= 1
    assert a["record_class"].get("previous_customer") == 1           # contact-only class
    assert a["would_activate_as_leads_if_ready_committed"] == 0
    assert a["sms_ready"] == 0
    # Nothing is in the CRM before approval.
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0

    # Re-running analysis again changes nothing (idempotent, no duplication).
    client.post("/intake/batches/%s/analyze" % bid, headers=h)
    again = _get(client, h, bid)
    assert again["workflow"]["step"] == 5 and again["analysis"]["total_rows"] == 25
    assert db_session.query(ImportStagedRow).filter(ImportStagedRow.batch_id == bid).count() == 25

    # Approve with the safe default (stage only): nothing enters the CRM.
    r = client.post("/intake/batches/%s/commit" % bid, headers=h, json={})
    assert r.status_code == 202 and r.json()["status"] == "staged"
    assert _get(client, h, bid)["workflow"]["step"] == 6
    assert db_session.query(OrgContact).filter(OrgContact.organization_id == org.id).count() == 0
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0


def test_a_new_classification_column_clears_the_confirmation(client, db_session):
    org = _org(db_session, "Atlantis Remap", "atl-remap")
    h = _headers(db_session, _user(db_session, org, "org_admin", "rm@atl.test"))
    bid = _upload(client, h, _atlantis_like_csv()).json()["id"]
    client.post("/intake/batches/%s/analyze" % bid, headers=h)
    client.put("/intake/batches/%s/mapping" % bid, headers=h,
               json={"classification": {"value_map": {}, "fallback": "contact"}})
    client.post("/intake/batches/%s/analyze" % bid, headers=h)
    assert _get(client, h, bid)["workflow"]["classified"] is True
    # Re-map so the classification column is no longer mapped: classify again.
    r = client.put("/intake/batches/%s/mapping" % bid, headers=h, json={"mapping": {
        "First Name": {"kind": "standard", "target": "first_name"},
        "Last Name": {"kind": "standard", "target": "last_name"},
        "Email": {"kind": "standard", "target": "email"},
        "Phone": {"kind": "standard", "target": "phone"},
        "Import Segment": {"kind": "ignore"}}})
    assert r.status_code == 200 and r.json()["workflow"]["classified"] is False
    client.post("/intake/batches/%s/analyze" % bid, headers=h)
    assert _get(client, h, bid)["workflow"]["step"] == 3
