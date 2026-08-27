"""GATE 30 - the cleanup receipt survives, and the audit feed shows it.

Two production failures produced this gate.

P0A. The manifest for a real 104-record deletion existed only in the browser tab
that requested it. The tab reloaded; the manifest was gone; the only surviving
record of WHICH 29 leads were removed was a count. And the two attempts that
failed before it left no trace at all - correct behaviour (they rolled back) with
no evidence, which is how somebody ends up believing a deletion happened that
did not.

P0B. `data_cleanup.executed` and `platform_owner.*` rows were being written and
committed, and the God Ops audit view showed none of them, because its action
allowlist had never been updated. A false negative in the one screen you consult
to find out what happened.

The load-bearing test here is the FAILURE case. Success paths are easy; this
gate deliberately breaks a delete mid-flight and requires the attempt to be on
disk afterwards, saying zero rows went.

Nothing here touches production. Every id below is invented.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="receipt_")
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
    Base, Platform, Organization, User, Lead, Message, AuditLogEntry,
)
from app.models.cleanup_models import CleanupExecution               # noqa: E402
from app.services.auth_service import hash_password                  # noqa: E402
from sqlalchemy import event as _sa_event                            # noqa: E402


# MAKE SQLITE BEHAVE LIKE POSTGRES.
#
# SQLite ignores foreign keys unless asked not to. Production is Postgres, which
# does not - and the delete that failed in production failed precisely because a
# NOT NULL child with no cascade pointed at a lead being removed. Without this
# pragma the sabotage below silently succeeds and the gate passes while proving
# nothing, which is exactly how the original bug reached production.
@_sa_event.listens_for(engine, "connect")
def _enforce_fks(dbapi_conn, _rec):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()

PW = "ProbeTest!2026"
FAIL, PASSED = [], []


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:240]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-rcpt"))
    db.flush()
    db.add(Organization(id="org-a", name="Customer A", slug="cust-a-rcpt",
                        platform_id="plt", is_active=True))
    db.flush()
    for uid, email, role, org in (("u-god", "god@probe.test", "god_admin", None),
                                  ("u-adm", "adm@probe.test", "org_admin", "org-a"),
                                  ("u-adv", "adv@probe.test", "advisor", "org-a")):
        db.add(User(id=uid, organization_id=org, email=email, full_name=uid,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))
    db.flush()
    for i in range(3):
        lid = "samp-%d" % i
        db.add(Lead(id=lid, organization_id="org-a", first_name="Sample%d" % i,
                    last_name="X", phone="+1555000%04d" % i, status="new",
                    source_file="SAMPLE_DATA", created_at=datetime.utcnow()))
        db.flush()
        db.add(Message(id="m-%d" % i, lead_id=lid, sender_id="u-adv", body="hi"))
    db.add(Lead(id="real-1", organization_id="org-a", first_name="Real",
                last_name="Lead", phone="+15550009999", status="new",
                created_at=datetime.utcnow()))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def rows(model):
    db = SessionLocal()
    try:
        return db.query(model).count()
    finally:
        db.close()


def main():
    print("=" * 78)
    print("GATE 30 - DURABLE CLEANUP RECEIPT + AUDIT VISIBILITY")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")

        section("P0A - PREVIEW WRITES A DURABLE PLAN")
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["sample_data"], "include_manifest": True})
        pv = r.json()
        eid = pv.get("execution_id")
        check("preview returns a server-generated execution id", bool(eid), eid)
        check("...and the plan row exists on disk before anything is deleted",
              rows(CleanupExecution) == 1, rows(CleanupExecution))
        check("...but nothing was deleted", rows(Lead) == 4, rows(Lead))

        rec = c.get("/god/customers/cleanup/history/%s" % eid, headers=god).json()
        check("the receipt records status=previewed", rec["status"] == "previewed",
              rec["status"])
        check("...the executing owner", rec["actor_email"] == "god@probe.test",
              rec["actor_email"])
        check("...the exact confirmation phrase demanded",
              rec["confirmation_phrase"] == pv["confirmation_phrase"],
              rec["confirmation_phrase"])
        check("...the exact record ids targeted",
              set(rec["target_lead_ids"]) == {"samp-0", "samp-1", "samp-2"},
              sorted(rec["target_lead_ids"]))
        check("...per-table expected counts",
              rec["expected_counts"].get("leads") == 3
              and rec["expected_counts"].get("messages") == 3,
              rec["expected_counts"])
        check("...total expected rows", rec["expected_total"] == 6,
              rec["expected_total"])
        check("...the classification rule", rec["rules"] == ["sample_data"],
              rec["rules"])
        check("...the protected/excluded list",
              any("Customer organizations" in x for x in rec["excluded"]),
              rec["excluded"])
        check("...and the full manifest", len(rec.get("manifest") or []) == 3,
              len(rec.get("manifest") or []))
        check("actual counts are still null - nothing has run",
              rec["actual_total"] is None, rec["actual_total"])

        section("P0A - A FAILED ATTEMPT LEAVES A RECORD SAYING NOTHING WENT")
        # Break the delete for real: a NOT NULL child with no cascade pointing at
        # a targeted lead is exactly what took down the first production run.
        db = SessionLocal()
        try:
            from app.models.models import PipelineConversation
            db.add(PipelineConversation(id="pc-block", lead_id="samp-0",
                                        organization_id="org-a",
                                        advisor_id="u-adv", stage="outreach_sent"))
            db.commit()
        finally:
            db.close()
        import app.services.data_cleanup as dc
        original = list(dc.LEAD_CHILDREN)
        dc.LEAD_CHILDREN = [(k, m) for k, m in original
                            if k != "pipeline_conversations"]
        try:
            r = c.post("/god/customers/cleanup/preview", headers=god,
                       json={"rules": ["sample_data"]})
            eid2 = r.json()["execution_id"]
            phrase2 = r.json()["confirmation_phrase"]
            r = c.post("/god/customers/cleanup/execute", headers=god,
                       json={"rules": ["sample_data"], "confirmation": phrase2,
                             "execution_id": eid2})
            check("the broken delete is refused with 500, not a false success",
                  r.status_code == 500, "%s %s" % (r.status_code, r.text[:160]))
            check("...and NOTHING was deleted", rows(Lead) == 4, rows(Lead))
            rec2 = c.get("/god/customers/cleanup/history/%s" % eid2, headers=god).json()
            check("the failed attempt is ON DISK", rec2["status"] == "failed",
                  rec2["status"])
            check("...recording zero rows deleted, not null",
                  rec2["actual_total"] == 0, rec2["actual_total"])
            check("...and why it failed", bool(rec2["error"]), rec2["error"])
            check("...and it does NOT claim the counts matched",
                  rec2["counts_match"] is False, rec2["counts_match"])
        finally:
            dc.LEAD_CHILDREN = original
            db = SessionLocal()
            try:
                from app.models.models import PipelineConversation as PC
                db.query(PC).filter(PC.id == "pc-block").delete()
                db.commit()
            finally:
                db.close()

        section("P0A - THE SUCCESSFUL RUN RECONCILES")
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["sample_data"]})
        eid3, phrase3 = r.json()["execution_id"], r.json()["confirmation_phrase"]
        r = c.post("/god/customers/cleanup/execute", headers=god,
                   json={"rules": ["sample_data"], "confirmation": phrase3,
                         "execution_id": eid3})
        res = r.json()
        check("execute succeeds", r.status_code == 200, "%s %s" % (r.status_code, r.text[:160]))
        check("...and returns the SAME execution id", res["execution_id"] == eid3,
              "%s vs %s" % (res.get("execution_id"), eid3))
        check("...expected total equals actual total", res["counts_match"] is True,
              "expected=%s actual=%s" % (res.get("expected_total"), res.get("total_deleted")))
        rec3 = c.get("/god/customers/cleanup/history/%s" % eid3, headers=god).json()
        check("the receipt is now succeeded", rec3["status"] == "succeeded",
              rec3["status"])
        check("...with per-table actual counts", rec3["actual_counts"].get("leads") == 3,
              rec3["actual_counts"])
        check("...and an executed_at timestamp", bool(rec3["executed_at"]),
              rec3["executed_at"])
        check("the real lead survived", rows(Lead) == 1, rows(Lead))

        section("P0A - A PLAN CANNOT BE REPLAYED, OR RUN ON A CHANGED SET")
        r = c.post("/god/customers/cleanup/execute", headers=god,
                   json={"rules": ["sample_data"], "confirmation": phrase3,
                         "execution_id": eid3})
        check("re-running a completed plan is refused", r.status_code == 409,
              "%s %s" % (r.status_code, r.text[:160]))
        r = c.post("/god/customers/cleanup/preview", headers=god,
                   json={"rules": ["sample_data"]})
        eid4, phrase4 = r.json()["execution_id"], r.json()["confirmation_phrase"]
        db = SessionLocal()
        try:
            db.add(Lead(id="samp-new", organization_id="org-a", first_name="New",
                        last_name="Sample", phone="+15550007777", status="new",
                        source_file="SAMPLE_DATA", created_at=datetime.utcnow()))
            db.commit()
        finally:
            db.close()
        r = c.post("/god/customers/cleanup/execute", headers=god,
                   json={"rules": ["sample_data"], "confirmation": phrase4,
                         "execution_id": eid4})
        check("a plan whose candidate set changed is refused", r.status_code == 409,
              "%s %s" % (r.status_code, r.text[:200]))
        check("...and the new record was NOT deleted", rows(Lead) == 2, rows(Lead))

        section("P0B - THE AUDIT FEED SHOWS CONTROL-PLANE ACTIONS")
        a = c.get("/god/ops/audit?limit=100", headers=god).json()
        acts = [e["action"] for e in a["entries"]]
        for want in ("data_cleanup.previewed", "data_cleanup.executed",
                     "data_cleanup.failed"):
            check("audit feed includes %s" % want, want in acts, acts[:8])
        check("...every entry carries a category",
              all(e.get("category") for e in a["entries"]),
              [(e["action"], e.get("category")) for e in a["entries"][:4]])
        check("...and the category map is returned",
              "data_lifecycle" in (a.get("categories") or {}),
              list((a.get("categories") or {})))

        r = c.get("/god/ops/audit?category=data_lifecycle&limit=50", headers=god).json()
        check("filtering by category works",
              r["entries"] and all(e["category"] == "data_lifecycle" for e in r["entries"]),
              len(r["entries"]))

        section("P0B - IT IS STILL AN ALLOWLIST, NOT A FIREHOSE")
        db = SessionLocal()
        try:
            db.add(AuditLogEntry(id="noise-1", organization_id="org-a",
                                 actor_user_id="u-adm", action="lead.update",
                                 target_type="lead", target_id="real-1"))
            db.commit()
        finally:
            db.close()
        a = c.get("/god/ops/audit?limit=200", headers=god).json()
        check("ordinary tenant activity does NOT appear",
              "lead.update" not in [e["action"] for e in a["entries"]],
              [e["action"] for e in a["entries"]][:10])
        r = c.get("/god/ops/audit?action=lead.update", headers=god)
        check("...and cannot be pulled in by naming it directly",
              r.status_code == 400, "%s %s" % (r.status_code, r.text[:140]))

        section("P0B - PLATFORM-OWNER ACTIONS ARE VISIBLE")
        c.post("/god/platform/context/customer/org-a", headers=god)
        c.post("/god/platform/context/exit",
               headers=dict(god, **{"X-Org-Override": "org-a"}))
        a = c.get("/god/ops/audit?category=platform_owner&limit=50", headers=god).json()
        acts = [e["action"] for e in a["entries"]]
        # The emitted names are enter_customer / exit_customer.  The category map
        # also carries the context_entered / context_exited spellings so audit rows
        # already written in production under either name still categorise.
        check("context entry is visible", "platform_owner.enter_customer" in acts, acts)
        check("context exit is visible", "platform_owner.exit_customer" in acts, acts)
        from app.routers.god_ops_router import _ACTION_CATEGORY
        check("...and the legacy spellings still map to platform_owner",
              all(_ACTION_CATEGORY.get(a) == "platform_owner" for a in
                  ("platform_owner.context_entered", "platform_owner.context_exited",
                   "platform_owner.enter_customer", "platform_owner.exit_customer")),
              {a: _ACTION_CATEGORY.get(a) for a in
               ("platform_owner.context_entered", "platform_owner.enter_customer")})

        section("PERMISSION GATING IS UNCHANGED")
        adm = token(c, "adm@probe.test")
        for p in ("/god/ops/audit", "/god/customers/cleanup/history"):
            r = c.get(p, headers=adm)
            check("%s refuses a customer admin" % p, r.status_code == 403,
                  r.status_code)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nTHE RECEIPT OUTLIVES THE REQUEST - and the audit feed shows it.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
