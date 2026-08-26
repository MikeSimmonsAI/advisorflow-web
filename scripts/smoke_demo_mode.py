"""Demo Mode end to end: both scenarios, reset, isolation and RBAC.

RUNS THE ACTUAL ACCEPTANCE TESTS from the Checkpoint 5.5 brief rather than
approximations of them. Every step of both scenarios is executed through the
real runner, and the resulting records are then read back out of the real
tables - because a demo that seeds rows the product cannot render is not a
demo.

THE FIREWALL IS INSTALLED FOR THE WHOLE RUN. Anything that reached for a
provider would raise, so "no real side effects" is not asserted at the end, it
is enforced throughout.

    python scripts/smoke_demo_mode.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime

TMP = tempfile.mkdtemp(prefix="demomode_")
os.environ["APP_ENV"] = "demo"
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                       # noqa: E402
from app.deps import SessionLocal, engine                         # noqa: E402
from app.models.models import (                                   # noqa: E402
    Base, Organization, User, Lead, Message, Reply, EmailMessage,
    CadenceState, BookingLink, VoiceCall, PortalEvent, Proposal,
)
from app.models.sales_models import (                             # noqa: E402
    Opportunity, BrandSalesOrg, Membership,
)
from app.models.scheduling_models import SalesAppointment          # noqa: E402
from app.models.demo_models import (                               # noqa: E402
    DemoScenarioState, DemoEvent, DEMO_ID_PREFIX,
)
import app.models.integration_models                               # noqa: E402,F401
from app.services import demo_firewall as fw                       # noqa: E402
from app.services import demo_runner as runner                     # noqa: E402
from app.services import demo_scenarios as registry                # noqa: E402
from app.services.demo_scenarios.base import assert_safe_contact   # noqa: E402

FAILURES = []
SNAP = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def counts(db):
    """A census of everything a scenario could have created."""
    out = {}
    for model in (Organization, User, Lead, Message, Reply, EmailMessage,
                  CadenceState, BookingLink, VoiceCall, PortalEvent, Proposal,
                  Opportunity, BrandSalesOrg, Membership, SalesAppointment):
        out[model.__name__] = db.query(model).count()
    return out


def run_all_steps(db, key):
    scenario = registry.get(key)
    results = []
    for _ in range(len(scenario.steps())):
        out = runner.advance_scenario(db, key)
        results.append(out)
        if not out.get("ok"):
            break
    return results


# ── 1. the environment is what the suite thinks it is ───────────────────────

def s1_setup(db):
    print("\n[1] Demo environment, firewall up")
    from app.services import environment as env
    check("this process is the demo environment", env.is_demo())
    check("THE FIREWALL IS INSTALLED FOR THE WHOLE RUN",
          fw.install() and fw.is_installed())
    check("three scenarios are registered", len(registry.all_scenarios()) == 3,
          [s.key for s in registry.all_scenarios()])
    cat = registry.catalogue()
    domains = {c["domain"] for c in cat}
    check("BOTH PRODUCT DOMAINS ARE REPRESENTED",
          domains == {"customer", "brand"}, domains)
    check("every scenario declares its steps",
          all(c["total_steps"] > 0 for c in cat), cat)


# ── 2. acceptance test, customer side ───────────────────────────────────────

def s2_customer(db):
    print("\n[2] ACCEPTANCE - customer reactivation, end to end")
    key = "customer_reactivation"
    out = runner.seed_scenario(db, key)
    check("the scenario seeds", out.get("ok"), out.get("error"))
    check("it reports what it built",
          "Cedar Hollow" in str(out.get("summary")), out.get("summary"))

    st = out["state"]
    check("it starts at step zero", st["current_step"] == 0, st["current_step"])
    check("AND TELLS THE OPERATOR WHAT COMES NEXT",
          st["next_step"] and st["next_step"]["label"], st.get("next_step"))
    check("the next step carries narration to read out",
          st["next_step"].get("narration"), st["next_step"])

    lead = db.query(Lead).filter(
        Lead.id == "demo-customer_reactivation-lead-harrelson").first()
    check("the family exists as a real Lead row", lead is not None)
    check("it is cold and four years stale",
          lead and lead.status == "new"
          and (datetime.utcnow() - lead.last_contact_date).days > 1400,
          lead.last_contact_date if lead else None)

    results = run_all_steps(db, key)
    failed = [r for r in results if not r.get("ok")]
    check("EVERY STEP RUNS", not failed,
          [r.get("error") for r in failed][:3])

    st = runner.state_out(db, registry.get(key))
    check("the scenario reports itself complete", st["status"] == "complete", st["status"])
    check("no next step remains", st["next_step"] is None)

    # The records the product actually renders.
    msgs = db.query(Message).filter(Message.lead_id == lead.id).all()
    reps = db.query(Reply).filter(Reply.lead_id == lead.id).all()
    mails = db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all()
    calls = db.query(VoiceCall).filter(VoiceCall.lead_id == lead.id).all()
    books = db.query(BookingLink).filter(BookingLink.lead_id == lead.id).all()
    cad = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()

    check("outbound SMS exist", len(msgs) >= 3, len(msgs))
    check("an inbound reply exists", len(reps) == 1, len(reps))
    check("THE REPLY IS CLASSIFIED BY THE PRODUCT'S OWN ENUM",
          reps and reps[0].classification.value == "interested",
          reps[0].classification if reps else None)
    check("an email touch exists", len(mails) == 1, len(mails))
    check("A VOICE CALL WITH A TRANSCRIPT EXISTS", len(calls) == 1, len(calls))
    check("the transcript is a real conversation, not a placeholder",
          calls and "Taffiney:" in calls[0].transcript
          and len(calls[0].transcript) > 600,
          len(calls[0].transcript) if calls else 0)
    check("the call records booking intent",
          calls and calls[0].outcome == "booking_requested",
          calls[0].outcome if calls else None)
    check("AN APPOINTMENT IS BOOKED", len(books) == 1 and books[0].status == "booked",
          [b.status for b in books])
    check("the cadence stopped itself when she replied",
          cad and cad.status == "stopped_replied", cad.status if cad else None)
    check("the lead ends up booked", lead.status == "booked", lead.status)

    # Chronology - the thing the operator points at.
    events = ([(m.sent_at, "sms_out") for m in msgs]
              + [(r.received_at, "sms_in") for r in reps]
              + [(e.sent_at, "email") for e in mails]
              + [(c.started_at, "voice") for c in calls])
    # Sorted by timestamp, the story must read in the order it was told: the
    # first outbound touch first, the inbound reply after it, the voice call
    # after the email. An earlier version backdated the call four minutes and
    # this assertion is what caught it.
    ordered = [e[1] for e in sorted(events, key=lambda e: e[0])]
    check("THE HISTORY READS IN CHRONOLOGICAL ORDER",
          ordered[0] == "sms_out"
          and ordered.index("sms_in") < ordered.index("email")
          and ordered.index("email") < ordered.index("voice"),
          ordered)

    SNAP["customer"] = counts(db)


# ── 3. acceptance test, brand-sales side ────────────────────────────────────

def s3_brand(db):
    print("\n[3] ACCEPTANCE - EvoSys Pro sales cycle, end to end")
    key = "brand_sales"
    # Full wipe first, so the census taken at the end of this section describes
    # the brand scenario ALONE. Section 4 then re-seeds the customer story to
    # prove the two can coexist.
    runner.reset_all(db)
    out = runner.seed_scenario(db, key)
    check("the scenario seeds", out.get("ok"), out.get("error"))

    opp = db.query(Opportunity).filter(
        Opportunity.id == "demo-brand_sales-opp-brightwater").first()
    check("the opportunity exists", opp is not None)
    check("discovery is already done, demo ready - a real starting state",
          opp and opp.stage == "demo" and opp.discovery_completed_at is not None,
          opp.stage if opp else None)

    results = run_all_steps(db, key)
    failed = [r for r in results if not r.get("ok")]
    check("EVERY STEP RUNS", not failed, [r.get("error") for r in failed][:3])

    db.refresh(opp)
    props = (db.query(Proposal)
             .filter(Proposal.opportunity_id == opp.id)
             .order_by(Proposal.version).all())
    portal = db.query(PortalEvent).filter(
        PortalEvent.opportunity_id == opp.id).all()
    appts = db.query(SalesAppointment).filter(
        SalesAppointment.opportunity_id == opp.id).all()

    check("a meeting was scheduled", len(appts) == 1, len(appts))
    check("TWO PROPOSAL VERSIONS EXIST - the revision is real",
          len(props) == 2, [p.version for p in props])
    check("the first version is still on record", props[0].version == 1)
    check("BUYER ACTIVITY IS REAL PORTAL EVENTS",
          len(portal) >= 6, len(portal))
    check("activity is recorded against both versions",
          len({p.proposal_version for p in portal}) == 2,
          {p.proposal_version for p in portal})
    check("THE DEAL IS WON", opp.stage == "won" and opp.status == "won",
          (opp.stage, opp.status))

    # The pricing authority story - a manager approved, not the rep.
    from app.models.sales_models import PricingApprovalRequest
    reqs = db.query(PricingApprovalRequest).all()
    check("an approval request was raised and decided",
          len(reqs) == 1 and reqs[0].status == "approved",
          [(r.status) for r in reqs])
    check("THE MANAGER IS RECORDED AS THE DECIDER, NOT THE REP",
          reqs and reqs[0].decided_by == "demo-brand_sales-manager",
          reqs[0].decided_by if reqs else None)

    SNAP["brand"] = counts(db)


# ── 4. the two trees never touched ──────────────────────────────────────────

def s4_separation(db):
    print("\n[4] The two product domains stayed apart")
    # Seeding one scenario resets only ITS OWN records, so both stories can be
    # loaded at once - which is the state an operator demonstrating both would
    # actually be in, and the only state in which this section means anything.
    runner.seed_scenario(db, "customer_reactivation")
    run_all_steps(db, "customer_reactivation")

    leads = db.query(Lead).all()
    opps = db.query(Opportunity).all()
    check("BOTH SCENARIOS ARE LOADED AT THE SAME TIME",
          len(leads) > 0 and len(opps) > 0, (len(leads), len(opps)))

    lead_orgs = {l.organization_id for l in leads}
    brands = {o.brand_sales_org_id for o in opps}
    check("NO ID IS SHARED BETWEEN A LEAD'S ORG AND A BRAND SALES ORG",
          not (lead_orgs & brands), lead_orgs & brands)

    # Brand-sales staff have organization_id NULL by design.
    sales_users = (db.query(User)
                   .join(Membership, Membership.user_id == User.id)
                   .filter(Membership.is_active.is_(True)).all())
    check("BRAND-SALES STAFF HAVE NO CUSTOMER TENANT",
          all(u.organization_id is None for u in sales_users),
          [(u.full_name, u.organization_id) for u in sales_users][:4])

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "app", "services", "demo_scenarios",
                            "brand_sales.py"), encoding="utf-8").read()
    # Imports only. Both modules name the other tree in their docstrings to
    # explain the separation, so matching the bare word would fail for the
    # wrong reason.
    def imports(path):
        import ast
        tree = ast.parse(open(path, encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    names.add(a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    names.add(a.name)
        return names

    here = os.path.dirname(os.path.abspath(__file__))
    brand_imports = imports(os.path.join(
        here, "..", "app", "services", "demo_scenarios", "brand_sales.py"))
    cust_imports = imports(os.path.join(
        here, "..", "app", "services", "demo_scenarios",
        "customer_reactivation.py"))
    check("THE BRAND SCENARIO NEVER IMPORTS Lead",
          "Lead" not in brand_imports, sorted(brand_imports)[:12])
    check("THE CUSTOMER SCENARIO NEVER IMPORTS Opportunity",
          "Opportunity" not in cust_imports, sorted(cust_imports)[:12])
    check("nor does it import any sales model",
          not any(n in cust_imports for n in
                  ("BrandSalesOrg", "Proposal", "SalesAppointment", "Membership")),
          sorted(cust_imports))


# ── 5. every contact detail is unreachable ──────────────────────────────────

def s5_contacts(db):
    print("\n[5] Nothing seeded could ever reach a real person")
    bad = []
    for lead in db.query(Lead).all():
        try:
            assert_safe_contact(lead.phone, lead.email)
        except ValueError as e:
            bad.append(str(e))
    check("EVERY LEAD PHONE IS IN THE RESERVED 555-01xx RANGE AND EVERY EMAIL "
          "IS A RESERVED DOMAIN", not bad, bad[:3])

    for u in db.query(User).all():
        try:
            assert_safe_contact(None, u.email)
        except ValueError as e:
            bad.append(str(e))
    check("every demo user's email is undeliverable by design", not bad, bad[:3])

    for c in db.query(VoiceCall).all():
        try:
            assert_safe_contact(c.to_phone, None)
            assert_safe_contact(c.from_phone, None)
        except ValueError as e:
            bad.append(str(e))
    check("so is every number a voice call would have dialled", not bad, bad[:3])

    for o in db.query(Opportunity).all():
        try:
            assert_safe_contact(o.phone, o.email)
        except ValueError as e:
            bad.append(str(e))
    check("and every prospect contact on the sales side", not bad, bad[:3])


# ── 6. nothing reached a provider ───────────────────────────────────────────

def s6_no_side_effects(db):
    print("\n[6] Zero real side effects across both scenarios")
    check("the firewall is still installed", fw.is_installed())
    check("NOTHING ATTEMPTED AN OUTBOUND CALL DURING EITHER SCENARIO",
          len(fw.blocked_attempts()) == 0,
          fw.blocked_attempts()[:5])

    # Every simulated provider id is labelled as such, so a real Twilio SID and
    # a demo one can never be confused in a support ticket.
    sids = [m.twilio_sid for m in db.query(Message).all() if m.twilio_sid]
    check("every message carries a clearly SIMULATED provider id",
          sids and all(s.startswith("SIMULATED-DEMO-") for s in sids), sids[:3])
    ids = [e.provider_message_id for e in db.query(EmailMessage).all()
           if e.provider_message_id]
    check("so does every email", all(s.startswith("SIMULATED-DEMO-") for s in ids),
          ids[:3])
    cal = [b.calendar_event_id for b in db.query(BookingLink).all()
           if b.calendar_event_id]
    check("and every calendar event id", all(s.startswith("SIMULATED-DEMO-")
                                             for s in cal), cal[:3])


# ── 7. reset and idempotency ────────────────────────────────────────────────

def s7_reset(db):
    print("\n[7] ACCEPTANCE - reset returns both scenarios to clean")
    before = counts(db)
    check("there is data to remove", before["Lead"] > 0 and before["Opportunity"] > 0,
          before)

    out = runner.reset_all(db)
    check("reset reports success", out.get("ok"))
    check("AND SAYS WHAT IT REMOVED", out.get("total", 0) > 0, out.get("total"))

    after = counts(db)
    leftovers = {k: v for k, v in after.items() if v}
    check("EVERY DEMO RECORD IS GONE", not leftovers, leftovers)

    states = db.query(DemoScenarioState).all()
    check("every scenario is back to empty",
          all(s.status == "empty" and s.current_step == 0 for s in states),
          [(s.scenario_key, s.status) for s in states])

    second = runner.reset_all(db)
    check("A SECOND RESET REMOVES NOTHING - it is idempotent",
          second.get("total") == 0, second.get("total"))

    # Re-seed and compare. The same scenario must produce the same world.
    runner.seed_scenario(db, "customer_reactivation")
    run_all_steps(db, "customer_reactivation")
    again = counts(db)
    check("RE-SEEDING PRODUCES AN IDENTICAL CENSUS - no duplicates",
          again == SNAP["customer"],
          {k: (SNAP["customer"][k], again[k]) for k in again
           if again[k] != SNAP["customer"][k]})

    runner.reset_all(db)
    runner.seed_scenario(db, "brand_sales")
    run_all_steps(db, "brand_sales")
    again_b = counts(db)
    check("and so does the brand scenario", again_b == SNAP["brand"],
          {k: (SNAP["brand"][k], again_b[k]) for k in again_b
           if again_b[k] != SNAP["brand"][k]})


# ── 8. reset cannot touch a record it does not own ──────────────────────────

def s8_reset_safety(db):
    print("\n[8] Reset cannot reach a record it does not own")
    # A record with a normal uuid, exactly as production holds them.
    real_id = "c0ffee00-1111-2222-3333-444455556666"
    db.add(Organization(id=real_id, name="Not A Demo Org",
                        slug="not-a-demo-org", is_active=True))
    db.commit()

    runner.reset_all(db)
    survivor = db.query(Organization).filter(Organization.id == real_id).first()
    check("A NON-PREFIXED RECORD SURVIVES RESET UNTOUCHED",
          survivor is not None and survivor.name == "Not A Demo Org",
          survivor)
    db.delete(survivor)
    db.commit()

    # THE OPERATOR MUST SURVIVE A FULL RESET.
    #
    # An earlier version gave the demo operator a `demo-` prefixed id, so
    # pressing "Reset everything" deleted the account of the person pressing it
    # and logged them out mid-presentation. Found by actually using the button.
    op_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "demo_operator.py"), encoding="utf-8").read()
    import re as _re
    ids = _re.findall(r'^OPERATOR\w*_ID = "([^"]+)"', op_src, _re.M)
    check("the operator seeder defines its ids", len(ids) >= 2, ids)
    check("NO OPERATOR ID CARRIES THE DEMO PREFIX - reset must not log the "
          "operator out", all(not i.startswith("demo-") for i in ids), ids)

    # Prove it against the live sweep rather than only in the source.
    db.add(User(id=ids[0] + "-probe", organization_id=None,
                email="operator-probe@example.com", full_name="Operator Probe",
                password_hash="x", role="god_admin", is_active=True))
    db.commit()
    runner.reset_all(db)
    survived = db.query(User).filter(User.id == ids[0] + "-probe").first()
    check("AN OPERATOR-SHAPED ID SURVIVES reset_all", survived is not None)
    if survived:
        db.delete(survived)
        db.commit()

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "app", "services", "demo_runner.py"),
               encoding="utf-8").read()
    check("reset deletes by the demo prefix, never by table truncation",
          "DELETE FROM %s WHERE id LIKE" in src and "TRUNCATE" not in src)
    check("every public runner function requires the demo environment",
          src.count("env.require_demo()") >= 4, src.count("env.require_demo()"))


# ── 9. the demo surface is invisible outside the demo ───────────────────────

def s9_routes():
    print("\n[9] The demo surface does not exist in production")
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import environment as env

    c = TestClient(app)

    os.environ["APP_ENV"] = "production"
    for path, method in (("/demo/scenarios", "get"), ("/demo/state", "get"),
                         ("/demo/seed", "post"), ("/demo/advance", "post"),
                         ("/demo/reset", "post")):
        r = (c.post(path, json={"scenario": "brand_sales"})
             if method == "post" else c.get(path))
        check("PRODUCTION RETURNS 404 FOR %s - not 403, not 401" % path,
              r.status_code == 404, (r.status_code, r.text[:120]))

    r = c.get("/demo/environment")
    check("the environment probe still answers in production",
          r.status_code == 200 and r.json()["demo_mode"] is False, r.text[:120])
    check("and paints no banner there", r.json()["banner"] is None)

    os.environ["APP_ENV"] = "demo"
    r = c.get("/demo/environment")
    check("in demo it announces itself",
          r.json()["demo_mode"] is True and "DEMO MODE" in r.json()["banner"],
          r.text[:160])

    r = c.get("/demo/state")
    check("BUT THE CONTROLS STILL REQUIRE A LOGIN IN DEMO",
          r.status_code in (401, 403), r.status_code)
    r = c.post("/demo/reset")
    check("RESET IS NOT REACHABLE WITHOUT AUTH EVEN IN DEMO",
          r.status_code in (401, 403), r.status_code)


# ── 10. RBAC is not relaxed for the demo ────────────────────────────────────

def s10_rbac():
    print("\n[10] Demo Mode does not weaken RBAC")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "app", "routers", "demo_router.py"),
               encoding="utf-8").read()
    body = src.split('"""', 2)[-1]
    check("every mutating route requires a demo operator",
          body.count("Depends(require_demo_operator)") >= 4,
          body.count("Depends(require_demo_operator)"))
    check("THE OPERATOR CHECK REQUIRES A PLATFORM OWNER",
          "god_admin" in body and "super_admin" in body)
    check("the operator check runs the environment check first",
          "require_demo_env()" in body)

    # Nothing in the demo build may hand out elevated access elsewhere.
    for mod in ("demo_runner.py", "demo_firewall.py", "environment.py"):
        s = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                              "app", "services", mod), encoding="utf-8").read()
        check("%s adds no impersonation or role bypass" % mod,
              "impersonat" not in s.lower() and "role =" not in s
              and "is_god" not in s)


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        s1_setup(db)
        s2_customer(db)
        s3_brand(db)
        s4_separation(db)
        s5_contacts(db)
        s6_no_side_effects(db)
        s7_reset(db)
        s8_reset_safety(db)
        db.close()
        s9_routes()
        s10_rbac()
    except Exception:
        import traceback
        print(traceback.format_exc().encode("ascii", "replace").decode("ascii"))
        FAILURES.append("UNHANDLED EXCEPTION")
    finally:
        try:
            fw.uninstall(fw._TEST_TOKEN)
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)

    print()
    if FAILURES:
        print("  %d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES[:8])))
        sys.exit(1)
    print("  ALL DEMO MODE CHECKS PASSED")


if __name__ == "__main__":
    main()
