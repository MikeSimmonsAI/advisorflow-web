"""Chariot enrollment workflow: switched off by default, residential only,
one submission per person/meter, recorded before the call, every outcome
confirmed by a person, identity data never stored. No test reaches Chariot."""
import json

import pytest

from app.models.models import Lead, User
from app.models.provider_transaction_models import ProviderTransaction
from app.services import chariot_enrollment as ce
from app.services import energy_enrollment as ee
from app.services.auth_service import create_access_token, hash_password
from app.services.providers import base, chariot

FIELDS = {
    "Zip": "78415", "CustomerTypeID": "1", "ProductID": "23639",
    "FirstName": "BILLIE", "LastName": "BEATTY", "DOB": "12/12/1990",
    "EmailAddress": "synthetic@example.com", "PhoneNumber": "1111111111",
    "BillDeliverMethodID": "Email", "languagePreference": "English",
    "ESIID": "10032789426879101", "ServiceType": "switching",
    "ServiceStartDate": "05/20/2019", "IsAccountHolder": "Yes",
    "BillingAddressTypeID": "No", "AutoPay": "No", "PayDeposit": "No",
    "IsPromoInterested": "No", "SSN": "666730809",
}
DONE = {"Status": "Enrollment has been completed",
        "Message": "Passed CreditCheck & No Deposit required", "RefrenceNumber": "STB1"}
RES = ee.CLASS_RESIDENTIAL_RATE_REVIEW


class _R:
    def __init__(self, body):
        self._b, self.status_code, self.text = body, 200, json.dumps(body)

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(chariot.ENV_API_KEY, "test-key-not-real")
    monkeypatch.setenv(chariot.ENV_ENVIRONMENT, "test")
    monkeypatch.delenv(chariot.ENV_ALLOW_TRANSACTIONS, raising=False)


@pytest.fixture
def wire():
    calls, queue = [], []

    def fake(method, url, *, params=None, files=None, timeout=None):
        calls.append(url)
        item = queue.pop(0) if queue else DONE
        if isinstance(item, Exception):
            raise item
        return _R(item)
    prev = chariot.set_transport(fake)
    fake.calls, fake.queue = calls, queue
    yield fake
    chariot.set_transport(prev)


def _lead(db, org, first="Billie"):
    lead = Lead(organization_id=org.id, first_name=first, last_name="Beatty", status="new")
    db.add(lead)
    db.commit()
    return lead


def _submit(db, org, lead, **kw):
    return ce.submit_enrollment(db, organization_id=org.id, lead=lead, actor_user_id=None,
                                classification=kw.pop("classification", RES),
                                fields=kw.pop("fields", FIELDS), **kw)


def test_switched_off_by_default_nothing_sent_nothing_written(db_session, sample_org, wire):
    lead = _lead(db_session, sample_org)
    with pytest.raises(ce.SubmissionRefused):
        _submit(db_session, sample_org, lead)
    assert wire.calls == [] and db_session.query(ProviderTransaction).count() == 0


def test_business_is_refused_before_anything(db_session, sample_org, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(db_session, sample_org)
    with pytest.raises(ee.EnrollmentNotReady):
        _submit(db_session, sample_org, lead, classification=ee.CLASS_COMMERCIAL_ENERGY)
    assert wire.calls == [] and db_session.query(ProviderTransaction).count() == 0


def test_completed_still_waits_for_a_person_and_blocks_a_second_submit(db_session, sample_org,
                                                                        wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(db_session, sample_org)
    row = _submit(db_session, sample_org, lead)
    assert row.normalized_status == base.PROVIDER_CONFIRMED and row.needs_human is True
    assert row.provider_reference == "STB1" and row.resolved_at is None
    assert "666730809" not in (row.request_echo or "") and "12/12/1990" not in (row.request_echo or "")
    with pytest.raises(ce.SubmissionRefused):
        _submit(db_session, sample_org, lead)                     # same person
    other = _lead(db_session, sample_org, first="Other")
    with pytest.raises(ce.SubmissionRefused):
        _submit(db_session, sample_org, other)                    # same meter
    assert len(wire.calls) == 1


def test_a_timeout_is_outcome_unknown_and_only_not_received_allows_a_retry(db_session, sample_org,
                                                                            wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(db_session, sample_org)
    wire.queue.append(TimeoutError("read timed out"))
    row = _submit(db_session, sample_org, lead)
    assert row.normalized_status == base.PENDING_PROVIDER
    assert row.reason_code == chariot.REASON_OUTCOME_UNKNOWN and row.needs_human is True
    with pytest.raises(ce.SubmissionRefused):
        _submit(db_session, sample_org, lead)
    with pytest.raises(ValueError):
        ce.reconcile(db_session, row, user_id=None, outcome=ce.OUTCOME_NOT_RECEIVED, note="")
    ce.reconcile(db_session, row, user_id=None, outcome=ce.OUTCOME_NOT_RECEIVED,
                 note="Chariot support: no enrollment on file for this ESIID")
    again = _submit(db_session, sample_org, lead)
    assert again.id != row.id and len(wire.calls) == 2


def test_a_resolved_enrolled_row_still_blocks(db_session, sample_org, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    lead = _lead(db_session, sample_org)
    row = _submit(db_session, sample_org, lead)
    ce.reconcile(db_session, row, user_id=None, outcome=ce.OUTCOME_ENROLLED, note="customer confirmed")
    with pytest.raises(ce.SubmissionRefused):
        _submit(db_session, sample_org, lead)


def test_queue_and_reconcile_over_the_api(client, db_session, sample_org, wire, monkeypatch):
    monkeypatch.setenv(chariot.ENV_ALLOW_TRANSACTIONS, "true")
    admin = User(organization_id=sample_org.id, email="energy-admin@example.com",
                 password_hash=hash_password("x"), full_name="Energy Admin", role="org_admin",
                 is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    lead = _lead(db_session, sample_org)
    row = _submit(db_session, sample_org, lead)
    q = client.get("/energy-ops/enrollments", headers=h)
    assert q.status_code == 200, q.text
    assert [i["id"] for i in q.json()["items"]] == [row.id]
    r = client.post("/energy-ops/enrollments/%s/reconcile" % row.id, headers=h,
                    json={"outcome": "enrolled", "note": "Customer confirmed; Chariot weekly report"})
    assert r.status_code == 200, r.text
    assert r.json()["verified_outcome"] == "enrolled"
    db_session.refresh(lead)
    assert lead.tier == "contract_signed"
    assert client.get("/energy-ops/enrollments", headers=h).json()["items"] == []


def test_the_enroll_route_refuses_while_switched_off(client, db_session, sample_org, wire):
    admin = User(organization_id=sample_org.id, email="energy-admin2@example.com",
                 password_hash=hash_password("x"), full_name="Energy Admin", role="org_admin",
                 is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(admin, db_session)}
    lead = _lead(db_session, sample_org)
    r = client.post("/energy-ops/leads/%s/enroll" % lead.id, headers=h, json={"fields": FIELDS})
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    assert wire.calls == []
