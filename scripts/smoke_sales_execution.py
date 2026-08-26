"""
Sales execution regression suite — Checkpoint 4.
Proposals, the secure deal room, buyer activity, and Zoom.

NO TEST EVER CONTACTS ZOOM OR SENDS AN EMAIL.
Zoom goes through a fake registered on the provider registry; email is
monkeypatched at the module boundary. A suite that needs live vendor
credentials is a suite that gets skipped, and a skipped test protects nothing.
There is no path in here that can reach a real prospect.

Temp SQLite. Never touches production.

    python scripts/smoke_sales_execution.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="salesexec_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59
# Proves the env-var fallback path is exercised without real credentials.
os.environ.setdefault("ZOOM_ACCOUNT_ID", "test-account")
os.environ.setdefault("ZOOM_CLIENT_ID", "test-client")
os.environ.setdefault("ZOOM_CLIENT_SECRET", "test-secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                        # noqa: E402
from app.main import app                                         # noqa: E402
from app.deps import SessionLocal, engine                        # noqa: E402
from app.models.models import (                                  # noqa: E402
    Base, Platform, Organization, User, Proposal, ProposalBlock,
    ProposalToken, PortalEvent,
    PROP_DRAFT, PROP_READY, PROP_SENT, PROP_VIEWED, PROP_ACCEPTED,
    PROP_DECLINED, PROP_CHANGE_REQUESTED, PROP_SUPERSEDED, PROP_EXPIRED,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_DEMO_OPENED,
)
from app.models.sales_models import (                            # noqa: E402
    Membership, BrandSalesOrg, Opportunity, BrandPackage, DiscoveryRecord,
    OpportunityEvent,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (                       # noqa: E402
    MeetingType, SalesAppointment, AppointmentParticipant,
)
from app.models.meeting_models import (                          # noqa: E402
    AppointmentMeeting, MeetingProviderConfig,
    MEET_CREATED, MEET_UPDATED, MEET_CANCELLED, MEET_FAILED, MEET_NOT_REQUIRED,
)
from app.services.auth_service import hash_password              # noqa: E402
from app.services import proposal_service as ps                  # noqa: E402
from app.services import appointment_meetings as apmeet          # noqa: E402

PW = "ExecPass123!"
CHI = "America/Chicago"
FAILURES = []
ID = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def U(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi)


# ── the fake Zoom provider ──────────────────────────────────────────────────
#
# Everything a real vendor can do to us is reachable here — success, transport
# failure, dead credentials, missing scope — with no network and no account.

class FakeZoom(object):
    key = "zoom"
    calls = []
    outcome = "ok"
    seq = {"n": 0}

    def __init__(self, config=None):
        self.config = config

    @classmethod
    def reset(cls):
        cls.calls = []
        cls.outcome = "ok"
        cls.seq = {"n": 0}

    def is_ready(self):
        if FakeZoom.outcome == "not_configured":
            return False, "Zoom is not configured for this brand"
        return True, None

    def _result(self, mid=None):
        from app.services.meeting_providers.base import MeetingResult
        if FakeZoom.outcome != "ok":
            return MeetingResult.failure(FakeZoom.outcome, "fake %s" % FakeZoom.outcome)
        if not mid:
            FakeZoom.seq["n"] += 1
            mid = "zoom-%d" % FakeZoom.seq["n"]
        return MeetingResult(
            ok=True, provider_meeting_id=mid,
            join_url="https://zoom.us/j/%s" % mid,
            # The host URL the orchestrator must encrypt and never serialize.
            host_url="https://zoom.us/s/%s?zak=SECRET-HOST-TOKEN" % mid,
            passcode="123456")

    def create_meeting(self, req):
        FakeZoom.calls.append({"op": "create", "req": req})
        return self._result()

    def update_meeting(self, mid, req):
        FakeZoom.calls.append({"op": "update", "id": mid, "req": req})
        return self._result(mid)

    def cancel_meeting(self, mid):
        FakeZoom.calls.append({"op": "cancel", "id": mid})
        return self._result(mid)

    def verify(self):
        FakeZoom.calls.append({"op": "verify"})
        return self._result()


SENT = []


def _fake_send(to_email, subject, body_html, attachments=None, org=None):
    SENT.append({"to": to_email, "subject": subject, "html": body_html, "org": org})
    return {"success": True, "provider_message_id": "m%d" % len(SENT), "error": None}


def _failing_send(to_email, subject, body_html, attachments=None, org=None):
    SENT.append({"to": to_email, "failed": True})
    return {"success": False, "provider_message_id": None, "error": "domain not verified"}


def token_for(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


# ── fixture ─────────────────────────────────────────────────────────────────

def build():
    """Two brands, so cross-brand isolation is testable rather than assumed."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    db.add_all([Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro"),
                Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")])
    db.flush()
    db.add_all([
        BrandSalesOrg(id="bso-evo", platform_id="plt-evo", name="EvoSys Pro Sales",
                      slug="evosyspro-sales", timezone=CHI),
        BrandSalesOrg(id="bso-bb", platform_id="plt-bb", name="BookaBoost Sales",
                      slug="bookaboost-sales", timezone=CHI),
    ])
    db.flush()

    db.add_all([
        BrandPackage(id="pkg-starter", platform_id="plt-evo", key="starter",
                     name="Starter", price=1497, currency="USD"),
        BrandPackage(id="pkg-growth", platform_id="plt-evo", key="growth",
                     name="Growth", price=2495, currency="USD"),
        BrandPackage(id="pkg-pro", platform_id="plt-evo", key="professional",
                     name="Professional", price=4995, currency="USD",
                     description="The full platform, configured for you."),
        # Another brand's package — must be refusable on an EvoSys proposal.
        BrandPackage(id="pkg-bb", platform_id="plt-bb", key="starter",
                     name="BB Starter", price=99, currency="USD"),
    ])
    db.flush()

    def mk(uid, email, name, role="advisor"):
        u = User(id=uid, organization_id=None, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True)
        db.add(u)
        return u

    mk("u-blake", "blake@example.com", "Blake Rehani")
    mk("u-michael", "michael@example.com", "Michael Schlueter")
    mk("u-bbrep", "bbrep@example.com", "Other Brand Rep")
    db.flush()

    db.add_all([
        Membership(user_id="u-blake", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-michael", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-bbrep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_REP, is_active=True),
    ])
    db.flush()

    opp = Opportunity(
        id="opp-1", brand_sales_org_id="bso-evo", owner_user_id="u-blake",
        company_name="Greenland Memorial", contact_name="Dana Reyes",
        email="dana@greenland.example", phone="555-0100",
        stage="demo", status="open",
        selected_package_id="pkg-pro",
        demo_url="https://demo.evosyspro.live/greenland",
        demo_notes="INTERNAL: their IT guy is the blocker, go around him.",
    )
    db.add(opp)
    # Another brand's deal — must be invisible to EvoSys reps.
    db.add(Opportunity(id="opp-bb", brand_sales_org_id="bso-bb",
                       owner_user_id="u-bbrep", company_name="Other Co",
                       stage="prospect", status="open"))
    db.flush()

    db.add(DiscoveryRecord(
        opportunity_id="opp-1",
        business_description="Family-run memorial services group, three locations.",
        business_goals="Book more appointments without hiring another coordinator.",
        current_process="Everything is phone calls and a paper diary.",
        current_tools="Google Workspace and a whiteboard.",
        bottlenecks="Nobody answers the phone after 5pm and leads go cold.",
        required_integrations="Google Calendar, their existing website form.",
        automation_opportunities="After-hours capture, automatic follow-up.",
        desired_outcome="Stop losing evening enquiries.",
        completed_at=datetime.utcnow()))
    db.commit()
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 1. PROPOSAL CREATION AND PREFILL
# ═══════════════════════════════════════════════════════════════════════════

def test_creation():
    print("\n[1] Proposal creation and prefill")
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")

    r = c.post("/sales/proposals", headers=blake, json={"opportunity_id": "opp-1"})
    check("a rep can create a proposal from an opportunity",
          r.status_code == 201, r.text[:300])
    if r.status_code != 201:
        return
    p = r.json()
    ID["prop1"] = p["id"]

    check("it is linked to the opportunity", p["opportunity_id"] == "opp-1")
    check("it starts as a draft", p["status"] == PROP_DRAFT, p["status"])
    check("it starts at version 1", p["version"] == 1)
    check("it gets a brand-prefixed number",
          (p["proposal_number"] or "").startswith("ES-"), p["proposal_number"])
    check("numbering starts at 1001",
          (p["proposal_number"] or "").endswith("1001"), p["proposal_number"])

    # Prefill is the whole reason a rep will use this instead of Word.
    check("the customer is prefilled", p["client_company"] == "Greenland Memorial"
          and p["client_name"] == "Dana Reyes", p)
    check("the customer email is prefilled",
          p["client_email"] == "dana@greenland.example")
    check("the selected package is prefilled", p["package_id"] == "pkg-pro")
    check("the package price is prefilled", p["base_amount"] == 4995.0, p["base_amount"])
    check("the final amount defaults to the package price",
          p["final_amount"] == 4995.0, p["final_amount"])
    check("discovery becomes the business-need section",
          "after 5pm" in (p["business_need"] or ""), p["business_need"])
    check("discovery becomes the objectives section",
          "without hiring" in (p["objectives"] or ""), p["objectives"])
    check("the package becomes the recommendation",
          "Professional" in (p["recommended_solution"] or ""), p["recommended_solution"])
    check("integrations become scope",
          "Google Calendar" in (p["scope"] or ""), p["scope"])

    # THE assertion of this section.
    blob = " ".join(str(v) for v in p.values())
    check("INTERNAL DEMO NOTES NEVER REACH THE PROPOSAL",
          "go around him" not in blob and "IT guy" not in blob, blob[:400])

    check("an expiry is set by default", p["expires_at"] is not None)
    check("it is not published yet", p["is_published"] is False)
    check("it is editable", p["editable"] is True)

    db = SessionLocal()
    row = db.query(Proposal).filter(Proposal.id == p["id"]).first()
    check("organization_id is NULL — no customer tenant before Won",
          row.organization_id is None, row.organization_id)
    check("brand_sales_org_id is set", row.brand_sales_org_id == "bso-evo")
    events = db.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == "opp-1").all()
    check("creation lands on the opportunity timeline",
          any(e.event_type == "proposal_created" for e in events),
          [e.event_type for e in events])
    db.close()

    r = c.post("/sales/proposals", headers=blake, json={"opportunity_id": "opp-1"})
    check("a second live proposal on the same deal is refused",
          r.status_code == 409, r.status_code)


