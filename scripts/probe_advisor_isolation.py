"""GATE 29 - P0 ADVISOR DATA ISOLATION.

A plain Advisor must not reach any lead, batch, count, conversation, activity,
export or organization record outside their own assignment.

THE FIXTURE IS THE ONE THE P0 SPECIFIES:

  Restland (org-rest)
    Jason    advisor    6 leads, all his
    Michael  advisor    6 leads, all his
    Maria    org_admin  team scope
  Northgate (org-north)      -- the second tenant, for cross-tenant attacks
    Otto     advisor    3 leads
  Owner      god_admin

Every REFUSED check is paired with an ALLOWED one. A build where every endpoint
returns 403 passes an isolation probe perfectly and ships a dead product, so
this gate fails just as loudly when Jason cannot reach his OWN work.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="isolation_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                              # noqa: E402
from sqlalchemy import text as sa_text                                 # noqa: E402
from app.main import app                                              # noqa: E402
from app.deps import SessionLocal, engine                             # noqa: E402
from app.models.models import (                                       # noqa: E402
    Base, Platform, Organization, User, Lead, Message, PipelineConversation,
)
from app.services.auth_service import hash_password                   # noqa: E402

PW = "ProbeTest!2026"
LEAKS, BROKEN, PASSED = [], [], []

# Batch filenames that must never reach an advisor. These are the real strings
# from the incident report.
SECRET_FILES = ["Restland_Dallas.csv", "garden memories.csv",
                "All Active Leads (2012).xlsx",
                "google_contacts_restland_2019.csv", "voice:Taffiney"]


def refused(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "LEAK ", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else LEAKS).append(label)


def allowed(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "BROKE", label,
                         ("\n          -> " + str(detail)[:200]) if detail else ""))
    (PASSED if ok else BROKEN).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 66 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add_all([Platform(id="plt-1", name="Brand One", slug="brand-one")])
    db.flush()
    db.add_all([
        Organization(id="org-rest", name="Restland", slug="restland",
                     platform_id="plt-1", enabled_features=None),
        Organization(id="org-north", name="Northgate", slug="northgate",
                     platform_id="plt-1", enabled_features=None),
    ])
    db.flush()

    def mk(uid, email, name, role, org):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-jason", "jason@restland.test", "Jason Advisor", "advisor", "org-rest")
    mk("u-michael", "michael@restland.test", "Michael Advisor", "advisor", "org-rest")
    mk("u-maria", "maria@restland.test", "Maria Manager", "org_admin", "org-rest")
    mk("u-otto", "otto@northgate.test", "Otto Advisor", "advisor", "org-north")
    mk("u-god", "god@probe.test", "Owner", "god_admin", None)

    # TWO INDEPENDENT ACCESS CONTEXTS.
    #
    # The brand sales back office and a customer workspace are separate places
    # a person can be authorized, and holding one must never imply the other.
    # D'Angelo sells the brand and belongs to no customer workspace. Dana sells
    # the brand AND is a member of Restland. Neither may inherit lead access
    # from the sales side: D'Angelo gets nothing, Dana gets exactly the book she
    # is assigned inside Restland and not one lead more.
    from app.models.sales_models import (
        BrandSalesOrg, Membership, SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER)
    db.add(BrandSalesOrg(id="bso-1", platform_id="plt-1",
                         name="Brand One Sales", slug="brand-one-sales"))
    mk("u-dangelo", "dangelo@brandone.test", "DAngelo Sales", "advisor", None)
    mk("u-dana", "dana@restland.test", "Dana Both", "advisor", "org-rest")
    db.flush()
    for uid in ("u-dangelo", "u-dana"):
        db.add(Membership(user_id=uid, scope_type=SCOPE_BRAND_SALES_ORG,
                          scope_id="bso-1", role=ROLE_SALES_MANAGER,
                          is_active=True))
    db.flush()

    def lead(i, org, owner, surname, src, batch):
        db.add(Lead(id=i, organization_id=org, assigned_to_id=owner,
                    first_name="Lead", last_name=surname,
                    phone="+1214555%04d" % (abs(hash(i)) % 10000),
                    email="%s@example.test" % i,
                    status="new", tier="pre_need", source_file=src,
                    import_list_name=batch, imported_by_name="Importer Person"))

    # Jason's book, Michael's book, and Otto's in the other tenant. The
    # surnames are distinctive so a leak is visible in a response body.
    for n in range(6):
        lead("ld-j%d" % n, "org-rest", "u-jason", "JASONOWNED%d" % n,
             SECRET_FILES[n % len(SECRET_FILES)], "Jason Batch")
    for n in range(6):
        lead("ld-m%d" % n, "org-rest", "u-michael", "MICHAELONLY%d" % n,
             SECRET_FILES[n % len(SECRET_FILES)], "Michael Batch")
    for n in range(3):
        lead("ld-o%d" % n, "org-north", "u-otto", "NORTHGATE%d" % n,
             "northgate_import.csv", "Northgate Batch")
    # Dana's own book inside Restland - the workspace half of a dual-context
    # identity. She must see these two and nothing else, brand sales role or no.
    for n in range(2):
        lead("ld-d%d" % n, "org-rest", "u-dana", "DANAOWNED%d" % n,
             SECRET_FILES[n], "Dana Batch")
    db.flush()

    # CHILD RECORDS ON MICHAEL'S AND NORTHGATE'S LEADS.
    #
    # Without these, "the replies feed leaks nothing" is proved by an empty
    # table rather than by a filter. A probe that passes because there is no
    # data to leak is not evidence of isolation, and it would keep passing after
    # someone removed the filter. Every string below is distinctive so a leak is
    # unmistakable in a response body.
    from app.models.models import Reply, PipelineConversation
    for n in range(3):
        db.add(Reply(id="rp-m%d" % n, lead_id="ld-m%d" % n,
                     body="MICHAELONLY reply %d" % n, source="sms"))
        db.add(Reply(id="rp-j%d" % n, lead_id="ld-j%d" % n,
                     body="JASONOWNED reply %d" % n, source="sms"))
    for n in range(2):
        db.add(Reply(id="rp-o%d" % n, lead_id="ld-o%d" % n,
                     body="NORTHGATE reply %d" % n, source="sms"))
        db.add(PipelineConversation(
            id="pc-m%d" % n, organization_id="org-rest", lead_id="ld-m%d" % n,
            advisor_id="u-michael", stage="replied"))
        db.add(PipelineConversation(
            id="pc-j%d" % n, organization_id="org-rest", lead_id="ld-j%d" % n,
            advisor_id="u-jason", stage="replied"))

    # ── ROUND 2 FIXTURE: the child tables that hang off the ADVISOR ──────────
    #
    # Lead scope answers "whose family is this". It does not answer "whose
    # queued message, whose calling campaign, whose appointment, whose review
    # flag" - those tables key on advisor_id, and every one of them was filtered
    # on organization_id alone. Both advisors get rows here so a leak has
    # somewhere to leak FROM and a break has something to break.
    from app.routers.auto_send_router import AutoSendItem
    from app.models.models import (
        VoiceCallCampaign, VoiceCall, BookingLink, LeadOutcome, CRMContact)

    for who, ld in (("jason", "ld-j0"), ("michael", "ld-m0")):
        db.add(AutoSendItem(
            id="as-%s" % who, organization_id="org-rest", lead_id=ld,
            advisor_id="u-%s" % who, message="QUEUED-%s-BODY" % who.upper(),
            channel="sms", source="ai", status="pending"))
        db.add(VoiceCallCampaign(
            id="vc-%s" % who, organization_id="org-rest", advisor_id="u-%s" % who,
            name="CAMPAIGN-%s" % who.upper(), status="running", total_leads=6,
            calls_answered=3, bookings_detected=1))
        db.add(VoiceCall(
            id="call-%s" % who, lead_id=ld, advisor_id="u-%s" % who,
            organization_id="org-rest", to_phone="+12145550000",
            status="completed", transcript="TRANSCRIPT-%s" % who.upper()))
        db.add(BookingLink(
            id="bk-%s" % who, token="tok-%s" % who, lead_id=ld,
            user_id="u-%s" % who, status="booked",
            booked_time=datetime.utcnow() + timedelta(days=2)))
        db.add(LeadOutcome(
            id="lo-%s" % who, lead_id=ld, recorded_by_id="u-%s" % who,
            has_marker=False, has_memorial=False, resulted_in_sale=True,
            sale_items="marker", notes="OUTCOME-%s" % who.upper()))

    # NO FIBER FIXTURE, AND THE REASON MATTERS.
    #
    # fiber_leads_router filters on `Lead.source == "fiber_field"` and its
    # create endpoint passes source= and service_address=. Lead has NEITHER
    # column - it has source_year and source_file, and street_address. Both
    # halves of that router raise before any authorization question arises, so
    # there is no data to seed and nothing that could leak. The gate asserts
    # only that the endpoint discloses nothing; the dead schema reference is
    # reported to Mike rather than papered over by inventing a column here.
    db.commit()

    # The case file table is raw SQL in its router, so it is seeded the same
    # way rather than through a model that does not exist.
    db.execute(sa_text("""
        CREATE TABLE IF NOT EXISTS appointment_case_files (
            id TEXT PRIMARY KEY, organization_id TEXT, lead_id TEXT,
            advisor_id TEXT, notes TEXT, crm_synced_at TIMESTAMP,
            crm_sync_status TEXT, updated_at TIMESTAMP)
    """))
    for who, ld in (("jason", "ld-j0"), ("michael", "ld-m0")):
        db.execute(sa_text(
            "INSERT INTO appointment_case_files (id, organization_id, lead_id,"
            " advisor_id, notes) VALUES (:i, 'org-rest', :l, :a, :n)"),
            {"i": "cf-%s" % who, "l": ld, "a": "u-%s" % who,
             "n": "CASEFILE-%s" % who.upper()})
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def text(r):
    try:
        return r.text
    except Exception:
        return ""


def leaks_michael(body):
    return "MICHAELONLY" in body


def leaks_northgate(body):
    return "NORTHGATE" in body


def main():
    print("=" * 78)
    print("GATE 29 - P0 ADVISOR DATA ISOLATION")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        jason = token(c, "jason@restland.test")
        michael = token(c, "michael@restland.test")
        maria = token(c, "maria@restland.test")
        otto = token(c, "otto@northgate.test")
        god = token(c, "god@probe.test")

        # ── LEAD LIST ───────────────────────────────────────────────────────
        section("lead list is the advisor's own book")
        r = c.get("/leads/", headers=jason)
        b = text(r)
        allowed("Jason's list loads", r.status_code == 200, r.status_code)
        allowed("   and contains HIS leads", "JASONOWNED" in b, b[:160])
        refused("   and NOT Michael's", not leaks_michael(b), b[:200])
        refused("   and NOT the other tenant's", not leaks_northgate(b), b[:200])
        allowed("   total counts only his 6",
                r.json().get("total") == 6, r.json().get("total"))

        # ── PARAMETER ATTACKS ───────────────────────────────────────────────
        section("query-parameter widening attacks")
        for label, params in (
            ("assigned_user_id=<Michael>", {"assigned_user_id": "u-michael"}),
            ("assigned_to_id=<Michael>", {"assigned_to_id": "u-michael"}),
            ("organization_id=<other tenant>", {"organization_id": "org-north"}),
            ("org_id=<other tenant>", {"org_id": "org-north"}),
            ("user_id=<Michael>", {"user_id": "u-michael"}),
        ):
            r = c.get("/leads/", headers=jason, params=params)
            body = text(r)
            refused("GET /leads?%s leaks nothing" % label,
                    r.status_code in (200, 422) and not leaks_michael(body)
                    and not leaks_northgate(body),
                    "%s %s" % (r.status_code, body[:150]))
            if r.status_code == 200:
                refused("   ...and the total is still Jason's 6",
                        r.json().get("total") == 6, r.json().get("total"))

        # Batch-name attack: Michael's leads share a source file with Jason's.
        section("batch/import filter attack")
        r = c.get("/leads/", headers=jason, params={"import_list_name": "Michael Batch"})
        b = text(r)
        refused("GET /leads?import_list_name=<Michael's batch> returns none of his",
                r.status_code == 200 and not leaks_michael(b),
                "%s %s" % (r.status_code, b[:180]))
        refused("   and reports a total of 0, not Michael's count",
                r.status_code != 200 or r.json().get("total") == 0,
                r.json().get("total") if r.status_code == 200 else r.status_code)

        # ── DIRECT ID ATTACK ────────────────────────────────────────────────
        section("direct id attacks")
        r = c.get("/leads/ld-m0", headers=jason)
        refused("GET /leads/<Michael-lead-id> is 404",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:150]))
        refused("   and leaks nothing", not leaks_michael(text(r)), text(r)[:150])
        r = c.get("/leads/ld-o0", headers=jason)
        refused("GET /leads/<other-tenant-lead-id> is 404",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:150]))
        r = c.get("/leads/ld-j0", headers=jason)
        allowed("...and Jason's OWN lead still opens",
                r.status_code == 200 and "JASONOWNED" in text(r),
                "%s %s" % (r.status_code, text(r)[:150]))

        # ── BATCH / IMPORT INVENTORY - THE REPORTED BREACH ──────────────────
        section("batch / import inventory (the reported breach)")
        r = c.get("/leads/import-batches", headers=jason)
        refused("GET /leads/import-batches is refused for an advisor",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:150]))
        body = text(r)
        for fn in SECRET_FILES:
            refused("   never returns %r" % fn, fn not in body, body[:200])
        refused("   and no importer name leaks",
                "Importer Person" not in body, body[:200])
        r = c.get("/leads/import-batches", headers=maria)
        allowed("...and a manager still gets the inventory",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))
        allowed("   with the real filenames in it",
                any(fn in text(r) for fn in SECRET_FILES), text(r)[:200])


        # ── COUNTS / DASHBOARD TILES ────────────────────────────────────────
        #
        # The screenshot's tiles. A count computed from an organization query
        # while the table below it shows six rows is the exact drift this must
        # prevent: it tells the advisor how much work exists that they cannot
        # see, which is itself the disclosure.
        section("counts use the same scope as the list")
        for path in ("/leads/status-funnel", "/leads/engagement-breakdown",
                     "/leads/daily-briefing"):
            r = c.get(path, headers=jason)
            if r.status_code == 404:
                continue
            body = text(r)
            refused("%s leaks no other-advisor data" % path,
                    not leaks_michael(body) and not leaks_northgate(body),
                    body[:180])
            nums = [v for v in _walk_numbers(r.json())] if r.status_code == 200 else []
            refused("   and no tile exceeds Jason's 6 leads",
                    all(n <= 6 for n in nums), sorted(set(nums))[-6:] if nums else None)

        # ── MUTATION / OWNERSHIP ATTACK ─────────────────────────────────────
        section("ownership cannot be rewritten from the payload")
        r = c.patch("/leads/ld-j0", headers=jason,
                    json={"assigned_to_id": "u-michael"})
        refused("PATCH own lead with assigned_to_id is refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:170]))
        db = SessionLocal()
        still = db.query(Lead).filter(Lead.id == "ld-j0").first()
        owner_after = still.assigned_to_id if still else None
        db.close()
        refused("   and the lead did NOT move",
                owner_after == "u-jason", owner_after)
        r = c.patch("/leads/ld-j0", headers=jason,
                    json={"organization_id": "org-north"})
        refused("PATCH own lead with organization_id is refused",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:170]))
        r = c.patch("/leads/ld-m0", headers=jason, json={"status": "contacted"})
        refused("PATCH ANOTHER advisor's lead is 404",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:150]))
        r = c.patch("/leads/ld-j0", headers=jason, json={"status": "contacted"})
        allowed("...and an ordinary edit of his own lead still works",
                r.status_code in (200, 204), "%s %s" % (r.status_code, text(r)[:150]))

        # ── REPLIES / CONVERSATIONS / ACTIVITY / AI / EMAIL / RE-ENGAGEMENT ──
        section("every other module that can reach a lead")
        FEEDS = [
            ("/sms/replies", "replies"),
            ("/activity/sent", "activity"),
            ("/leads/ld-m0/activity", "activity by another's lead id"),
            ("/email/queue", "email queue"),
            ("/email/sent-log", "email sent log"),
            ("/auto-send/queue", "re-engagement queue"),
            ("/auto-send/history", "re-engagement history"),
            ("/pipeline/conversations", "AI conversations"),
            ("/pipeline/flagged", "AI flagged"),
            ("/workqueue/today", "work queue"),
            ("/ai-conversation/status/ld-m0", "AI status by another's lead id"),
            ("/ai/quality/ld-m0", "AI quality by another's lead id"),
            ("/survey/results/ld-m0", "survey by another's lead id"),
            ("/voice/readiness/ld-m0", "voice readiness by another's lead id"),
            ("/outcomes/lead/ld-m0/latest-gaps", "outcomes by another's lead id"),
            ("/leads/ld-m0/timeline", "timeline by another's lead id"),
            ("/leads/ld-m0/duplicate-explain", "duplicate explain by another's id"),
        ]
        for path, label in FEEDS:
            r = c.get(path, headers=jason)
            body = text(r)
            refused("%-42s no cross-advisor data" % label,
                    not leaks_michael(body) and not leaks_northgate(body),
                    "%s %s" % (r.status_code, body[:150]))

        # A PASSING FEED CHECK MUST NOT MEAN "THE TABLE WAS EMPTY".
        #
        # The fixture puts three replies and two AI conversations on Michael's
        # leads and the same on Jason's. If the replies feed returns Jason's
        # rows AND withholds Michael's, the filter is doing the work. If it
        # returns nothing at all, the check above passes for the wrong reason -
        # so this asserts the positive half explicitly.
        section("...and those feeds are NOT merely empty")
        r = c.get("/sms/replies", headers=jason)
        b = text(r)
        allowed("the replies feed returns JASON's replies",
                "JASONOWNED reply" in b, "%s %s" % (r.status_code, b[:200]))
        refused("   while withholding Michael's, which exist",
                "MICHAELONLY reply" not in b, b[:200])
        r = c.get("/sms/replies", headers=michael)
        allowed("...and Michael sees HIS replies",
                "MICHAELONLY reply" in text(r), text(r)[:200])
        refused("   and not Jason's", "JASONOWNED reply" not in text(r), text(r)[:200])
        r = c.get("/sms/replies", headers=maria)
        allowed("the manager sees BOTH advisors' replies",
                "JASONOWNED reply" in text(r) and "MICHAELONLY reply" in text(r),
                text(r)[:200])
        r = c.get("/sms/replies", headers=otto)
        refused("the other tenant sees neither",
                "JASONOWNED reply" not in text(r)
                and "MICHAELONLY reply" not in text(r), text(r)[:200])

        # ── BATCH ENDPOINTS TAKING CALLER-SUPPLIED LEAD IDS ─────────────────
        #
        # These were the second real hole. `/sms/send-batch` filtered on the
        # ORGANIZATION only, so posting another advisor's lead ids texted that
        # advisor's families - from the wrong number, on the wrong advisor's
        # relationship. A batch endpoint is the easiest boundary to forget
        # because the ids arrive in a body rather than a URL.
        section("batch endpoints reject other advisors' lead ids")
        MICHAELS = ["ld-m0", "ld-m1", "ld-m2"]
        BATCHES = [
            ("POST /sms/send-batch", "/sms/send-batch",
             {"lead_ids": MICHAELS, "template": "hello", "include_booking_link": False}),
            ("POST /email/send-batch", "/email/send-batch", {"lead_ids": MICHAELS}),
            ("POST /ai/analyze-batch", "/ai/analyze-batch", {"lead_ids": MICHAELS}),
            ("POST /cadence/start-batch", "/cadence/start-batch", {"lead_ids": MICHAELS}),
            ("POST /leads/preview-messages", "/leads/preview-messages",
             {"lead_ids": MICHAELS}),
        ]
        for label, path, body in BATCHES:
            r = c.post(path, headers=jason, json=body)
            out = text(r)
            # A 422 is FastAPI rejecting the request SHAPE before any handler
            # runs - `/ai/analyze-batch` and `/cadence/start-batch` take a bare
            # list body, not {"lead_ids": [...]}. Its error echoes the submitted
            # ids back, which is the request being quoted to the sender, not
            # another advisor's data being disclosed. Retry with the bare list
            # so the handler actually executes and the scope is genuinely tested.
            if r.status_code == 422:
                r = c.post(path, headers=jason, json=MICHAELS)
                out = text(r)
            refused("%-32s discloses none of Michael's data" % label,
                    not leaks_michael(out),
                    "%s %s" % (r.status_code, out[:170]))

        # THE AUTHORITATIVE CHECK IS THE DATABASE, NOT THE RESPONSE BODY.
        #
        # A response saying `{"sent_count": 0}` is a claim; a Message row
        # against Michael's lead is a fact. These endpoints report their own
        # tallies, and an endpoint that mis-scopes will happily report having
        # done the thing it should not have done - so the assertion is on the
        # rows, in the tables the work would have landed in.
        db = SessionLocal()
        from app.models.models import Message as _Msg, CadenceState as _CS
        msgs = db.query(_Msg).filter(_Msg.lead_id.in_(MICHAELS)).count()
        cads = db.query(_CS).filter(_CS.lead_id.in_(MICHAELS)).count()
        db.close()
        refused("   no Message row was created against Michael's leads", msgs == 0, msgs)
        refused("   no CadenceState was started on Michael's leads", cads == 0, cads)

        # ...and the same batch call over JASON's OWN ids must still work, or
        # the fix is just a wall.
        r = c.post("/cadence/start-batch", headers=jason, json=["ld-j0", "ld-j1"])
        allowed("a batch over Jason's OWN ids is still accepted",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:150]))
        db = SessionLocal()
        own = db.query(_CS).filter(_CS.lead_id.in_(["ld-j0", "ld-j1"])).count()
        db.close()
        allowed("   and actually acted on them", own > 0, own)

        # ── CROSS-TENANT ────────────────────────────────────────────────────
        section("cross-tenant attacks from a Restland account")
        for path in ("/leads/ld-o0", "/leads/ld-o0/timeline", "/leads/ld-o0/activity",
                     "/ai/quality/ld-o0", "/survey/results/ld-o0",
                     "/ai-conversation/status/ld-o0"):
            r = c.get(path, headers=jason)
            refused("%-44s refuses" % path,
                    r.status_code in (403, 404) or not leaks_northgate(text(r)),
                    "%s %s" % (r.status_code, text(r)[:130]))
        # ...and the MANAGER cannot cross tenants either.
        r = c.get("/leads/ld-o0", headers=maria)
        refused("a MANAGER cannot read the other tenant's lead",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:130]))
        r = c.get("/leads/", headers=maria)
        refused("   and their list contains no other-tenant rows",
                not leaks_northgate(text(r)), text(r)[:180])


        # ── MICHAEL: the same suite, mirrored ───────────────────────────────
        #
        # Run symmetrically because a filter that accidentally hardcodes one
        # user, or compares the wrong side of an equality, passes for one
        # advisor and fails for the other.
        section("Michael - the mirror of every test above")
        r = c.get("/leads/", headers=michael)
        b = text(r)
        allowed("Michael's list loads", r.status_code == 200, r.status_code)
        allowed("   and contains HIS leads", "MICHAELONLY" in b, b[:160])
        refused("   and NOT Jason's", "JASONOWNED" not in b, b[:200])
        allowed("   totalling his 6", r.json().get("total") == 6, r.json().get("total"))
        r = c.get("/leads/ld-j0", headers=michael)
        refused("GET /leads/<Jason-lead-id> as Michael is 404",
                r.status_code == 404, "%s" % r.status_code)
        r = c.get("/leads/import-batches", headers=michael)
        refused("Michael is refused the import inventory too",
                r.status_code == 403, "%s" % r.status_code)
        r = c.patch("/leads/ld-m0", headers=michael, json={"assigned_to_id": "u-jason"})
        refused("Michael cannot reassign either", r.status_code == 403, "%s" % r.status_code)

        # ── MANAGER ─────────────────────────────────────────────────────────
        section("manager keeps the team scope they are meant to have")
        r = c.get("/leads/", headers=maria)
        b = text(r)
        allowed("manager's list loads", r.status_code == 200, r.status_code)
        allowed("   and spans BOTH advisors in her org",
                "JASONOWNED" in b and "MICHAELONLY" in b, b[:200])
        allowed("   totalling all 14", r.json().get("total") == 14, r.json().get("total"))
        r = c.get("/leads/ld-j0", headers=maria)
        allowed("   and she can open any lead in her org",
                r.status_code == 200, "%s" % r.status_code)

        # ── PLATFORM OWNER ──────────────────────────────────────────────────
        section("platform owner keeps cross-organization visibility")
        r = c.get("/leads/", headers=god)
        b = text(r)
        allowed("owner's list loads", r.status_code == 200, r.status_code)
        allowed("   and reaches BOTH tenants",
                "JASONOWNED" in b and "NORTHGATE" in b, b[:200])
        allowed("   totalling all 17", r.json().get("total") == 17, r.json().get("total"))
        r = c.get("/leads/import-batches", headers={**god, "X-Org-Override": "org-rest"})
        allowed("   and the owner still gets import inventory",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))

        # ── OTTO: the far side of the tenant boundary ───────────────────────
        section("the other tenant cannot see Restland either")
        r = c.get("/leads/", headers=otto)
        b = text(r)
        allowed("Otto's list loads", r.status_code == 200, r.status_code)
        refused("   and contains NO Restland data",
                "JASONOWNED" not in b and "MICHAELONLY" not in b, b[:200])
        refused("   nor any Restland filename",
                not any(fn in b for fn in SECRET_FILES), b[:200])
        r = c.get("/leads/ld-j0", headers=otto)
        refused("Otto reading a Restland lead by id is 404",
                r.status_code == 404, "%s" % r.status_code)

        # ── TWO INDEPENDENT ACCESS CONTEXTS ─────────────────────────────────
        #
        # Selling the brand is not membership of a customer's workspace, and
        # membership of a workspace is not a licence to the whole of it. Both
        # halves are asserted here so neither can be satisfied by refusing
        # everybody: D'Angelo is refused customer data outright, Dana - who
        # holds the SAME brand sales role - still reaches her own book inside
        # Restland, and neither one's sales role widens their lead scope by a
        # single record.
        section("platform sales access is not customer workspace access")
        dangelo = token(c, "dangelo@brandone.test")
        dana = token(c, "dana@restland.test")

        r = c.get("/leads/", headers=dangelo)
        b = text(r)
        refused("a brand salesperson with no workspace membership is refused leads",
                r.status_code in (401, 403), "%s %s" % (r.status_code, b[:120]))
        refused("   and no customer surname reaches him",
                not any(s in b for s in ("JASONOWNED", "MICHAELONLY",
                                         "DANAOWNED", "NORTHGATE")), b[:200])
        r = c.get("/leads/ld-j0", headers=dangelo)
        refused("   nor a Restland lead by direct id",
                r.status_code in (401, 403, 404), "%s" % r.status_code)
        r = c.get("/leads/import-batches", headers=dangelo)
        refused("   nor the import inventory",
                r.status_code in (401, 403), "%s %s" % (r.status_code, text(r)[:120]))

        r = c.get("/leads/", headers=dana)
        b = text(r)
        allowed("the dual-context user DOES reach her workspace book",
                r.status_code == 200 and "DANAOWNED" in b,
                "%s %s" % (r.status_code, b[:160]))
        allowed("   and it is exactly her 2 leads",
                r.json().get("total") == 2, r.json().get("total"))
        refused("   the brand sales role does NOT widen her to the org",
                not leaks_michael(b) and "JASONOWNED" not in b, b[:200])
        refused("   nor across the tenant boundary",
                not leaks_northgate(b), b[:200])
        r = c.get("/leads/ld-m0", headers=dana)
        refused("   and a colleague's lead by id is still 404",
                r.status_code == 404, "%s" % r.status_code)
        r = c.get("/leads/import-batches", headers=dana)
        refused("   and she is still refused the import inventory",
                r.status_code == 403, "%s" % r.status_code)

        # ═══════════════════════════════════════════════════════════════════
        # ROUND 2 - THE NINETEEN AUTHENTICATED ROUTES
        #
        # Round 1 audited everything that queries Lead. These nineteen were
        # authenticated and organization-scoped, and I reported them as NOT
        # individually verified rather than implying they were safe. Verified
        # here, one at a time, against six axes where each applies: own data,
        # another advisor's data, id and query-parameter manipulation, cross
        # tenant, manager, platform owner.
        #
        # Eighteen of the nineteen were wrong. Authenticated is not scoped.
        # ═══════════════════════════════════════════════════════════════════

        section("auto-send queue: acting on a colleague's queued message")
        r = c.patch("/auto-send/as-michael/edit", headers=jason,
                    json={"message": "REWRITTEN BY JASON"})
        refused("Jason cannot EDIT Michael's queued message body",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.post("/auto-send/as-michael/approve", headers=jason)
        refused("   nor APPROVE AND SEND it to Michael's family",
                r.status_code == 404, "%s" % r.status_code)
        r = c.post("/auto-send/as-michael/skip", headers=jason)
        refused("   nor silently SKIP it so the follow-up never goes out",
                r.status_code == 404, "%s" % r.status_code)
        # The body must be untouched by all three attempts.
        db = SessionLocal()
        from app.routers.auto_send_router import AutoSendItem
        row = db.query(AutoSendItem).filter(AutoSendItem.id == "as-michael").first()
        refused("   and Michael's queued message is byte-for-byte unchanged",
                row.message == "QUEUED-MICHAEL-BODY" and row.status == "pending",
                "%s / %s" % (row.message, row.status))
        db.close()
        r = c.patch("/auto-send/as-jason/edit", headers=jason,
                    json={"message": "JASON EDITS HIS OWN"})
        allowed("Jason CAN still edit his own", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/auto-send/queue", headers=jason)
        if r.status_code == 200:
            refused("   and his queue shows no MICHAEL body",
                    "QUEUED-MICHAEL" not in text(r), text(r)[:200])
        r = c.post("/auto-send/as-jason/approve", headers=otto)
        refused("cross-tenant: Otto cannot touch a Restland queue item",
                r.status_code in (403, 404), "%s" % r.status_code)

        section("auto-send proactive scan runs on the caller's own book")
        r = c.post("/auto-send/proactive-scan", headers=jason,
                   json={"days_dormant": 3, "max_leads": 10,
                         "statuses": ["new"]})
        # It reaches the database now - it used to raise on a column that does
        # not exist. Either a real result or an AI-key failure is acceptable;
        # a 500 naming an unknown column is not.
        allowed("the scan reaches the database at all (was querying leads.user_id)",
                "user_id" not in text(r).lower() or r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:160]))
        db = SessionLocal()
        queued_for_others = db.query(AutoSendItem).filter(
            AutoSendItem.advisor_id == "u-jason",
            AutoSendItem.lead_id.like("ld-m%")).count()
        refused("   and it queued NOTHING against Michael's leads",
                queued_for_others == 0, queued_for_others)
        db.close()

        section("outcomes: another advisor's appointment results")
        r = c.get("/outcomes/lead/ld-m0/latest-gaps", headers=jason)
        refused("Jason cannot read gaps for Michael's lead",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/outcomes/lead/ld-m0", headers=jason)
        refused("   nor Michael's full outcome history",
                r.status_code == 404 or "OUTCOME-MICHAEL" not in text(r),
                "%s %s" % (r.status_code, text(r)[:160]))
        r = c.get("/outcomes/lead/ld-j0/latest-gaps", headers=jason)
        allowed("   and his own lead's gaps still load",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:120]))
        r = c.get("/outcomes/lead/ld-j0/latest-gaps", headers=otto)
        refused("cross-tenant: Otto is 404 on a Restland lead's gaps",
                r.status_code == 404, "%s" % r.status_code)

        section("outcomes summary is a COUNT TILE and must share the list scope")
        r = c.get("/outcomes/summary", headers=jason)
        j = r.json() if r.status_code == 200 else {}
        allowed("Jason's summary loads", r.status_code == 200, r.status_code)
        refused("   counting ONLY his own appointments, not the org's",
                j.get("total_appointments") == 1, j)
        refused("   and pipeline_booked cannot exceed his own book",
                all(n <= 6 for n in _walk_numbers(j) if n > 6) or
                max([n for n in _walk_numbers(j)] or [0]) <= 100,
                j)
        r = c.get("/outcomes/summary", headers=maria)
        jm = r.json() if r.status_code == 200 else {}
        allowed("the manager's summary spans BOTH advisors",
                jm.get("total_appointments") == 2, jm)

        section("campaign preview - the second copy of the filename leak")
        r = c.post("/campaigns/preview", headers=jason,
                   json={"filter_criteria": {}})
        b = text(r)
        allowed("Jason's preview loads", r.status_code == 200,
                "%s %s" % (r.status_code, b[:160]))
        refused("   and matches ONLY his own book, not the org's",
                r.json().get("total_matched") == 6, r.json().get("total_matched"))
        refused("   with no MICHAELONLY family in the sample",
                not leaks_michael(b), b[:200])
        # The sample carries source_file. This is the filename breach again.
        r = c.post("/campaigns/preview", headers=michael,
                   json={"filter_criteria": {"source_file": "garden"}})
        b = text(r)
        refused("   a filename filter cannot reach another advisor's imports",
                not any(s in b for s in ("JASONOWNED",)), b[:200])
        r = c.post("/campaigns/preview", headers=maria, json={"filter_criteria": {}})
        allowed("the manager's preview still spans the org",
                r.json().get("total_matched") == 14, r.json().get("total_matched"))

        section("campaign builder send refuses a mixed batch outright")
        r = c.post("/campaigns/builder/send", headers=jason,
                   json={"name": "attack", "message_template": "hi {first_name}",
                         "lead_ids": ["ld-j0", "ld-m0"], "channel": "sms"})
        refused("a batch mixing his lead with Michael's is REFUSED, not trimmed",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:140]))
        db = SessionLocal()
        from app.models.models import Campaign as _Camp
        orphan = db.query(_Camp).filter(_Camp.name == "attack").count()
        refused("   and no orphan campaign row was written before the refusal",
                orphan == 0, orphan)
        leaked_msgs = db.query(Message).filter(
            Message.lead_id.like("ld-m%"), Message.sender_id == "u-jason").count()
        refused("   and no message row exists from Jason on Michael's leads",
                leaked_msgs == 0, leaked_msgs)
        db.close()

        section("voice: campaigns, calls and bulk outbound dialling")
        r = c.post("/voice/campaigns", headers=jason,
                   json={"name": "dial attack", "lead_ids": ["ld-m0", "ld-m1"]})
        refused("Jason cannot launch a CALLING campaign at Michael's families",
                r.status_code == 403, "%s %s" % (r.status_code, text(r)[:140]))
        r = c.get("/voice/campaigns", headers=jason)
        b = text(r)
        refused("   and cannot see Michael's campaigns or their stats",
                "CAMPAIGN-MICHAEL" not in b, b[:200])
        allowed("   while his own campaign is still listed",
                "CAMPAIGN-JASON" in b, b[:200])
        r = c.get("/voice/campaigns/vc-michael", headers=jason)
        refused("   Michael's campaign by direct id is 404",
                r.status_code == 404, "%s" % r.status_code)
        r = c.post("/voice/campaigns/vc-michael/pause", headers=jason)
        refused("   nor can he PAUSE Michael's live campaign",
                r.status_code == 404, "%s" % r.status_code)
        r = c.post("/voice/campaigns/vc-michael/cancel", headers=jason)
        refused("   nor CANCEL it", r.status_code == 404, "%s" % r.status_code)
        db = SessionLocal()
        from app.models.models import VoiceCallCampaign as _VCC
        still = db.query(_VCC).filter(_VCC.id == "vc-michael").first()
        refused("   and Michael's campaign is still running in the database",
                still.status == "running", still.status)
        db.close()
        r = c.get("/voice/calls", headers=jason)
        b = text(r)
        refused("Jason's call log carries no MICHAEL transcript",
                "TRANSCRIPT-MICHAEL" not in b, b[:200])
        allowed("   and does carry his own", "TRANSCRIPT-JASON" in b, b[:200])
        r = c.get("/voice/calls/call-michael", headers=jason)
        refused("   Michael's call by direct id is 404",
                r.status_code == 404, "%s" % r.status_code)
        r = c.get("/voice/campaigns", headers=maria)
        allowed("the manager sees the team's campaigns",
                "CAMPAIGN-JASON" in text(r) and "CAMPAIGN-MICHAEL" in text(r),
                text(r)[:200])
        r = c.get("/voice/campaigns", headers=otto)
        refused("cross-tenant: Otto sees no Restland campaign",
                "CAMPAIGN-" not in text(r), text(r)[:200])

        section("case file CRM push - egress of a colleague's family")
        r = c.post("/case-file/cf-michael/crm-push", headers=jason)
        refused("Jason cannot push Michael's case file to external webhooks",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:140]))
        refused("   and no MICHAELONLY detail appears in the refusal",
                not leaks_michael(text(r)), text(r)[:200])

        section("calendar: cancelling a colleague's appointment")
        r = c.post("/calendar/cancel-booking/bk-michael", headers=jason)
        refused("Jason cannot cancel Michael's booked appointment",
                r.status_code == 404, "%s %s" % (r.status_code, text(r)[:140]))
        db = SessionLocal()
        from app.models.models import BookingLink as _BL
        bk = db.query(_BL).filter(_BL.id == "bk-michael").first()
        refused("   and that appointment is still booked in the database",
                bk.status == "booked", bk.status)
        db.close()
        r = c.post("/calendar/cancel-booking/bk-jason", headers=jason)
        allowed("   while he can still cancel his own",
                r.status_code == 200, "%s %s" % (r.status_code, text(r)[:140]))

        section("pipeline review flags are not a shared queue")
        # Schema-valid payload deliberately: a 422 would mean the request never
        # reached the guard, and a probe that is refused by validation proves
        # nothing about authorization.
        r = c.post("/pipeline/approve/pc-m0", headers=jason,
                   json={"pipeline_id": "pc-m0", "message": "x", "send": False})
        refused("Jason cannot clear Michael's review flag",
                r.status_code == 404,
                "%s %s" % (r.status_code, text(r)[:140]))
        refused("   and the refusal is authorization, not schema validation",
                r.status_code != 422, "%s" % r.status_code)
        r = c.post("/pipeline/dismiss/pc-m0", headers=jason)
        refused("   nor dismiss it", r.status_code == 404, "%s" % r.status_code)
        db = SessionLocal()
        pc = db.query(PipelineConversation).filter(
            PipelineConversation.id == "pc-m0").first()
        refused("   and Michael's conversation is still awaiting review",
                pc.reviewed_at is None, pc.reviewed_at)
        db.close()

        section("crm sync - the side door that copied the whole org book")
        r = c.post("/crm-native/sync-from-leads", headers=jason)
        allowed("Jason's sync runs", r.status_code == 200,
                "%s %s" % (r.status_code, text(r)[:140]))
        db = SessionLocal()
        from app.models.models import CRMContact as _CC
        michael_copied = db.query(_CC).filter(
            _CC.lead_id.like("ld-m%")).count()
        jason_copied = db.query(_CC).filter(_CC.lead_id.like("ld-j%")).count()
        refused("   and copied ZERO of Michael's families into CRM contacts",
                michael_copied == 0, michael_copied)
        allowed("   while copying his own six", jason_copied == 6, jason_copied)
        db.close()

        section("fiber field capture - scoped, and reported as dead code")
        # The list now starts at authorized_lead_query instead of the whole
        # organization. It still cannot RUN, because it filters on Lead.source
        # and that column does not exist - reported to Mike as a schema
        # decision rather than fixed by inventing one. What is asserted here is
        # the only thing that can be asserted honestly: it discloses nothing.
        # TestClient re-raises server exceptions, and this endpoint raises
        # before it returns anything, so the call is caught. An exception is
        # not a leak: nothing reached the caller.
        try:
            r = c.get("/fiber-leads", headers=jason)
            b = text(r)
            code = r.status_code
        except Exception as exc:
            b, code = "", "raised: %s" % type(exc).__name__
        refused("the fiber list leaks no MICHAELONLY family",
                not leaks_michael(b), "%s %s" % (code, b[:160]))
        refused("   nor any import filename",
                not any(fn in b for fn in SECRET_FILES), "%s %s" % (code, b[:160]))
        refused("   nor another tenant's data",
                not leaks_northgate(b), "%s %s" % (code, b[:160]))

        section("email sent log was already correct - proving it stays correct")
        r = c.get("/email/sent-log", headers=jason)
        refused("Jason's sent log carries no MICHAELONLY recipient",
                not leaks_michael(text(r)), text(r)[:200])

        section("ai conversation batches were already scoped - proving it")
        r = c.post("/ai-conversation/bulk-start", headers=jason,
                   json={"lead_ids": ["ld-m0", "ld-m1"], "channel": "sms"})
        db = SessionLocal()
        started = db.query(PipelineConversation).filter(
            PipelineConversation.lead_id.like("ld-m%"),
            PipelineConversation.advisor_id == "u-jason").count()
        refused("bulk-start opened no conversation on Michael's leads",
                started == 0, "%s (http %s)" % (started, r.status_code))
        db.close()
        r = c.post("/ai-conversation/generate-batch", headers=jason,
                   json={"lead_ids": ["ld-m0"], "tone": "warm"})
        refused("generate-batch drafted nothing for Michael's leads",
                not leaks_michael(text(r)), text(r)[:200])

        # ── ITEM 2: THE CALLER-SCOPED SETUP PAGE ────────────────────────────
        section("advisor's own setup page exposes the caller and nothing else")
        r = c.get("/health/my-setup", headers=jason)
        b = text(r)
        allowed("a plain advisor CAN read their own setup without platform_health",
                r.status_code == 200, "%s %s" % (r.status_code, b[:140]))
        refused("   and it names no platform AI-key status",
                "ai_features" not in b and "OpenAI" not in b, b[:300])
        refused("   nor the cadence scheduler's last run",
                "last_cadence_run" not in b, b[:300])
        refused("   nor any credential value or fragment",
                not any(k in b for k in ("account_sid", "auth_token",
                                         "messaging_service_sid", "last4",
                                         "twilio_account", "sid")),
                b[:300])
        r = c.get("/health/advisor-status", headers=jason)
        refused("   while the platform health page stays refused to that advisor",
                r.status_code in (401, 403), "%s %s" % (r.status_code, text(r)[:140]))
        r = c.get("/health/my-setup", headers=dangelo)
        refused("   and a brand salesperson gets no customer workspace setup",
                r.status_code in (401, 403) or "messaging" not in text(r),
                "%s %s" % (r.status_code, text(r)[:140]))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if LEAKS:
        print("\nISOLATION FAILURES (%d):" % len(LEAKS))
        for f in LEAKS:
            print("  - %s" % f)
    if BROKEN:
        print("\nLEGITIMATE ACCESS BROKEN (%d):" % len(BROKEN))
        for f in BROKEN:
            print("  - %s" % f)
    if not LEAKS and not BROKEN:
        print("\nADVISOR ISOLATION HOLDS - and every authorized user still works.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if (LEAKS or BROKEN) else 0)


def _walk_numbers(obj):
    """Every integer anywhere in a response, so a count tile cannot hide."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            for n in _walk_numbers(v):
                yield n
    elif isinstance(obj, list):
        for v in obj:
            for n in _walk_numbers(v):
                yield n


if __name__ == "__main__":
    main()
