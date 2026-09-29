# -*- coding: utf-8 -*-
"""Sales Pipeline command center (WS4).

    GET  /pipeline/summary        KPIs / stages / funnel / activity, truthful
    GET  /pipeline/appointments   booking-link appointments, scoped
    POST /pipeline/approve/{id}   a human approval is stamped send_source=MANUAL

Numbers come from real rows; what the data model cannot state is null.
"""
import itertools
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import (AuditLogEntry, BookingLink, Lead, Organization,
                               PipelineConversation, Reply, User)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
NOW = datetime.utcnow()


def _org(db, name, industry="energy"):
    o = Organization(name=name, slug="ps-%s" % uuid.uuid4().hex[:8], plan="enterprise",
                     industry=industry, is_active=True)
    db.add(o)
    db.commit()
    return o


def _user(db, org, role, label="u"):
    u = User(organization_id=org.id, email="%s-%d@ps.test" % (label, next(_SEQ)),
             password_hash=hash_password("Pass12345!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


def _conv(db, org, advisor, stage, *, days_ago=1, sent=1, replies=0, booked=False,
          flagged=False, is_test=False, channel="sms"):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="L%d" % next(_SEQ),
                last_name="X", tier="new_inquiry", status="sent", is_test=is_test)
    db.add(lead)
    db.flush()
    at = NOW - timedelta(days=days_ago)
    p = PipelineConversation(organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
                             stage=stage, channel=channel, messages_sent=sent,
                             replies_received=replies, flagged=flagged,
                             booked_at=at if booked else None, created_at=at, updated_at=at)
    db.add(p)
    db.commit()
    return p, lead


@pytest.fixture()
def world(db_session):
    db = db_session
    org = _org(db, "Energy Co")
    other = _org(db, "Other Co")
    admin = _user(db, org, "org_admin", "admin")
    adv = _user(db, org, "advisor", "adv")
    fadmin = _user(db, other, "org_admin", "fadmin")
    fadv = _user(db, other, "advisor", "fadv")
    return dict(db=db, org=org, other=other, admin=admin, adv=adv, fadmin=fadmin, fadv=fadv)


def _seed(w):
    db, org, admin, adv = w["db"], w["org"], w["admin"], w["adv"]
    # current 30-day cohort: 4 conversations, 2 replied, 1 booked
    _conv(db, org, adv, "outreach_sent", days_ago=2)
    _conv(db, org, adv, "replied", days_ago=3, replies=1)
    _, booked_lead = _conv(db, org, admin, "booked", days_ago=4, replies=1, booked=True)
    _conv(db, org, admin, "booking_sent", days_ago=5, channel="email")
    # previous cohort: 2 conversations, 2 replied, 0 booked
    _conv(db, org, adv, "stopped", days_ago=40, replies=1)
    _conv(db, org, adv, "flagged", days_ago=45, replies=1, flagged=True)
    # never counted
    _conv(db, org, adv, "replied", days_ago=2, replies=1, is_test=True)
    _conv(db, w["other"], w["fadv"], "replied", days_ago=2, replies=1)
    db.add(BookingLink(lead_id=booked_lead.id, user_id=admin.id, status="confirmed",
                       booked_time=NOW + timedelta(days=3), confirmed_at=NOW - timedelta(days=1),
                       appt_label="Energy Rate Review"))
    db.add(Reply(lead_id=booked_lead.id, body="Can you send me a rate quote?",
                 received_at=NOW - timedelta(hours=2)))
    db.commit()
    return booked_lead


def test_summary_kpis_are_computed_from_real_rows(client, world):
    _seed(world)
    r = client.get("/pipeline/summary?days=30", headers=_h(world["db"], world["admin"]))
    assert r.status_code == 200, r.text
    s = r.json()
    k = s["kpis"]
    assert s["total_conversations"] == 6          # test lead + foreign org excluded
    assert k["active_conversations"]["value"] == 5  # stopped excluded
    assert k["awaiting_booking"]["value"] == 1
    assert k["needs_human"]["value"] == 1
    assert k["reply_rate"]["value"] == 50.0 and k["reply_rate"]["previous"] == 100.0
    assert k["reply_rate"]["trend_points"] == -50.0
    assert k["conversion_rate"]["value"] == 25.0 and k["conversion_rate"]["previous"] == 0.0
    assert k["conversations_started"]["value"] == 4 and k["conversations_started"]["trend_pct"] == 100.0
    assert k["confirmed_appointments"]["value"] == 1
    assert k["confirmed_appointments"]["trend_pct"] is None   # previous period was 0
    assert k["upcoming_appointments"]["value"] == 1
    assert k["projected_bookings"]["value"] is None
    assert k["active_conversations"]["trend_pct"] is None      # snapshot: no history


def test_stage_cards_and_untracked_stages(client, world):
    _seed(world)
    s = client.get("/pipeline/summary", headers=_h(world["db"], world["admin"])).json()
    by = {x["key"]: x for x in s["stages"]}
    assert by["outreach_sent"]["count"] == 1 and by["replied"]["count"] == 1
    assert by["booking_sent"]["by_channel"] == {"email": 1}
    assert by["flagged"]["label"] == "Needs Human" and by["flagged"]["count"] == 1
    for untracked in ("confirmed", "kept", "sale"):
        assert by[untracked]["count"] is None and by[untracked]["tracked"] is False
    assert by["replied"]["avg_age_days"] is not None


def test_funnel_is_cumulative_from_the_period_cohort(client, world):
    _seed(world)
    f = {x["key"]: x for x in client.get(
        "/pipeline/summary", headers=_h(world["db"], world["admin"])).json()["funnel"]}
    assert f["started"]["count"] == 4 and f["outreach_sent"]["count"] == 4
    assert f["replied"]["count"] == 2 and f["booking_sent"]["count"] == 2
    assert f["booked"]["count"] == 1 and f["confirmed"]["count"] == 1
    assert f["kept"]["count"] is None and f["booked"]["pct_of_started"] == 25.0


def test_empty_workspace_reports_null_rates_not_zero(client, world):
    s = client.get("/pipeline/summary", headers=_h(world["db"], world["admin"])).json()
    assert s["kpis"]["reply_rate"]["value"] is None
    assert s["kpis"]["conversion_rate"]["value"] is None
    assert s["kpis"]["active_conversations"]["value"] == 0
    assert s["recent_activity"] == []


def test_advisor_sees_only_their_own_conversations(client, world):
    _seed(world)
    s = client.get("/pipeline/summary", headers=_h(world["db"], world["adv"])).json()
    assert s["scope"] == "own_conversations"
    assert s["total_conversations"] == 4
    # the confirmed booking belongs to the admin's lead
    assert s["kpis"]["confirmed_appointments"]["value"] == 0


def test_other_tenant_sees_none_of_it(client, world):
    _seed(world)
    s = client.get("/pipeline/summary", headers=_h(world["db"], world["fadmin"])).json()
    assert s["organization_id"] == world["other"].id
    assert s["total_conversations"] == 1
    assert not [a for a in s["recent_activity"] if a["kind"] == "reply"]
    appts = client.get("/pipeline/appointments", headers=_h(world["db"], world["fadmin"])).json()
    assert appts["items"] == []


def test_recent_activity_is_real_replies_and_audit_rows(client, world):
    lead = _seed(world)
    db = world["db"]
    db.add(AuditLogEntry(organization_id=world["org"].id, actor_user_id=world["admin"].id,
                         action="lead.stage_moved", target_type="lead", target_id=lead.id))
    db.add(AuditLogEntry(organization_id=world["other"].id, actor_user_id=world["fadmin"].id,
                         action="lead.stage_moved", target_type="lead", target_id="x"))
    db.commit()
    acts = client.get("/pipeline/summary", headers=_h(db, world["admin"])).json()["recent_activity"]
    kinds = [(a["kind"], a["title"]) for a in acts]
    assert ("reply", "Lead replied") in kinds
    assert ("audit", "Lead moved to a new stage") in kinds
    assert len([a for a in acts if a["kind"] == "audit"]) == 1


def test_lead_types_follow_the_vertical(client, world, db_session):
    s = client.get("/pipeline/summary", headers=_h(world["db"], world["admin"])).json()
    values = [t["value"] for t in s["lead_types"]]
    assert "commercial" in values and "residential" in values
    assert "file_check" not in values and "code_lead" not in values


def test_appointments_endpoint(client, world):
    _seed(world)
    items = client.get("/pipeline/appointments", headers=_h(world["db"], world["admin"])).json()["items"]
    assert len(items) == 1 and items[0]["status"] == "confirmed" and items[0]["upcoming"] is True
    assert items[0]["appointment_type"] == "Energy Rate Review"


def test_approve_stamps_manual_send_source_and_keeps_the_gate(client, world, monkeypatch):
    db = world["db"]
    p, lead = _conv(db, world["org"], world["adv"], "flagged", flagged=True)
    calls = []

    def fake_send_sms(**kw):
        calls.append(kw)

    import app.services.sms_service as sms
    monkeypatch.setattr(sms, "send_sms", fake_send_sms)
    r = client.post("/pipeline/approve/%s" % p.id, headers=_h(db, world["adv"]),
                    json={"pipeline_id": p.id, "message": "Happy to help", "send": True})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0]["send_source"] == "manual"
    assert calls[0]["sent_by_user_id"] == world["adv"].id
    # a foreign workspace cannot approve it
    p2, _ = _conv(db, world["org"], world["adv"], "flagged", flagged=True)
    r = client.post("/pipeline/approve/%s" % p2.id, headers=_h(db, world["fadmin"]),
                    json={"pipeline_id": p2.id, "message": "x", "send": False})
    assert r.status_code == 404
    assert len(calls) == 1


