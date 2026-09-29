"""Contacts -> Leads in bulk (owner, 2026-09-29: "I have to open each one and promote it?").

    GET  /intake/contacts/ids          every id matching the Contacts filter
    POST /intake/contacts/bulk-promote {ids, tier?}

Same rules as the single promote: no consent granted, nothing enrolled, DNC
refused, already-a-lead skipped; plus contacts with no phone and no working
email are skipped with a reason. Acting workspace only.
"""
import uuid

import pytest

from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization
from app.services.auth_service import create_access_token


@pytest.fixture()
def admin(db_session, sample_advisor):
    sample_advisor.role = "org_admin"
    db_session.commit()
    return sample_advisor


@pytest.fixture()
def h(db_session, admin):
    return {"Authorization": "Bearer " + create_access_token(admin, db_session)}


def _c(db, org_id, **kw):
    kw.setdefault("first_name", "P%s" % uuid.uuid4().hex[:4])
    c = OrgContact(organization_id=org_id, **kw)
    db.add(c)
    db.commit()
    return c


def test_bulk_promote_creates_leads_and_reports_skips(client, db_session, sample_org, admin, h):
    good_phone = _c(db_session, sample_org.id, phone="+12145550401")
    good_email = _c(db_session, sample_org.id, email="ok@example.com", email_status="ready")
    dnc = _c(db_session, sample_org.id, phone="+12145550402", sms_status="dnc")
    dead = _c(db_session, sample_org.id, email="bad@example.com", email_status="invalid")
    other = Organization(name="Other", slug="o-%s" % uuid.uuid4().hex[:6])
    db_session.add(other)
    db_session.commit()
    foreign = _c(db_session, other.id, phone="+12145550403")
    ids = [good_phone.id, good_email.id, dnc.id, dead.id, foreign.id]
    r = client.post("/intake/contacts/bulk-promote", json={"ids": ids}, headers=h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["promoted"] == 2
    assert out["skipped_by_reason"] == {"Do not contact": 1, "No phone and no working email": 1,
                                        "Not a contact in this workspace": 1}
    leads = db_session.query(Lead).filter(Lead.id.in_(out["lead_ids"])).all()
    assert {l.organization_id for l in leads} == {sample_org.id}
    assert all(l.sms_consent is not True and l.status == "new" for l in leads)
    assert db_session.query(Lead).filter(Lead.organization_id == other.id).count() == 0
    # Doing it again is harmless: both are already leads.
    again = client.post("/intake/contacts/bulk-promote", json={"ids": ids[:2]}, headers=h).json()
    assert again["promoted"] == 0 and again["skipped_by_reason"] == {"Already a lead": 2}


def test_select_all_matching_ids_follow_the_filter(client, db_session, sample_org, admin, h):
    a = _c(db_session, sample_org.id, first_name="Zed", phone="+12145550411")
    b = _c(db_session, sample_org.id, first_name="Zed", email="z@example.com")
    _c(db_session, sample_org.id, first_name="Amy")
    r = client.get("/intake/contacts/ids?search=Zed", headers=h).json()
    assert set(r["ids"]) == {a.id, b.id} and r["total"] == 2 and r["truncated"] is False
    r = client.get("/intake/contacts/ids?search=Zed&has_phone=true", headers=h).json()
    assert r["ids"] == [a.id]


def test_limits_and_permissions(client, db_session, sample_org, admin, h, auth_headers):
    assert client.post("/intake/contacts/bulk-promote", json={"ids": []}, headers=h).status_code == 400
    too_many = ["x%d" % i for i in range(201)]
    assert client.post("/intake/contacts/bulk-promote", json={"ids": too_many}, headers=h).status_code == 400
    c = _c(db_session, sample_org.id, phone="+12145550421")
    assert client.post("/intake/contacts/bulk-promote", json={"ids": [c.id], "tier": "nope"},
                       headers=h).status_code == 400
