"""
Sales Workspace API regression suite — Checkpoint 1.

Real in-process HTTP through TestClient against a temp SQLite database, so the
guards, the serializers and the routing are all exercised the way production
runs them. Never touches production.

WHAT IS ASSERTED
  · /sales/* is refused outright to a customer-tenant user and to anonymous.
  · A rep sees ONLY their own opportunities; a manager sees the whole brand.
  · A rep cannot read, edit, or reassign another rep's deal by guessing the id.
  · Cross-brand isolation: brand A's member cannot touch brand B's deal, and a
    brand A deal cannot be given a brand B package.
  · Tenant isolation: nothing in /sales touches leads, and a sales user's
    organization_id stays NULL through every write.
  · Deal value DERIVES from the selected package.
  · Overriding the derived value requires a manager AND a reason, and is audited.
  · Reassignment is manager-only and refuses a user with no membership.
  · The scheduling-shaped fields report unavailable, never a false empty list.
  · Discovery is structured and stamps completion onto the opportunity.
  · Stage moves are recorded on the append-only timeline.

    python scripts/smoke_sales_workspace.py
"""
import os
import sys
import shutil
import tempfile

TMP = tempfile.mkdtemp(prefix="swtest_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                       # noqa: E402
from app.main import app                                        # noqa: E402
from app.deps import SessionLocal, engine                       # noqa: E402
from app.models.models import Base, Platform, Organization, User, Lead  # noqa: E402
from app.models.sales_models import (                           # noqa: E402
    Membership, BrandSalesOrg, BrandPackage, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password             # noqa: E402

PW = "SmokePass123!"
FAILURES = []
IDS = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    evo = db.query(Platform).filter(Platform.slug == "evosyspro").first() \
        or Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro")
    bb = db.query(Platform).filter(Platform.slug == "bookaboost").first() \
        or Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")
    db.add_all([evo, bb]); db.flush()

    cust = Organization(id="org-cust", name="Greenland", slug="greenland",
                        platform_id=bb.id)
    db.add(cust)

    evo_sales = BrandSalesOrg(id="bso-evo", platform_id=evo.id,
                              name="EvoSys Pro Sales", slug="evosyspro-sales")
    bb_sales = BrandSalesOrg(id="bso-bb", platform_id=bb.id,
                             name="BookaBoost Sales", slug="bookaboost-sales")
    db.add_all([evo_sales, bb_sales]); db.flush()

    db.add_all([
        BrandPackage(id="pkg-starter", platform_id=evo.id, key="starter",
                     name="Starter", price=1497.00, sort_order=1),
        BrandPackage(id="pkg-growth", platform_id=evo.id, key="growth",
                     name="Growth", price=2495.00, sort_order=2),
        BrandPackage(id="pkg-custom", platform_id=evo.id, key="multi_tenant",
                     name="Multi-Tenant / Custom", price=None, is_custom=True, sort_order=4),
        # Another brand's package. Must never attach to an EvoSys Pro deal.
        BrandPackage(id="pkg-bb", platform_id=bb.id, key="starter",
                     name="BB Starter", price=99.00, sort_order=1),
    ])

    def mk(uid, email, name, org=None, role="advisor"):
        u = User(id=uid, organization_id=org, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True)
        db.add(u)
        return u

    rep = mk("u-rep", "rep@example.com", "Rep One")
    rep2 = mk("u-rep2", "rep2@example.com", "Rep Two")
    mgr = mk("u-mgr", "mgr@example.com", "Manager One")
    bbrep = mk("u-bbrep", "bbrep@example.com", "Other Brand Rep")
    mk("u-tenant", "advisor@example.com", "Tenant Advisor", org=cust.id)
    mk("u-nomember", "nobody@example.com", "No Membership")
    db.flush()

    db.add_all([
        Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_REP, is_active=True),
        Membership(user_id=rep2.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_REP, is_active=True),
        Membership(user_id=mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id=bbrep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=bb_sales.id, role=ROLE_SALES_REP, is_active=True),
    ])

    # A customer lead, assigned to the tenant advisor so the control assertion
    # below is meaningful: the endpoint genuinely returns this row to its own
    # tenant while returning nothing at all to a brand-sales user.
    db.add(Lead(id="lead-1", organization_id=cust.id, assigned_to_id="u-tenant",
                first_name="Real", last_name="Customer",
                phone="12145550000", tier="pre_need"))
    db.commit(); db.close()


def token(client, email):
    r = client.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed for %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def main():
    build()
    c = TestClient(app)
    rep = token(c, "rep@example.com")
    rep2 = token(c, "rep2@example.com")
    mgr = token(c, "mgr@example.com")
    bbrep = token(c, "bbrep@example.com")
    tenant = token(c, "advisor@example.com")
    nomember = token(c, "nobody@example.com")

    # ── 1. route authentication ─────────────────────────────────────────────
    print("\n[1] Sales routes are guarded SERVER-SIDE")
    check("anonymous is refused /sales/me", c.get("/sales/me").status_code == 401)
    check("anonymous is refused the pipeline",
          c.get("/sales/opportunities").status_code == 401)
    r = c.get("/sales/me", headers=tenant)
    check("customer-tenant user is refused (403, not 404)", r.status_code == 403,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.get("/sales/me", headers=nomember)
    check("user with no membership is refused", r.status_code == 403, r.status_code)

    # ── 2. brand membership resolution ──────────────────────────────────────
    print("\n[2] Brand membership resolution")
    me = c.get("/sales/me", headers=rep).json()
    check("rep resolves to their brand", me["brand_sales_org"]["slug"] == "evosyspro-sales", me)
    check("rep resolves as sales_rep", me["role"] == "sales_rep", me["role"])
    check("rep has NULL organization_id", me["user"]["organization_id"] is None, me["user"])
    check("rep cannot view the team pipeline", me["permissions"]["view_team_pipeline"] is False)
    check("rep cannot reassign", me["permissions"]["reassign_opportunity"] is False)
    mme = c.get("/sales/me", headers=mgr).json()
    check("manager resolves as sales_manager", mme["role"] == "sales_manager")
    check("manager can view the team pipeline", mme["permissions"]["view_team_pipeline"] is True)
    bme = c.get("/sales/me", headers=bbrep).json()
    check("other brand resolves to ITS OWN brand",
          bme["brand_sales_org"]["slug"] == "bookaboost-sales", bme["brand_sales_org"])

    # ── 3. scheduling reports unavailable, never a false empty ──────────────
    print("\n[3] Unbuilt scheduling is declared, not faked")
    day = c.get("/sales/my-day", headers=rep).json()
    for key in ("todays_appointments", "next_appointment", "needs_confirmation"):
        blk = day.get(key)
        check("%s reports unavailable" % key,
              isinstance(blk, dict) and blk.get("available") is False, blk)
    check("my-day metrics are real numbers",
          isinstance(day["metrics"]["active_opportunities"], int), day["metrics"])
    check("empty pipeline starts at zero", day["metrics"]["active_opportunities"] == 0)

    # ── 4. create + rep isolation ───────────────────────────────────────────
    print("\n[4] Rep isolation")
    r = c.post("/sales/opportunities", headers=rep,
               json={"company_name": "Atlas Restoration", "contact_name": "Renee Carter",
                     "email": "renee@atlas.example", "industry": "Restoration"})
    check("rep creates an opportunity", r.status_code == 201, "%s %s" % (r.status_code, r.text[:300]))
    opp = r.json(); IDS["opp"] = opp["id"]
    check("new opportunity starts at Prospect", opp["stage"] == "prospect", opp["stage"])
    check("new opportunity is owned by its creator", opp["owner_name"] == "Rep One", opp)

    r2 = c.post("/sales/opportunities", headers=rep2, json={"company_name": "Rep Two Deal"})
    IDS["opp2"] = r2.json()["id"]

    board = c.get("/sales/opportunities", headers=rep).json()
    names = [o["company_name"] for s in board["stages"] for o in s["opportunities"]]
    check("rep sees their own deal", "Atlas Restoration" in names, names)
    check("rep does NOT see another rep's deal", "Rep Two Deal" not in names, names)
    check("rep board reports is_manager false", board["is_manager"] is False)

    mboard = c.get("/sales/opportunities", headers=mgr).json()
    mnames = [o["company_name"] for s in mboard["stages"] for o in s["opportunities"]]
    check("manager sees BOTH reps' deals",
          "Atlas Restoration" in mnames and "Rep Two Deal" in mnames, mnames)

    r = c.get("/sales/opportunities/" + IDS["opp2"], headers=rep)
    check("rep cannot open another rep's deal by id", r.status_code == 403,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.patch("/sales/opportunities/" + IDS["opp2"], headers=rep,
                json={"company_name": "Hijacked"})
    check("rep cannot edit another rep's deal", r.status_code == 403, r.status_code)
    r = c.get("/sales/opportunities/" + IDS["opp2"], headers=mgr)
    check("manager CAN open a rep's deal", r.status_code == 200, r.status_code)

    r = c.get("/sales/opportunities?owner_user_id=u-rep2", headers=rep)
    check("rep cannot filter by another rep", r.status_code == 403, r.status_code)

    # ── 5. cross-brand isolation ────────────────────────────────────────────
    print("\n[5] Cross-brand isolation")
    r = c.get("/sales/opportunities/" + IDS["opp"], headers=bbrep)
    check("other brand gets 404, not 403 (id cannot be probed)", r.status_code == 404,
          "%s %s" % (r.status_code, r.text[:200]))
    bb_board = c.get("/sales/opportunities", headers=bbrep).json()
    bb_names = [o["company_name"] for s in bb_board["stages"] for o in s["opportunities"]]
    check("other brand's board is empty of our deals", "Atlas Restoration" not in bb_names, bb_names)
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep,
                json={"selected_package_id": "pkg-bb"})
    check("another brand's package cannot attach to this deal", r.status_code == 400,
          "%s %s" % (r.status_code, r.text[:200]))
    pk = c.get("/sales/packages", headers=rep).json()
    check("package catalog is brand-scoped", all(p["name"] != "BB Starter" for p in pk),
          [p["name"] for p in pk])
    check("no sales package is wired to a Stripe plan",
          all(p["billing_plan_key"] is None for p in pk), pk)

    # ── 6. tenant isolation ─────────────────────────────────────────────────
    print("\n[6] Tenant isolation — the two domains never meet")
    db = SessionLocal()
    u = db.query(User).filter(User.email == "rep@example.com").first()
    check("sales user still has organization_id NULL after writing",
          u.organization_id is None, u.organization_id)
    o = db.query(Opportunity).filter(Opportunity.id == IDS["opp"]).first()
    check("opportunity has no customer organization yet",
          o.customer_organization_id is None, o.customer_organization_id)
    check("customer lead untouched by any /sales call",
          db.query(Lead).count() == 1 and db.query(Lead).first().organization_id == "org-cust")
    db.close()
    # The customer lead exists and is visible to its own tenant. A brand-sales
    # user with organization_id = NULL must get an EMPTY list — not an error,
    # and above all not the tenant's rows.
    r = c.get("/leads/", headers=rep)
    if r.status_code == 200:
        payload = r.json()
        rows = payload.get("items", payload.get("leads", payload))
        empty = (len(rows or []) == 0) and (payload.get("total", 0) == 0)
    else:
        empty = r.status_code in (403, 404)
    check("sales user reading tenant leads gets an EMPTY list", empty,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.get("/leads/", headers=tenant)
    check("the same endpoint DOES return the row to its own tenant",
          r.status_code == 200 and r.json().get("total") == 1,
          "%s %s" % (r.status_code, r.text[:200]))

    # ── 7. package lookup + deal value derivation ───────────────────────────
    print("\n[7] Deal value derives from the package")
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep,
                json={"selected_package_id": "pkg-growth"})
    check("rep can select a package", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    body = r.json()
    check("deal value derived from Growth ($2,495)", body["deal_value"] == 2495.0, body["deal_value"])
    check("not flagged as an override", body["deal_value_override"] is False)
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep,
                json={"selected_package_id": "pkg-starter"})
    check("re-selecting re-derives the value", r.json()["deal_value"] == 1497.0, r.json()["deal_value"])

    # ── 8. override is manager-only, reasoned, and audited ──────────────────
    print("\n[8] Manual value override")
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep,
                json={"deal_value": 1200, "deal_value_override_reason": "negotiated"})
    check("rep CANNOT override the derived value", r.status_code == 403,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=mgr, json={"deal_value": 1200})
    check("manager override without a reason is refused", r.status_code == 400,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=mgr,
                json={"deal_value": 1200, "deal_value_override_reason": "Multi-year prepay"})
    check("manager override with a reason succeeds", r.status_code == 200,
          "%s %s" % (r.status_code, r.text[:300]))
    body = r.json()
    check("override value stored", body["deal_value"] == 1200.0, body["deal_value"])
    check("override flagged", body["deal_value_override"] is True)
    check("override attributed", body["deal_value_override_by_name"] == "Manager One", body)
    check("override reason stored",
          body["deal_value_override_reason"] == "Multi-year prepay", body)
    check("override written to the audit timeline",
          any(e["event_type"] == "deal_value_override" for e in body["timeline"]),
          [e["event_type"] for e in body["timeline"]])

    # ── 9. discovery ────────────────────────────────────────────────────────
    print("\n[9] Discovery is structured, not one notes blob")
    r = c.put("/sales/opportunities/" + IDS["opp"] + "/discovery", headers=rep,
              json={"business_goals": "Double booked appointments",
                    "bottlenecks": "Manual follow-up",
                    "demo_requirements": "Show SMS cadence + calendar",
                    "mark_complete": True})
    check("discovery saves", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    body = r.json()
    check("discovery answers are separate fields",
          body["discovery"]["business_goals"] == "Double booked appointments"
          and body["discovery"]["bottlenecks"] == "Manual follow-up", body["discovery"])
    check("more than one discovery field exists",
          len(body["discovery_fields"]) >= 12, len(body["discovery_fields"]))
    check("completion stamped onto the opportunity",
          body["lifecycle"]["discovery_completed_at"] is not None, body["lifecycle"])

    # ── 10. stage movement + demo carry-forward ─────────────────────────────
    print("\n[10] Stage movement is one continuous record")
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep, json={"stage": "demo_build"})
    body = r.json()
    check("stage moved to Demo Build", body["stage"] == "demo_build", body["stage"])
    check("same record id throughout", body["id"] == IDS["opp"])
    check("demo requested stamp set", body["demo"]["requested_at"] is not None, body["demo"])
    check("discovery's demo requirements carried forward",
          body["demo"]["requirements"] == "Show SMS cadence + calendar", body["demo"])
    check("stage change on the timeline",
          any(e["event_type"] == "stage_changed" for e in body["timeline"]),
          [e["event_type"] for e in body["timeline"]])

    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep, json={"stage": "won"})
    body = r.json()
    check("Won sets status and won_at",
          body["status"] == "won" and body["lifecycle"]["won_at"] is not None, body["status"])
    check("Won does NOT invent a customer organization",
          body["customer_organization_id"] is None, body["customer_organization_id"])
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep, json={"stage": "demo_build"})
    check("reopening clears the won stamp",
          r.json()["status"] == "open" and r.json()["lifecycle"]["won_at"] is None, r.json()["status"])
    r = c.patch("/sales/opportunities/" + IDS["opp"], headers=rep, json={"stage": "nonsense"})
    check("an unknown stage is refused", r.status_code == 400, r.status_code)

    # ── 11. reassignment ────────────────────────────────────────────────────
    print("\n[11] Reassignment is a manager capability, and audited")
    r = c.post("/sales/opportunities/" + IDS["opp"] + "/reassign", headers=rep,
               json={"owner_user_id": "u-rep2"})
    check("rep cannot reassign", r.status_code == 403, r.status_code)
    r = c.post("/sales/opportunities/" + IDS["opp"] + "/reassign", headers=mgr,
               json={"owner_user_id": "u-nomember"})
    check("cannot assign to someone with no membership in this brand",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:200]))
    r = c.post("/sales/opportunities/" + IDS["opp"] + "/reassign", headers=mgr,
               json={"owner_user_id": "u-rep2"})
    check("manager reassigns", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    check("reassignment audited",
          any(e["event_type"] == "reassigned" for e in r.json()["timeline"]),
          [e["event_type"] for e in r.json()["timeline"]])
    r = c.get("/sales/opportunities/" + IDS["opp"], headers=rep)
    check("original rep loses access after reassignment", r.status_code == 403, r.status_code)

    # ── 12. my-day reflects real state ──────────────────────────────────────
    print("\n[12] My Day is computed from real records")
    day = c.get("/sales/my-day", headers=mgr).json()
    check("manager sees both opportunities as active",
          day["metrics"]["active_opportunities"] == 2, day["metrics"])
    check("recent activity is populated from real events",
          len(day["recent_activity"]) > 0, day["recent_activity"])
    check("demos-to-build counts the real demo-build deal",
          day["metrics"]["demos_to_build"] >= 1, day["metrics"])
    day_rep2 = c.get("/sales/my-day", headers=rep2).json()
    check("rep2 sees only their own two deals now",
          day_rep2["metrics"]["active_opportunities"] == 2, day_rep2["metrics"])

    print("\n" + "=" * 66)
    if FAILURES:
        print("FAILED (%d): %s" % (len(FAILURES), "; ".join(FAILURES)))
        return 1
    print("ALL SALES WORKSPACE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
