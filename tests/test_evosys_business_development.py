"""EvoSys Business Development (PR #663): add a networking card, find the
leads already there, build a campaign around an event from picked leads,
warm messages from saved facts only, no overlapping sequences, approve then
turn on, pause, a reply, an opt-out, access. Synthetic data, mocked outbound.

Works with the industry + market campaigns and lead grouping that were built
alongside (test_evosys_sales_walkthrough.py covers those)."""
from datetime import date, datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysCampaign, EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import outreach as O

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, brand_b, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)

MON_10AM = datetime(2026, 11, 23, 16, 0)          # Monday 10:00 Central
API = "/sales/evosys"


@pytest.fixture(autouse=True)
def _brand_env(monkeypatch, brand):
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", brand.id)


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, to, msg, s, opp_id):
        self.calls.append((to, msg))
        return {"success": True, "provider_message_id": "re_%d" % len(self.calls)}


def _team(client, h):
    r = client.put(API + "/outreach", json={"mailing_address": "100 Main St, Dallas, TX 75201",
                                            "booking_url": "https://app.evosyspro.live/book/evosys"}, headers=h)
    assert r.status_code == 200, r.text


def _card(client, h, **kw):
    body = {"contact_name": "Dana Lee", "company_name": "Lee Logistics", "email": "dana@lee.test",
            "phone": "214-555-0101", "role": "Owner", "city": "Dallas", "source": "networking",
            "source_detail": "Dallas Chamber Mixer", "met_on": "2026-11-15", "notes": "Wants help with follow-up",
            "preferred_contact": "email"}
    body.update(kw)
    return client.post(API + "/contacts", json=body, headers=h)


def _event_campaign(client, h, ids, **kw):
    body = {"name": "Dallas Chamber Mixer Follow-Up", "kind": "event", "context": "Dallas Chamber Mixer",
            "pick": {"lead_ids": ids}}
    body.update(kw)
    r = client.post(API + "/campaigns/picked", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


class TestAddContacts:
    def test_card_saves_and_shows_at_once(self, client, db_session, brand, manager_headers):
        r = _card(client, manager_headers)
        assert r.status_code == 201, r.text
        lead = r.json()["lead"]
        assert lead["source_key"] == "networking" and lead["source_detail"] == "Dallas Chamber Mixer"
        assert lead["role"] == "Owner" and lead["met_on"] == "2026-11-15" and lead["market"] == "DFW"
        o = db_session.get(Opportunity, lead["id"])
        assert o.stage == "prospect" and o.brand_sales_org_id == brand.id and o.preferred_contact == "email"
        ev = db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id).one()
        assert "Wants help" in ev.detail
        listed = client.get(API + "/contacts?source=networking", headers=manager_headers).json()["leads"]
        assert [x["id"] for x in listed] == [o.id]

    def test_only_a_name_is_needed(self, client, manager_headers):
        assert client.post(API + "/contacts", json={"contact_name": "Sam"}, headers=manager_headers).status_code == 201
        assert client.post(API + "/contacts", json={}, headers=manager_headers).status_code == 400

    def test_duplicate_is_shown_not_created_unless_forced(self, client, db_session, brand, manager_headers):
        assert _card(client, manager_headers).status_code == 201
        r = _card(client, manager_headers, email="DANA@lee.test ")
        assert r.status_code == 409 and r.json()["detail"]["duplicates"][0]["company"] == "Lee Logistics"
        assert _card(client, manager_headers, email=None, phone="(214) 555-0101").status_code == 409
        assert _card(client, manager_headers, force=True).status_code == 201
        assert db_session.query(Opportunity).filter_by(brand_sales_org_id=brand.id).count() == 2

    def test_existing_batches_are_counted(self, client, db_session, brand, manager_headers):
        for i in range(3):
            db_session.add(Opportunity(brand_sales_org_id=brand.id, company_name="FB %d" % i, stage="prospect",
                                       status="open", source_system="facebook_ads", import_batch_id="e601ca8e-1"))
        db_session.add(Opportunity(brand_sales_org_id=brand.id, company_name="Old FB", stage="prospect", status="open",
                                   source_system="facebook_ad", source="FB-CLEAN"))
        db_session.commit()
        _card(client, manager_headers)
        b = {x["batch"]: x for x in client.get(API + "/batches", headers=manager_headers).json()["batches"]}
        assert b["Import e601ca8e"]["total"] == 3 and b["Networking cards"]["total"] == 1
        assert any(k.startswith("FB-CLEAN") for k in b)
        src = client.get(API + "/sources", headers=manager_headers).json()
        st = {s["key"]: s["status_label"] for s in src["sources"]}
        assert st["networking"] == "Connected" and st["facebook_ads"] == "Import only"
        assert "isn't built" in src["card_scanning"] and src["import_path"].startswith("/imports/sales?target=")


