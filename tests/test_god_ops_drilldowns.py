"""
God-scoped drilldown endpoint tests.

Covers: GET /god/ops/opportunities (all filter variants),
        GET /god/ops/proposals,
        GET /god/ops/appointments.

All tests run against SQLite (via conftest.py client fixture).
"""
import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import (
    Organization, Platform, User,
    Proposal, PROP_SENT, PROP_VIEWED, PROP_ACCEPTED, PROP_CHANGE_REQUESTED,
)
from app.models.sales_models import (
    BrandSalesOrg, BrandPackage, Opportunity,
    STAGE_CLOSING,
)
from app.models.scheduling_models import SalesAppointment, APPT_SCHEDULED
from app.services.auth_service import create_access_token, hash_password
from app.services.god_operations import STALLED_DAYS

_SEQ = itertools.count(1)


# ── object factories ──────────────────────────────────────────────────────────

def _user(db, role="advisor", org_id=None):
    n = next(_SEQ)
    u = User(organization_id=org_id,
             email="drilldown%d@test.local" % n,
             password_hash=hash_password("x"),
             full_name="Test User %d" % n,
             role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def _platform(db):
    n = next(_SEQ)
    p = Platform(name="Platform %d" % n, slug="plat-%d" % n)
    db.add(p); db.commit()
    return p


def _brand(db):
    p = _platform(db)
    n = next(_SEQ)
    b = BrandSalesOrg(platform_id=p.id, name="Brand %d" % n,
                      slug="brand-%d" % n)
    db.add(b); db.commit()
    return b


def _opp(db, bso, **kw):
    defaults = dict(
        brand_sales_org_id=bso.id,
        company_name="Acme %d" % next(_SEQ),
        contact_name="Jane Doe",
        stage="prospect",
        status="open",
        deal_value=5000.0,
    )
    defaults.update(kw)
    o = Opportunity(**defaults)
    db.add(o); db.commit(); db.refresh(o)
    return o


def _prop(db, bso, creator_id=None, **kw):
    if creator_id is None:
        creator = _user(db)
        creator_id = creator.id
    defaults = dict(
        brand_sales_org_id=bso.id,
        proposal_number="P-%d" % next(_SEQ),
        title="Test Proposal",
        client_company="Buyer Co",
        sales_status=PROP_SENT,
        created_by_id=creator_id,
    )
    defaults.update(kw)
    p = Proposal(**defaults)
    db.add(p); db.commit(); db.refresh(p)
    return p


def _appt(db, bso, **kw):
    defaults = dict(
        brand_sales_org_id=bso.id,
        title="Discovery Call %d" % next(_SEQ),
        starts_at=datetime.utcnow() + timedelta(days=1),
        ends_at=datetime.utcnow() + timedelta(days=1, hours=1),
        status=APPT_SCHEDULED,
    )
    defaults.update(kw)
    a = SalesAppointment(**defaults)
    db.add(a); db.commit(); db.refresh(a)
    return a


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def god(db_session):
    return _user(db_session, role="god_admin")


@pytest.fixture()
def regular(db_session):
    return _user(db_session, role="advisor")


@pytest.fixture()
def bso(db_session):
    return _brand(db_session)


# ── opportunity list ──────────────────────────────────────────────────────────

class TestGodOpportunityList:
    def test_returns_list(self, client, db_session, god, bso):
        _opp(db_session, bso)
        r = client.get("/god/ops/opportunities", headers=_h(db_session, god))
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_contains_created_opp(self, client, db_session, god, bso):
        o = _opp(db_session, bso, company_name="FindMe Inc")
        r = client.get("/god/ops/opportunities", headers=_h(db_session, god))
        assert r.status_code == 200
        names = [row["company_name"] for row in r.json()]
        assert "FindMe Inc" in names

    def test_filter_open(self, client, db_session, god, bso):
        _opp(db_session, bso, status="open")
        _opp(db_session, bso, status="won")
        r = client.get("/god/ops/opportunities?filter_by=open",
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert row["status"] == "open"

    def test_filter_won(self, client, db_session, god, bso):
        _opp(db_session, bso, status="won", company_name="Won Co")
        _opp(db_session, bso, status="open", company_name="Open Co")
        r = client.get("/god/ops/opportunities?filter_by=won",
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert row["status"] == "won"

    def test_filter_closing(self, client, db_session, god, bso):
        _opp(db_session, bso, status="open", stage=STAGE_CLOSING,
             company_name="Closing Co")
        _opp(db_session, bso, status="open", stage="prospect",
             company_name="Prospect Co")
        r = client.get("/god/ops/opportunities?filter_by=closing",
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert row["stage"] == STAGE_CLOSING

    def test_filter_stalled(self, client, db_session, god, bso):
        old_ts = datetime.utcnow() - timedelta(days=STALLED_DAYS + 2)
        _opp(db_session, bso, status="open", updated_at=old_ts,
             company_name="Stale Co")
        _opp(db_session, bso, status="open", company_name="Fresh Co")
        r = client.get("/god/ops/opportunities?filter_by=stalled_or_overdue",
                       headers=_h(db_session, god))
        assert r.status_code == 200
        names = [row["company_name"] for row in r.json()]
        assert "Stale Co" in names

    def test_filter_overdue(self, client, db_session, god, bso):
        past_due = datetime.utcnow() - timedelta(days=3)
        _opp(db_session, bso, status="open", next_action_due_at=past_due,
             company_name="Overdue Co")
        r = client.get("/god/ops/opportunities?filter_by=stalled_or_overdue",
                       headers=_h(db_session, god))
        assert r.status_code == 200
        names = [row["company_name"] for row in r.json()]
        assert "Overdue Co" in names

    def test_brand_filter(self, client, db_session, god, bso):
        _opp(db_session, bso)
        r = client.get("/god/ops/opportunities?brand_id=%s" % bso.id,
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert str(row["brand_id"]) == str(bso.id)

    def test_row_has_required_keys(self, client, db_session, god, bso):
        _opp(db_session, bso)
        r = client.get("/god/ops/opportunities", headers=_h(db_session, god))
        assert r.status_code == 200
        rows = r.json()
        assert rows
        row = rows[0]
        for key in ("id", "company_name", "stage", "stage_label", "status",
                    "deal_value", "is_stalled", "is_overdue",
                    "owner_name", "brand_id", "brand_name"):
            assert key in row, "missing key: %s" % key

    def test_non_god_blocked(self, client, db_session, regular):
        r = client.get("/god/ops/opportunities", headers=_h(db_session, regular))
        assert r.status_code in (401, 403)

    def test_unauthenticated_blocked(self, client):
        r = client.get("/god/ops/opportunities")
        assert r.status_code in (401, 403)


# ── proposal list ─────────────────────────────────────────────────────────────

class TestGodProposalList:
    def test_returns_outstanding_only(self, client, db_session, god, bso):
        _prop(db_session, bso, sales_status=PROP_SENT)
        _prop(db_session, bso, sales_status=PROP_ACCEPTED)
        r = client.get("/god/ops/proposals", headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert row["sales_status"] in (
                PROP_SENT, PROP_VIEWED, PROP_CHANGE_REQUESTED
            )

    def test_contains_sent_proposal(self, client, db_session, god, bso):
        _prop(db_session, bso, client_company="Target Buyer")
        r = client.get("/god/ops/proposals", headers=_h(db_session, god))
        assert r.status_code == 200
        companies = [row["client_company"] for row in r.json()]
        assert "Target Buyer" in companies

    def test_row_has_required_keys(self, client, db_session, god, bso):
        _prop(db_session, bso)
        r = client.get("/god/ops/proposals", headers=_h(db_session, god))
        assert r.status_code == 200
        rows = r.json()
        assert rows
        row = rows[0]
        for key in ("id", "proposal_number", "client_company",
                    "sales_status", "sales_status_label",
                    "brand_id", "brand_name"):
            assert key in row, "missing key: %s" % key

    def test_brand_filter(self, client, db_session, god, bso):
        _prop(db_session, bso)
        r = client.get("/god/ops/proposals?brand_id=%s" % bso.id,
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert str(row["brand_id"]) == str(bso.id)

    def test_non_god_blocked(self, client, db_session, regular):
        r = client.get("/god/ops/proposals", headers=_h(db_session, regular))
        assert r.status_code in (401, 403)


# ── appointment list ──────────────────────────────────────────────────────────

class TestGodAppointmentList:
    def test_returns_future_scheduled(self, client, db_session, god, bso):
        _appt(db_session, bso)
        r = client.get("/god/ops/appointments", headers=_h(db_session, god))
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_past_appointment_excluded(self, client, db_session, god, bso):
        past = datetime.utcnow() - timedelta(days=2)
        _appt(db_session, bso,
              starts_at=past,
              ends_at=past + timedelta(hours=1),
              status=APPT_SCHEDULED,
              title="Old Call")
        r = client.get("/god/ops/appointments", headers=_h(db_session, god))
        assert r.status_code == 200
        titles = [row["title"] for row in r.json()]
        assert "Old Call" not in titles

    def test_row_has_required_keys(self, client, db_session, god, bso):
        _appt(db_session, bso)
        r = client.get("/god/ops/appointments", headers=_h(db_session, god))
        assert r.status_code == 200
        rows = r.json()
        assert rows
        row = rows[0]
        for key in ("id", "title", "prospect_name", "starts_at",
                    "status", "brand_id", "brand_name"):
            assert key in row, "missing key: %s" % key

    def test_brand_filter(self, client, db_session, god, bso):
        _appt(db_session, bso)
        r = client.get("/god/ops/appointments?brand_id=%s" % bso.id,
                       headers=_h(db_session, god))
        assert r.status_code == 200
        for row in r.json():
            assert str(row["brand_id"]) == str(bso.id)

    def test_non_god_blocked(self, client, db_session, regular):
        r = client.get("/god/ops/appointments", headers=_h(db_session, regular))
        assert r.status_code in (401, 403)
