"""GATE 27 - cleanup removes what it names, and nothing else.

The dangerous property of a cleanup tool is not that it fails to delete. It is
that it deletes something nobody asked about, and this repository shipped two
routes that did exactly that:

  · "demo wipe" selected every lead in the organization and every user whose
    role was 'advisor' - a real funeral home's entire staff and lead list.
  · "clear sample data" had an inverted condition, so on the run where there
    was NOTHING to clear it deleted every campaign the caller had ever made.

Both are fixed. This gate exists so they cannot come back, and so the new
workflow can be trusted with production.

The load-bearing assertions here are all NEGATIVE - what survived. A cleanup
gate that only counts deletions passes happily while destroying the database.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="cleanup_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import (                                      # noqa: E402
    Base, Platform, Organization, User, Lead, Message, Campaign,
)
from app.services.auth_service import hash_password                  # noqa: E402
from app.services.platform_owner import GOD_PLATFORM_ORG_ID          # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:250]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-cl"))
    db.flush()
    db.add_all([
        Organization(id="org-a", name="Real Customer A", slug="cust-a",
                     platform_id="plt", is_active=True),
        Organization(id="org-b", name="Real Customer B", slug="cust-b",
                     platform_id="plt", is_active=True),
        Organization(id=GOD_PLATFORM_ORG_ID, name="AdvisorFlow Platform",
                     slug="advisorflow-platform", plan="god", is_active=True),
    ])
    db.flush()

    def mk(uid, email, name, role, org):
        db.add(User(id=uid, organization_id=org, email=email, full_name=name,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))

    mk("u-god", "god@probe.test", "Owner", "god_admin", None)
    mk("u-admin-a", "admin.a@probe.test", "Admin A", "org_admin", "org-a")
    # A REAL advisor with a normal id. The old demo-wipe deleted people like her.
    mk("u-real-advisor", "real@probe.test", "Real Advisor", "advisor", "org-a")
    # A demo advisor, recognisable by the runner's id prefix.
    mk("demo-advisor-1", "demo.adv@probe.test", "Demo Advisor", "advisor", "org-a")
    db.flush()

    def lead(lid, org, first, **kw):
        db.add(Lead(id=lid, organization_id=org, first_name=first, last_name="X",
                    phone="+1555000%04d" % (abs(hash(lid)) % 9999),
                    status="new", created_at=datetime.utcnow(), **kw))

    lead("real-a-1", "org-a", "RealA")                       # must survive
    lead("real-a-2", "org-a", "RealA2")                      # must survive
    lead("real-b-1", "org-b", "RealB")                       # must survive
    lead("test-a-1", "org-a", "TestA", is_test=True, test_note="QA fixture, Mike")
    lead("sample-a-1", "org-a", "SampleA", source_file="SAMPLE_DATA")
    lead("demo-a-1", "org-a", "DemoA")                       # demo- prefix
    lead("batch-a-1", "org-a", "BatchA", source_file="jan_import.csv")
    lead("orphan-1", GOD_PLATFORM_ORG_ID, "Orphan")          # pseudo-org
    db.flush()
    # Message has no organization_id — it scopes through its lead, which is
    # exactly why the cleanup preview reports it as a dependency rather than
    # selecting it directly.
    db.add(Message(id="msg-1", lead_id="test-a-1", sender_id="u-real-advisor",
                   body="hi"))
    db.add(Campaign(id="camp-1", organization_id="org-a", name="Real Campaign",
                    created_by_id="u-admin-a", filter_criteria="{}",
                    message_track="pre_need_lock_price"))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def exists(model, ident):
    db = SessionLocal()
    try:
        return db.query(model).filter(model.id == ident).first() is not None
    finally:
        db.close()


def count(model):
    db = SessionLocal()
    try:
        return db.query(model).count()
    finally:
        db.close()


def main():
    print("=" * 78)
    print("GATE 27 - SCOPED TEST-DATA CLEANUP")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")

        section("PREVIEW DELETES NOTHING")
        before_leads = count(Lead)
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["is_test", "sample_data", "demo_prefix", "orphaned"]})
        pv = r.json() if r.status_code == 200 else {}
        check("preview returns 200", r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:200]))
        check("...and deleted nothing", count(Lead) == before_leads,
              "before=%s after=%s" % (before_leads, count(Lead)))

        leads_cat = next((x for x in pv.get("categories", []) if x["key"] == "leads"), {})
        check("it counts exactly the 4 marked leads", leads_cat.get("count") == 4,
              leads_cat.get("count"))
        ids = {s["id"] for s in leads_cat.get("sample", [])}
        check("...and they are the marked ones, not the real ones",
              ids == {"test-a-1", "sample-a-1", "demo-a-1", "orphan-1"}, sorted(ids))
        check("every selected record carries WHY it was classified",
              all(s["why"] for s in leads_cat.get("sample", [])),
              [(s["id"], s["why"]) for s in leads_cat.get("sample", [])])
        check("the is_test row shows the operator's note",
              any("QA fixture" in w for s in leads_cat.get("sample", [])
                  for w in s["why"]),
              [s["why"] for s in leads_cat.get("sample", []) if s["id"] == "test-a-1"])
        check("dependencies are shown (the message on a selected lead)",
              next(x["count"] for x in pv["categories"] if x["key"] == "messages") == 1,
              pv["categories"])
        check("scope is broken down by organization",
              any(s["name"] == "Real Customer A" for s in leads_cat.get("scope", [])),
              leads_cat.get("scope"))
        check("the protected list is stated up front",
              any("Customer organizations" in p for p in pv.get("protected", [])),
              pv.get("protected"))

        section("THE MANIFEST DESCRIBES EXACTLY WHAT GOES")
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["is_test", "sample_data", "demo_prefix", "orphaned"],
                         "include_manifest": True})
        man = r.json().get("manifest")
        check("a manifest is returned when asked for", isinstance(man, list),
              type(man).__name__)
        check("...with one row per candidate lead", len(man or []) == 4, len(man or []))
        check("...covering exactly the marked ids",
              {m["lead_id"] for m in man} == {"test-a-1", "sample-a-1", "demo-a-1", "orphan-1"},
              sorted(m["lead_id"] for m in man or []))
        check("...naming the organization, not just its id",
              all(m["organization"] for m in man), [m.get("organization") for m in man])
        check("...carrying the classification rule for each",
              all(m["classification"] for m in man),
              [(m["lead_id"], m["classification"]) for m in man])
        tst = next(m for m in man if m["lead_id"] == "test-a-1")
        check("...and the operator's note on the is_test row",
              tst["test_note"] == "QA fixture, Mike", tst["test_note"])
        check("dependents are counted per lead (the message on test-a-1)",
              tst["dependents"]["messages"] == 1, tst["dependents"])
        check("total_records per lead = the lead plus its dependents",
              all(m["total_records"] == 1 + sum(m["dependents"].values()) for m in man),
              [(m["lead_id"], m["total_records"], m["dependents"]) for m in man])
        check("the manifest total EQUALS the confirmation-phrase count",
              sum(m["total_records"] for m in man) == r.json()["total_records"],
              "manifest=%s preview=%s" % (sum(m["total_records"] for m in man),
                                          r.json()["total_records"]))
        r2 = c.post("/god/customers/cleanup/preview", headers=god,
                    json={"rules": ["is_test"]})
        check("the manifest is omitted unless requested",
              r2.json().get("manifest") is None, r2.json().get("manifest"))
        check("asking for a manifest still deletes nothing",
              count(Lead) == before_leads, count(Lead))

        section("AN EMPTY RULE SET SELECTS NOTHING (not everything)")
        r = c.post("/god/customers/cleanup/preview", headers=god, json={"rules": []})
        check("no rules -> zero records", r.json()["total_records"] == 0,
              r.json()["total_records"])
        r = c.post("/god/customers/cleanup/execute", headers=god,
                   json={"rules": [], "confirmation": "DELETE 0 TEST RECORDS"})
        check("...and executing it deletes nothing",
              r.status_code == 200 and r.json()["total_deleted"] == 0
              and count(Lead) == before_leads, r.text[:160])

        section("CONFIRMATION MUST MATCH THE PREVIEW EXACTLY")
        rules = ["is_test", "sample_data", "demo_prefix", "orphaned"]
        for bad in ("yes", "DELETE", "delete 4 test records", "DELETE 3 TEST RECORDS"):
            r = c.post("/god/customers/cleanup/execute", headers=god,
                       json={"rules": rules, "confirmation": bad})
            check("refuses confirmation %r" % bad, r.status_code == 400, r.status_code)
        check("...and nothing was deleted by any of those attempts",
              count(Lead) == before_leads, count(Lead))

        section("EXECUTE - exact counts, and only the named records")
        # Take the phrase from the preview rather than hardcoding it. The count
        # in it is the TOTAL of everything that goes, dependent rows included
        # (4 leads + 1 message here), which is the honest number to make someone
        # type — and re-reading it is also how an operator would really do this.
        phrase = c.post("/god/customers/cleanup/preview", headers=god,
                        json={"rules": rules}).json()["confirmation_phrase"]
        check("the phrase counts dependents too, not just leads",
              phrase == "DELETE 5 TEST RECORDS", phrase)
        r = c.post("/god/customers/cleanup/execute", headers=god,
                   json={"rules": rules, "confirmation": phrase})
        res = r.json() if r.status_code == 200 else {}
        check("execute succeeds", r.status_code == 200,
              "%s %s" % (r.status_code, r.text[:200]))
        check("...and reports exact counts", res.get("deleted", {}).get("leads") == 4,
              res.get("deleted"))

        section("THE CONFIRMED NUMBER IS THE NUMBER THAT HAPPENS")
        # The property that failed on the first production run. Four child
        # tables cascade at the database level, so they were deleted without
        # ever being counted; two more do NOT cascade and are NOT NULL, so the
        # delete raised IntegrityError. Either way the operator's confirmed
        # figure was not the figure that occurred.
        check("total_deleted EQUALS the count in the confirmation phrase",
              res.get("total_deleted") == 5,
              "deleted=%s phrase said 5" % res.get("total_deleted"))
        check("...and every child table is reported by name, not silently cascaded",
              set(res.get("deleted", {})) >= {"messages", "replies", "outcomes",
                                              "cadence_state", "email_messages",
                                              "booking_links", "pipeline_conversations",
                                              "voice_calls", "leads"},
              sorted(res.get("deleted", {})))

        section("WHAT SURVIVED - the assertions that actually matter")
        for lid in ("real-a-1", "real-a-2", "real-b-1", "batch-a-1"):
            check("real lead %s survived" % lid, exists(Lead, lid))
        for lid in ("test-a-1", "sample-a-1", "demo-a-1", "orphan-1"):
            check("marked lead %s was removed" % lid, not exists(Lead, lid))
        check("the REAL advisor account survived", exists(User, "u-real-advisor"))
        check("the customer admin survived", exists(User, "u-admin-a"))
        check("the organizations survived",
              exists(Organization, "org-a") and exists(Organization, "org-b"))
        check("the platform survived", exists(Platform, "plt"))
        check("the real campaign survived", exists(Campaign, "camp-1"))

        section("AN IMPORT BATCH IS ONLY REMOVED WHEN NAMED")
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["import_batch"]})
        check("naming no batch selects nothing", r.json()["total_records"] == 0,
              r.json()["total_records"])
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["import_batch"],
                         "import_batches": ["jan_import.csv"]})
        check("naming the batch selects exactly it", r.json()["total_records"] == 1,
              r.json()["total_records"])

        section("SCOPING TO ONE CUSTOMER")
        db = SessionLocal()
        try:
            db.add(Lead(id="test-b-1", organization_id="org-b", first_name="TestB",
                        last_name="X", phone="+15550009999", status="new",
                        is_test=True, created_at=datetime.utcnow()))
            db.commit()
        finally:
            db.close()
        def lead_count(body):
            return next(x["count"] for x in body["categories"] if x["key"] == "leads")

        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["is_test"], "org_ids": ["org-a"]})
        check("scoping to customer A excludes customer B's test row",
              lead_count(r.json()) == 0, lead_count(r.json()))
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["is_test"], "org_ids": ["org-b"]})
        check("...and scoping to B finds exactly it", lead_count(r.json()) == 1,
              lead_count(r.json()))

        section("UNKNOWN RULES ARE REFUSED")
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["delete_everything"]})
        check("an invented rule is refused", r.status_code == 400
              and "delete_everything" in r.text, r.text[:160])

        section("THE OLD DEMO WIPE NO LONGER TAKES REAL RECORDS")
        # Restore a marked row and prove the legacy route is now narrow too.
        db = SessionLocal()
        try:
            db.add(Lead(id="sample-a-2", organization_id="org-a", first_name="Sample2",
                        last_name="X", phone="+15550008888", status="new",
                        source_file="SAMPLE_DATA", created_at=datetime.utcnow()))
            db.commit()
        finally:
            db.close()
        r = c.delete("/admin/demo/wipe/org-a", headers=god)
        check("demo wipe runs", r.status_code == 200, "%s %s" % (r.status_code, r.text[:160]))
        check("...and removed only the sample row",
              not exists(Lead, "sample-a-2"), "sample-a-2 still present")
        check("...leaving the real leads alone",
              exists(Lead, "real-a-1") and exists(Lead, "real-a-2"))
        check("...and the REAL advisor still has an account",
              exists(User, "u-real-advisor"))
        check("...while the demo advisor was removed",
              not exists(User, "demo-advisor-1"))

        section("ONLY THE OWNER MAY RUN CLEANUP")
        adm = token(c, "admin.a@probe.test")
        for path in ("/god/customers/cleanup/preview", "/god/customers/cleanup/execute"):
            r = c.post(path, headers=adm, json={"rules": ["is_test"],
                                                "confirmation": "x"})
            check("%s refuses a customer admin" % path, r.status_code == 403,
                  r.status_code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nCLEANUP REMOVES WHAT IT NAMES, AND NOTHING ELSE.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