class TestPickedCampaign:
    def test_preview_before_create_writes_nothing(self, client, db_session, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]["id"]
        b = _card(client, manager_headers, contact_name="Pat Moore", company_name="ClearView", email=None,
                  phone="469-555-0122").json()["lead"]["id"]
        r = client.post(API + "/campaigns/audience/preview",
                        json={"kind": "event", "pick": {"lead_ids": [a, b]}}, headers=manager_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["will_email"] == 1 and d["held"] == 1
        assert {x["company"]: x["held_label"] for x in d["rows"]}["ClearView"] == "No email - call instead"
        assert db_session.query(EvoSysCampaign).filter(EvoSysCampaign.kind == "event").count() == 0
        assert db_session.query(EvoSysOutreachState).count() == 0

    def test_suggestions_group_by_event_and_referral(self, client, manager_headers):
        a = _card(client, manager_headers).json()["lead"]["id"]
        b = _card(client, manager_headers, contact_name="Sam Ortiz", company_name="Harbor Pest", email="sam@h.test",
                  phone="214-555-0177", source="referral", source_detail="Blake Turner").json()["lead"]["id"]
        g = client.post(API + "/campaigns/suggest", json={"lead_ids": [a, b]}, headers=manager_headers).json()["groups"]
        names = {x["name"]: x for x in g}
        assert names["Dallas Chamber Mixer Follow-Up"]["kind"] == "event"
        assert names["Partner Introductions"]["tone"] == "warm"

    def test_warm_preview_uses_saved_facts_and_never_invents(self, client, db_session, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]["id"]
        b = _card(client, manager_headers, contact_name="Jo Kim", company_name="Clean Slate", email="jo@cs.test",
                  phone="214-555-0150", source="facebook", source_detail=None).json()["lead"]["id"]
        camp = _event_campaign(client, manager_headers, [a, b])
        cid = camp["campaign"]["id"]
        assert camp["campaign"]["status"]["key"] == "draft" and camp["campaign"]["sending_on"] is False
        held = {x["company"]: x["reason"] for x in camp["added"]["held"]}
        assert held == {"Clean Slate": "Missing event"}
        ex = client.get(API + "/campaigns/%s/examples" % cid, headers=manager_headers).json()["examples"]
        lee = [e for e in ex if e["lead"]["company"] == "Lee Logistics"][0]["steps"][0]
        assert "Dallas Chamber Mixer" in lee["text"] and {"key": "event", "value": "Dallas Chamber Mixer",
                                                         "from": "where you met (saved on the contact)"} in lee["facts_used"]
        cs = [e for e in ex if e["lead"]["company"] == "Clean Slate"][0]["steps"][0]
        assert cs["missing"] == ["event"] and "{event}" in cs["text"]

    def test_full_flow_off_approve_on_pause_reply_opt_out(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]["id"]
        b = _card(client, manager_headers, contact_name="Chris Hale", company_name="Greenline Lawn",
                  email="chris@green.test", phone="469-555-0122").json()["lead"]["id"]
        cid = _event_campaign(client, manager_headers, [a, b])["campaign"]["id"]
        snd = Sender()
        O.run_pass(db_session, now=MON_10AM, sender=snd)
        assert snd.calls == []                                     # new campaign is Off: nothing goes out
        assert client.post(API + "/campaigns/%s/switch" % cid, json={"on": True},
                           headers=manager_headers).status_code == 400   # not approved
        assert client.post(API + "/campaigns/%s/approve" % cid, headers=manager_headers).status_code == 200
        O.run_pass(db_session, now=MON_10AM, sender=snd)
        assert snd.calls == []                                     # approval alone sends nothing
        r = client.post(API + "/campaigns/%s/switch" % cid, json={"on": True}, headers=manager_headers)
        assert r.status_code == 200, r.text
        O.run_pass(db_session, now=MON_10AM, sender=snd)
        assert sorted(t for t, _ in snd.calls) == ["chris@green.test", "dana@lee.test"]
        assert "Dallas Chamber Mixer" in snd.calls[0][1]["text"]
        assert db_session.query(EvoSysOutreachMessage).filter_by(campaign_id=cid, status="sent").count() == 2
        # The same people re-picked: nothing is added or sent twice.
        again = client.post(API + "/campaigns/%s/leads" % cid, json={"lead_ids": [a, b]}, headers=manager_headers)
        assert again.json()["already_here"] == 2 and again.json()["added"] == 0
        # Pause: the next email waits, nothing is lost.
        client.post(API + "/campaigns/%s/switch" % cid, json={"on": False}, headers=manager_headers)
        O.run_pass(db_session, now=MON_10AM + timedelta(days=4), sender=snd)
        assert len(snd.calls) == 2
        st = db_session.get(EvoSysOutreachState, a)
        assert st.status == "active" and st.next_step == 1
        # A copy change needs a fresh approval and a fresh switch-on.
        steps = client.get(API + "/campaigns/%s" % cid, headers=manager_headers).json()["steps"]
        steps[1]["body"] += "\nP.S. Thanks again."
        client.put(API + "/campaigns/%s" % cid, json={"steps": steps}, headers=manager_headers)
        m = client.get(API + "/campaigns/%s/members" % cid, headers=manager_headers).json()
        assert m["status"]["key"] == "needs_approval" and m["sending_on"] is False
        client.post(API + "/campaigns/%s/approve" % cid, headers=manager_headers)
        client.post(API + "/campaigns/%s/switch" % cid, json={"on": True}, headers=manager_headers)
        # Dana replies (logged by hand); Chris opts out.
        assert client.post(API + "/leads/%s/reply" % a, json={"note": "Call me Thursday"},
                           headers=manager_headers).status_code == 200
        O.stop(db_session, b, "opted_out")
        db_session.get(EvoSysOutreachState, b).opted_out_at = datetime.utcnow()
        db_session.commit()
        O.run_pass(db_session, now=MON_10AM + timedelta(days=5), sender=snd)
        assert len(snd.calls) == 2                                 # neither gets another email
        # An opted-out contact can't be put in another campaign.
        cid2 = _event_campaign(client, manager_headers, [], name="Second")["campaign"]["id"]
        r = client.post(API + "/campaigns/%s/leads" % cid2, json={"lead_ids": [b]}, headers=manager_headers)
        assert r.json()["added"] == 0 and r.json()["refused"][0]["reason"] == "Opted out"

    def test_no_overlap_with_a_live_campaign(self, client, db_session, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]["id"]
        c1 = _event_campaign(client, manager_headers, [a])["campaign"]["id"]
        client.post(API + "/campaigns/%s/approve" % c1, headers=manager_headers)
        client.post(API + "/campaigns/%s/switch" % c1, json={"on": True}, headers=manager_headers)
        c2 = _event_campaign(client, manager_headers, [], name="Other")["campaign"]["id"]
        r = client.post(API + "/campaigns/%s/leads" % c2, json={"lead_ids": [a], "move": True}, headers=manager_headers)
        assert r.json()["added"] == 0 and "another campaign" in r.json()["refused"][0]["reason"]
        assert db_session.get(EvoSysOutreachState, a).campaign_id == c1

    def test_routing_never_adds_to_a_picked_campaign(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        cid = _event_campaign(client, manager_headers, [])["campaign"]["id"]
        client.post(API + "/campaigns/%s/approve" % cid, headers=manager_headers)
        client.post(API + "/campaigns/%s/switch" % cid, json={"on": True}, headers=manager_headers)
        _card(client, manager_headers, email="x@other.test", company_name="Other Co", phone="214-555-0999")
        O.run_pass(db_session, now=MON_10AM, sender=Sender())
        assert db_session.query(EvoSysOutreachState).filter_by(campaign_id=cid).count() == 0

    def test_saving_the_missing_fact_releases_the_hold(self, client, db_session, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers, source_detail=None).json()["lead"]["id"]
        cid = _event_campaign(client, manager_headers, [a])["campaign"]["id"]
        assert db_session.get(EvoSysOutreachState, a).held_reason == "missing:event"
        client.patch(API + "/contacts/%s" % a, json={"source_detail": "Dallas Chamber Mixer"}, headers=manager_headers)
        db_session.expire_all()
        assert db_session.get(EvoSysOutreachState, a).held_reason is None
        client.post(API + "/campaigns/%s/approve" % cid, headers=manager_headers)
        client.post(API + "/campaigns/%s/switch" % cid, json={"on": True}, headers=manager_headers)
        snd = Sender()
        O.run_pass(db_session, now=MON_10AM, sender=snd)
        assert len(snd.calls) == 1

    def test_placeholder_offer_is_not_an_offer(self):
        assert CA.real_offer("[Your offer goes here: the $250 Growth Pilot]") is None
        assert CA.real_offer("Free 30-day pilot") == "Free 30-day pilot"


class TestToday:
    def test_pricing_reply_needs_review_without_an_approved_offer(self, client, db_session, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]["id"]
        client.post(API + "/leads/%s/reply" % a, json={"note": "What does this cost per month?"}, headers=manager_headers)
        t = client.get(API + "/today", headers=manager_headers).json()
        assert t["counts"]["replies"] == 1 and t["replies"][0]["needs_review"] is True
        assert t["suggestion"]["kind"] == "pricing"
        client.post(API + "/contacts/%s/handled" % a, json={"note": "Called her"}, headers=manager_headers)
        assert client.get(API + "/today", headers=manager_headers).json()["counts"]["replies"] == 0


class TestAccess:
    def test_outsiders_and_other_brands_are_refused(self, client, db_session, brand_b):
        outsider = _make_sales_user(db_session)
        other = _make_sales_user(db_session)
        _add_membership(db_session, other, brand_b, "sales_manager")
        for u in (outsider, other):
            h = _token_headers(u, db_session)
            for p in ("/today", "/contacts", "/batches", "/campaign-list", "/inbox", "/calls"):
                assert client.get(API + p, headers=h).status_code == 403, p
            assert client.post(API + "/contacts", json={"contact_name": "X"}, headers=h).status_code == 403

    def test_rep_cannot_build_campaigns(self, client, rep_headers):
        r = client.post(API + "/campaigns/picked", json={"name": "Mine", "kind": "event"}, headers=rep_headers)
        assert r.status_code == 403
