"""Sales Manager workspace regression suite — Checkpoint 5.

Team Today, Attention Required, Approvals, Closing Pipeline, Rep rollup,
drill-down, permissions, and multi-brand isolation.

NO TEST EVER CONTACTS ZOOM, A CALENDAR, OR SENDS AN EMAIL. Nothing in this
suite calls a provider at all: every failure state it asserts on is written
directly as the row a real failure would have left behind. There is no path in
here that can reach a real prospect.

Temp SQLite. Never touches production.

    python scripts/smoke_manager_workspace.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="mgrws_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                        # noqa: E402
from app.main import app                                         # noqa: E402
from app.deps import SessionLocal, engine                        # noqa: E402
from app.models.models import (                                  # noqa: E402
    Base, Platform, User, Proposal, PortalEvent,
    PROP_DRAFT, PROP_READY, PROP_SENT, PROP_VIEWED, PROP_DECLINED,
    PROP_CHANGE_REQUESTED, PROP_ACCEPTED,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED,
)
from app.models.sales_models import (                            # noqa: E402
    Membership, BrandSalesOrg, Opportunity, BrandPackage, OpportunityEvent,
    PricingApprovalRequest,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DENIED, APPROVAL_WITHDRAWN,
    APPROVAL_STALE,
    STAGE_CLOSING, STAGE_DEMO_BUILD, STAGE_PROPOSAL,
)
from app.models.scheduling_models import (                       # noqa: E402
    MeetingType, SalesAppointment, AppointmentParticipant, CONF_PENDING,
    CONF_CONFIRMED,
)
from app.models.calendar_models import SYNC_REAUTH, SYNC_SYNCED  # noqa: E402
from app.models.meeting_models import (                          # noqa: E402
    AppointmentMeeting, MEET_CREATED, MEET_FAILED,
)
from app.services.auth_service import hash_password              # noqa: E402
from app.services import proposal_service as ps                  # noqa: E402
from app.services import availability as av                      # noqa: E402
from app.services.meeting_roles import ensure_meeting_types      # noqa: E402

PW = "MgrPass123!"
CHI = "America/Chicago"
FAILURES = []
ID = {}
NOW = datetime.utcnow()


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def token_for(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def local_today_at(hour_utc_offset_hours):
    """An instant inside TODAY's local window in the brand timezone.

    Computed from the same helper the service uses, rather than assuming UTC
    and local days line up — they do not, and a test that assumed they did
    would pass in one timezone and fail in another.
    """
    day = av.utc_to_local(NOW, CHI).date()
    start = av.local_to_utc(day, 9 * 60, CHI)      # 9am local
    return start + timedelta(hours=hour_utc_offset_hours)


# ── fixture ─────────────────────────────────────────────────────────────────

def build():
    """Two brands, one manager and two reps in the first, so both isolation and
    per-rep attribution are testable rather than assumed."""
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
        BrandPackage(id="pkg-pro", platform_id="plt-evo", key="professional",
                     name="Professional", price=4995, currency="USD"),
        BrandPackage(id="pkg-bb", platform_id="plt-bb", key="starter",
                     name="BB Starter", price=99, currency="USD"),
    ])
    db.flush()

    def mk(uid, email, name):
        db.add(User(id=uid, organization_id=None, email=email, full_name=name,
                    password_hash=hash_password(PW), role="advisor",
                    must_change_password=False, is_active=True))

    mk("u-michael", "michael@example.com", "Michael Schlueter")   # manager
    mk("u-blake", "blake@example.com", "Blake Rehani")            # rep
    mk("u-casey", "casey@example.com", "Casey Nolan")             # rep
    mk("u-bbmgr", "bbmgr@example.com", "Other Brand Manager")
    mk("u-bbrep", "bbrep@example.com", "Other Brand Rep")
    db.flush()

    db.add_all([
        Membership(user_id="u-michael", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-blake", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-casey", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-evo", role=ROLE_SALES_REP, is_active=True),
        Membership(user_id="u-bbmgr", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id="u-bbrep", scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id="bso-bb", role=ROLE_SALES_REP, is_active=True),
    ])
    db.flush()
    ensure_meeting_types(db, "bso-evo")
    ensure_meeting_types(db, "bso-bb")
    db.flush()

    def opp(oid, owner, company, stage, **kw):
        o = Opportunity(id=oid, brand_sales_org_id="bso-evo", owner_user_id=owner,
                        company_name=company, contact_name=company + " Contact",
                        email="c@%s.example" % oid, stage=stage, status="open",
                        selected_package_id="pkg-pro", deal_value=4995,
                        stage_changed_at=NOW - timedelta(days=2),
                        next_action="Follow up",
                        next_action_due_at=NOW + timedelta(days=1), **kw)
        db.add(o)
        return o

    # Blake's book
    opp("opp-live", "u-blake", "Greenland Memorial", STAGE_CLOSING)
    opp("opp-stalled", "u-blake", "Ridgeway Funeral", STAGE_DEMO_BUILD,
        )
    opp("opp-noaction", "u-blake", "Pinecrest Services", STAGE_PROPOSAL)
    # Casey's book
    opp("opp-casey", "u-casey", "Lakeview Chapel", STAGE_PROPOSAL)
    # A deal nobody has touched at all: no proposal, no timeline, no meeting.
    # Created directly rather than through the API precisely so it has no
    # events — this is the deal that quietly dies without a manager screen.
    opp("opp-quiet", "u-casey", "Hollow Oak Memorial", STAGE_DEMO_BUILD)
    # Another brand's deal — must never appear in an EvoSys manager's view.
    db.add(Opportunity(id="opp-bb", brand_sales_org_id="bso-bb",
                       owner_user_id="u-bbrep", company_name="Other Co",
                       stage="prospect", status="open", deal_value=99))
    db.flush()

    # Make the attention rules real, one deal per rule so a failure names itself.
    stalled = db.query(Opportunity).filter_by(id="opp-stalled").first()
    stalled.stage_changed_at = NOW - timedelta(days=40)
    stalled.next_action_due_at = NOW - timedelta(days=3)          # overdue

    noaction = db.query(Opportunity).filter_by(id="opp-noaction").first()
    noaction.next_action = None
    noaction.next_action_due_at = None

    db.flush()

    # Timeline activity so "no activity" is only claimed where it is true.
    for oid in ("opp-live", "opp-casey"):
        db.add(OpportunityEvent(opportunity_id=oid, event_type="note",
                                summary="Touched", actor_user_id="u-blake",
                                occurred_at=NOW - timedelta(hours=3)))
    db.flush()

    # ── proposals in the states the attention rules care about ──
    blake = db.query(User).filter_by(id="u-blake").first()
    michael = db.query(User).filter_by(id="u-michael").first()

    live = db.query(Opportunity).filter_by(id="opp-live").first()
    p_live = ps.create_proposal(db, live, blake)
    db.flush()
    ps.publish_proposal(db, p_live, blake)
    p_live.sales_status = PROP_SENT
    p_live.sent_at = NOW - timedelta(days=5)          # sent, never opened
    p_live.expires_at = NOW + timedelta(days=1)       # and expiring in 1 day
    ID["p_live"] = p_live.id

    casey_opp = db.query(Opportunity).filter_by(id="opp-casey").first()
    p_casey = ps.create_proposal(db, casey_opp, db.query(User).filter_by(id="u-casey").first())
    db.flush()
    p_casey.sales_status = PROP_CHANGE_REQUESTED
    p_casey.customer_response_note = "Can you split the payments?"
    ID["p_casey"] = p_casey.id

    # A draft that a rep will ask for a discount on.
    pine = db.query(Opportunity).filter_by(id="opp-noaction").first()
    p_pine = ps.create_proposal(db, pine, blake)
    db.flush()
    ID["p_pine"] = p_pine.id

    # An accepted proposal — must NOT generate attention noise.
    ridge = db.query(Opportunity).filter_by(id="opp-stalled").first()
    p_ridge = ps.create_proposal(db, ridge, blake)
    db.flush()
    p_ridge.sales_status = PROP_ACCEPTED
    p_ridge.accepted_at = NOW - timedelta(days=1)
    ID["p_ridge"] = p_ridge.id

    db.add_all([
        PortalEvent(proposal_id=p_casey.id, opportunity_id="opp-casey",
                    event_type=PORTAL_OPENED, proposal_version=1,
                    occurred_at=NOW - timedelta(hours=6)),
        PortalEvent(proposal_id=p_casey.id, opportunity_id="opp-casey",
                    event_type=PORTAL_PROPOSAL_VIEWED, proposal_version=1,
                    occurred_at=NOW - timedelta(hours=5)),
    ])
    db.flush()

    # ── today's meetings ──
    mt = {t.key: t for t in db.query(MeetingType)
          .filter(MeetingType.brand_sales_org_id == "bso-evo").all()}

    def appt(aid, key, oid, users, at, conf=CONF_CONFIRMED):
        t = mt.get(key)
        a = SalesAppointment(
            id=aid, brand_sales_org_id="bso-evo", opportunity_id=oid,
            meeting_type_id=t.id if t else None,
            title=(t.name if t else key), status="scheduled",
            starts_at=at, ends_at=at + timedelta(minutes=60),
            timezone=CHI, confirmation_status=conf, created_by="u-blake")
        db.add(a)
        db.flush()
        for u in users:
            db.add(AppointmentParticipant(
                appointment_id=aid, user_id=u, role_slot="any_rep",
                busy_start_at=at, busy_end_at=at + timedelta(minutes=60),
                is_blocking=True, sync_status=SYNC_SYNCED))
        return a

    appt("appt-disc", "discovery", "opp-live", ["u-blake", "u-michael"],
         local_today_at(1))
    appt("appt-close", "closing", "opp-live", ["u-blake", "u-michael"],
         local_today_at(4), conf=CONF_PENDING)
    appt("appt-casey", "demo", "opp-casey", ["u-casey"], local_today_at(2))
    # A meeting that is definitely still ahead whatever hour this suite runs at,
    # so "the next meeting" is testable without depending on the wall clock.
    appt("appt-future", "closing", "opp-live", ["u-blake"],
         NOW + timedelta(days=3))
    # Another brand's meeting today — must not appear in EvoSys totals.
    bb_t = db.query(MeetingType).filter(
        MeetingType.brand_sales_org_id == "bso-bb").first()
    db.add(SalesAppointment(
        id="appt-bb", brand_sales_org_id="bso-bb", opportunity_id="opp-bb",
        meeting_type_id=bb_t.id if bb_t else None, title="Other brand call",
        status="scheduled", starts_at=local_today_at(3),
        ends_at=local_today_at(3) + timedelta(minutes=30),
        timezone=CHI, confirmation_status=CONF_CONFIRMED, created_by="u-bbrep"))
    db.flush()

    # ── the plumbing failing underneath them ──
    # A calendar that needs reconnecting, written exactly as a real failure
    # would have left it. No provider is called.
    part = (db.query(AppointmentParticipant)
            .filter_by(appointment_id="appt-close", user_id="u-blake").first())
    part.sync_status = SYNC_REAUTH
    part.sync_error = "Microsoft rejected the stored grant."

    db.add(AppointmentMeeting(
        appointment_id="appt-close", brand_sales_org_id="bso-evo",
        provider="zoom", status=MEET_FAILED,
        provider_error="Zoom is not configured for this brand.", attempts=2))
    db.add(AppointmentMeeting(
        appointment_id="appt-disc", brand_sales_org_id="bso-evo",
        provider="zoom", status=MEET_CREATED,
        provider_meeting_id="zoom-1", join_url="https://zoom.us/j/zoom-1"))
    db.commit()
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 1. WHO MAY OPEN THE MANAGER WORKSPACE AT ALL
# ═══════════════════════════════════════════════════════════════════════════

def test_permissions():
    print("\n[1] Manager permissions")
    c = TestClient(app)
    rep = token_for(c, "blake@example.com")
    mgr = token_for(c, "michael@example.com")

    r = c.get("/sales/manager/overview")
    check("the workspace requires authentication", r.status_code in (401, 403),
          r.status_code)

    r = c.get("/sales/manager/overview", headers=rep)
    check("A REP CANNOT OPEN THE MANAGER WORKSPACE", r.status_code == 403, r.status_code)
    check("and the refusal says why",
          "manager" in (r.json().get("detail") or "").lower(), r.text[:200])

    r = c.get("/sales/manager/reps/u-blake", headers=rep)
    check("a rep cannot read the rep drill-down", r.status_code == 403, r.status_code)

    r = c.get("/sales/manager/approvals", headers=rep)
    check("a rep cannot read the approval queue", r.status_code == 403, r.status_code)

    r = c.get("/sales/manager/overview", headers=mgr)
    check("a manager can", r.status_code == 200, r.text[:300])
    ID["overview"] = r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 2. MULTI-BRAND ISOLATION
# ═══════════════════════════════════════════════════════════════════════════

def test_brand_isolation():
    print("\n[2] Multi-brand isolation")
    c = TestClient(app)
    mgr = token_for(c, "michael@example.com")
    bb = token_for(c, "bbmgr@example.com")

    ov = ID["overview"]
    companies = {r["company"] for r in ov["closing_pipeline"]["rows"]}
    companies |= {i["company"] for i in ov["attention"]["items"]}
    check("NO OTHER BRAND'S DEAL APPEARS ANYWHERE", "Other Co" not in companies,
          sorted(companies))
    check("no other brand's rep is on the team",
          all(m["email"] != "bbrep@example.com" for m in ov["team"]),
          [m["email"] for m in ov["team"]])
    check("another brand's meeting is not in today's total",
          ov["team_today"]["total_meetings"] == 3,
          ov["team_today"]["total_meetings"])

    r = c.get("/sales/manager/overview?brand_sales_org_id=bso-evo", headers=bb)
    check("a manager cannot name another brand and get its data",
          r.status_code == 404, r.status_code)
    check("and it looks like 'not found', not 'forbidden'",
          r.status_code == 404, r.status_code)

    r = c.get("/sales/manager/reps/u-blake?brand_sales_org_id=bso-evo", headers=bb)
    check("nor drill into another brand's rep", r.status_code == 404, r.status_code)

    # The other brand's own manager still sees their own, empty, workspace.
    r = c.get("/sales/manager/overview", headers=bb)
    check("the other brand's manager sees their OWN brand", r.status_code == 200,
          r.text[:200])
    if r.status_code == 200:
        check("and it is scoped to them", r.json()["brand_sales_org_id"] == "bso-bb",
              r.json().get("brand_sales_org_id"))

    # A rep of this brand is not silently upgraded by naming their own brand.
    rep = token_for(c, "blake@example.com")
    r = c.get("/sales/manager/overview?brand_sales_org_id=bso-evo", headers=rep)
    check("naming your own brand does not make a rep a manager",
          r.status_code == 403, r.status_code)


# ═══════════════════════════════════════════════════════════════════════════
# 3. TEAM TODAY
# ═══════════════════════════════════════════════════════════════════════════

def test_team_today():
    print("\n[3] Team today")
    ov = ID["overview"]
    t = ov["team_today"]

    check("today is computed in the brand's timezone", t["timezone"] == CHI,
          t["timezone"])
    check("today's meetings are counted", t["total_meetings"] == 3,
          t["total_meetings"])
    check("unconfirmed meetings are surfaced", t["unconfirmed"] == 1, t["unconfirmed"])
    check("meetings are broken down by kind",
          t["by_kind"]["discovery"] == 1 and t["by_kind"]["closing"] == 1
          and t["by_kind"]["demo"] == 1, t["by_kind"])

    people = {p["user_id"]: p for p in t["people"]}
    check("every team member has a row", set(people) ==
          {"u-michael", "u-blake", "u-casey"}, sorted(people))
    check("a rep's meetings are attributed to them",
          people["u-blake"]["meeting_count"] == 2, people["u-blake"]["meeting_count"])
    check("a manager attending is counted on their own row too",
          people["u-michael"]["meeting_count"] == 2,
          people["u-michael"]["meeting_count"])
    check("a meeting shows the deal it belongs to",
          any(m["company"] == "Greenland Memorial"
              for m in people["u-blake"]["meetings"]),
          people["u-blake"]["meetings"])
    check("a failing video link is flagged on the meeting",
          any(m["video_needs_attention"] for m in people["u-blake"]["meetings"]),
          [(m["title"], m["video_needs_attention"]) for m in people["u-blake"]["meetings"]])
    check("local start time is provided so the UI never re-zones it",
          all(m.get("starts_at_local") for m in people["u-blake"]["meetings"]))
    check("'nothing booked' is stated as a fact, not an empty row",
          "clear" in people["u-michael"])


# ═══════════════════════════════════════════════════════════════════════════
# 4. ATTENTION REQUIRED
# ═══════════════════════════════════════════════════════════════════════════

def test_attention():
    print("\n[4] Attention required")
    ov = ID["overview"]
    att = ov["attention"]
    kinds = att["by_kind"]

    check("attention items are produced", att["total"] > 0, att["total"])
    check("a stalled deal is detected", kinds.get("stalled", 0) >= 1, kinds)
    check("an overdue next action is detected", kinds.get("overdue_action", 0) >= 1, kinds)
    check("a deal with no next action is detected",
          kinds.get("no_next_action", 0) >= 1, kinds)
    check("a proposal sent and never opened is detected",
          kinds.get("proposal_unopened", 0) >= 1, kinds)
    check("an expiring proposal is detected", kinds.get("proposal_expiring", 0) >= 1, kinds)
    check("a change request is detected", kinds.get("change_requested", 0) >= 1, kinds)
    check("A CALENDAR SYNC FAILURE IS DETECTED", kinds.get("calendar_sync", 0) >= 1, kinds)
    check("A ZOOM FAILURE IS DETECTED", kinds.get("video_failed", 0) >= 1, kinds)
    check("a deal with no recorded activity is detected",
          kinds.get("no_activity", 0) >= 1, kinds)

    items = att["items"]
    check("EVERY item names the deal", all(i["opportunity_id"] for i in items))
    check("every item names the rep", all(i["owner_user_id"] for i in items))
    check("every item carries the rep's NAME, not just an id",
          all(i["owner_name"] for i in items),
          [i for i in items if not i["owner_name"]][:2])
    check("every item says what to do about it",
          all(i["action"] or i["kind"] == "overdue_action" for i in items),
          [i["kind"] for i in items if not i["action"]])
    check("every item is red or amber",
          all(i["level"] in ("red", "amber") for i in items))

    levels = [i["level"] for i in items]
    if "red" in levels and "amber" in levels:
        last_red = max(n for n, lv in enumerate(levels) if lv == "red")
        check("red is ordered before amber",
              levels.index("amber") > last_red, levels)

    check("attention is attributed per rep for the team rollup",
          set(att["by_owner"]) <= {"u-blake", "u-casey", "u-michael"},
          att["by_owner"])
    check("an ACCEPTED proposal generates no proposal noise",
          not any(i["proposal_id"] == ID["p_ridge"] and
                  i["kind"].startswith("proposal") for i in items),
          [i["kind"] for i in items if i["proposal_id"] == ID["p_ridge"]])


# ═══════════════════════════════════════════════════════════════════════════
# 5. APPROVALS — the thing that did not exist before this checkpoint
# ═══════════════════════════════════════════════════════════════════════════

def test_approvals():
    print("\n[5] Pricing approvals")
    c = TestClient(app)
    rep = token_for(c, "blake@example.com")
    casey = token_for(c, "casey@example.com")
    mgr = token_for(c, "michael@example.com")
    bb = token_for(c, "bbmgr@example.com")
    pid = ID["p_pine"]

    # A rep still cannot simply set the price.
    r = c.patch("/sales/proposals/%s" % pid, headers=rep,
                json={"adjustment": -500, "price_override_reason": "trying it on"})
    check("A REP STILL CANNOT SET A PRICE DIRECTLY", r.status_code == 403, r.status_code)

    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -500, "reason": ""})
    check("a request with no reason is refused", r.status_code == 400, r.status_code)

    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": 0, "reason": "no change"})
    check("asking for the current price is refused", r.status_code == 400, r.status_code)

    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -99999, "reason": "free please"})
    check("a request that would go below zero is refused",
          r.status_code == 400, r.status_code)

    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -500,
                     "reason": "Competing against Vendor X on price."})
    check("a rep CAN ask", r.status_code == 201, r.text[:300])
    req = r.json()
    ID["req"] = req["id"]
    check("the request is pending", req["status"] == "pending", req["status"])
    check("it carries the amount asked for",
          req["requested_adjustment"] == -500, req["requested_adjustment"])
    check("it carries what the customer would then pay",
          req["requested_total"] == 4495, req["requested_total"])
    check("it carries the rep's words verbatim",
          "Vendor X" in req["reason"], req["reason"])
    check("it names who asked", req["requested_by_name"] == "Blake Rehani",
          req.get("requested_by_name"))

    # Asking does not change the price.
    r = c.get("/sales/proposals/%s" % pid, headers=rep)
    check("ASKING DOES NOT CHANGE THE PRICE", r.json()["final_amount"] == 4995,
          r.json()["final_amount"])
    check("the proposal reports the outstanding ask",
          (r.json().get("pricing_request") or {}).get("id") == ID["req"])

    # A second ask replaces the first rather than stacking.
    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -300, "reason": "Meeting them halfway."})
    check("a second ask is accepted", r.status_code == 201, r.text[:200])
    ID["req2"] = r.json()["id"]
    db = SessionLocal()
    old = db.query(PricingApprovalRequest).filter_by(id=ID["req"]).first()
    check("THE FIRST ASK IS WITHDRAWN, NOT STACKED",
          old.status == APPROVAL_WITHDRAWN, old.status)
    open_count = (db.query(PricingApprovalRequest)
                  .filter_by(proposal_id=pid, status=APPROVAL_PENDING).count())
    check("exactly one live request per proposal", open_count == 1, open_count)
    db.close()

    # The manager's queue.
    r = c.get("/sales/manager/approvals", headers=mgr)
    check("the manager sees the queue", r.status_code == 200, r.text[:200])
    q = r.json()
    check("the live request is in it", q["pending_count"] == 1, q["pending_count"])
    check("the queue names the deal",
          q["pending"][0]["opportunity_id"] == "opp-noaction",
          q["pending"][0].get("opportunity_id"))

    r = c.get("/sales/manager/approvals", headers=bb)
    check("ANOTHER BRAND'S MANAGER SEES NONE OF IT",
          r.status_code == 200 and r.json()["pending_count"] == 0, r.text[:200])

    r = c.post("/sales/manager/approvals/%s/decide" % ID["req2"], headers=bb,
               json={"approve": True})
    check("another brand's manager cannot decide it", r.status_code == 404, r.status_code)

    r = c.post("/sales/manager/approvals/%s/decide" % ID["req2"], headers=rep,
               json={"approve": True})
    check("A REP CANNOT APPROVE THEIR OWN REQUEST", r.status_code == 403, r.status_code)

    r = c.post("/sales/manager/approvals/%s/decide" % ID["req2"], headers=mgr,
               json={"approve": True, "note": "Fine for this one."})
    check("the manager can approve", r.status_code == 200, r.text[:300])
    check("and it reports that the price was applied", r.json()["applied"] is True)

    r = c.get("/sales/proposals/%s" % pid, headers=mgr)
    p = r.json()
    check("THE PRICE IS NOW WHAT WAS ASKED FOR", p["final_amount"] == 4695,
          p["final_amount"])
    check("the adjustment is stored", p["adjustment"] == -300, p["adjustment"])
    check("the audit records the MANAGER as the approver, not the rep",
          p["price_override_by_name"] == "Michael Schlueter",
          p.get("price_override_by_name"))
    check("the reason records who asked and why",
          "Blake Rehani" in (p["price_override_reason"] or "")
          and "halfway" in (p["price_override_reason"] or ""),
          p.get("price_override_reason"))
    check("the manager's note is kept too",
          "Fine for this one" in (p["price_override_reason"] or ""),
          p.get("price_override_reason"))

    r = c.get("/sales/proposals/%s" % pid, headers=rep)
    check("A REP STILL CANNOT READ THE OVERRIDE REASON",
          r.json()["price_override_reason"] is None)
    check("nor who approved it", r.json()["price_override_by_name"] is None)
    check("the answered request no longer shows as outstanding",
          r.json().get("pricing_request") is None)

    db = SessionLocal()
    ev = (db.query(OpportunityEvent)
          .filter_by(opportunity_id="opp-noaction").all())
    kinds = {e.event_type for e in ev}
    check("the ask is on the deal timeline", "pricing_approval_requested" in kinds, kinds)
    check("the approval is on the deal timeline", "pricing_approval_approved" in kinds, kinds)
    check("so is the price override itself", "proposal_price_override" in kinds, kinds)
    db.close()

    r = c.post("/sales/manager/approvals/%s/decide" % ID["req2"], headers=mgr,
               json={"approve": True})
    check("the same request cannot be decided twice", r.status_code == 400, r.status_code)

    # Denial.
    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -1000, "reason": "One more try."})
    deny_id = r.json()["id"]
    before = c.get("/sales/proposals/%s" % pid, headers=mgr).json()["final_amount"]
    r = c.post("/sales/manager/approvals/%s/decide" % deny_id, headers=mgr,
               json={"approve": False, "note": "We hold price on this package."})
    check("a manager can deny", r.status_code == 200, r.text[:200])
    check("denial applies nothing", r.json()["applied"] is False)
    after = c.get("/sales/proposals/%s" % pid, headers=mgr).json()["final_amount"]
    check("A DENIED REQUEST LEAVES THE PRICE UNTOUCHED", before == after,
          (before, after))
    db = SessionLocal()
    d = db.query(PricingApprovalRequest).filter_by(id=deny_id).first()
    check("the denial is recorded with the reason",
          d.status == APPROVAL_DENIED and "hold price" in (d.decision_note or ""),
          (d.status, d.decision_note))
    check("and names who denied it", d.decided_by == "u-michael", d.decided_by)
    db.close()

    # Withdrawal is the asker's alone.
    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -200, "reason": "Last idea."})
    check("a fresh ask is allowed after a denial", r.status_code == 201, r.status_code)
    r = c.post("/sales/proposals/%s/pricing-request/withdraw" % pid, headers=casey)
    check("another rep cannot withdraw someone else's ask",
          r.status_code in (400, 403, 404), r.status_code)
    r = c.post("/sales/proposals/%s/pricing-request/withdraw" % pid, headers=rep)
    check("the person who asked can withdraw it", r.status_code == 200, r.text[:200])


def test_approval_goes_stale():
    print("\n[6] A request the world moved past")
    c = TestClient(app)
    rep = token_for(c, "blake@example.com")
    mgr = token_for(c, "michael@example.com")
    pid = ID["p_pine"]

    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -250, "reason": "Still hoping."})
    rid = r.json()["id"]

    # The proposal is sent before the manager gets to it.
    db = SessionLocal()
    p = db.query(Proposal).filter_by(id=pid).first()
    p.sales_status = PROP_SENT
    p.sent_at = NOW
    db.commit()
    db.close()

    r = c.post("/sales/manager/approvals/%s/decide" % rid, headers=mgr,
               json={"approve": True})
    check("approving a locked proposal is refused, not silently ignored",
          r.status_code == 400, r.status_code)
    check("and the refusal explains what to do",
          "version" in (r.json().get("detail") or "").lower(), r.text[:200])

    db = SessionLocal()
    row = db.query(PricingApprovalRequest).filter_by(id=rid).first()
    check("THE DEAD REQUEST IS CLOSED, NOT LEFT ROTTING IN THE QUEUE",
          row.status == APPROVAL_STALE, row.status)
    check("with a reason a human can read",
          "moved on" in (row.decision_note or ""), row.decision_note)
    db.close()

    r = c.get("/sales/manager/approvals", headers=mgr)
    check("and it is gone from the queue", r.json()["pending_count"] == 0,
          r.json()["pending_count"])

    # Asking on a locked proposal is refused up front.
    r = c.post("/sales/proposals/%s/pricing-request" % pid, headers=rep,
               json={"requested_adjustment": -100, "reason": "?"})
    check("a rep cannot ask on a locked proposal", r.status_code == 400, r.status_code)


# ═══════════════════════════════════════════════════════════════════════════
# 7. CLOSING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

def test_closing_pipeline():
    print("\n[7] Closing pipeline")
    c = TestClient(app)
    mgr = token_for(c, "michael@example.com")
    ov = c.get("/sales/manager/overview", headers=mgr).json()
    cp = ov["closing_pipeline"]

    check("the closing pipeline has rows", cp["count"] > 0, cp["count"])
    check("it carries a total worth naming", cp["total_value"] > 0, cp["total_value"])
    rows = {r["opportunity_id"]: r for r in cp["rows"]}
    check("a deal in closing is in it", "opp-live" in rows, sorted(rows))
    check("every row names the rep",
          all(r["owner_name"] for r in cp["rows"]),
          [r["company"] for r in cp["rows"] if not r["owner_name"]])

    live = rows.get("opp-live", {})
    check("the row carries the proposal status", live.get("proposal_status") == PROP_SENT,
          live.get("proposal_status"))
    check("and the proposal number", live.get("proposal_number") is not None)
    check("and when it expires", live.get("proposal_expires_at") is not None)
    check("and the last touch, in words", live.get("last_touch_ago") is not None,
          live.get("last_touch_ago"))
    check("and the next action", "next_action" in live)
    check("and the next meeting", live.get("next_meeting_at") is not None)

    casey = rows.get("opp-casey", {})
    check("buyer activity is counted", casey.get("buyer_events", 0) == 2,
          casey.get("buyer_events"))
    check("and dated in words", casey.get("buyer_last_ago") is not None,
          casey.get("buyer_last_ago"))

    exps = [r["proposal_expires_at"] for r in cp["rows"] if r["proposal_expires_at"]]
    check("rows are ordered by the clock a manager is racing",
          exps == sorted(exps), exps)


# ═══════════════════════════════════════════════════════════════════════════
# 8. REP ROLLUP AND DRILL-DOWN
# ═══════════════════════════════════════════════════════════════════════════

def test_reps():
    print("\n[8] Rep activity and drill-down")
    c = TestClient(app)
    mgr = token_for(c, "michael@example.com")
    ov = c.get("/sales/manager/overview", headers=mgr).json()
    reps = {r["user_id"]: r for r in ov["reps"]}

    check("every team member has a rollup row",
          set(reps) == {"u-michael", "u-blake", "u-casey"}, sorted(reps))
    blake = reps["u-blake"]
    check("open deals are counted", blake["open_deals"] == 3, blake["open_deals"])
    check("attention is attributed to the rep", blake["needs_attention"] > 0,
          blake["needs_attention"])
    check("overdue actions are counted", blake["overdue_actions"] >= 1,
          blake["overdue_actions"])
    check("today's meetings are counted", blake["meetings_today"] == 2,
          blake["meetings_today"])
    check("proposals waiting on the customer are counted",
          "proposals_with_customer" in blake, sorted(blake))
    check("pipeline value is carried", blake["pipeline_value"] > 0,
          blake["pipeline_value"])
    check("last recorded activity is reported in words",
          "last_recorded_activity_ago" in blake)

    surveillance = {"messages_sent", "calls_made", "hours_worked", "response_time",
                    "logins", "active_minutes", "score", "rank"}
    leaked = surveillance & set(blake)
    check("THE ROLLUP CARRIES NO EFFORT OR SURVEILLANCE METRICS",
          not leaked, sorted(leaked))

    order = [r["needs_attention"] for r in ov["reps"]]
    check("the person with the most stuck deals is first",
          order == sorted(order, reverse=True), order)

    r = c.get("/sales/manager/reps/u-blake", headers=mgr)
    check("the drill-down loads", r.status_code == 200, r.text[:200])
    d = r.json()
    check("it names the rep", d["name"] == "Blake Rehani", d.get("name"))
    check("it returns their open deals", d["open_deals"] == 3, d["open_deals"])
    check("EVERY DEAL IS THEIRS", all(x["owner_user_id"] == "u-blake"
                                      for x in d["deals"]),
          [x["owner_user_id"] for x in d["deals"]])
    check("deals link by id into the existing opportunity screen",
          all(x["id"] for x in d["deals"]))
    check("each deal carries its proposal status", 
          any(x.get("proposal_status") for x in d["deals"]))
    check("deals needing attention sort first",
          d["deals"][0]["attention"] is not None, d["deals"][0].get("attention"))

    r = c.get("/sales/manager/reps/u-bbrep", headers=mgr)
    check("drilling into another brand's rep returns an empty book, never their deals",
          r.status_code == 200 and r.json()["open_deals"] == 0, r.text[:200])


# ═══════════════════════════════════════════════════════════════════════════
# 9. QUEUES AT TEAM SCALE
# ═══════════════════════════════════════════════════════════════════════════

def test_team_queues():
    print("\n[9] Proposal queues at team scale")
    c = TestClient(app)
    mgr = token_for(c, "michael@example.com")
    ov = c.get("/sales/manager/overview", headers=mgr).json()
    q = ov["proposal_queues"]

    check("the manager's queues cover the whole team",
          q["counts"]["follow_up_required"] >= 1, q["counts"])
    rows = (q["to_finish"] + q["ready_to_send"] + q["recently_viewed"]
            + q["follow_up_required"] + q["expiring"])
    check("EVERY QUEUE ROW NAMES WHOSE DEAL IT IS",
          all(r.get("owner_user_id") for r in rows),
          [r["company"] for r in rows if not r.get("owner_user_id")])
    check("and carries the name, not just the id",
          all(r.get("owner_name") for r in rows),
          [r["company"] for r in rows if not r.get("owner_name")])
    check("rows from more than one rep appear",
          len({r["owner_user_id"] for r in rows}) >= 2,
          {r["owner_user_id"] for r in rows})

    # The rep-side call must be unchanged by the team-scale additions.
    rep = token_for(c, "blake@example.com")
    r = c.get("/sales/my-day", headers=rep)
    check("my-day still works for a rep", r.status_code == 200, r.text[:200])
    check("and still carries its proposal queues", "proposals" in r.json())


# ═══════════════════════════════════════════════════════════════════════════
# 10. THE STATIC GUARANTEES
# ═══════════════════════════════════════════════════════════════════════════

def test_static_guarantees():
    print("\n[10] Static guarantees")
    import inspect
    from app.services import manager_workspace as mw
    from app.routers import sales_manager_router as smr

    src = inspect.getsource(mw)
    check("the manager service never reaches customer-tenant tables",
          "from app.models.models import Lead" not in src
          and "Lead)" not in src and " Lead," not in src)
    check("it never reads the encrypted Zoom host url",
          "host_url" not in src, "host_url appears in manager_workspace")
    check("it never reads a provider credential",
          "client_secret" not in src and "account_id_encrypted" not in src)

    rsrc = inspect.getsource(smr)
    check("EVERY manager route is gated by require_sales_manager",
          rsrc.count("Depends(require_sales_manager)") ==
          rsrc.count("@router."), (rsrc.count("Depends(require_sales_manager)"),
                                   rsrc.count("@router.")))
    check("no manager route uses the plain member guard",
          "require_sales_member" not in rsrc)

    # Expiry must be read from the date, because the sweep that would set the
    # status is not wired to anything. A status-only check would silently
    # report zero expired proposals forever.
    check("expired proposals are detected by DATE, not by a status nothing sets",
          "p.expires_at < now" in src, "manager_workspace does not date-check expiry")

    # The N+1 the Checkpoint 4 report missed: closing_view costs ~10 queries per
    # deal. A manager view must never loop it.
    check("THE MANAGER VIEW NEVER LOOPS closing_view",
          "closing_view(" not in src.split('"""', 2)[-1],
          "manager_workspace calls closing_view")

    from app.models.sales_models import PricingApprovalRequest as P
    cols = {c.name for c in P.__table__.columns}
    for needed in ("brand_sales_org_id", "opportunity_id", "proposal_id",
                   "requested_by", "requested_adjustment", "reason", "status",
                   "decided_by", "decided_at", "decision_note"):
        check("the request records %s" % needed, needed in cols, sorted(cols))
    idx = {i.name for i in P.__table__.indexes}
    check("the queue is indexed by brand and status",
          "ix_pricing_approval_brand_status" in idx, sorted(idx))


def main():
    print("=" * 74)
    print("  SALES MANAGER WORKSPACE — CHECKPOINT 5")
    print("=" * 74)
    build()
    test_permissions()
    test_brand_isolation()
    test_team_today()
    test_attention()
    test_approvals()
    test_approval_goes_stale()
    test_closing_pipeline()
    test_reps()
    test_team_queues()
    test_static_guarantees()

    print("\n" + "=" * 74)
    if FAILURES:
        print("  %d FAILURES" % len(FAILURES))
        for f in FAILURES:
            print("   - %s" % f)
        shutil.rmtree(TMP, ignore_errors=True)
        sys.exit(1)
    print("  ALL MANAGER WORKSPACE CHECKS PASSED")
    print("=" * 74)
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
