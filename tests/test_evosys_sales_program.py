"""EvoSys Sales program (owner request 2026-11-16): access, outreach approval and
switch, the send pass (window, daily limit, stop rules), replies, opt-out."""
from datetime import datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, ROLE_SALES_MANAGER, ROLE_SALES_REP
from app.services.evosys_sales import outreach as O

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, brand_b, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)

# Tuesday 2026-11-17 10:00 Central = 16:00 UTC; Monday 9 PM Central = 03:00 UTC.
IN_WINDOW = datetime(2026, 11, 17, 16, 0)
NIGHT = datetime(2026, 11, 17, 3, 0)


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


def _lead(db, brand, owner=None, company="Sparkle Co", email="owner@sparkle.test", stage="prospect"):
    o = Opportunity(brand_sales_org_id=brand.id, owner_user_id=owner.id if owner else None,
                    company_name=company, contact_name="Ana Ruiz", phone="2145550100",
                    email=email, stage=stage, status="open")
    db.add(o); db.commit()
    return o


def _ready(db, brand, on=True):
    s = O.get_settings(db, brand.id)
    s.mailing_address = "100 Main St, Dallas, TX 75201"
    s.offer = "Try it free for 30 days."
    s.approved_at = datetime.utcnow()
    s.sending_on = on
    db.commit()
    return s


class FakeSender:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, to, msg, s, opp_id):
        self.calls.append((to, msg, opp_id))
        return {"success": self.ok, "provider_message_id": "m1" if self.ok else None,
                "error": None if self.ok else "bounced"}


class TestAccess:
    def test_outsider_refused(self, client, db_session, brand, brand_b):
        u = _make_sales_user(db_session)
        _add_membership(db_session, u, brand_b, ROLE_SALES_MANAGER)
        r = client.get("/sales/evosys/overview", headers=_token_headers(u, db_session))
        assert r.status_code == 403

    def test_rep_sees_only_own_leads_and_cannot_change_outreach(self, client, db_session, brand,
                                                                rep, rep_headers, manager):
        _lead(db_session, brand, rep, "Mine Co")
        _lead(db_session, brand, manager, "Theirs Co")
        r = client.get("/sales/evosys/leads", headers=rep_headers).json()
        assert [x["company"] for x in r["leads"]] == ["Mine Co"]
        assert client.post("/sales/evosys/outreach/switch", json={"on": True},
                           headers=rep_headers).status_code == 403

    def test_manager_sees_all(self, client, db_session, brand, manager, manager_headers, rep):
        _lead(db_session, brand, rep, "A"); _lead(db_session, brand, manager, "B")
        assert client.get("/sales/evosys/leads", headers=manager_headers).json()["total"] == 2


class TestApproval:
    def test_cannot_turn_on_before_approval_and_edit_clears_it(self, client, db_session, brand, manager_headers):
        r = client.get("/sales/evosys/outreach", headers=manager_headers).json()
        assert r["sending_on"] is False and r["approved"] is False
        assert any("mailing address" in p for p in r["problems"])
        assert client.post("/sales/evosys/outreach/switch", json={"on": True},
                           headers=manager_headers).status_code == 400
        assert client.post("/sales/evosys/outreach/approve", headers=manager_headers).status_code == 400
        client.put("/sales/evosys/outreach", json={"mailing_address": "100 Main St, Dallas, TX",
                                                   "offer": "Free for 30 days."}, headers=manager_headers)
        assert client.post("/sales/evosys/outreach/approve", headers=manager_headers).status_code == 200
        r = client.post("/sales/evosys/outreach/switch", json={"on": True}, headers=manager_headers).json()
        assert r["sending_on"] is True
        r = client.put("/sales/evosys/outreach", json={"offer": "Changed."}, headers=manager_headers).json()
        assert r["approved"] is False and r["sending_on"] is False

    def test_from_must_be_evosys(self, db_session, brand):
        s = _ready(db_session, brand)
        s.from_email = "someone@gmail.com"
        assert any("evosyspro.live" in p for p in O.settings_problems(s))


