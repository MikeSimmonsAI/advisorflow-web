"""A double-click on POST /sms/send-batch must not text every lead twice.

Lease per lead + exact template (send_guard). A lead already claimed is
reported skipped (duplicate_ids); a lead that did not send (test record, DNC,
provider error) has its claim released so a corrected retry goes out.
No provider is contacted: send_sms is a recorder.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.models.models import Lead


def _leads(db, org, advisor):
    out = []
    for i, status in enumerate(["new", "new", "dnc"]):
        lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="B%d" % i,
                    last_name="Batch", phone="1214555%04d" % (7400 + i), status=status)
        db.add(lead)
        out.append(lead)
    db.commit()
    return [lead.id for lead in out]


def test_batch_double_click_sends_each_lead_once(client, db_session, sample_org, sample_advisor, auth_headers):
    ids = _leads(db_session, sample_org, sample_advisor)
    body = {"lead_ids": ids, "template": "Checking in, {first_name}.", "include_booking_link": False}
    n = iter(range(100))
    with patch("app.services.sms_service.send_sms",
               side_effect=lambda *a, **k: SimpleNamespace(id="m%d" % next(n))) as sent:
        first = client.post("/sms/send-batch", json=body, headers=auth_headers)
        second = client.post("/sms/send-batch", json=body, headers=auth_headers)
    assert first.status_code == 200, first.text
    assert first.json()["sent_count"] == 2
    assert second.json()["sent_count"] == 0
    assert sorted(second.json()["duplicate_ids"]) == sorted(ids[:2])
    assert sent.call_count == 2
    # the DNC lead never sent, so its claim was released - not reported duplicate
    assert ids[2] not in second.json()["duplicate_ids"]
