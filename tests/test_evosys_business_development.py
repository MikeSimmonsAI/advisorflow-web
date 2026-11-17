"""EvoSys Business Development (PR #663): add a networking card, find existing
leads, form a campaign from selected leads, warm preview with the event,
no double enrolment, approve and turn on, pause with work queued, reply and
opt-out, signup handoff with attribution, access boundaries. Synthetic data,
mocked outbound."""
from datetime import date, datetime, timedelta

import pytest

from app.models.evosys_sales_models import EvoSysCampaign, EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent, ROLE_SALES_MANAGER
from app.services.evosys_sales import ai_assist
from app.services.evosys_sales import campaigns as C
from app.services.evosys_sales import outreach as O

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, brand_b, rep, manager, rep_headers, manager_headers,
    _make_sales_user, _add_membership, _token_headers, _SEQ,
)

IN_WINDOW = max(datetime(2026, 11, 17, 16, 0), datetime.utcnow().replace(hour=16, minute=0, second=0, microsecond=0)
                + timedelta(days=(1 - datetime.utcnow().weekday()) % 7 or 7))   # a Tuesday 10:00 AM Central ahead
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


def _team(client, h, markets=("any",)):
    r = client.put(API + "/outreach", json={"mailing_address": "100 Main St, Dallas, TX 75201",
                                            "eligible_markets": list(markets)}, headers=h)
    assert r.status_code == 200, r.text


def _card(client, h, **kw):
    body = {"contact_name": "Dana Lee", "company_name": "Lee Logistics", "email": "dana@lee.test",
            "phone": "214-555-0101", "role": "Owner", "industry": "Logistics", "market": "DFW",
            "source": "networking", "source_detail": "Dallas Chamber Mixer", "met_on": "2026-11-15",
            "notes": "Wants help with follow-up", "preferred_contact": "email"}
    body.update(kw)
    return client.post(API + "/contacts", json=body, headers=h)


def _lead(db, brand, company, email, **kw):
    o = Opportunity(brand_sales_org_id=brand.id, company_name=company, contact_name="Ana Ruiz",
                    phone=kw.pop("phone", None), email=email, stage="prospect", status="open", **kw)
    db.add(o); db.commit()
    return o


class TestAddContacts:
    def test_card_saves_every_field_and_shows_at_once(self, client, db_session, brand, manager_headers):
        r = _card(client, manager_headers)
        assert r.status_code == 201, r.text
        lead = r.json()["lead"]
        assert lead["source_key"] == "networking" and lead["source_detail"] == "Dallas Chamber Mixer"
        assert lead["role"] == "Owner" and lead["market"] == "DFW" and lead["met_on"] == "2026-11-15"
        o = db_session.get(Opportunity, lead["id"])
        assert o.stage == "prospect" and o.brand_sales_org_id == brand.id and o.preferred_contact == "email"
        ev = db_session.query(OpportunityEvent).filter_by(opportunity_id=o.id).one()
        assert "Wants help" in ev.detail and ev.occurred_at is not None
        listed = client.get(API + "/leads?source=networking", headers=manager_headers).json()["leads"]
        assert [x["id"] for x in listed] == [o.id]

    def test_only_a_name_is_needed(self, client, manager_headers):
        r = client.post(API + "/contacts", json={"contact_name": "Sam"}, headers=manager_headers)
        assert r.status_code == 201
        assert client.post(API + "/contacts", json={}, headers=manager_headers).status_code == 400

    def test_duplicate_is_shown_not_created_unless_forced(self, client, db_session, brand, manager_headers):
        assert _card(client, manager_headers).status_code == 201
        r = _card(client, manager_headers, email="DANA@lee.test ")
        assert r.status_code == 409 and r.json()["detail"]["duplicates"][0]["company"] == "Lee Logistics"
        r = _card(client, manager_headers, email=None, phone="(214) 555-0101")
        assert r.status_code == 409
        assert _card(client, manager_headers, force=True).status_code == 201
        assert db_session.query(Opportunity).filter_by(brand_sales_org_id=brand.id).count() == 2


