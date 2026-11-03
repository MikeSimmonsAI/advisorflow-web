"""SCI campaign intelligence: every event is attributed to a campaign from
the strongest evidence on record (never guessed), each campaign's numbers are
separate, All campaigns reconciles, the inbox filters and stamps by campaign,
and no tenant sees another tenant's campaign. Synthetic families only."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from app.models.models import (BookingLink, CadenceState, EmailMessage, Lead, Message, Organization, User)
from app.models.program_models import (CampaignFamily, ProgramEmailTouch, ProgramImportRun, ProgramResponse,
                                       ProgramSourceRecord)
from app.services.programs import campaign_attribution as CA
from app.services.programs import sci_analytics as SA
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_analytics import _today_at, _touch

CA_KEY = "central_alabama_ms"


@pytest.fixture(autouse=True)
def _fresh_ledger():
    SA._CACHE.clear()
    yield
    SA._CACHE.clear()


def _central_alabama(db, org, *, created=None):
    fam = CampaignFamily(organization_id=org.id, key=CA_KEY, name="Central Alabama 2025–2026 MS", is_active=True)
    run = ProgramImportRun(organization_id=org.id, filename="synthetic-central-alabama.csv", dry_run=False)
    db.add_all([fam, run])
    db.flush()
    return run


def _ca_member(db, org, run, lead_or_none, sid, *, created=None, phone="2145550199", first="Synth", advisor=None):
    rec = ProgramSourceRecord(organization_id=org.id, source_lead_id=sid, raw_json="{}", import_run_id=run.id,
                              campaign_family=CA_KEY, location_status="mapped",
                              created_at=created or datetime.utcnow() - timedelta(days=20))
    lead = lead_or_none
    if lead is None:
        lead = Lead(organization_id=org.id, assigned_to_id=advisor.id if advisor else None,
                    first_name=first, last_name="Alabamacase", phone="1" + phone,
                    email="%s@example.test" % sid.lower(), status="sent")
        db.add(lead)
        db.flush()
        db.add(CadenceState(lead_id=lead.id, status="active"))
    rec.lead_id = lead.id
    db.add(rec)
    db.commit()
    return lead


def _reply(db, lead, text, cls="hot", at=None, family=None):
    r = ProgramResponse(organization_id=lead.organization_id, lead_id=lead.id, channel="sms", response_class=cls,
                        body_excerpt=text, received_at=at or _today_at(), handling_status="new",
                        campaign_family=family)
    db.add(r)
    db.commit()
    return r


@pytest.fixture()
def two_campaigns(program, sample_advisor):
    """Original campaign (the fixture's import: Veteran Planning Guide +
    General Survey) and a synthetic Central Alabama campaign."""
    db, org = program["db"], program["org"]
    for rec in db.query(ProgramSourceRecord).filter_by(organization_id=org.id).all():
        rec.created_at = datetime.utcnow() - timedelta(days=40)
    db.commit()
    orig = _lead_for(db, org, sample_advisor, "L001", first="Origa")
    survey = _lead_for(db, org, sample_advisor, "L005", phone="2145550105", first="Surva", email="sv@example.com")
    run = _central_alabama(db, org)
    ca1 = _ca_member(db, org, run, None, "CA-SYN-1", phone="2145550191", first="Bama", advisor=sample_advisor)
    ca2 = _ca_member(db, org, run, None, "CA-SYN-2", phone="2145550192", first="Selma", advisor=sample_advisor)
    week = datetime.utcnow() - timedelta(days=3)
    _touch(db, orig, 1, at=week)
    _touch(db, survey, 1)
    _touch(db, ca1, 1)
    _touch(db, ca2, 1, status="undelivered", code="30003")
    _reply(db, orig, "Yes please send the guide", "hot", at=week + timedelta(hours=2))
    _reply(db, ca1, "When can we meet?", "hot")
    _reply(db, survey, "Who is this?", "active")
    return {"db": db, "org": org, "orig": orig, "survey": survey, "ca1": ca1, "ca2": ca2, "run": run,
            "h": _h(db, program["admin"]), "admin": program["admin"]}


def _a(client, h, q):
    r = client.get("/program/command/analytics?" + q, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _m(s):
    return {k: v["value"] for k, v in s["metrics"].items()}


# ── attribution rules ───────────────────────────────────────────────────────
def test_attribution_evidence_order(two_campaigns):
    t = two_campaigns
    db, org = t["db"], t["org"]
    M = CA.Memberships(db, org.id)
    now = datetime.utcnow()
    assert M.attribute(t["ca1"].id, now)[0] == CA_KEY and M.attribute(t["ca1"].id, now)[2] == "membership"
    assert M.attribute(t["orig"].id, now)[0] == "veteran_planning_guide"
    # explicit campaign on the event wins
    assert M.attribute(t["orig"].id, now, CA_KEY)[2] == "explicit"
    # an unknown explicit key is not trusted; falls back to membership
    assert M.attribute(t["orig"].id, now, "not_a_campaign")[2] == "membership"
    # a family joining a second campaign later: earlier events stay with the
    # first campaign, later ones are ambiguous (never guessed)
    _ca_member(db, org, t["run"], t["orig"], "CA-SYN-3", created=now - timedelta(days=1))
    M = CA.Memberships(db, org.id)
    assert M.attribute(t["orig"].id, now - timedelta(days=2))[0] == "veteran_planning_guide"
    k, _r, ev = M.attribute(t["orig"].id, now)
    assert k is None and ev == "ambiguous"
    # a family with no campaign membership at all
    assert M.attribute("no-such-lead", now) == (None, None, "unattributed")


def test_attribution_writes_nothing(two_campaigns, client):
    t = two_campaigns
    db = t["db"]
    before = (db.query(ProgramSourceRecord).count(), db.query(Message).count(), db.query(ProgramResponse).count(),
              [(r.id, r.campaign_family) for r in db.query(ProgramResponse).all()])
    for q in ("range=all", "range=all&campaign=" + CA_KEY, "range=7d&campaign=veteran_planning_guide"):
        _a(client, t["h"], q)
    client.get("/program/command/analytics/campaigns?range=all", headers=t["h"])
    client.get("/program/command?campaign=" + CA_KEY, headers=t["h"])
    db.expire_all()
    after = (db.query(ProgramSourceRecord).count(), db.query(Message).count(), db.query(ProgramResponse).count(),
             [(r.id, r.campaign_family) for r in db.query(ProgramResponse).all()])
    assert before == after          # read-only: no backfill, no send, no relabel


# ── analytics by campaign ───────────────────────────────────────────────────
def test_each_campaign_has_its_own_numbers(two_campaigns, client):
    t = two_campaigns
    allm = _m(_a(client, t["h"], "range=7d"))
    ca = _m(_a(client, t["h"], "range=7d&campaign=" + CA_KEY))
    vpg = _m(_a(client, t["h"], "range=7d&campaign=veteran_planning_guide"))
    gs = _m(_a(client, t["h"], "range=7d&campaign=general_survey"))
    assert (ca["attempts"], ca["delivered"], ca["failed"], ca["replies"], ca["interested"]) == (2, 1, 1, 1, 1)
    assert (vpg["attempts"], vpg["replies"], vpg["interested"]) == (1, 1, 1)
    assert (gs["attempts"], gs["replies"], gs["interested"]) == (1, 1, 0)
    assert allm["attempts"] == ca["attempts"] + vpg["attempts"] + gs["attempts"] == 4
    assert ca["loaded"] == 2 and vpg["loaded"] == 1
    # the original import as one scope (both of its families)
    group = [g for g in _a(client, t["h"], "range=7d")["campaign_options"]["groups"]]
    assert len(group) == 1 and group[0]["campaign_keys"] == ["general_survey", "veteran_planning_guide"]
    orig = _m(_a(client, t["h"], "range=7d&campaign=" + group[0]["value"]))
    assert orig["attempts"] == 2 and orig["replies"] == 2


def test_dates_apply_to_the_campaign(two_campaigns, client):
    t = two_campaigns
    q = "&campaign=veteran_planning_guide"
    assert _m(_a(client, t["h"], "range=today" + q))["attempts"] == 0      # its touch was 3 days ago
    assert _m(_a(client, t["h"], "range=7d" + q))["attempts"] == 1
    assert _m(_a(client, t["h"], "range=30d" + q))["attempts"] == 1
    assert _m(_a(client, t["h"], "range=all" + q))["attempts"] == 1
    d = (datetime.utcnow() - timedelta(days=3))
    day = SA.ct_day(d).isoformat()
    cu = _a(client, t["h"], "range=custom&start=%s&end=%s%s" % (day, day, q))
    assert _m(cu)["attempts"] == 1 and cu["window"]["preset"] == "custom"
    assert _m(_a(client, t["h"], "range=today&campaign=" + CA_KEY))["attempts"] == 2


def test_selector_lists_real_campaigns_with_status(two_campaigns, client):
    t = two_campaigns
    opts = _a(client, t["h"], "range=all")["campaign_options"]
    by = {c["key"]: c for c in opts["campaigns"]}
    assert by[CA_KEY]["name"] == "Central Alabama 2025–2026 MS" and by[CA_KEY]["status"] == "Active"
    assert by[CA_KEY]["families"] == 2
    assert by["veteran_planning_guide"]["status"] == "Off"          # the fixture's families are switched off


def test_drill_down_keeps_the_campaign_and_matches_the_card(two_campaigns, client):
    t = two_campaigns
    for metric in ("attempts", "failed", "replies", "interested", "families_contacted", "loaded"):
        card = _m(_a(client, t["h"], "range=7d&campaign=" + CA_KEY))[metric]
        d = client.get("/program/command/analytics/records?metric=%s&range=7d&campaign=%s" % (metric, CA_KEY),
                       headers=t["h"]).json()
        assert d["count"] == card == len(d["items"]), metric
        assert d["campaign"] == CA_KEY and d["campaign_name"].startswith("Central Alabama")
        assert all(i["campaign"]["key"] == CA_KEY for i in d["items"]), metric
    intr = client.get("/program/command/analytics/records?metric=interested&range=7d&campaign=" + CA_KEY,
                      headers=t["h"]).json()
    assert [i["name"] for i in intr["items"]] == ["Bama Alabamacase"]


def test_error_codes_and_email_touch_attribution(two_campaigns, client):
    t = two_campaigns
    db = t["db"]
    db.add(ProgramEmailTouch(organization_id=t["org"].id, lead_id=t["ca1"].id, campaign_family=CA_KEY,
                             touch_number=1, status="failed", reason="Bounced", attempted_at=_today_at()))
    db.commit()
    s = _a(client, t["h"], "range=7d&campaign=" + CA_KEY)
    assert {c["code"]: c["count"] for c in s["sms_error_codes"]} == {"30003": 1}
    m = _m(s)
    assert m["failed"] == 2                       # one SMS (30003) and one email
    email_fail = client.get("/program/command/analytics/records?metric=failed&channel=email&range=7d&campaign="
                            + CA_KEY, headers=t["h"]).json()
    assert email_fail["count"] == 1 and email_fail["items"][0]["campaign"]["evidence"] == "explicit"
    assert _m(_a(client, t["h"], "range=7d&campaign=veteran_planning_guide"))["failed"] == 0


def test_appointment_counts_once_for_its_campaign(two_campaigns, client):
    t = two_campaigns
    db = t["db"]
    b = BookingLink(lead_id=t["ca1"].id, user_id=t["admin"].id, token="tok-syn-ca1", status="booked",
                    booked_time=datetime.utcnow() + timedelta(days=2), calendar_event_id="evt-syn",
                    confirmed_at=_today_at())
    db.add(b)
    db.commit()
    assert _m(_a(client, t["h"], "range=7d&campaign=" + CA_KEY))["appointments"] == 1
    assert _m(_a(client, t["h"], "range=7d&campaign=veteran_planning_guide"))["appointments"] == 0
    assert _m(_a(client, t["h"], "range=7d"))["appointments"] == 1


def test_all_campaigns_reconciles(two_campaigns, client):
    t = two_campaigns
    db, org = t["db"], t["org"]
    # a family in BOTH campaigns: joined Central Alabama after the original touch
    _ca_member(db, org, t["run"], t["orig"], "CA-SYN-4", created=datetime.utcnow() - timedelta(days=2))
    _touch(db, t["orig"], 2)                      # today: ambiguous (two campaigns, nothing on the event)
    _reply(db, t["orig"], "Thanks", "active", family=CA_KEY)      # explicit
    SA._CACHE.clear()
    c = client.get("/program/command/analytics/campaigns?range=7d", headers=t["h"]).json()
    assert c["balanced"] is True
    rec = {r["metric"]: r for r in c["reconciliation"]}
    assert rec["attempts"]["all"] == 5 and rec["attempts"]["ambiguous"] == 1
    assert rec["replies"]["all"] == rec["replies"]["campaigns"] + rec["replies"]["unattributed"] + rec["replies"]["ambiguous"]
    fam = {r["metric"]: r for r in c["families_distinct"]}
    # the shared family is counted once overall but in two parts
    assert fam["families_contacted"]["all_distinct"] == 4 < fam["families_contacted"]["sum_of_campaigns"]
    names = {r["name"] for r in c["campaigns"]}
    assert "Central Alabama 2025–2026 MS" in names and "Veteran Planning Guide" in names
    ca = next(r for r in c["campaigns"] if r["scope"] == CA_KEY)
    assert ca["delivery_denominator"] == 2 and ca["delivery_rate"] == 50.0 and ca["rate_denominator"] == 2


def test_no_activity_campaign_is_marked(two_campaigns, client):
    t = two_campaigns
    c = client.get("/program/command/analytics/campaigns?range=today", headers=t["h"]).json()
    vpg = next(r for r in c["campaigns"] if r["scope"] == "veteran_planning_guide")
    assert vpg["no_activity"] is True and vpg["attempts"] == 0 and vpg["reply_rate"] is None


# ── inbox ───────────────────────────────────────────────────────────────────
def _inbox(client, h, q=""):
    r = client.get("/program/command?limit=200" + q, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_inbox_stamps_and_filters_by_campaign(two_campaigns, client):
    t = two_campaigns
    all_ = _inbox(client, t["h"])
    stamps = {i["name"]: i["campaign"]["name"] for i in all_["inbox"]}
    assert stamps["Bama Alabamacase"] == "Central Alabama 2025–2026 MS"
    assert stamps["Origa Testcase"] == "Veteran Planning Guide"
    ca = _inbox(client, t["h"], "&campaign=" + CA_KEY)
    assert [i["name"] for i in ca["inbox"]] == ["Bama Alabamacase"] and ca["campaign_name"].startswith("Central")
    # combines with the existing filters
    assert _inbox(client, t["h"], "&campaign=%s&filter=unread" % CA_KEY)["filter_counts"]["all"] == 1
    assert _inbox(client, t["h"], "&campaign=%s&filter=email" % CA_KEY)["inbox"] == []
    group = all_["campaign_options"]["groups"][0]["value"]
    assert sorted(i["name"] for i in _inbox(client, t["h"], "&campaign=" + group)["inbox"]) == \
        ["Origa Testcase", "Surva Testcase"]
    assert _inbox(client, t["h"], "&campaign=unattributed")["inbox"] == []


def test_multi_campaign_family_keeps_its_history(two_campaigns, client):
    t = two_campaigns
    db, org = t["db"], t["org"]
    _ca_member(db, org, t["run"], t["orig"], "CA-SYN-5", created=datetime.utcnow() - timedelta(days=1))
    _reply(db, t["orig"], "Still interested", "hot", family=CA_KEY)
    item = next(i for i in _inbox(client, t["h"])["inbox"] if i["lead_id"] == t["orig"].id)
    assert item["campaign"]["key"] == CA_KEY and item["campaign"]["evidence"] == "explicit"
    assert {c["key"] for c in item["campaigns"]} == {"veteran_planning_guide", CA_KEY}
    # one conversation, in both campaign views - never split into two threads
    for scope in ("veteran_planning_guide", CA_KEY):
        assert [i["lead_id"] for i in _inbox(client, t["h"], "&campaign=" + scope)["inbox"]].count(t["orig"].id) == 1
    fam = client.get("/program/command/family/%s" % t["orig"].id, headers=t["h"]).json()
    assert [h["key"] for h in fam["campaign_history"]] == ["veteran_planning_guide", CA_KEY]
    by_text = {m["text"]: m["campaign"] for m in fam["thread"] if m.get("text")}
    assert by_text["Yes please send the guide"]["key"] == "veteran_planning_guide"   # before Central Alabama
    assert by_text["Still interested"]["key"] == CA_KEY                               # recorded on the reply


def test_unattributed_is_labelled(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L006", phone="2145550106", first="Nofam")
    rec = db.query(ProgramSourceRecord).filter_by(lead_id=lead.id).one()
    rec.campaign_family = None
    db.commit()
    _reply(db, lead, "Hello?", "active")
    h = _h(db, program["admin"])
    item = next(i for i in _inbox(client, h)["inbox"] if i["lead_id"] == lead.id)
    assert item["campaign"] == {"key": None, "name": "Unattributed", "evidence": "unattributed"}
    assert [i["lead_id"] for i in _inbox(client, h, "&campaign=unattributed")["inbox"]] == [lead.id]


# ── security ────────────────────────────────────────────────────────────────
def test_unknown_or_foreign_campaign_is_not_found(two_campaigns, client, db_session):
    t = two_campaigns
    other = Organization(name="Other Synthetic Co", slug="other-synth-co")
    db_session.add(other)
    db_session.flush()
    foreign_run = ProgramImportRun(organization_id=other.id, filename="theirs.csv", dry_run=False)
    db_session.add(foreign_run)
    db_session.commit()
    for scope in ("nope", "import:" + foreign_run.id, "import:nope"):
        for path in ("/program/command/analytics?campaign=", "/program/command?campaign=",
                     "/program/command/analytics/records?metric=attempts&campaign="):
            r = client.get(path + scope, headers=t["h"])
            assert r.status_code == 404 and r.json()["detail"] == "Campaign not found.", (path, scope)
    opts = _a(client, t["h"], "range=all")["campaign_options"]
    assert all(g["import_run_id"] != foreign_run.id for g in opts["groups"])


def test_other_tenant_sees_no_campaign_data(two_campaigns, client, db_session):
    t = two_campaigns
    from app.services.auth_service import hash_password
    other = Organization(name="Other Synthetic Org", slug="other-synth-org")
    db_session.add(other)
    db_session.flush()
    u = User(email="admin@other-synth.example.test", password_hash=hash_password("x" * 12),
             full_name="Other Admin", role="org_admin", organization_id=other.id, is_active=True)
    db_session.add(u)
    db_session.commit()
    h = _h(db_session, u)
    for path in ("/program/command/analytics?campaign=" + CA_KEY, "/program/command?campaign=" + CA_KEY,
                 "/program/command/analytics/campaigns", "/program/command/family/%s" % t["ca1"].id):
        r = client.get(path, headers=h)
        assert r.status_code in (403, 404), (path, r.status_code)
        assert "Alabamacase" not in r.text
