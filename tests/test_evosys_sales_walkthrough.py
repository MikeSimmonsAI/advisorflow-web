"""EvoSys Sales walkthrough (PR #663): a mixed import, a market fix, AI copy,
one campaign on, a pricing reply, an AI failure, a taken booking slot, and
proof that an opt-out and a paused campaign cannot send.

Real importer, poller, campaign, AI-guard and booking code. Mocked: outbound
email (a recording sender), the AI provider (a fake client through the real
gateway), and the scheduler call that would write to a calendar.
"""
import io
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.evosys_sales_models import (EvoSysAITask, EvoSysCampaign, EvoSysLead, EvoSysOutreachMessage,
                                            EvoSysOutreachState)
from app.models.inbound_mailbox_models import InboundMailbox
from app.models.models import Organization, User
from app.models.sales_models import Opportunity, OpportunityEvent
from app.services import inbound_mailbox_service as S
from app.services.evosys_sales import ai as AI
from app.services.evosys_sales import campaigns as CA
from app.services.evosys_sales import outreach as O

from tests.test_import_stage_b import sales, _h  # noqa: F401  (fixture)

MON_10AM = datetime(2026, 11, 23, 16, 0)          # Monday 10:00 Central
CSV = (
    "company,source_record_id,full_name,phone,email,city,state,industry,notes\n"
    "Sparkle Janitorial,EVS-1,Ana Ruiz,2145550101,ana@sparklejan.test,Dallas,TX,Commercial cleaning,Office cleaning\n"
    "Clear View Windows,EVS-2,Ben Cole,2145550102,ben@clearview.test,,TX,Window washing,Serves DFW and Houston\n"
    "Sparkle Commercial Team,EVS-3,Cara Diaz,2145550103,ops@sparklejan.test,Dallas,TX,Commercial cleaning,\n"
    "Sparkle Test Cleaning LLC,EVS-5,Eve Moss,2145550105,eve@sparkletest.test,Dallas,TX,Commercial cleaning,\n"
    "Nowhere Clean Co,EVS-4,Dan Ng,2145550104,dan@nowhere.test,,,Commercial cleaning,No location given\n"
)


class Sender:
    def __init__(self):
        self.calls = []

    def __call__(self, to, msg, s, opp_id):
        self.calls.append(to)
        return {"success": True, "provider_message_id": "re_%d" % len(self.calls)}


class FakeAI:
    """A stand-in provider. `answers` is consumed in order; an Exception is raised."""
    def __init__(self, *answers):
        self.answers = list(answers)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(a)))],
                               usage=None)


@pytest.fixture()
def world(sales, monkeypatch):
    p = sales
    monkeypatch.setenv("EVOSYS_SALES_BRAND_ID", p["brand"].id)
    monkeypatch.setenv("AI_MANUAL_ACTIONS_ENABLED", "true")
    db = p["db"]
    box = InboundMailbox(address="support@evosyspro.live", is_active=True, last_status="ok")
    db.add(box)
    s = O.get_settings(db, p["brand"].id)
    s.mailing_address = "100 Main St, Dallas, TX 75201"
    db.commit()
    p.update(box=box, s=s, H=_h(db, p["manager"]))
    return p