class TestExistingLeads:
    def test_fb_clean_and_import_batches_are_visible_and_counted(self, client, db_session, brand, manager_headers):
        _lead(db_session, brand, "Old FB Co", None, source="Facebook ad | FB-CLEAN-017 | NJ", source_system="facebook_ad")
        _lead(db_session, brand, "Batch Co", "b@x.test", source="Manually collected Facebook business ads",
              source_system="facebook_ads", import_batch_id="e601ca8e-c33d-484b")
        _lead(db_session, brand, "Card Co", "c@x.test", source="Networking Bohemian Bull")
        leads = client.get(API + "/leads", headers=manager_headers).json()
        assert leads["total"] == 3
        assert {x["source_key"] for x in leads["leads"]} == {"facebook", "networking"}
        b = {x["batch"]: x for x in client.get(API + "/leads/batches", headers=manager_headers).json()["batches"]}
        assert b["FB-CLEAN (entered Nov 3)"]["total"] == 1 and b["FB-CLEAN (entered Nov 3)"]["with_email"] == 0
        assert b["Import e601ca8e"]["with_email"] == 1
        assert b["Networking cards"]["total"] == 1


class TestCampaigns:
    def test_networking_card_to_warm_campaign_preview_uses_the_event(self, client, db_session, brand,
                                                                      manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]
        b = _card(client, manager_headers, contact_name="Lou Park", company_name="Park Dental",
                  email="lou@park.test", phone="214-555-0102").json()["lead"]
        g = client.post(API + "/campaigns/suggest", json={"lead_ids": [a["id"], b["id"]]},
                        headers=manager_headers).json()["groups"]
        assert g[0]["name"] == "Dallas Chamber Mixer Follow-Up" and g[0]["tone"] == "warm" and g[0]["count"] == 2
        r = client.post(API + "/campaigns", json={"name": g[0]["name"], "kind": "event", "context": g[0]["context"],
                                                   "pick": {"lead_ids": [a["id"], b["id"]]}},
                        headers=manager_headers)
        k = r.json()["campaign"]
        assert k["sending_on"] is False and k["tone"] == "warm" and r.json()["added"]["added"] == 2
        ex = client.get(API + "/campaigns/%s/preview?lead_id=%s" % (k["id"], a["id"]),
                        headers=manager_headers).json()["examples"][0]["steps"][0]
        assert "Good meeting you at Dallas Chamber Mixer" in ex["text"] and "Hey Dana" in ex["text"]
        assert {"event", "first_name", "company"} <= {f["key"] for f in ex["facts_used"]}
        assert ex["missing"] == []

    def test_warm_message_never_invents_a_meeting(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        researched = _lead(db_session, brand, "Cold Co", "cold@x.test", source_system="facebook_ads", market="DFW")
        k = client.post(API + "/campaigns", json={"name": "Warm", "kind": "event",
                                                   "pick": {"lead_ids": [researched.id]}},
                        headers=manager_headers).json()
        assert k["added"]["held"][0]["reason"].startswith("Needs review: no saved the event")
        ex = client.get(API + "/campaigns/%s/preview?lead_id=%s" % (k["campaign"]["id"], researched.id),
                        headers=manager_headers).json()["examples"][0]["steps"][0]
        assert ex["missing"] == ["event"] and "[event]" in ex["text"]

    def test_full_flow_approve_on_pause_reply_optout_no_double_enrol(self, client, db_session, brand,
                                                                     manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]
        b = _card(client, manager_headers, contact_name="Lou Park", company_name="Park Dental",
                  email="lou@park.test", phone="214-555-0102").json()["lead"]
        c_ = _card(client, manager_headers, contact_name="Ken Ito", company_name="Ito Law",
                   email="ken@ito.test", phone="214-555-0103").json()["lead"]
        k = client.post(API + "/campaigns", json={"name": "Mixer", "kind": "event",
                                                   "pick": {"lead_ids": [a["id"], b["id"], c_["id"]]}},
                        headers=manager_headers).json()["campaign"]
        url = API + "/campaigns/" + k["id"]
        # A second campaign may not take someone the first already has, unless moved
        k2 = client.post(API + "/campaigns", json={"name": "Other", "kind": "list"},
                         headers=manager_headers).json()["campaign"]
        prev = client.post(API + "/campaigns/%s/audience/preview" % k2["id"], json={"lead_ids": [a["id"]]},
                           headers=manager_headers).json()
        assert prev["in_other_campaigns"] == 1 and prev["rows"][0]["other_campaign"] == "Mixer"
        r = client.post(API + "/campaigns/%s/leads" % k2["id"], json={"lead_ids": [a["id"]]},
                        headers=manager_headers).json()
        assert r["added"] == 0 and "Mixer" in r["refused"][0]["reason"]
        # Approve (sends nothing), then on
        assert client.post(url + "/approve", headers=manager_headers).status_code == 200
        snd = Sender()
        assert O.run_pass(db_session, IN_WINDOW, snd)["sent"] == 0
        assert client.post(url + "/switch", json={"on": True}, headers=manager_headers).json()["started"] == 3
        # Now it is sending: moving a contact away is refused
        r = client.post(API + "/campaigns/%s/leads" % k2["id"], json={"lead_ids": [a["id"]], "move": True},
                        headers=manager_headers).json()
        assert r["added"] == 0
        # Pause with work queued: nothing goes out, nothing is lost
        client.post(url + "/switch", json={"on": False}, headers=manager_headers)
        assert O.run_pass(db_session, IN_WINDOW, snd)["sent"] == 0 and snd.calls == []
        assert db_session.query(EvoSysOutreachState).filter_by(campaign_id=k["id"], status="active").count() == 3
        client.post(url + "/switch", json={"on": True}, headers=manager_headers)
        assert O.run_pass(db_session, IN_WINDOW, snd)["sent"] == 3
        assert all(m.campaign_id == k["id"] for m in db_session.query(EvoSysOutreachMessage).all())
        assert db_session.get(Opportunity, a["id"]).bd_campaign_id == k["id"]
        # A genuine reply (logged) and an opt-out stop those two
        client.post(API + "/leads/%s/reply" % a["id"], json={"note": "How much does it cost per month?"},
                    headers=manager_headers)
        # the reply arrives after the (simulated, future) send time
        db_session.query(EvoSysOutreachMessage).filter_by(direction="in").update(
            {"created_at": IN_WINDOW + timedelta(hours=1)})
        db_session.commit()
        token = O.opt_out_url(b["id"]).rsplit("/", 1)[1]
        assert client.get("/evosys-sales-public/stop/" + token).status_code == 200
        ov = client.get(API + "/overview", headers=manager_headers).json()
        assert ov["counts"]["replies"] == 1
        rep_ = ov["replies"][0]
        assert rep_["intent"]["key"] == "pricing"
        # The offer is filled in by default from team settings? No: warm steps don't use it, so
        # a pricing question with no approved offer goes to review.
        snd.calls.clear()
        O.run_pass(db_session, IN_WINDOW + timedelta(days=6), snd)   # Monday
        assert [t for t, _ in snd.calls] == ["ken@ito.test"]
        # Opted-out contact can't be put back in any campaign
        r = client.post(API + "/campaigns/%s/leads" % k2["id"], json={"lead_ids": [b["id"]], "move": True},
                        headers=manager_headers).json()
        assert r["added"] == 0 and r["refused"][0]["reason"] == "Opted out"
        # Marking the reply answered takes it off the queue
        client.post(API + "/leads/%s/handled" % a["id"], json={"note": "Called her"}, headers=manager_headers)
        db_session.query(EvoSysOutreachMessage).filter_by(status="logged", direction="out").update(
            {"created_at": IN_WINDOW + timedelta(hours=2)})
        db_session.commit()
        assert client.get(API + "/overview", headers=manager_headers).json()["counts"]["replies"] == 0

    def test_market_rules_and_reimport_no_duplicate_sends(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers, markets=["DFW"])
        dfw = _lead(db_session, brand, "DFW Co", "d@x.test", market="DFW", industry="Cleaning")
        hou = _lead(db_session, brand, "Houston Co", "h@x.test", market="Houston", industry="Cleaning")
        unk = _lead(db_session, brand, "Somewhere Co", "s@x.test", industry="Cleaning")
        k = client.post(API + "/campaigns", json={"name": "Cleaning", "kind": "industry", "offer": "Free 30 days.",
                                                   "pick": {"lead_ids": [dfw.id, hou.id, unk.id]}},
                        headers=manager_headers).json()
        held = {h["company"]: h["reason"] for h in k["added"]["held"]}
        assert held == {"Houston Co": "Market not cleared for outreach", "Somewhere Co": "Market not known - review"}
        url = API + "/campaigns/" + k["campaign"]["id"]
        client.post(url + "/approve", headers=manager_headers)
        assert client.post(url + "/switch", json={"on": True}, headers=manager_headers).json()["started"] == 1
        snd = Sender()
        O.run_pass(db_session, IN_WINDOW, snd)
        # adding the same people again changes nothing and sends nothing new
        r = client.post(url + "/leads", json={"lead_ids": [dfw.id, hou.id, unk.id]}, headers=manager_headers).json()
        assert r["already_here"] == 3 and r["added"] == 0
        O.run_pass(db_session, IN_WINDOW + timedelta(minutes=5), snd)
        assert [t for t, _ in snd.calls] == ["d@x.test"]
        # fixing the market lets the held contact go on the next pass
        client.patch(API + "/leads/%s" % unk.id, json={"market": "DFW"}, headers=manager_headers)
        O.run_pass(db_session, IN_WINDOW + timedelta(minutes=10), snd)
        assert [t for t, _ in snd.calls] == ["d@x.test", "s@x.test"]

    def test_switch_on_refused_until_markets_decided(self, client, db_session, brand, manager_headers):
        client.put(API + "/outreach", json={"mailing_address": "100 Main St"}, headers=manager_headers)
        o = _lead(db_session, brand, "Co", "c@x.test")
        k = client.post(API + "/campaigns", json={"name": "X", "kind": "list", "offer": "Free.",
                                                   "pick": {"lead_ids": [o.id]}},
                        headers=manager_headers).json()["campaign"]
        assert any("markets" in p for p in k["problems"])
        assert client.post(API + "/campaigns/%s/approve" % k["id"], headers=manager_headers).status_code == 400

    def test_older_follow_up_rows_keep_their_progress(self, db_session, brand):
        s = O.get_settings(db_session, brand.id)
        s.mailing_address, s.offer, s.eligible_markets = "100 Main", "Free.", ["any"]
        s.approved_at, s.sending_on = datetime.utcnow(), True
        o = _lead(db_session, brand, "Old Co", "o@x.test")
        db_session.add(EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=brand.id, status="active",
                                           next_step=1, next_due_at=IN_WINDOW - timedelta(minutes=1),
                                           started_at=IN_WINDOW - timedelta(days=3)))
        db_session.commit()
        snd = Sender()
        r = O.run_pass(db_session, IN_WINDOW, snd)
        camp = db_session.query(EvoSysCampaign).filter_by(name=C.LEGACY_NAME).one()
        assert r["sent"] == 1 and "Re:" in snd.calls[0][1]["subject"]        # step 2, not step 1
        st = db_session.get(EvoSysOutreachState, o.id)
        assert st.campaign_id == camp.id and st.next_step == 2