def test_summary_requires_auth(client):
    assert client.get("/pipeline/summary").status_code in (401, 403)


def test_summary_matches_a_python_recount_over_many_rows(client, world):
    """SQL aggregation must give exactly what counting the rows by hand gives."""
    import random
    rnd = random.Random(7)
    db, org = world["db"], world["org"]
    stages = ["outreach_sent", "replied", "ai_responding", "booking_sent", "booked",
              "stopped", "dnc", "completed", "flagged"]
    made = []
    for i in range(80):
        st = rnd.choice(stages)
        sent, rep = rnd.randint(0, 3), rnd.randint(0, 2)
        booked = st == "booked" and rnd.random() < 0.7
        p, _ = _conv(db, org, rnd.choice([world["admin"], world["adv"]]), st,
                     days_ago=rnd.choice([0, 1, 5, 12, 20, 28, 33, 40, 50, 58]),  # clear of the 30-day edge sent=sent, replies=rep, booked=booked,
                     flagged=(st == "flagged"), channel=rnd.choice(["sms", "email"]))
        made.append(p)
    s = client.get("/pipeline/summary?days=30", headers=_h(db, world["admin"])).json()
    cutoff = NOW - timedelta(days=30)
    cur = [p for p in made if p.created_at >= cutoff]
    by_stage = {}
    for p in made:
        by_stage[p.stage] = by_stage.get(p.stage, 0) + 1
    got = {x["key"]: x["count"] for x in s["stages"] if x["tracked"]}
    for st, n in by_stage.items():
        assert got[st] == n
    assert s["total_conversations"] == 80
    terminal = {"stopped", "dnc", "completed", "sale"}
    assert s["kpis"]["active_conversations"]["value"] == sum(1 for p in made if p.stage not in terminal)
    sent = [p for p in cur if p.messages_sent > 0]
    exp_reply = round(100.0 * sum(1 for p in sent if p.replies_received > 0) / len(sent), 1) if sent else None
    assert s["kpis"]["reply_rate"]["value"] == exp_reply
    booked = sum(1 for p in cur if p.booked_at or p.stage == "booked")
    assert s["kpis"]["conversion_rate"]["value"] == (round(100.0 * booked / len(cur), 1) if cur else None)
    assert s["funnel"][0]["count"] == len(cur)
    email_bs = sum(1 for p in made if p.stage == "booking_sent" and p.channel == "email")
    assert (next(x for x in s["stages"] if x["key"] == "booking_sent")["by_channel"].get("email", 0)
            == email_bs)
    ages = next(x for x in s["stages"] if x["key"] == "replied")
    if by_stage.get("replied"):
        assert ages["avg_age_days"] is not None and ages["oldest_age_days"] >= ages["avg_age_days"]


def test_conversations_paging_is_backward_compatible(client, world):
    db = world["db"]
    for i in range(7):
        _conv(db, world["org"], world["adv"], "replied", days_ago=i)
    h = _h(db, world["admin"])
    legacy = client.get("/pipeline/conversations", headers=h).json()
    assert isinstance(legacy, list) and len(legacy) == 7
    p1 = client.get("/pipeline/conversations?paged=true&limit=5&offset=0", headers=h).json()
    p2 = client.get("/pipeline/conversations?paged=true&limit=5&offset=5", headers=h).json()
    assert p1["total"] == 7 and len(p1["items"]) == 5 and len(p2["items"]) == 2
    ids = [x["pipeline_id"] for x in p1["items"] + p2["items"]]
    assert len(set(ids)) == 7