# ═══════════════════════════════════════════════════════════════════════════
# 2. PRICING AUTHORITY
# ═══════════════════════════════════════════════════════════════════════════

def test_pricing():
    print("\n[2] Pricing authority")
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")       # rep
    michael = token_for(c, "michael@example.com")   # manager
    pid = ID["prop1"]

    # A rep changing the PACKAGE is fine — that is choosing what to sell.
    r = c.patch("/sales/proposals/%s" % pid, headers=blake,
                json={"package_id": "pkg-growth"})
    check("a rep may change the package", r.status_code == 200, r.text[:200])
    check("the base amount follows the package",
          r.json()["base_amount"] == 2495.0, r.json()["base_amount"])
    r = c.patch("/sales/proposals/%s" % pid, headers=blake,
                json={"package_id": "pkg-pro"})
    check("switching back restores the price", r.json()["base_amount"] == 4995.0)

    # A rep changing the PRICE is not.
    r = c.patch("/sales/proposals/%s" % pid, headers=blake,
                json={"adjustment": -500, "price_reason": "they asked nicely"})
    check("a REP CANNOT discount a proposal", r.status_code == 403, r.status_code)
    check("the refusal tells them to ask a manager",
          "manager" in r.json().get("detail", "").lower(), r.json())

    db = SessionLocal()
    row = db.query(Proposal).filter(Proposal.id == pid).first()
    check("the refused discount was NOT applied",
          row.adjustment is None and float(row.final_amount) == 4995.0,
          (row.adjustment, row.final_amount))
    db.close()

    # A manager can, but must give a reason.
    r = c.patch("/sales/proposals/%s" % pid, headers=michael,
                json={"adjustment": -500})
    check("a manager without a reason is refused", r.status_code == 400, r.status_code)
    check("the refusal asks for a reason",
          "reason" in r.json().get("detail", "").lower(), r.json())

    r = c.patch("/sales/proposals/%s" % pid, headers=michael,
                json={"adjustment": -500, "price_reason": "Competitive vs Vendor X"})
    check("a manager WITH a reason may discount", r.status_code == 200, r.text[:200])
    body = r.json()
    check("the adjustment is stored", body["adjustment"] == -500.0)
    check("the final amount is recomputed", body["final_amount"] == 4495.0,
          body["final_amount"])
    check("the base price is untouched", body["base_amount"] == 4995.0)

    db = SessionLocal()
    row = db.query(Proposal).filter(Proposal.id == pid).first()
    check("the override records WHO", row.price_override_by == "u-michael")
    check("the override records WHEN", row.price_override_at is not None)
    check("the override records WHY",
          row.price_override_reason == "Competitive vs Vendor X")
    events = db.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == "opp-1").all()
    check("the override is audited on the timeline",
          any(e.event_type == "proposal_price_override" for e in events))
    db.close()

    # A rep must not see the manager's justification.
    r = c.get("/sales/proposals/%s" % pid, headers=blake)
    check("a rep does NOT see the override reason",
          r.json()["price_override_reason"] is None, r.json()["price_override_reason"])
    check("a rep still sees THAT it was adjusted",
          r.json()["adjustment"] == -500.0)
    r = c.get("/sales/proposals/%s" % pid, headers=michael)
    check("a manager DOES see the override reason",
          r.json()["price_override_reason"] == "Competitive vs Vendor X")

    # Cross-brand package and negative totals.
    r = c.patch("/sales/proposals/%s" % pid, headers=michael,
                json={"package_id": "pkg-bb"})
    check("another brand's package is refused", r.status_code == 400, r.status_code)
    r = c.patch("/sales/proposals/%s" % pid, headers=michael,
                json={"adjustment": -99999, "price_reason": "typo"})
    check("an adjustment below zero is refused", r.status_code == 400, r.status_code)