class TestPass:
    def test_off_sends_nothing(self, db_session, brand):
        _lead(db_session, brand)
        f = FakeSender()
        assert O.run_pass(db_session, IN_WINDOW, f)["skipped"] == "off"
        assert f.calls == []
        _ready(db_session, brand, on=False)
        assert O.run_pass(db_session, IN_WINDOW, f)["skipped"] == "off"
        assert f.calls == []

    def test_sends_step_one_then_waits_and_marks_contacted(self, db_session, brand):
        o = _lead(db_session, brand)
        _lead(db_session, brand, company="No Email Co", email=None)
        _ready(db_session, brand)
        f = FakeSender()
        r = O.run_pass(db_session, IN_WINDOW, f)
        assert r["enrolled"] == 1 and r["sent"] == 1
        to, msg, _ = f.calls[0]
        assert to == "owner@sparkle.test"
        assert "Sparkle Co" in msg["subject"] and "Ana" in msg["text"]
        assert "100 Main St" in msg["html"] and "/evosys-sales-public/stop/" in msg["html"]
        db_session.refresh(o)
        assert o.stage == "contacted"
        # Nothing more until step 2 is due.
        assert O.run_pass(db_session, IN_WINDOW + timedelta(hours=1), f)["sent"] == 0
        assert O.run_pass(db_session, IN_WINDOW + timedelta(days=3), f)["sent"] == 1

    def test_never_outside_hours_or_over_limit(self, db_session, brand):
        for i in range(3):
            _lead(db_session, brand, company="Co %d" % i, email="c%d@x.test" % i)
        s = _ready(db_session, brand)
        f = FakeSender()
        assert O.run_pass(db_session, NIGHT, f)["skipped"] == "outside sending hours"
        assert f.calls == []
        # The limit is the campaign's; changing it needs a new approval and switch-on.
        from app.services.evosys_sales import campaigns as CA
        camp = CA.ensure_default(db_session, s)
        assert CA.save(db_session, camp, {"daily_limit": 2}, "u") and camp.sending_on is False
        assert O.run_pass(db_session, IN_WINDOW, f)["skipped"] == "off"
        CA.approve(db_session, camp, s, "u"); CA.switch(db_session, camp, s, True, "u"); db_session.commit()
        assert O.run_pass(db_session, IN_WINDOW, f)["sent"] == 2
        assert O.run_pass(db_session, IN_WINDOW + timedelta(minutes=5), f)["sent"] == 0

    def test_reply_booking_and_stage_stop_it(self, client, db_session, brand, manager_headers):
        a = _lead(db_session, brand, company="Replied Co", email="a@x.test")
        b = _lead(db_session, brand, company="Lost Co", email="b@x.test")
        _ready(db_session, brand)
        f = FakeSender()
        O.run_pass(db_session, IN_WINDOW, f)
        assert client.post("/sales/evosys/leads/%s/reply" % a.id, json={"note": "Call me"},
                           headers=manager_headers).status_code == 200
        b.stage = "lost"; db_session.commit()
        f.calls.clear()
        O.run_pass(db_session, IN_WINDOW + timedelta(days=6), f)  # Monday
        assert f.calls == []
        assert db_session.get(EvoSysOutreachState, a.id).stop_reason == "replied"
        assert db_session.get(EvoSysOutreachState, b.id).stop_reason == "stage"

    def test_text_steps_are_held_never_sent(self, db_session, brand):
        o = _lead(db_session, brand)
        s = _ready(db_session, brand)
        s.steps = [{"day": 0, "channel": "text", "subject": "", "body": "Hi {first_name}"}]
        db_session.commit()
        f = FakeSender()
        r = O.run_pass(db_session, IN_WINDOW, f)
        assert r["held"] == 1 and f.calls == []
        m = db_session.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id).one()
        assert m.status == "held" and m.channel == "text"

    def test_failures_stop_after_two(self, db_session, brand):
        o = _lead(db_session, brand)
        _ready(db_session, brand)
        f = FakeSender(ok=False)
        O.run_pass(db_session, IN_WINDOW, f)
        O.run_pass(db_session, IN_WINDOW + timedelta(hours=2), f)
        assert db_session.get(EvoSysOutreachState, o.id).stop_reason == "email_failed"


class TestOptOut:
    def test_link_stops_and_bad_token_refused(self, client, db_session, brand):
        o = _lead(db_session, brand)
        _ready(db_session, brand)
        O.run_pass(db_session, IN_WINDOW, FakeSender())
        token = O.opt_out_url(o.id).rsplit("/", 1)[1]
        assert client.get("/evosys-sales-public/stop/" + token).status_code == 200
        st = db_session.get(EvoSysOutreachState, o.id)
        db_session.refresh(st)
        assert st.stop_reason == "opted_out" and st.opted_out_at is not None
        assert client.get("/evosys-sales-public/stop/forged").status_code == 404
        assert O.restart(db_session, o.id) is None


class TestScreens:
    def test_overview_conversations_results(self, client, db_session, brand, manager_headers):
        _lead(db_session, brand)
        _lead(db_session, brand, company="Call Co", email=None)
        _ready(db_session, brand)
        O.run_pass(db_session, IN_WINDOW, FakeSender())
        ov = client.get("/sales/evosys/overview", headers=manager_headers).json()
        assert ov["counts"]["leads"] == 2 and ov["counts"]["to_call"] == 1
        conv = client.get("/sales/evosys/conversations", headers=manager_headers).json()["conversations"]
        assert conv[0]["company"] == "Sparkle Co" and conv[0]["message_count"] == 1
        res = client.get("/sales/evosys/results", headers=manager_headers).json()
        assert res["totals"]["in_followup"] == 1
        assert client.get("/sales/evosys/appointments", headers=manager_headers).status_code == 200
        lead_id = conv[0]["id"]
        d = client.get("/sales/evosys/leads/%s" % lead_id, headers=manager_headers).json()
        assert d["messages"][0]["status"] == "sent" and d["next"]["step"] == 2
