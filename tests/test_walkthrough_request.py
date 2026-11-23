"""Free cleaning walkthrough request page (/evosys-sales-public/walkthrough):
public, saves the request, alerts the EvoSys Sales alert list, never creates
an EvoSys Sales opportunity or any outreach, and ignores bots."""
import pytest

from app.models.evosys_sales_models import EvoSysOutreachState
from app.models.sales_models import Opportunity
from app.models.walkthrough_request_models import WalkthroughRequest
from app.services.evosys_sales import sms_inbound as SI

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)

GOOD = {"name": "Pat Lee", "title": "Property Manager", "company": "Ridgeview Plaza", "email": "Pat@Ridgeview.test",
        "phone": "(972) 555-0142", "address": "100 Main St, Plano TX", "building_type": "Office",
        "timing": "Within 30 days", "src": "linkedin"}


@pytest.fixture(autouse=True)
def _env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(SI, "notify_owner", lambda s, line, subj, body: sent.append((line, subj, body)))
    return sent


def test_page_is_public(client):
    r = client.get("/evosys-sales-public/walkthrough")
    assert r.status_code == 200 and "Request my free walkthrough" in r.text
    assert "__PATH__" not in r.text and "<option>Office</option>" in r.text


def test_request_is_saved_and_alerted_and_never_a_sales_lead(client, db_session, alerts):
    r = client.post("/evosys-sales-public/walkthrough", json=GOOD)
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    w = db_session.query(WalkthroughRequest).one()
    assert (w.contact_name, w.company, w.email, w.source, w.status) == (
        "Pat Lee", "Ridgeview Plaza", "pat@ridgeview.test", "linkedin", "new")
    assert db_session.query(Opportunity).count() == 0
    assert db_session.query(EvoSysOutreachState).count() == 0
    assert len(alerts) == 1 and "WALKTHROUGH REQUEST" in alerts[0][0] and "Ridgeview Plaza" in alerts[0][1]
    assert "100 Main St" in alerts[0][2]


def test_missing_fields_are_explained(client, db_session, alerts):
    r = client.post("/evosys-sales-public/walkthrough", json={"name": "", "email": "nope"})
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert set(errs) == {"name", "company", "email", "phone", "address"}
    assert db_session.query(WalkthroughRequest).count() == 0 and not alerts


def test_bot_honeypot_saves_nothing(client, db_session, alerts):
    r = client.post("/evosys-sales-public/walkthrough", json=dict(GOOD, website="spam.example"))
    assert r.status_code == 200 and db_session.query(WalkthroughRequest).count() == 0 and not alerts


def test_alert_failure_never_loses_the_request(client, db_session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("mail down")
    monkeypatch.setattr(SI, "notify_owner", boom)
    assert client.post("/evosys-sales-public/walkthrough", json=GOOD).status_code == 200
    assert db_session.query(WalkthroughRequest).count() == 1


def test_managers_see_requests(client, db_session, alerts, manager_headers):
    client.post("/evosys-sales-public/walkthrough", json=GOOD)
    r = client.get("/sales/evosys/walkthrough-requests", headers=manager_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["url"].endswith("/evosys-sales-public/walkthrough")
    assert out["requests"][0]["company"] == "Ridgeview Plaza"


def test_strangers_cannot_see_requests(client):
    assert client.get("/sales/evosys/walkthrough-requests").status_code in (401, 403)