def _import(p, csv=CSV):
    c, b, H = p["c"], p["brand"].id, p["H"]
    r = c.post("/intake/sales/%s/batches" % b, headers=H, data={"source_system": "facebook_ads"},
               files={"file": ("mixed.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert c.post("/intake/sales/%s/batches/%s/analyze" % (b, bid), headers=H).status_code == 200
    rows = {x["business"]["source_record_id"]: x for x in
            c.get("/intake/sales/%s/batches/%s/rows" % (b, bid), headers=H).json()["items"]}
    rep = c.post("/intake/sales/%s/batches/%s/commit" % (b, bid), headers=H, json={"confirm_target_id": b}).json()
    return rows, rep


def _opp(db, sid):
    return db.query(Opportunity).filter(Opportunity.source_record_id == sid).first()


def _ai(monkeypatch, fake):
    monkeypatch.setattr(AI, "_get_client", lambda: fake)


def test_full_walkthrough(world, monkeypatch):
    p, db, c, H = world, world["db"], world["c"], world["H"]

    # 1. MIXED IMPORT: preview outcomes, then grouping.
    rows, rep = _import(p)
    assert rows["EVS-4"]["outcome"] == "held_for_review"           # no location: importer holds it
    assert rows["EVS-5"]["outcome"] == "matched_existing"          # exact duplicate of a hand-entered lead
    assert rep["created"] == 3, ({k: (r["outcome"], r["match_reason"]) for k, r in rows.items()}, rep)
    clean, window, dup = _opp(db, "EVS-1"), _opp(db, "EVS-2"), _opp(db, "EVS-3")
    assert _opp(db, "EVS-4") is None
    g = {o.source_record_id: db.get(EvoSysLead, o.id) for o in (clean, window, dup)}
    assert (g["EVS-1"].industry_key, g["EVS-1"].market_key, g["EVS-1"].review_status) == \
        ("commercial_cleaning", "dfw", "ok")
    assert g["EVS-2"].industry_key == "window_washing" and g["EVS-2"].market_key is None
    assert g["EVS-2"].review_status == "unclear_market" and "more than one market" in g["EVS-2"].group_reason
    # Same company email domain as EVS-1 but a different name and phone: flagged for a person.
    assert g["EVS-3"].review_status == "possible_duplicate" and g["EVS-3"].duplicate_of_id == clean.id
    assert g["EVS-1"].source_channel == "facebook_ads"

    # Replaying the same file creates nothing.
    rows2, rep2 = _import(p)
    assert rep2.get("created", 0) == 0
    assert {r["outcome"] for k, r in rows2.items() if k != "EVS-4"} == {"matched_existing"}
    assert len(rows2) == 5

    # Today's "needs your attention" lists them.
    kinds = {i["kind"] for i in c.get("/sales/evosys/attention", headers=H).json()["items"]}
    assert {"possible_duplicate", "unclear_market"} <= kinds

    # 2. AI SUGGESTS A MARKET for the ambiguous lead; it must quote real text.
    _ai(monkeypatch, FakeAI({"industry_key": "window_washing", "market_key": "dfw",
                             "reason": "Names DFW first", "evidence": ["Serves DFW and Houston"]}))
    t = c.post("/sales/evosys/leads/%s/ai-group" % window.id, headers=H).json()
    assert t["status"] == "done" and t["output"]["evidence"] == ["Serves DFW and Houston"], t
    # Mike corrects the market himself (accepting the suggestion).
    r = c.put("/sales/evosys/leads/%s/group" % window.id, headers=H,
              json={"industry_key": "window_washing", "market_key": "dfw", "from_ai_task_id": t["id"]}).json()
    assert r["market_key"] == "dfw" and r["review_status"] == "ok" and r["group_source"] == "ai_accepted"
    # He resolves the duplicate: same business -> excluded, never emailed.
    r = c.post("/sales/evosys/leads/%s/review" % dup.id, headers=H, json={"action": "same_business"}).json()
    assert r["review_status"] == "excluded"

    # 3. A WINDOW-WASHING DFW CAMPAIGN with AI-drafted copy.
    camp = c.post("/sales/evosys/campaigns", headers=H, json={"industry_key": "window_washing",
                                                              "market_key": "dfw"}).json()
    assert camp["sending_on"] is False and camp["approved"] is False
    bad = {"steps": [{"day": 0, "subject": "Quick question", "body": "Hi {first_name}, only $250! {booking_link}"}]}
    _ai(monkeypatch, FakeAI(bad))
    d = c.post("/sales/evosys/campaigns/%s/ai-draft" % camp["id"], headers=H).json()
    assert d["status"] == "needs_person" and "price" in d["output"]["problems"][0]
    good = {"steps": [{"day": 0, "subject": "Window jobs near {company}",
                       "body": "Hi {first_name}, I saw your ad. {offer} Pick a time: {booking_link}\n\n{sender_name}"},
                      {"day": 3, "subject": "Following up", "body": "Hi {first_name}, any interest? {booking_link}"}]}
    _ai(monkeypatch, FakeAI(good))
    # Pressing Draft again within a moment returns the saved draft (no second request)...
    assert c.post("/sales/evosys/campaigns/%s/ai-draft" % camp["id"], headers=H).json()["id"] == d["id"]
    # ...and "Draft again" asks for a new one.
    d = c.post("/sales/evosys/campaigns/%s/ai-draft" % camp["id"], headers=H, params={"again": "true"}).json()
    assert d["status"] == "done"
    camp = c.post("/sales/evosys/campaigns/%s/ai-draft/%s/apply" % (camp["id"], d["id"]), headers=H).json()
    assert camp["draft_origin"] == "ai" and camp["steps"][0]["subject"] == "Window jobs near {company}"
    camp = c.put("/sales/evosys/campaigns/%s" % camp["id"], headers=H, json={"offer": "Free 30-day trial."}).json()
    # Preview for a real lead flags "I saw your ad" when the lead did not come from an ad.
    db.get(EvoSysLead, window.id).source_channel = "cold_email_research"; db.commit()
    pv = c.get("/sales/evosys/outreach/preview", headers=H, params={"campaign_id": camp["id"], "lead_id": window.id}).json()
    assert pv["steps"][0]["warnings"] and "Free 30-day trial." in pv["steps"][0]["text"]
    # Mike fixes the line, approves this exact version and turns this campaign on.
    steps = camp["steps"]; steps[0]["body"] = "Hi {first_name}, {offer} Pick a time: {booking_link}\n\n{sender_name}"
    camp = c.put("/sales/evosys/campaigns/%s" % camp["id"], headers=H, json={"steps": steps}).json()
    assert c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True}).status_code == 400
    camp = c.post("/sales/evosys/campaigns/%s/approve" % camp["id"], headers=H).json()
    camp = c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True}).json()
    assert camp["can_send"] is True

    # Only that campaign sends: the cleaning lead's campaign ("All leads") is off.
    snd = Sender()
    O.run_pass(db, MON_10AM, snd)
    assert snd.calls == ["ben@clearview.test"]
    st = db.get(EvoSysOutreachState, window.id)
    assert st.campaign_id == camp["id"]

    # 4. A PRICING REPLY to the tagged address stops it; AI may only quote the approved offer.
    tag = db.query(EvoSysOutreachMessage).filter_by(opportunity_id=window.id, direction="out").one()
    reply_to = "info+evs-%s@evosyspro.live" % st.reply_code
    S.poll_mailbox(db, p["box"], fetch=lambda since: [{
        "id": "g1", "internetMessageId": "<g1@mail.test>", "subject": "Re: Window jobs",
        "from": {"emailAddress": {"address": "ben@clearview.test"}},
        "toRecipients": [{"emailAddress": {"address": reply_to}}],
        "receivedDateTime": (MON_10AM + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "body": {"content": "How much does it cost per month?"}}])
    db.refresh(st)
    assert st.stop_reason == "replied"
    reply = db.query(EvoSysOutreachMessage).filter_by(opportunity_id=window.id, direction="in").one()
    _ai(monkeypatch, FakeAI({"category": "pricing", "summary": "Asked the monthly price",
                             "draft": "It is $199 a month. Book here."}))
    a = c.post("/sales/evosys/messages/%s/assist" % reply.id, headers=H).json()
    assert a["status"] == "needs_person" and a["output"]["draft"] == ""        # $199 is not in the offer
    assert "isn't in the approved offer" in a["output"]["needs_person"][0]
    # Mike answers by hand through the verified send path; the follow-up stays stopped.
    monkeypatch.setattr(O, "_send", lambda to, msg, s, oid: {"success": True, "provider_message_id": "re_x"})
    r = c.post("/sales/evosys/leads/%s/send-reply" % window.id, headers=H,
               json={"subject": "Re: Window jobs", "body": "It starts with a free 30-day trial. Want a quick call?",
                     "ai_task_id": a["id"]})
    assert r.status_code == 200
    db.refresh(st)
    assert st.status == "stopped"
    assert db.get(EvoSysAITask, a["id"]).outcome == "edited"

    # 5. AN AI FAILURE leaves the manual path working; a retry works and keeps the saved result.
    other = Opportunity(brand_sales_org_id=p["brand"].id, company_name="Unclear Co", stage="prospect",
                        status="open", source="list")
    db.add(other); db.commit()
    _ai(monkeypatch, FakeAI(RuntimeError("provider down")))
    t = c.post("/sales/evosys/leads/%s/ai-group" % other.id, headers=H).json()
    assert t["status"] == "failed" and "couldn't finish" in t["error"]
    assert any(i["kind"] == "ai_failed" for i in c.get("/sales/evosys/attention", headers=H).json()["items"])
    r = c.put("/sales/evosys/leads/%s/group" % other.id, headers=H,
              json={"industry_key": "commercial_cleaning", "market_key": "houston"})
    assert r.status_code == 200 and r.json()["group_source"] == "manual"

    # 6. A TAKEN SLOT: nothing booked, the lead stays in view; the retry books once.
    from app.routers import sales_scheduling_router as SR
    from app.models.scheduling_models import SalesAppointment
    monkeypatch.setattr(SR, "create_appointment", lambda body, user, db: (_ for _ in ()).throw(
        HTTPException(409, "Slot taken")))
    when = (MON_10AM + timedelta(days=1)).isoformat() + "Z"
    r = c.post("/sales/evosys/leads/%s/intro-call" % clean.id, headers=H, json={"starts_at": when}).json()
    assert r["status"] == "conflict"
    assert db.query(SalesAppointment).filter_by(opportunity_id=clean.id).count() == 0
    assert any(i["kind"] == "booking_issue" for i in c.get("/sales/evosys/attention", headers=H).json()["items"])

    def ok_book(body, user, db):
        a = SalesAppointment(brand_sales_org_id=body.brand_sales_org_id, opportunity_id=body.opportunity_id,
                             title=body.title, starts_at=body.starts_at.replace(tzinfo=None),
                             ends_at=body.starts_at.replace(tzinfo=None) + timedelta(minutes=30),
                             timezone="America/Chicago", status="scheduled")
        db.add(a); db.commit()
        return {"id": a.id}
    monkeypatch.setattr(SR, "create_appointment", ok_book)
    r1 = c.post("/sales/evosys/leads/%s/intro-call" % clean.id, headers=H, json={"starts_at": when}).json()
    r2 = c.post("/sales/evosys/leads/%s/intro-call" % clean.id, headers=H, json={"starts_at": when}).json()
    assert r1["status"] == "booked" and r2["already_booked"] is True
    assert db.query(SalesAppointment).filter_by(opportunity_id=clean.id).count() == 1
    assert not any(i["kind"] == "booking_issue" for i in c.get("/sales/evosys/attention", headers=H).json()["items"])

    # 7. RESULTS by campaign, industry, market and source - separate outcomes.
    res = c.get("/sales/evosys/results/breakdown", headers=H, params={"by": "campaign"}).json()["rows"]
    win = next(r for r in res if r["name"] == camp["name"])
    assert (win["contacted"], win["replied"]) == (1, 1)
    for by in ("industry", "market", "source"):
        assert c.get("/sales/evosys/results/breakdown", headers=H, params={"by": by}).status_code == 200


def test_opt_out_and_paused_campaign_cannot_send(world):
    p, db, c, H = world, world["db"], world["c"], world["H"]
    _import(p, "company,source_record_id,full_name,email,city,state,industry\n"
               "Alpha Clean,A-1,Al,al@alpha.test,Dallas,TX,Cleaning\n"
               "Beta Clean,B-1,Bo,bo@beta.test,Plano,TX,Cleaning\n")
    a, b = _opp(db, "A-1"), _opp(db, "B-1")
    camp = c.post("/sales/evosys/campaigns", headers=H, json={"industry_key": "commercial_cleaning",
                                                              "market_key": "dfw", "offer": "Free 30-day trial."}).json()
    c.post("/sales/evosys/campaigns/%s/approve" % camp["id"], headers=H)
    c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True})
    snd = Sender()
    O.run_pass(db, MON_10AM, snd)
    assert sorted(snd.calls) == ["al@alpha.test", "bo@beta.test"]

    # Alpha opts out with the link in the email.
    token = O.opt_out_url(a.id).rsplit("/", 1)[1]
    assert c.get("/evosys-sales-public/stop/" + token).status_code == 200
    # The campaign is paused with Beta's step 2 waiting.
    c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": False})
    snd.calls.clear()
    out = O.run_pass(db, MON_10AM + timedelta(days=3), snd)
    assert snd.calls == [] and out["skipped"] == "off"
    att = c.get("/sales/evosys/attention", headers=H).json()["items"]
    assert any(i["kind"] == "campaign_paused" and i["count"] == 1 for i in att)
    # Restarting the opted-out lead is refused.
    assert c.post("/sales/evosys/leads/%s/restart" % a.id, headers=H).status_code == 409

    # A copy change while paused needs a new approval; turning it on with the old one is refused.
    c.put("/sales/evosys/campaigns/%s" % camp["id"], headers=H, json={"offer": "Free 14-day trial."})
    assert c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True}).status_code == 400
    c.post("/sales/evosys/campaigns/%s/approve" % camp["id"], headers=H)
    c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True})
    O.run_pass(db, MON_10AM + timedelta(days=3, hours=1), snd)
    O.run_pass(db, MON_10AM + timedelta(days=3, hours=2), snd)
    assert snd.calls == ["bo@beta.test"]          # once, not twice; Alpha never again