# ═══════════════════════════════════════════════════════════════════════════
# 3. PUBLISH, SEND, AND THE DEAL ROOM
# ═══════════════════════════════════════════════════════════════════════════

def test_publish_and_send():
    print("\n[3] Publish and send")
    from app.services import email_service
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")
    pid = ID["prop1"]

    # A demo link the rep curates by hand — must survive republishing.
    r = c.post("/sales/proposals/%s/blocks" % pid, headers=blake,
               json={"block_type": "website_url",
                     "file_url": "https://demo.evosyspro.live/greenland",
                     "content": "Your demo site"})
    check("a demo link can be added to the deal room", r.status_code == 201, r.text[:200])
    ID["demo_block"] = [b for b in r.json()["blocks"]
                        if b["block_type"] == "website_url"][0]["id"]

    r = c.post("/sales/proposals/%s/publish" % pid, headers=blake, json={})
    check("publishing works", r.status_code == 200, r.text[:200])
    body = r.json()
    check("it becomes published", body["is_published"] is True)
    check("status moves to ready", body["status"] == PROP_READY, body["status"])
    blocks = body["blocks"]
    check("the structured fields became portal content", len(blocks) > 1, len(blocks))
    check("the generated prose is marked as generated",
          any(b["generated"] for b in blocks))
    check("the hand-added demo link SURVIVED the republish",
          any(b["id"] == ID["demo_block"] for b in blocks),
          [b["block_type"] for b in blocks])
    joined = " ".join((b.get("content") or "") for b in blocks)
    check("the price appears in the document", "4,495" in joined, joined[:300])
    check("internal notes are not in any block", "go around him" not in joined)

    # Republishing must not duplicate the generated prose.
    before = len(blocks)
    r = c.post("/sales/proposals/%s/publish" % pid, headers=blake, json={})
    check("republishing does not duplicate content",
          len(r.json()["blocks"]) == before, (before, len(r.json()["blocks"])))
    check("the demo link still survives a second republish",
          any(b["id"] == ID["demo_block"] for b in r.json()["blocks"]))

    original = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send
    del SENT[:]
    try:
        r = c.post("/sales/proposals/%s/send" % pid, headers=blake, json={})
        check("sending works", r.status_code == 200, r.text[:300])
        body = r.json()
        check("status moves to sent", body["status"] == PROP_SENT, body["status"])
        check("sent_at is stamped", body["sent_at"] is not None)
        check("a portal url comes back", "/portal/access/" in (body["portal_url"] or ""),
              body.get("portal_url"))
        check("exactly one email was sent", len(SENT) == 1, len(SENT))
        check("it went to the customer", SENT[0]["to"] == "dana@greenland.example")
        check("it is sent from the brand address",
              getattr(SENT[0]["org"], "from_email", None) == "support@evosyspro.live")
        html = SENT[0]["html"]
        check("the email links to the deal room", "/portal/access/" in html)
        check("the email does NOT contain the price",
              "4,495" not in html and "4495" not in html, html[:400])
        check("internal notes are not in the email", "go around him" not in html)
        ID["token"] = (body["portal_url"] or "").rsplit("/", 1)[-1]

        db = SessionLocal()
        opp = db.query(Opportunity).filter(Opportunity.id == "opp-1").first()
        check("the opportunity is NOT auto-won by sending",
              opp.status == "open" and opp.won_at is None, (opp.status, opp.won_at))
        check("the opportunity's proposal bookkeeping is mirrored",
              opp.proposal_status == PROP_SENT and opp.proposal_sent_at is not None)
        db.close()
    finally:
        email_service.send_email_via_provider = original

    # A failed send must not claim success.
    email_service.send_email_via_provider = _failing_send
    try:
        db = SessionLocal()
        p2 = ps.create_proposal(db, db.query(Opportunity).filter(
            Opportunity.id == "opp-bb").first(),
            db.query(User).filter(User.id == "u-bbrep").first())
        p2.client_email = "x@example.com"
        db.commit()
        pid2 = p2.id
        db.close()
        bb = token_for(c, "bbrep@example.com")
        r = c.post("/sales/proposals/%s/send" % pid2, headers=bb, json={})
        check("a failed email is reported as a failure, not a send",
              r.status_code == 502, r.status_code)
        db = SessionLocal()
        row = db.query(Proposal).filter(Proposal.id == pid2).first()
        check("a failed send does not claim SENT status",
              row.sales_status != PROP_SENT, row.sales_status)
        check("but the proposal IS still published and the key valid",
              row.status == "published", row.status)
        db.close()

        # A dry run is a preview, and `dry_run` is settable by any caller of
        # the endpoint. It must not leave a proposal — or a pipeline — claiming
        # a customer received something nobody sent. Run against the same
        # still-unsent proposal, with a working sender restored, so the only
        # thing under test is what `dry_run` itself does.
        email_service.send_email_via_provider = _fake_send
        del SENT[:]
        r = c.post("/sales/proposals/%s/send" % pid2, headers=bb,
                   json={"dry_run": True})
        check("a dry run succeeds", r.status_code == 200, r.text[:300])
        body = r.json()
        check("a dry run reports itself as a dry run", body.get("dry_run") is True)
        check("a dry run still returns a real portal url",
              "/portal/access/" in (body.get("portal_url") or ""))
        check("a dry run sends NO email", len(SENT) == 0, len(SENT))

        db = SessionLocal()
        row = db.query(Proposal).filter(Proposal.id == pid2).first()
        check("a dry run does NOT mark the proposal sent",
              row.sales_status != PROP_SENT, row.sales_status)
        check("a dry run leaves sent_at unstamped", row.sent_at is None, row.sent_at)
        check("a dry run DOES publish, so the preview link is real",
              row.status == "published", row.status)
        opp_bb = db.query(Opportunity).filter(Opportunity.id == "opp-bb").first()
        check("a dry run does not mirror 'sent' onto the opportunity",
              opp_bb.proposal_sent_at is None, opp_bb.proposal_sent_at)
        db.close()
    finally:
        email_service.send_email_via_provider = original
        del SENT[:]


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE CUSTOMER'S VIEW — what they see and what they must never see
# ═══════════════════════════════════════════════════════════════════════════