class TestHandoffAndResults:
    def test_signup_handoff_keeps_attribution(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        a = _card(client, manager_headers).json()["lead"]
        k = client.post(API + "/campaigns", json={"name": "Mixer", "kind": "event",
                                                   "pick": {"lead_ids": [a["id"]]}},
                        headers=manager_headers).json()["campaign"]
        r = client.post(API + "/leads/%s/signup" % a["id"], headers=manager_headers).json()
        assert r["path"] == "/sales/opportunities/%s" % a["id"] and r["campaign"] == "Mixer"
        assert r["source"] == "Networking cards"
        o = db_session.get(Opportunity, a["id"])
        o.stage, o.won_at = "won", datetime.utcnow(); db_session.commit()
        res = client.get(API + "/results", headers=manager_headers).json()
        assert res["by_campaign"][0]["label"] == "Mixer" and res["by_campaign"][0]["signups"] == 1
        assert res["by_source"][0]["label"] == "Networking cards" and res["totals"]["signups"] == 1
        del k

    def test_sources_report_what_is_built(self, client, manager_headers):
        s = {x["key"]: x for x in client.get(API + "/sources", headers=manager_headers).json()["sources"]}
        assert s["networking"]["status_label"] == "Connected"
        assert s["tiktok"]["status_label"] == "Import only"
        assert s["website"]["status_label"] == "Connected"


class TestAccess:
    def test_unrelated_user_and_other_team_denied_everywhere(self, client, db_session, brand, brand_b):
        u = _make_sales_user(db_session)
        _add_membership(db_session, u, brand_b, ROLE_SALES_MANAGER)
        h = _token_headers(u, db_session)
        for path in ("/overview", "/leads", "/campaigns", "/sources", "/results", "/leads/batches", "/review"):
            assert client.get(API + path, headers=h).status_code == 403, path
        assert client.post(API + "/contacts", json={"contact_name": "x"}, headers=h).status_code == 403
        nobody = _make_sales_user(db_session)
        assert client.get(API + "/overview", headers=_token_headers(nobody, db_session)).status_code == 403

    def test_rep_adds_contacts_but_cannot_run_campaigns(self, client, db_session, brand, rep_headers,
                                                        manager_headers):
        assert _card(client, rep_headers).status_code == 201
        assert client.post(API + "/campaigns", json={"name": "X"}, headers=rep_headers).status_code == 403
        k = client.post(API + "/campaigns", json={"name": "X"}, headers=manager_headers).json()["campaign"]
        assert client.post(API + "/campaigns/%s/switch" % k["id"], json={"on": True},
                           headers=rep_headers).status_code == 403
        assert client.put(API + "/campaigns/%s" % k["id"], json={"offer": "y"},
                          headers=rep_headers).status_code == 403


class TestAI:
    def test_ai_draft_uses_only_known_placeholders_and_fails_safely(self, client, db_session, brand,
                                                                    manager_headers, monkeypatch):
        k = client.post(API + "/campaigns", json={"name": "Partners", "kind": "relationship"},
                        headers=manager_headers).json()["campaign"]
        good = '{"steps":[{"day":0,"subject":"{referrer} said hi","body":"Hi {first_name}, {booking_link}"}]}'
        monkeypatch.setattr(ai_assist, "_call", lambda *a, **kw: good)
        r = client.post(API + "/campaigns/%s/ai-draft" % k["id"], headers=manager_headers)
        assert r.status_code == 200 and r.json()["steps"][0]["subject"] == "{referrer} said hi"
        # nothing saved until the manager saves it
        assert client.get(API + "/campaigns/%s" % k["id"], headers=manager_headers).json()["steps"][0]["subject"] \
            != "{referrer} said hi"
        monkeypatch.setattr(ai_assist, "_call", lambda *a, **kw: '{"steps":[{"day":0,"subject":"x","body":"{price}"}]}')
        assert client.post(API + "/campaigns/%s/ai-draft" % k["id"], headers=manager_headers).status_code == 503

        def boom(*a, **kw):
            raise ai_assist.AIUnavailable("AIDisabled")
        monkeypatch.setattr(ai_assist, "_call", boom)
        r = client.post(API + "/campaigns/%s/ai-draft" % k["id"], headers=manager_headers)
        assert r.status_code == 503 and "yourself" in r.json()["detail"]

    def test_reply_draft_never_quotes_a_price_without_an_offer(self, client, db_session, brand, manager_headers,
                                                               monkeypatch):
        o = _lead(db_session, brand, "Ask Co", "a@x.test")
        client.post(API + "/leads/%s/reply" % o.id, json={"note": "What's the price?"}, headers=manager_headers)
        monkeypatch.setattr(ai_assist, "_call", lambda *a, **kw: '{"summary":"price","draft":"It is $250 a month."}')
        r = client.post(API + "/conversations/%s/suggest" % o.id, headers=manager_headers).json()
        assert r["ok"] is False and r["needs_review"] is True and r["original"] == "What's the price?"
        monkeypatch.setattr(ai_assist, "_call",
                            lambda *a, **kw: '{"summary":"Asks price","draft":"Thanks Ana, Mike will follow up."}')
        r = client.post(API + "/conversations/%s/suggest" % o.id, headers=manager_headers).json()
        assert r["ok"] is True and "follow up" in r["draft"]


class TestPreviewAndOffer:
    def test_audience_preview_before_create_writes_nothing(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        a = _lead(db_session, brand, "Alpha Clean", "a@alpha.test", industry="Cleaning", market="DFW")
        b = _lead(db_session, brand, "Beta Clean", None, phone="214-555-0199", industry="Cleaning", market="DFW")
        r = client.post(API + "/campaigns/audience/preview",
                        json={"kind": "industry", "market": "DFW", "pick": {"lead_ids": [a.id, b.id]}}, headers=manager_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["will_email"] == 1 and d["held"] == 1
        assert {x["company"]: x["will_email"] for x in d["rows"]} == {"Alpha Clean": True, "Beta Clean": False}
        assert db_session.query(EvoSysCampaign).count() == 0
        assert db_session.query(EvoSysOutreachState).count() == 0

    def test_placeholder_offer_is_not_an_offer(self, client, db_session, brand, manager_headers):
        _team(client, manager_headers)
        r = client.post(API + "/campaigns", json={"name": "Test", "kind": "list"}, headers=manager_headers)
        assert r.status_code in (200, 201), r.text
        camp = db_session.get(EvoSysCampaign, r.json()["campaign"]["id"])
        assert camp.offer is None
        assert C.real_offer("[Your offer goes here: the $250 Growth Pilot]") is None
        assert C.real_offer("Free 30-day pilot") == "Free 30-day pilot"
