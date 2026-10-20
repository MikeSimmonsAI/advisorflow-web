# -*- coding: utf-8 -*-
"""EvoSys Pro Roofing program - /roofing-ops/*.

Covers: explicit feature gate (NULL allow-list is NOT roofing), tenant
isolation, owner vs salesperson scope, setup (fields the importer auto-maps,
the follow-up sequence), journey derivation from real rows, metrics
denominators and touch attribution, inbox prioritization, inspection booking
against real capacity (and refusal of a taken slot), pipeline moves, outreach
enrollment respecting qualification and the per-workspace text switch, and
the sample workspace. Nothing here reaches a provider (conftest blocks Twilio).
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import (BookingLink, CadenceState, CadenceTemplate, Lead, Message, Notification,
                               Organization, Reply, ReplyClassification, User)
from app.models.roofing_models import RoofingOpportunity, RoofingStageEvent
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def org(db, name, features=("leads", "roofing_program")):
    o = Organization(name=name, slug="rf-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry="roofing", is_active=True, timezone="America/Chicago",
                     enabled_features=json.dumps(list(features)) if features is not None else None)
    db.add(o)
    db.commit()
    return o


def user(db, o, role, label, **kw):
    u = User(organization_id=o.id, email="%s-%d@roof.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(), role=role,
             is_active=True, must_change_password=False, **kw)
    db.add(u)
    db.commit()
    return u


def h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def lead(db, o, first="James", owner=None, created=None, **kw):
    n = next(_SEQ)
    defaults = dict(phone="+1214555%04d" % (200 + n), email="home%d@example.com" % n, sms_consent=True, status="new",
                    street_address="%d Oak Hollow Dr" % n, city="Plano", state="TX", zip_code="75024")
    defaults.update(kw)
    l = Lead(organization_id=o.id, first_name=first, last_name="Homeowner", assigned_to_id=owner.id if owner else None,
             created_at=created or datetime.utcnow() - timedelta(days=2), **defaults)
    db.add(l)
    db.commit()
    return l


def sent(db, l, sender, at, state="delivered"):
    m = Message(lead_id=l.id, sender_id=sender.id, body="Hi there", send_state=state, sent_at=at)
    db.add(m)
    db.commit()
    return m


def reply(db, l, at, cls=ReplyClassification.INTERESTED, body="Yes please", reviewed=False):
    r = Reply(lead_id=l.id, body=body, source="sms", classification=cls, received_at=at,
              reviewed_at=at if reviewed else None)
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def world(db_session):
    db = db_session
    a = org(db, "Acme Roofing")
    b = org(db, "Other Roofing")
    a_owner = user(db, a, "org_admin", "owner")
    a_rep = user(db, a, "advisor", "alex", available_days="0,1,2,3,4,5,6", available_start_time="08:00",
                 available_end_time="18:00", booking_timezone="America/Chicago")
    a_rep2 = user(db, a, "advisor", "jordan")
    b_owner = user(db, b, "org_admin", "bowner")
    return dict(db=db, a=a, b=b, a_owner=a_owner, a_rep=a_rep, a_rep2=a_rep2, b_owner=b_owner)


# ── gating & isolation ──────────────────────────────────────────────────────
def test_feature_required_and_null_allow_list_is_not_roofing(client, db_session):
    db = db_session
    no_feat = org(db, "Plain", features=("leads",))
    legacy = org(db, "Legacy", features=None)            # NULL = everything, but NOT roofing
    u1 = user(db, no_feat, "org_admin", "plain")
    u2 = user(db, legacy, "org_admin", "legacy")
    assert client.get("/roofing-ops/status", headers=h(db, u1)).status_code in (402, 403)
    r = client.get("/roofing-ops/status", headers=h(db, u2))
    assert r.status_code == 403 and "Roofing" in r.json()["detail"]


def test_status_and_setup_registers_import_fields_and_sequence(client, world):
    db, a, owner = world["db"], world["a"], world["a_owner"]
    r = client.get("/roofing-ops/status", headers=h(db, owner))
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False and r.json()["can_manage"] is True
    r = client.post("/roofing-ops/setup", headers=h(db, owner))
    assert r.status_code == 200, r.text
    db.refresh(a)
    keys = {d["key"] for d in json.loads(a.crm_custom_fields)}
    assert {"roof_issue", "service_type", "inspection_interest", "urgency"} <= keys
    # The universal importer now auto-maps a roofing column to the custom field.
    from app.services.intake import engine as ENG
    from app.services.intake import fields as F
    m = F.suggest_mapping(["First Name", "Roof Issue", "Urgency"], ENG.org_custom_field_keys(db, a.id))
    assert m["Roof Issue"] == {"kind": "custom", "target": "roof_issue", "confidence": "high"}
    t = db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == a.id).one()
    assert t.is_default and t.industry == "roofing"
    # idempotent
    assert client.post("/roofing-ops/setup", headers=h(db, owner)).json()["fields_added"] == []
    assert db.query(CadenceTemplate).filter(CadenceTemplate.organization_id == a.id).count() == 1


def test_salesperson_cannot_configure_and_sees_only_own_book(client, world):
    db, a, rep, rep2 = world["db"], world["a"], world["a_rep"], world["a_rep2"]
    mine = lead(db, a, "Mine", owner=rep)
    lead(db, a, "Theirs", owner=rep2)
    assert client.post("/roofing-ops/setup", headers=h(db, rep)).status_code == 403
    assert client.put("/roofing-ops/profile", json={"company_name": "X"}, headers=h(db, rep)).status_code == 403
    rows = client.get("/roofing-ops/leads", headers=h(db, rep)).json()["items"]
    assert [r["lead_id"] for r in rows] == [mine.id]
    assert client.get("/roofing-ops/leads", headers=h(db, world["a_owner"])).json()["total"] == 2


def test_other_company_ids_are_404(client, world):
    db = world["db"]
    other = lead(db, world["b"], "Bee")
    hdr = h(db, world["a_owner"])
    assert client.get("/roofing-ops/leads/%s" % other.id, headers=hdr).status_code == 404
    assert client.post("/roofing-ops/leads/%s/stage" % other.id, json={"stage": "won"}, headers=hdr).status_code == 404
    assert client.patch("/roofing-ops/leads/%s" % other.id, json={"urgency": "high"}, headers=hdr).status_code == 404
    # and B's dashboard knows nothing of A
    lead(db, world["a"], "Aye")
    d = client.get("/roofing-ops/dashboard", headers=h(db, world["b_owner"])).json()
    assert d["kpis"][0]["value"] == 1


# ── journey ─────────────────────────────────────────────────────────────────
def test_journey_moves_forward_from_real_activity_only(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    l = lead(db, a, owner=rep)
    hdr = h(db, owner)
    assert client.get("/roofing-ops/leads/%s" % l.id, headers=hdr).json()["opportunity"]["stage"] in ("imported", "qualified")
    sent(db, l, rep, datetime.utcnow() - timedelta(hours=5), state="blocked")      # never left
    assert client.get("/roofing-ops/leads/%s" % l.id, headers=hdr).json()["opportunity"]["stage"] in ("imported", "qualified")
    sent(db, l, rep, datetime.utcnow() - timedelta(hours=4))
    assert client.get("/roofing-ops/leads/%s" % l.id, headers=hdr).json()["opportunity"]["stage"] == "contacted"
    reply(db, l, datetime.utcnow() - timedelta(hours=3))
    w = client.get("/roofing-ops/leads/%s" % l.id, headers=hdr).json()
    assert w["opportunity"]["stage"] == "engaged"
    assert w["next_action"]["key"] == "reply"
    # a person marks it estimate_sent; activity never drags it back
    assert client.post("/roofing-ops/leads/%s/stage" % l.id, json={"stage": "estimate_sent"},
                       headers=hdr).json()["stage"] == "estimate_sent"
    sent(db, l, rep, datetime.utcnow() - timedelta(hours=1))
    assert client.get("/roofing-ops/leads/%s" % l.id, headers=hdr).json()["opportunity"]["stage"] == "estimate_sent"
    # lost needs a reason
    assert client.post("/roofing-ops/leads/%s/stage" % l.id, json={"stage": "lost"}, headers=hdr).status_code == 400
    r = client.post("/roofing-ops/leads/%s/stage" % l.id, json={"stage": "lost", "reason": "Price"}, headers=hdr)
    assert r.json()["lost_reason"] == "Price"
    ev = db.query(RoofingStageEvent).filter(RoofingStageEvent.lead_id == l.id).all()
    assert [e.to_stage for e in ev][-1] == "lost" and ev[-1].actor_user_id == owner.id


def test_patch_roofing_fields_validates_choices_and_owner(client, world):
    db, a, owner = world["db"], world["a"], world["a_owner"]
    l = lead(db, a)
    hdr = h(db, owner)
    assert client.patch("/roofing-ops/leads/%s" % l.id, json={"urgency": "whenever"}, headers=hdr).status_code == 400
    w = client.patch("/roofing-ops/leads/%s" % l.id, headers=hdr,
                     json={"roof_issue": "Hail damage", "service_type": "replacement", "urgency": "high",
                           "owner_id": world["a_rep"].id, "estimate_amount": 14600}).json()
    assert w["roofing"]["roof_issue"] == "Hail damage" and w["lead"]["owner_id"] == world["a_rep"].id
    assert w["opportunity"]["estimate_amount"] == 14600
    assert client.patch("/roofing-ops/leads/%s" % l.id, json={"owner_id": world["b_owner"].id},
                        headers=hdr).status_code == 404


# ── metrics ─────────────────────────────────────────────────────────────────
def test_dashboard_denominators_and_touch_attribution(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    now = datetime.utcnow()
    l1 = lead(db, a, "One", owner=rep, created=now - timedelta(days=5))
    l2 = lead(db, a, "Two", owner=rep, created=now - timedelta(days=5))
    l3 = lead(db, a, "Three", owner=rep, created=now - timedelta(days=5))
    lead(db, a, "Old", owner=rep, created=now - timedelta(days=90))          # outside range
    # l1: touch1, touch2, reply after touch 2
    sent(db, l1, rep, now - timedelta(days=4)); sent(db, l1, rep, now - timedelta(days=3))
    reply(db, l1, now - timedelta(days=2, hours=20))
    # l2: touch1, then STOP
    sent(db, l2, rep, now - timedelta(days=4))
    reply(db, l2, now - timedelta(days=3, hours=20), cls=ReplyClassification.DNC, body="STOP", reviewed=True)
    d = client.get("/roofing-ops/dashboard", headers=h(db, owner)).json()
    k = {m["key"]: m for m in d["kpis"]}
    assert k["imported"]["value"] == 3
    assert (k["contacted"]["value"], k["contacted"]["denominator"]) == (2, 3)
    assert (k["response_rate"]["value"], k["response_rate"]["denominator"], k["response_rate"]["pct"]) == (2, 2, 100.0)
    assert (k["engagement"]["value"], k["engagement"]["pct"]) == (1, 50.0)
    assert k["opt_outs"]["value"] == 1
    t = {b["touch"]: b for b in d["by_touch"]}
    assert (t["1"]["received"], t["1"]["replied"], t["1"]["opted_out"]) == (2, 0, 1)
    assert (t["2"]["received"], t["2"]["replied"], t["2"]["reply_rate"]) == (1, 1, 100.0)
    f = {s["key"]: s for s in d["funnel"]}
    assert f["imported"]["count"] == 3 and f["engaged"]["count"] == 1
    assert d["has_data"] and not d["sample_data"]


def test_dashboard_empty_is_honest(client, world):
    d = client.get("/roofing-ops/dashboard", headers=h(world["db"], world["a_owner"])).json()
    assert d["has_data"] is False and d["kpis"][1]["pct"] is None
    assert all(b["reply_rate"] is None for b in d["by_touch"])


# ── inbox ───────────────────────────────────────────────────────────────────
def test_inbox_prioritizes_human_then_ready_to_book(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    now = datetime.utcnow()
    q = lead(db, a, "Question", owner=rep); sent(db, q, rep, now - timedelta(hours=6))
    reply(db, q, now - timedelta(hours=5), cls=ReplyClassification.QUESTION, body="Is it free?")
    y = lead(db, a, "Yes", owner=rep); sent(db, y, rep, now - timedelta(hours=6))
    reply(db, y, now - timedelta(minutes=5))
    s = lead(db, a, "Stop", owner=rep); sent(db, s, rep, now - timedelta(hours=6))
    reply(db, s, now - timedelta(hours=1), cls=ReplyClassification.DNC, body="STOP")
    r = client.get("/roofing-ops/inbox", headers=h(db, owner)).json()
    order = [(i["name"].split()[0], i["bucket"]) for i in r["items"]]
    assert order == [("Question", "needs_human"), ("Yes", "ready_to_book"), ("Stop", "opted_out")]
    counts = {b["key"]: b["count"] for b in r["buckets"]}
    assert counts["needs_human"] == 1 and counts["ready_to_book"] == 1
    only = client.get("/roofing-ops/inbox?bucket=ready_to_book", headers=h(db, owner)).json()["items"]
    assert [i["lead_id"] for i in only] == [y.id]


# ── inspections ─────────────────────────────────────────────────────────────
def _next_weekday_noon_utc():
    d = datetime.utcnow() + timedelta(days=2)
    return d.date()


def test_openings_and_booking_on_real_capacity(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    client.post("/roofing-ops/setup", headers=h(db, owner))
    l = lead(db, a, "James", owner=rep)
    day = _next_weekday_noon_utc().isoformat()
    o = client.get("/roofing-ops/leads/%s/openings?date_from=%s&days=1" % (l.id, day), headers=h(db, owner)).json()
    assert o["availability_status"] == "ok" and o["slots"], o
    assert o["offer_text"].startswith("Great, James!") and o["slots"][0]["label"] in o["offer_text"]
    start = o["slots"][0]["starts_at"]
    b = client.post("/roofing-ops/leads/%s/book" % l.id, headers=h(db, owner),
                    json={"starts_at": start, "inspection_type": "Roof Inspection"})
    assert b.status_code == 200, b.text
    out = b.json()
    assert out["stage"] == "inspection_booked" and out["confirmation"]["sent"] is False
    bl = db.query(BookingLink).filter(BookingLink.lead_id == l.id).one()
    assert bl.status == "booked" and bl.appt_duration == 60
    # one alert for the salesperson, and no duplicate on replay of the view
    assert db.query(Notification).filter(Notification.lead_id == l.id).count() == 1
    # the same slot is now taken, for anyone
    l2 = lead(db, a, "Maria", owner=rep)
    again = client.post("/roofing-ops/leads/%s/book" % l2.id, headers=h(db, owner), json={"starts_at": start})
    assert again.status_code == 409
    o2 = client.get("/roofing-ops/leads/%s/openings?date_from=%s&days=1" % (l2.id, day), headers=h(db, owner)).json()
    assert start not in [s["starts_at"] for s in o2["slots"]]


def test_booking_refuses_opted_out_homeowner(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    l = lead(db, a, owner=rep, status="dnc")
    day = _next_weekday_noon_utc().isoformat()
    o = client.get("/roofing-ops/leads/%s/openings?date_from=%s&days=1" % (l.id, day), headers=h(db, owner)).json()
    r = client.post("/roofing-ops/leads/%s/book" % l.id, headers=h(db, owner), json={"starts_at": o["slots"][0]["starts_at"]})
    assert r.status_code == 409


# ── pipeline ────────────────────────────────────────────────────────────────
def test_pipeline_columns_owner_filter_and_values(client, world):
    db, a, rep, rep2, owner = world["db"], world["a"], world["a_rep"], world["a_rep2"], world["a_owner"]
    l1 = lead(db, a, "One", owner=rep)
    lead(db, a, "Two", owner=rep2)
    hdr = h(db, owner)
    client.patch("/roofing-ops/leads/%s" % l1.id, json={"estimate_amount": 12000}, headers=hdr)
    client.post("/roofing-ops/leads/%s/stage" % l1.id, json={"stage": "estimate_sent"}, headers=hdr)
    p = client.get("/roofing-ops/pipeline", headers=hdr).json()
    keys = [c["key"] for c in p["columns"]]
    assert keys == ["imported", "qualified", "contacted", "engaged", "inspection_offered", "inspection_booked",
                    "inspected", "estimate_sent", "won", "lost"]
    col = {c["key"]: c for c in p["columns"]}
    assert col["estimate_sent"]["count"] == 1 and p["open_estimate_value"] == 12000
    mine = client.get("/roofing-ops/pipeline?owner=%s" % rep2.id, headers=hdr).json()
    assert mine["total"] == 1


# ── outreach ────────────────────────────────────────────────────────────────
def test_enroll_respects_qualification_and_text_switch(client, world):
    db, a, rep, owner = world["db"], world["a"], world["a_rep"], world["a_owner"]
    a.enabled_features = json.dumps(["leads", "roofing_program", "cadences", "sms"])
    db.commit()
    good = lead(db, a, "Good", owner=rep)
    lead(db, a, "NoPhone", owner=rep, phone=None)
    lead(db, a, "Dnc", owner=rep, status="dnc")
    r = client.post("/roofing-ops/outreach/enroll", json={}, headers=h(db, owner))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["enrolled"] == 1 and out["texts_on"] is False and "OFF" in out["note"]
    assert db.query(CadenceState).filter(CadenceState.lead_id == good.id).count() == 1
    assert db.query(Message).count() == 0          # enrolment sends nothing
    assert client.post("/roofing-ops/outreach/enroll", json={}, headers=h(db, rep)).status_code == 403
    assert client.post("/roofing-ops/text-switch", json={"on": True}, headers=h(db, owner)).status_code == 403


# ── sample workspace ────────────────────────────────────────────────────────
def test_sample_workspace_is_labelled_and_unsendable(db_session):
    from app.services.roofing import demo_seed
    out = demo_seed.seed(db_session)
    assert out["homeowners_total"] == 64
    org_ = db_session.query(Organization).filter(Organization.id == out["organization_id"]).one()
    feats = json.loads(org_.enabled_features)
    assert org_.is_demo and "sms" not in feats and "email" not in feats and "cadences" not in feats
    leads = db_session.query(Lead).filter(Lead.organization_id == org_.id).all()
    assert all(l.is_test and l.email.endswith("@example.com") for l in leads)
    again = demo_seed.seed(db_session)
    assert again["homeowners_added"] == 0
    from app.services.roofing import metrics as M
    d0, d1 = M.parse_range(None, None)
    d = M.compute(db_session, org_, leads, d0, d1)
    assert d["sample_data"] is True and d["has_data"]
    stages = {s["key"]: s["count"] for s in d["stage_now"]}
    assert sum(stages.values()) == 64 and stages["won"] + stages["lost"] > 0