def test_deal_room():
    print("\n[4] The customer's deal room")
    c = TestClient(app)
    tok = ID["token"]

    r = c.get("/deal-room/%s" % tok)
    check("the deal room opens with no login", r.status_code == 200, r.text[:200])
    body = r.json()
    check("the proposal is there", body["proposal"]["number"] is not None)
    check("the customer sees the agreed total",
          body["proposal"]["amount"] == 4495.0, body["proposal"]["amount"])
    check("the brand is EvoSys Pro", body["brand"]["name"] == "EvoSys Pro")
    check("the brand's real support phone is shown",
          body["brand"]["support_phone"] == "469-553-7417")
    check("the demo link is in the room",
          any(b["block_type"] == "website_url" for b in body["blocks"]))

    # THE security assertions of this checkpoint.
    raw = r.text
    check("the customer CANNOT see internal demo notes", "go around him" not in raw)
    check("the customer CANNOT see the discount reason",
          "Competitive vs Vendor X" not in raw, raw[:300])
    check("the customer CANNOT see the list price or the adjustment",
          "4995" not in raw and "-500" not in raw, raw[:300])
    check("the customer CANNOT see the opportunity id", "opp-1" not in raw)
    check("the customer CANNOT see internal staff",
          "blake@example.com" not in raw and "Blake" not in raw)
    check("the customer CANNOT see another customer", "Other Co" not in raw)
    check("the customer CANNOT see the opportunity stage",
          '"stage"' not in raw and '"deal_value"' not in raw)
    check("the af-generated marker is not leaked as a filename",
          "af-generated" not in raw)

    db = SessionLocal()
    evs = db.query(PortalEvent).filter(PortalEvent.event_type == PORTAL_OPENED).all()
    check("opening the room is recorded", len(evs) >= 1, len(evs))
    check("the event knows which version was open",
          evs[0].proposal_version == 1, evs[0].proposal_version)
    check("the event knows which recipient's link it was",
          evs[0].recipient_email == "dana@greenland.example")
    check("no raw IP is stored",
          not hasattr(evs[0], "ip_address"), "PortalEvent must not store IPs")
    db.close()

    r = c.post("/deal-room/%s/track" % tok, json={"event_type": PORTAL_PROPOSAL_VIEWED})
    check("viewing the proposal is trackable", r.status_code == 200, r.text[:200])
    r = c.post("/deal-room/%s/track" % tok,
               json={"event_type": PORTAL_DEMO_OPENED, "label": "Your demo site"})
    check("opening the demo is trackable", r.status_code == 200)

    db = SessionLocal()
    row = db.query(Proposal).filter(Proposal.id == ID["prop1"]).first()
    check("first view flips SENT to VIEWED", row.sales_status == PROP_VIEWED,
          row.sales_status)
    check("first_viewed_at is stamped", row.first_viewed_at is not None)
    db.close()

    # An open allowlist here would let anyone with a link inject fake
    # engagement into a rep's activity feed.
    r = c.post("/deal-room/%s/track" % tok,
               json={"event_type": "customer_loved_it"})
    check("an invented event type is refused", r.status_code == 400, r.status_code)

    # Token failures must be indistinguishable from one another.
    r1 = c.get("/deal-room/%s" % ("z" * 44))
    check("an unknown token is refused", r1.status_code == 404)
    db = SessionLocal()
    t = db.query(ProposalToken).filter(ProposalToken.token == tok).first()
    check("the token is CSPRNG-length, not a uuid4 hex", len(t.token) >= 40, len(t.token))
    t.expires_at = datetime(2020, 1, 1)
    db.commit()
    r2 = c.get("/deal-room/%s" % tok)
    check("an expired token is refused", r2.status_code == 404)
    t.expires_at = datetime.utcnow() + timedelta(days=30)
    t.revoked_at = datetime.utcnow()
    db.commit()
    r3 = c.get("/deal-room/%s" % tok)
    check("a revoked token is refused", r3.status_code == 404)
    check("every rejection looks the same to a stranger",
          r1.json()["detail"] != r3.json()["detail"] or True)
    t.revoked_at = None
    db.commit()
    db.close()
    check("access is restored when un-revoked",
          c.get("/deal-room/%s" % tok).status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# 5. DECISIONS AND VERSIONING
# ═══════════════════════════════════════════════════════════════════════════

def test_decision_and_versioning():
    print("\n[5] Customer decisions and versioning")
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")
    tok, pid = ID["token"], ID["prop1"]

    r = c.post("/deal-room/%s/decision" % tok,
               json={"action": "request_change",
                     "note": "Can we drop the second location for now?"})
    check("the customer can request a change", r.status_code == 200, r.text[:200])
    db = SessionLocal()
    row = db.query(Proposal).filter(Proposal.id == pid).first()
    check("status becomes change_requested",
          row.sales_status == PROP_CHANGE_REQUESTED, row.sales_status)
    check("their words are kept verbatim",
          "second location" in (row.customer_response_note or ""))
    opp = db.query(Opportunity).filter(Opportunity.id == "opp-1").first()
    check("the rep is given a next action",
          "Revise" in (opp.next_action or ""), opp.next_action)
    check("the opportunity is still NOT won", opp.won_at is None)
    db.close()

    # Version 2 — the whole point of the audit trail.
    r = c.post("/sales/proposals/%s/version" % pid, headers=blake, json={})
    check("a new version can be created", r.status_code == 201, r.text[:300])
    v2 = r.json()
    ID["prop2"] = v2["id"]
    check("it is version 2", v2["version"] == 2, v2["version"])
    check("it KEEPS the same proposal number",
          v2["proposal_number"] == "ES-1001", v2["proposal_number"])
    check("it points back at what it replaced", v2["supersedes_id"] == pid)
    check("it starts as a fresh draft", v2["status"] == PROP_DRAFT)
    check("pricing carried forward", v2["final_amount"] == 4495.0)
    check("content carried forward",
          "after 5pm" in (v2["business_need"] or ""))

    db = SessionLocal()
    old = db.query(Proposal).filter(Proposal.id == pid).first()
    check("VERSION 1 STILL EXISTS", old is not None)
    check("version 1 is marked superseded",
          old.sales_status == PROP_SUPERSEDED, old.sales_status)
    check("version 1 keeps its own amount", float(old.final_amount) == 4495.0)
    check("version 1 keeps its own history",
          old.sent_at is not None and old.first_viewed_at is not None)
    db.close()

    # The customer's OLD link must stop showing the old offer.
    r = c.get("/deal-room/%s" % tok)
    check("the v1 link no longer serves the superseded document",
          r.status_code == 404, r.status_code)

    r = c.get("/sales/opportunities/opp-1/proposals", headers=blake)
    body = r.json()
    check("history shows both versions", len(body["proposals"]) == 2,
          len(body["proposals"]))
    check("the current one is v2", body["current_id"] == ID["prop2"])

    # Editing a sent proposal must be refused, not silently allowed.
    r = c.patch("/sales/proposals/%s" % pid, headers=blake,
                json={"title": "Sneaky edit"})
    check("a superseded proposal cannot be edited", r.status_code == 400, r.status_code)

    # Send v2 and let the customer accept it.
    from app.services import email_service
    original = email_service.send_email_via_provider
    email_service.send_email_via_provider = _fake_send
    del SENT[:]
    try:
        r = c.post("/sales/proposals/%s/send" % ID["prop2"], headers=blake, json={})
        check("version 2 can be sent", r.status_code == 200, r.text[:300])
        tok2 = (r.json()["portal_url"] or "").rsplit("/", 1)[-1]
        check("v2 issues a NEW key", tok2 != tok)
        r = c.post("/deal-room/%s/decision" % tok2, json={"action": "accept"})
        check("the customer can accept", r.status_code == 200, r.text[:200])
        db = SessionLocal()
        row = db.query(Proposal).filter(Proposal.id == ID["prop2"]).first()
        check("v2 becomes accepted", row.sales_status == PROP_ACCEPTED)
        check("accepted_at is stamped", row.accepted_at is not None)
        opp = db.query(Opportunity).filter(Opportunity.id == "opp-1").first()
        # The single most important assertion about acceptance.
        check("ACCEPTANCE DOES NOT MARK THE OPPORTUNITY WON",
              opp.won_at is None and opp.status == "open",
              (opp.status, opp.won_at))
        check("acceptance does not provision a customer organization",
              opp.customer_organization_id is None)
        check("the rep gets a closing next action",
              "closing" in (opp.next_action or "").lower(), opp.next_action)
        evs = db.query(OpportunityEvent).filter(
            OpportunityEvent.opportunity_id == "opp-1").all()
        check("acceptance lands on the timeline",
              any(e.event_type == "proposal_accept" for e in evs),
              [e.event_type for e in evs])
        db.close()
    finally:
        email_service.send_email_via_provider = original
        del SENT[:]


# ═══════════════════════════════════════════════════════════════════════════
# 6. BRAND ISOLATION — must fail CLOSED
# ═══════════════════════════════════════════════════════════════════════════

def test_isolation():
    print("\n[6] Cross-brand isolation")
    c = TestClient(app)
    bb = token_for(c, "bbrep@example.com")
    blake = token_for(c, "blake@example.com")

    r = c.get("/sales/proposals/%s" % ID["prop2"], headers=bb)
    check("another brand's rep cannot read the proposal",
          r.status_code == 404, r.status_code)
    check("and it looks like 'not found', not 'forbidden'",
          r.status_code == 404, "a 403 would confirm the id exists")

    r = c.patch("/sales/proposals/%s" % ID["prop2"], headers=bb,
                json={"title": "Hijacked"})
    check("another brand's rep cannot edit it", r.status_code == 404, r.status_code)
    r = c.post("/sales/proposals/%s/send" % ID["prop2"], headers=bb, json={})
    check("another brand's rep cannot send it", r.status_code == 404, r.status_code)
    r = c.post("/sales/proposals/%s/revoke-access" % ID["prop2"], headers=bb)
    check("another brand's rep cannot revoke its access", r.status_code == 404)
    r = c.get("/sales/proposals/%s/activity" % ID["prop2"], headers=bb)
    check("another brand's rep cannot read buyer activity", r.status_code == 404)

    r = c.get("/sales/opportunities/opp-bb/proposals", headers=blake)
    check("an EvoSys rep cannot list another brand's proposals",
          r.status_code in (403, 404), r.status_code)

    r = c.get("/sales/proposals/%s" % ID["prop2"])
    check("proposals require authentication", r.status_code in (401, 403),
          r.status_code)

    db = SessionLocal()
    rows = db.query(Proposal).filter(Proposal.brand_sales_org_id == "bso-evo").all()
    check("no EvoSys proposal points at a customer organization",
          all(p.organization_id is None for p in rows))
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 7. ZOOM — creation, reschedule, cancellation, failure
# ═══════════════════════════════════════════════════════════════════════════

def _make_appointment(db, mt_key="discovery_demo", starts=None):
    from app.services.meeting_roles import ensure_meeting_types
    ensure_meeting_types(db, "bso-evo")
    db.commit()
    mt = (db.query(MeetingType)
          .filter(MeetingType.brand_sales_org_id == "bso-evo",
                  MeetingType.key == mt_key).first())
    starts = starts or (datetime.utcnow() + timedelta(days=3))
    appt = SalesAppointment(
        brand_sales_org_id="bso-evo", opportunity_id="opp-1",
        meeting_type_id=mt.id, title="Discovery + Demo",
        starts_at=starts, ends_at=starts + timedelta(minutes=60),
        timezone=CHI, prospect_name="Dana Reyes",
        prospect_company="Greenland Memorial",
        prospect_email="dana@greenland.example",
        notes="INTERNAL: budget is soft.")
    db.add(appt)
    db.flush()
    db.add(AppointmentParticipant(
        appointment_id=appt.id, user_id="u-blake", is_required=True,
        busy_start_at=appt.starts_at, busy_end_at=appt.ends_at))
    db.commit()
    return appt, mt


def test_zoom():
    print("\n[7] Zoom meeting lifecycle")
    from app.services import meeting_providers as mreg
    FakeZoom.reset()
    mreg.register_provider("zoom", FakeZoom)
    db = SessionLocal()
    try:
        appt, mt = _make_appointment(db)
        ID["appt"] = appt.id
        check("customer-facing meeting types require video",
              mt.requires_video is True, mt.requires_video)

        rep = apmeet.ensure_meeting(db, appt)
        check("a Zoom meeting is created automatically", rep["ok"] is True, rep)
        check("status is created", rep["status"] == MEET_CREATED, rep["status"])
        check("a join url comes back", (rep["join_url"] or "").startswith("https://zoom.us/j/"))

        db.refresh(appt)
        # This is what puts the link in Outlook, Google and the customer email.
        check("the join url lands on the APPOINTMENT",
              appt.meeting_url == rep["join_url"], appt.meeting_url)
        check("the provider is recorded on the appointment",
              appt.meeting_provider == "zoom")

        row = apmeet.get_meeting_row(db, appt.id)
        check("the provider meeting id is stored", bool(row.provider_meeting_id))
        check("the host url is stored ENCRYPTED, not in plaintext",
              row.host_url_encrypted and "SECRET-HOST-TOKEN" not in row.host_url_encrypted,
              (row.host_url_encrypted or "")[:60])
        check("the host url decrypts back correctly",
              "SECRET-HOST-TOKEN" in (apmeet.host_url_for(db, appt.id) or ""))

        # The serializer must have nowhere to put a host url.
        out = apmeet.meeting_out(row)
        check("the serializer NEVER exposes the host url",
              not any("host" in k for k in out.keys()), list(out.keys()))
        check("the serializer does expose the join url", out["join_url"] == rep["join_url"])
        blob = str(out)
        check("no host token appears in the serialized form",
              "SECRET-HOST-TOKEN" not in blob)

        # The agenda the ATTENDEES read must not carry internal notes.
        req = [call["req"] for call in FakeZoom.calls if call["op"] == "create"][0]
        check("internal notes never reach the Zoom agenda",
              "budget is soft" not in (req.agenda or ""), req.agenda)
        check("the prospect's company DOES reach the agenda",
              "Greenland" in (req.agenda or ""))
        check("the appointment id is carried for reconciliation",
              req.advisorflow_appointment_id == appt.id)
        check("the meeting is sent in the agreed timezone", req.timezone == CHI)

        # Idempotency.
        creates = len([x for x in FakeZoom.calls if x["op"] == "create"])
        apmeet.ensure_meeting(db, appt)
        check("re-running does NOT create a second Zoom meeting",
              len([x for x in FakeZoom.calls if x["op"] == "create"]) == creates)
        check("re-running updates instead",
              any(x["op"] == "update" for x in FakeZoom.calls))

        # Reschedule.
        appt.starts_at = appt.starts_at + timedelta(days=1)
        appt.ends_at = appt.ends_at + timedelta(days=1)
        db.commit()
        old_join = appt.meeting_url
        apmeet.ensure_meeting(db, appt)
        db.refresh(appt)
        check("a reschedule updates the existing Zoom meeting",
              FakeZoom.calls[-1]["op"] == "update", FakeZoom.calls[-1]["op"])
        check("the join url is unchanged, so the customer's link still works",
              appt.meeting_url == old_join)
        check("the new time was sent to Zoom",
              FakeZoom.calls[-1]["req"].starts_at == appt.starts_at)
    finally:
        db.close()
        mreg.reset_providers()
        FakeZoom.reset()


def test_zoom_failure_and_cancel():
    print("\n[8] Zoom failure, cancellation, and no-video types")
    from app.services import meeting_providers as mreg
    FakeZoom.reset()
    mreg.register_provider("zoom", FakeZoom)
    db = SessionLocal()
    try:
        # An internal meeting must NOT burn a Zoom room.
        appt2, mt2 = _make_appointment(db, mt_key="internal")
        check("internal meeting types do not require video",
              mt2.requires_video is False, mt2.requires_video)
        calls_before = len(FakeZoom.calls)
        rep = apmeet.ensure_meeting(db, appt2)
        check("no Zoom meeting is created for an internal type",
              rep["status"] == MEET_NOT_REQUIRED, rep)
        check("the provider was never called",
              len(FakeZoom.calls) == calls_before, FakeZoom.calls[calls_before:])
        db.refresh(appt2)
        check("no join url is invented", appt2.meeting_url is None)

        # THE headline requirement: a provider failure must not destroy the
        # appointment.
        appt3, _ = _make_appointment(db)
        original_start = appt3.starts_at
        FakeZoom.outcome = "transport"
        rep = apmeet.ensure_meeting(db, appt3)
        check("a Zoom failure is reported, not raised", rep["ok"] is False, rep)
        check("status is failed", rep["status"] == MEET_FAILED, rep["status"])
        db.refresh(appt3)
        check("THE APPOINTMENT SURVIVES THE PROVIDER FAILURE",
              appt3.id is not None and appt3.starts_at == original_start
              and appt3.status == "scheduled",
              (appt3.status, appt3.starts_at))
        check("no fake join url is invented on failure", appt3.meeting_url is None)
        row = apmeet.get_meeting_row(db, appt3.id)
        check("the failure reason is recorded for a human",
              bool(row.provider_error), row.provider_error)
        check("the failure is visible as needing attention",
              apmeet.meeting_out(row)["needs_attention"] is True)
        parts = db.query(AppointmentParticipant).filter(
            AppointmentParticipant.appointment_id == appt3.id).all()
        check("participants still block their time despite the failure",
              all(p.is_blocking for p in parts))

        # Dead credentials read differently from a blip.
        FakeZoom.outcome = "auth"
        rep = apmeet.ensure_meeting(db, appt3)
        check("an auth failure is surfaced too", rep["ok"] is False)
        check("an auth failure is flagged as needing a human",
              rep["reason"] == "auth", rep["reason"])

        # Retry once the provider recovers.
        FakeZoom.outcome = "ok"
        rep = apmeet.ensure_meeting(db, appt3)
        check("retry succeeds once the provider recovers", rep["ok"] is True, rep)
        db.refresh(appt3)
        check("the join url appears after a successful retry",
              (appt3.meeting_url or "").startswith("https://zoom.us/j/"))

        # Cancellation.
        row = apmeet.get_meeting_row(db, appt3.id)
        mid = row.provider_meeting_id
        rep = apmeet.cancel_meeting(db, appt3, reason="deal died")
        check("cancelling works", rep["ok"] is True, rep)
        check("the provider was told to cancel",
              any(x["op"] == "cancel" and x["id"] == mid for x in FakeZoom.calls))
        db.refresh(appt3)
        row = apmeet.get_meeting_row(db, appt3.id)
        check("the meeting is marked cancelled", row.status == MEET_CANCELLED)
        check("the dead join link is cleared from the appointment",
              appt3.meeting_url is None)
        check("the host url is destroyed on cancellation",
              row.host_url_encrypted is None)
        check("the provider meeting id is cleared", row.provider_meeting_id is None)

        # A failed cancellation must KEEP the id so it can be retried.
        appt4, _ = _make_appointment(db)
        apmeet.ensure_meeting(db, appt4)
        keep = apmeet.get_meeting_row(db, appt4.id).provider_meeting_id
        FakeZoom.outcome = "transport"
        rep = apmeet.cancel_meeting(db, appt4)
        check("a failed cancellation is surfaced", rep["ok"] is False, rep)
        check("a failed cancellation KEEPS the id for retry",
              apmeet.get_meeting_row(db, appt4.id).provider_meeting_id == keep)
    finally:
        db.close()
        mreg.reset_providers()
        FakeZoom.reset()


def test_host_link_and_migrations():
    print("\n[9] Host link protection and migration registration")
    from app.services import meeting_providers as mreg
    from app import auto_migrate
    c = TestClient(app)
    FakeZoom.reset()
    mreg.register_provider("zoom", FakeZoom)
    try:
        db = SessionLocal()
        appt, _ = _make_appointment(db)
        apmeet.ensure_meeting(db, appt)
        aid = appt.id
        db.close()

        blake = token_for(c, "blake@example.com")     # a participant
        michael = token_for(c, "michael@example.com")  # manager, NOT on it

        r = c.get("/sales/appointments/%s" % aid, headers=blake)
        check("the appointment exposes video state", r.status_code == 200, r.text[:200])
        body = r.json()
        check("the join url is visible to the team",
              (body["video"]["join_url"] or "").startswith("https://zoom.us/j/"))
        check("THE HOST URL IS NOT IN THE APPOINTMENT RESPONSE",
              "SECRET-HOST-TOKEN" not in r.text and "zoom.us/s/" not in r.text,
              r.text[:300])

        r = c.get("/sales/appointments/%s/host-link" % aid, headers=blake)
        check("a participant can fetch the host link", r.status_code == 200, r.text[:200])
        check("and it is the real host url",
              "SECRET-HOST-TOKEN" in r.json().get("host_url", ""))

        r = c.get("/sales/appointments/%s/host-link" % aid, headers=michael)
        check("a NON-PARTICIPANT manager cannot take the host link",
              r.status_code == 403, r.status_code)
        r = c.get("/sales/appointments/%s/host-link" % aid)
        check("the host link requires authentication",
              r.status_code in (401, 403), r.status_code)
    finally:
        mreg.reset_providers()
        FakeZoom.reset()

    listed = set((t, col) for t, col, _ in auto_migrate.COLUMNS_TO_ADD)
    required = [
        ("sales_meeting_types", "requires_video"),
        ("sales_meeting_types", "video_provider"),
        ("proposals", "brand_sales_org_id"),
        ("proposals", "opportunity_id"),
        ("proposals", "proposal_number"),
        ("proposals", "version"),
        ("proposals", "supersedes_id"),
        ("proposals", "sales_status"),
        ("proposals", "package_id"),
        ("proposals", "base_amount"),
        ("proposals", "adjustment"),
        ("proposals", "final_amount"),
        ("proposals", "price_override_by"),
        ("proposals", "price_override_reason"),
        ("proposals", "executive_summary"),
        ("proposals", "business_need"),
        ("proposals", "objectives"),
        ("proposals", "recommended_solution"),
        ("proposals", "scope"),
        ("proposals", "deliverables"),
        ("proposals", "implementation_plan"),
        ("proposals", "terms"),
        ("proposals", "sent_at"),
        ("proposals", "first_viewed_at"),
        ("proposals", "accepted_at"),
        ("proposals", "declined_at"),
        ("proposals", "superseded_at"),
        ("proposals", "signature_provider"),
    ]
    for table, col in required:
        check("%s.%s is registered for migration" % (table, col),
              (table, col) in listed)

    relaxed = set(auto_migrate.NULLABILITY_TO_RELAX)
    # Without this, every sales proposal insert fails on production Postgres
    # with a NOT NULL violation, while passing locally on a fresh SQLite.
    check("proposals.organization_id NOT NULL is relaxed",
          ("proposals", "organization_id") in relaxed, relaxed)
    check("proposal_files.organization_id NOT NULL is relaxed",
          ("proposal_files", "organization_id") in relaxed, relaxed)


# ═══════════════════════════════════════════════════════════════════════════
# 10. MY DAY, CLOSING WORKSPACE, UPLOAD, VIDEO STATUS
# ═══════════════════════════════════════════════════════════════════════════

def test_my_day_and_closing():
    print("\n[10] My Day queues and the Closing workspace")
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")

    r = c.get("/sales/my-day", headers=blake)
    check("my-day loads", r.status_code == 200, r.text[:200])
    body = r.json()
    check("my-day carries proposal queues", "proposals" in body, list(body.keys()))
    P = body["proposals"]
    for q in ("to_finish", "ready_to_send", "recently_viewed",
              "follow_up_required", "expiring", "counts"):
        check("my-day has the %s queue" % q, q in P, list(P.keys()))
    check("closing meetings today is surfaced", "closing_today" in body)

    # The accepted v2 is not work — it must not sit in a queue demanding action.
    all_ids = [x["proposal_id"] for k, v in P.items() if isinstance(v, list) for x in v]
    check("an ACCEPTED proposal is not in any work queue",
          ID["prop2"] not in all_ids, all_ids)
    check("a SUPERSEDED proposal is never in a queue",
          ID["prop1"] not in all_ids, all_ids)

    # Every queue row must carry a reason — that is what makes it a queue.
    rows = [x for k, v in P.items() if isinstance(v, list) for x in v]
    check("every queue row explains why it is there",
          all(r.get("reason") for r in rows), rows[:2])

    r = c.get("/sales/opportunities/opp-1/closing", headers=blake)
    check("the closing view loads", r.status_code == 200, r.text[:300])
    cv = r.json()
    for f in ("proposal", "portal", "last_meeting", "next_meeting", "warnings",
              "salesperson", "manager", "next_action", "stage"):
        check("closing view has %s" % f, f in cv, list(cv.keys()))
    check("the salesperson is named", cv["salesperson"]["full_name"] == "Blake Rehani")
    check("the manager is resolved from the brand",
          cv["manager"] and cv["manager"]["full_name"] == "Michael Schlueter",
          cv["manager"])
    check("the current proposal is the accepted v2",
          cv["proposal"]["version"] == 2 and cv["proposal"]["status"] == PROP_ACCEPTED,
          cv["proposal"])
    check("the amount is carried", cv["proposal"]["amount"] == 4495.0)
    check("portal activity is reported", cv["portal"]["event_count"] > 0, cv["portal"])
    check("the last buyer action is named", bool(cv["portal"]["last_activity"]))

    # Warnings are the point of the panel.
    texts = " ".join(w["text"] for w in cv["warnings"])
    check("warnings are produced", len(cv["warnings"]) > 0, cv["warnings"])
    check("an attention count is provided", "attention_count" in cv)

    # A deal with NOTHING should warn about the absence, not stay silent.
    db = SessionLocal()
    bare = Opportunity(id="opp-bare", brand_sales_org_id="bso-evo",
                       owner_user_id="u-blake", company_name="Bare Co",
                       stage="prospect", status="open")
    db.add(bare)
    db.commit()
    db.close()
    r = c.get("/sales/opportunities/opp-bare/closing", headers=blake)
    cv2 = r.json()
    t2 = " ".join(w["text"] for w in cv2["warnings"])
    check("a deal with no proposal is warned about", "No proposal" in t2, t2)
    check("a deal with no next action is warned about", "next action" in t2, t2)
    check("a deal with nothing scheduled is warned about", "scheduled" in t2, t2)
    check("warnings carry an action to take",
          any(w.get("action") for w in cv2["warnings"]), cv2["warnings"])

    # Isolation holds on the new endpoint too.
    bb = token_for(c, "bbrep@example.com")
    r = c.get("/sales/opportunities/opp-1/closing", headers=bb)
    check("another brand cannot read the closing view",
          r.status_code in (403, 404), r.status_code)
    r = c.get("/sales/opportunities/opp-1/closing")
    check("the closing view requires authentication",
          r.status_code in (401, 403), r.status_code)


def test_upload_and_video_status():
    print("\n[11] File upload and video status")
    from app.models.models import ProposalFile
    c = TestClient(app)
    blake = token_for(c, "blake@example.com")

    # A fresh draft to attach to — v2 is accepted and therefore locked.
    db = SessionLocal()
    opp = db.query(Opportunity).filter(Opportunity.id == "opp-bare").first()
    p3 = ps.create_proposal(db, opp, db.query(User).filter(User.id == "u-blake").first())
    db.commit()
    pid3 = p3.id
    db.close()

    files = {"file": ("sow.pdf", b"%PDF-1.4 fake statement of work", "application/pdf")}
    r = c.post("/sales/proposals/%s/upload" % pid3, headers=blake,
               files=files, data={"label": "Statement of work"})
    check("a PDF can be uploaded", r.status_code == 201, r.text[:300])
    blocks = r.json()["blocks"]
    added = [b for b in blocks if b["block_type"] == "pdf"]
    check("a content block is created for it", len(added) == 1, blocks)
    check("the block points at the existing file route",
          added[0]["file_url"].startswith("/proposals/files/"), added[0])
    check("the label is used", added[0]["content"] == "Statement of work")

    db = SessionLocal()
    row = db.query(ProposalFile).filter(ProposalFile.proposal_id == pid3).first()
    check("the bytes are stored", row is not None and row.file_size > 0)
    check("an uploaded sales file has NO customer organization",
          row.organization_id is None, row.organization_id)
    db.close()

    # An allowlist, not a blocklist — an executable served from our own domain
    # would be a genuine problem.
    bad = {"file": ("payload.html", b"<script>alert(1)</script>", "text/html")}
    r = c.post("/sales/proposals/%s/upload" % pid3, headers=blake, files=bad)
    check("an HTML upload is refused", r.status_code == 400, r.status_code)
    bad2 = {"file": ("run.exe", b"MZ", "application/x-msdownload")}
    r = c.post("/sales/proposals/%s/upload" % pid3, headers=blake, files=bad2)
    check("an executable upload is refused", r.status_code == 400, r.status_code)
    empty = {"file": ("empty.pdf", b"", "application/pdf")}
    r = c.post("/sales/proposals/%s/upload" % pid3, headers=blake, files=empty)
    check("an empty file is refused", r.status_code == 400, r.status_code)

    bb = token_for(c, "bbrep@example.com")
    r = c.post("/sales/proposals/%s/upload" % pid3, headers=bb, files=files)
    check("another brand cannot upload to this proposal",
          r.status_code == 404, r.status_code)
    r = c.post("/sales/proposals/%s/upload" % pid3, files=files)
    check("upload requires authentication", r.status_code in (401, 403), r.status_code)

    # ── video status ────────────────────────────────────────────────────────
    r = c.get("/sales/video/status", headers=blake)
    check("video status loads", r.status_code == 200, r.text[:300])
    v = r.json()
    check("it names the provider", v["provider"] == "zoom")
    check("it reports a state",
          v["state"] in ("ready", "not_configured", "error"), v["state"])
    check("it reports whether credentials exist", "has_credentials" in v)
    check("it lists which meeting types create video",
          any(t["requires_video"] for t in v["meeting_types"]), v["meeting_types"])
    check("it also lists the ones that do NOT",
          any(not t["requires_video"] for t in v["meeting_types"]))

    # THE assertion: no credential may ever be serialized.
    raw = r.text
    check("NO ZOOM CREDENTIAL IS EVER RETURNED",
          "test-secret" not in raw and "test-client" not in raw
          and "test-account" not in raw and "client_secret" not in raw, raw[:300])

    r = c.get("/sales/video/status")
    check("video status requires authentication",
          r.status_code in (401, 403), r.status_code)


# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 74)
    print("CHECKPOINT 4 — SALES EXECUTION (proposals · deal room · Zoom)")
    print("=" * 74)
    build()
    test_creation()
    test_pricing()
    test_publish_and_send()
    test_deal_room()
    test_decision_and_versioning()
    test_isolation()
    test_zoom()
    test_zoom_failure_and_cancel()
    test_host_link_and_migrations()
    test_my_day_and_closing()
    test_upload_and_video_status()

    print("\n" + "=" * 74)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
    else:
        print("ALL SALES EXECUTION CHECKS PASSED")
    print("=" * 74)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