def test_team_sender_change_stops_every_campaign(world):
    p, db, c, H = world, world["db"], world["c"], world["H"]
    camp = c.post("/sales/evosys/campaigns", headers=H, json={"market_key": "dfw", "offer": "Free trial."}).json()
    c.post("/sales/evosys/campaigns/%s/approve" % camp["id"], headers=H)
    c.post("/sales/evosys/campaigns/%s/switch" % camp["id"], headers=H, json={"on": True})
    c.put("/sales/evosys/outreach", headers=H, json={"mailing_address": "200 Elm St, Dallas, TX 75201"})
    camp = c.get("/sales/evosys/campaigns/%s" % camp["id"], headers=H).json()
    assert camp["sending_on"] is False and camp["approved"] is False


def test_existing_enrolments_keep_their_progress(world):
    p, db = world, world["db"]
    o = Opportunity(brand_sales_org_id=p["brand"].id, company_name="Old Lead", email="old@x.test",
                    stage="contacted", status="open")
    db.add(o); db.commit()
    due = MON_10AM + timedelta(days=2)
    db.add(EvoSysOutreachState(opportunity_id=o.id, brand_sales_org_id=p["brand"].id, status="active",
                               next_step=2, next_due_at=due, started_at=MON_10AM - timedelta(days=7)))
    s = p["s"]
    s.offer, s.approved_at, s.sending_on = "Free 30-day trial.", datetime.utcnow(), True
    db.commit()
    camp = CA.ensure_default(db, s)
    db.commit()
    st = db.get(EvoSysOutreachState, o.id)
    assert (st.campaign_id, st.next_step, st.next_due_at) == (camp.id, 2, due)
    assert CA.is_approved(camp, s) and camp.sending_on is True


def test_outsiders_and_service_company_tenants_are_refused(world):
    p, db, c = world, world["db"], world["c"]
    org = Organization(name="Divine Test Co", slug="divine-test-%s" % id(db), plan="trial", is_active=True)
    db.add(org); db.commit()
    tenant = User(organization_id=org.id, email="owner%s@tenant.test" % id(db), password_hash="x",
                  full_name="Tenant Owner", role="org_admin", must_change_password=False)
    db.add(tenant); db.commit()
    for u in (tenant, p["other_mgr"]):
        hdr = _h(db, u)
        for path in ("/sales/evosys/overview", "/sales/evosys/campaigns", "/sales/evosys/attention",
                     "/sales/evosys/results/breakdown"):
            assert c.get(path, headers=hdr).status_code == 403, (u.email, path)
    assert c.get("/sales/evosys/overview").status_code == 401
    # A rep can see the program but not change campaigns.
    rh = _h(db, p["rep"])
    assert c.get("/sales/evosys/campaigns", headers=rh).status_code == 200
    assert c.post("/sales/evosys/campaigns", headers=rh, json={"market_key": "dfw"}).status_code == 403
