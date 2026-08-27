"""GATE 31 - three price components, and none of them may be mistaken for another.

THE MISTAKE THIS GATE EXISTS TO PREVENT, in two parts.

FIRST: a one-time implementation fee is not a monthly rate. The catalogue's
existing $1,497 / $2,495 / $4,995 figures are ONE-TIME and live in `price`. The
new $597 / $1,297 / $2,597 figures are RECURRING and live in `monthly_price`.
Confusing them either overcharges a customer twelve times over or writes off a
setup fee entirely - and the numbers look plausible on screen both ways.

SECOND: the contracted monthly rate is not the package's price. It is LOWER, and
a customer EARNS it by committing to thirteen months. Show it as "the price" and
every customer gets the discount without the commitment; default a deal to it
and customers end up on an obligation nobody agreed to.

So most checks below are about what must NOT happen:
  - `price` must keep meaning the one-time figure, and `deal_value` must keep
    deriving from it, so no historical number changes meaning,
  - the term rate must never be reachable by default,
  - it must never be quoted for a package that has no term rate,
  - a "discount" that is not lower than the regular rate is refused,
  - savings and totals are computed, never stored,
  - there is NO free month and NO annual prepayment - all 13 months are billed,
  - month-to-month gets NO invented contract total.

Nothing here touches production. Every id below is invented.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="pricing_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import Base, Platform, User                   # noqa: E402
from app.models.sales_models import (                                # noqa: E402
    BrandSalesOrg, BrandPackage, Membership, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.services.auth_service import hash_password                  # noqa: E402
from app.services import package_pricing as pp                       # noqa: E402

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
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-price"))
    db.flush()
    db.add(BrandSalesOrg(id="bso", platform_id="plt", name="EvoSys Pro Sales",
                         slug="evo-sales-price", timezone="America/Chicago"))
    db.flush()
    # THE REAL PRODUCTION SHAPE. `price` holds the one-time implementation
    # figure and `setup_fee` is NULL - that is exactly how the live catalogue is
    # stored, and the resolver has to cope with it rather than reporting no fee.
    db.add(BrandPackage(id="pkg-start", platform_id="plt", key="starter",
                        name="Starter", price=1497, setup_fee=None,
                        monthly_price=597, contract_monthly_price=500,
                        contract_term_months=13, currency="USD", sort_order=1))
    db.add(BrandPackage(id="pkg-growth", platform_id="plt", key="growth",
                        name="Growth", price=2495, setup_fee=None,
                        monthly_price=1297, contract_monthly_price=1000,
                        contract_term_months=13, currency="USD", sort_order=2))
    db.add(BrandPackage(id="pkg-pro", platform_id="plt", key="professional",
                        name="Professional", price=4995, setup_fee=None,
                        monthly_price=2597, contract_monthly_price=2000,
                        contract_term_months=13, currency="USD", sort_order=3))
    # A package with NO term option. Nothing may offer one for it.
    db.add(BrandPackage(id="pkg-m2m", platform_id="plt", key="m2m_only",
                        name="Month To Month Only", price=999,
                        monthly_price=899, currency="USD", sort_order=4))
    # An explicit setup_fee must WIN over the legacy price column.
    db.add(BrandPackage(id="pkg-explicit", platform_id="plt", key="explicit_setup",
                        name="Explicit Setup", price=1111, setup_fee=2222,
                        monthly_price=300, currency="USD", sort_order=5))
    db.flush()
    for uid, email, role in (("u-god", "god@probe.test", "god_admin"),
                             ("u-mgr", "mgr@probe.test", "advisor"),
                             ("u-rep", "rep@probe.test", "advisor")):
        db.add(User(id=uid, organization_id=None, email=email, full_name=uid,
                    password_hash=hash_password(PW), role=role,
                    must_change_password=False, is_active=True,
                    last_login_at=datetime.utcnow() - timedelta(days=1)))
    db.flush()
    db.add(Membership(user_id="u-mgr", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_MANAGER, is_active=True))
    db.add(Membership(user_id="u-rep", scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id="bso", role=ROLE_SALES_REP, is_active=True))
    db.flush()
    db.add(Opportunity(id="opp-1", brand_sales_org_id="bso",
                       owner_user_id="u-rep", company_name="Building Equity Inc",
                       stage="discovery", status="open"))
    db.commit()
    db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s" % (email, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def pkg(db_id):
    db = SessionLocal()
    try:
        return db.query(BrandPackage).filter(BrandPackage.id == db_id).first()
    finally:
        db.close()


def opp():
    db = SessionLocal()
    try:
        return db.query(Opportunity).filter(Opportunity.id == "opp-1").first()
    finally:
        db.close()


def main():
    print("=" * 78)
    print("GATE 31 - TWO BILLING OPTIONS, AND THE REGULAR RATE STAYS REGULAR")
    print("=" * 78)
    build()

    with TestClient(app) as c:
        god = token(c, "god@probe.test")
        mgr = token(c, "mgr@probe.test")
        rep = token(c, "rep@probe.test")

        section("THE ONE-TIME FEE AND THE MONTHLY RATE ARE DIFFERENT NUMBERS")
        cat = c.get("/sales/packages", headers=rep).json()
        start = [p for p in cat if p["key"] == "starter"][0]
        pr = start["pricing"]
        check("`price` still holds the ONE-TIME figure, untouched",
              start["price"] == 1497.0, start["price"])
        check("...and it is resolved as the implementation fee",
              pr["implementation_fee"] == 1497.0, pr["implementation_fee"])
        check("...and labelled one-time, not monthly",
              pr["implementation_fee_is_one_time"] is True)
        check("the monthly rate is a SEPARATE number",
              pr["monthly_price"] == 597.0, pr["monthly_price"])
        check("the contracted monthly rate is separate again",
              pr["contract_monthly_price"] == 500.0, pr["contract_monthly_price"])
        check("the saving is the difference between the two MONTHLY rates",
              pr["savings_per_month"] == 97.0, pr["savings_per_month"])
        check("the term is stated", pr["contract_term_months"] == 13,
              pr["contract_term_months"])
        check("there is NO bare 'price' key in the pricing block",
              "price" not in pr, sorted(pr.keys()))

        section("AN EXPLICIT SETUP FEE WINS OVER THE LEGACY PRICE COLUMN")
        ex = [p for p in cat if p["key"] == "explicit_setup"][0]["pricing"]
        check("setup_fee is used when set", ex["implementation_fee"] == 2222.0,
              ex["implementation_fee"])
        check("...and the source says so",
              ex["options"][0]["implementation_fee_source"] == "package_setup_fee",
              ex["options"][0]["implementation_fee_source"])
        check("the legacy column is the source when setup_fee is NULL",
              pr["options"][0]["implementation_fee_source"] == "package_legacy_price",
              pr["options"][0]["implementation_fee_source"])

        section("GROWTH AND PROFESSIONAL CARRY THE SAME SHAPE")
        for key, setup, m2m, ctr, save in (
                ("growth", 2495.0, 1297.0, 1000.0, 297.0),
                ("professional", 4995.0, 2597.0, 2000.0, 597.0)):
            row = [p for p in cat if p["key"] == key][0]["pricing"]
            check("%s: $%s setup + $%s/mo or $%s/mo, saves $%s"
                  % (key, int(setup), int(m2m), int(ctr), int(save)),
                  row["implementation_fee"] == setup
                  and row["monthly_price"] == m2m
                  and row["contract_monthly_price"] == ctr
                  and row["savings_per_month"] == save,
                  (row["implementation_fee"], row["monthly_price"],
                   row["contract_monthly_price"], row["savings_per_month"]))

        section("BOTH OPTIONS ARE OFFERED, REGULAR RATE FIRST")
        check("two options", len(pr["options"]) == 2,
              [o["billing_option"] for o in pr["options"]])
        check("month-to-month is FIRST and the default",
              pr["options"][0]["billing_option"] == pp.BILLING_MONTH_TO_MONTH
              and pr["options"][0]["is_default"] is True)

        section("THE TERM AGREEMENT BILLS EVERY MONTH - NO FREE MONTH")
        term = [o for o in pr["options"]
                if o["billing_option"] == pp.BILLING_TERM_AGREEMENT][0]
        m2m = pr["options"][0]
        check("all 13 payments are required", term["payments_required"] == 13,
              term["payments_required"])
        check("billed monthly", term["billing_cadence"] == "monthly")
        check("THERE IS NO FREE MONTH", term["has_free_month"] is False)
        check("and NO annual prepayment", term["annual_prepayment"] is False)
        check("recurring contract value is 13 x $500 = $6,500, not 12",
              term["recurring_contract_value"] == 6500.0,
              term["recurring_contract_value"])
        check("TOTAL contract value adds the one-time fee: 6500 + 1497 = 7997",
              term["total_contract_value"] == 7997.0,
              term["total_contract_value"])
        check("the implementation fee does NOT change with the option",
              term["implementation_fee"] == m2m["implementation_fee"] == 1497.0,
              (term["implementation_fee"], m2m["implementation_fee"]))
        check("MRR mirrors the monthly rate", term["mrr"] == 500.0, term["mrr"])

        section("MONTH-TO-MONTH INVENTS NO CONTRACT TOTAL")
        check("no term", m2m["term_months"] is None, m2m["term_months"])
        check("no recurring contract value",
              m2m["recurring_contract_value"] is None,
              m2m["recurring_contract_value"])
        check("no payments-required count", m2m["payments_required"] is None,
              m2m["payments_required"])
        check("no saving is claimed for the regular rate",
              m2m["savings_per_month"] is None, m2m["savings_per_month"])

        section("THE UI IS TOLD WHAT TO LEAD WITH")
        check("a fixed term leads with TOTAL CONTRACT VALUE",
              term["primary_value"] == 7997.0
              and term["primary_value_label"] == "Total Contract Value",
              (term["primary_value"], term["primary_value_label"]))
        check("month-to-month leads with the monthly rate",
              m2m["primary_value"] == 597.0
              and m2m["primary_value_label"] == "Monthly Rate",
              (m2m["primary_value"], m2m["primary_value_label"]))

        section("A PACKAGE WITH NO TERM RATE OFFERS NO TERM")
        only = [p for p in cat if p["key"] == "m2m_only"][0]["pricing"]
        check("no term option is advertised",
              only["has_term_option"] is False and len(only["options"]) == 1,
              only["options"])
        check("...and no saving is invented", only["savings_per_month"] is None)
        check("the service refuses to rate it as a term deal",
              pp.monthly_rate(pkg("pkg-m2m"), pp.BILLING_TERM_AGREEMENT) is None)
        check("...and normalising falls back to month-to-month, never up",
              pp.normalize_option(pp.BILLING_TERM_AGREEMENT, pkg("pkg-m2m"))
              == pp.BILLING_MONTH_TO_MONTH)

        section("DEAL VALUE KEEPS ITS OLD MEANING - NOTHING IS RESTATED")
        r = c.patch("/sales/opportunities/opp-1",
                    json={"selected_package_id": "pkg-start"}, headers=rep)
        check("the rep may select a package", r.status_code == 200, r.status_code)
        o = opp()
        check("deal_value derives from `price`, exactly as it always did",
              float(o.deal_value) == 1497.0, float(o.deal_value))
        check("...and the deal defaults to month-to-month",
              o.billing_option == pp.BILLING_MONTH_TO_MONTH, o.billing_option)
        check("no term is claimed", o.contract_term_months is None)

        section("CHOOSING THE AGREEMENT DOES NOT MOVE deal_value")
        r = c.patch("/sales/opportunities/opp-1",
                    json={"billing_option": pp.BILLING_TERM_AGREEMENT},
                    headers=rep)
        check("the rep may choose the agreement", r.status_code == 200, r.status_code)
        o = opp()
        check("DEAL VALUE IS UNCHANGED - no historical semantics were restated",
              float(o.deal_value) == 1497.0, float(o.deal_value))
        check("the term is snapshotted onto the deal",
              o.contract_term_months == 13, o.contract_term_months)
        check("...and no manager override was demanded",
              o.deal_value_override is False)

        section("A PER-DEAL IMPLEMENTATION FEE OVERRIDES THE CATALOGUE'S")
        r = c.patch("/sales/opportunities/opp-1",
                    json={"implementation_fee": 1500}, headers=rep)
        check("the deal may carry its own setup figure", r.status_code == 200,
              (r.status_code, r.text[:120]))
        d = c.get("/sales/opportunities/opp-1", headers=rep).json()
        b = d["billing"]
        check("the deal quotes $1,500, not the catalogue's $1,497",
              b["implementation_fee"] == 1500.0, b["implementation_fee"])
        check("...and says the figure is this deal's",
              b["implementation_fee_source"] == "opportunity_override",
              b["implementation_fee_source"])
        check("THE CATALOGUE IS UNTOUCHED - Starter is still $1,497 for everyone",
              float(pkg("pkg-start").price) == 1497.0,
              float(pkg("pkg-start").price))
        check("the total follows the override: 6500 + 1500 = 8000",
              b["selected"]["total_contract_value"] == 8000.0,
              b["selected"]["total_contract_value"])
        check("a negative fee is refused",
              c.patch("/sales/opportunities/opp-1",
                      json={"implementation_fee": -5},
                      headers=rep).status_code == 400)

        section("THE DEAL STATES ITS OWN TERMS")
        check("the selected option is on the record",
              b["selected"]["billing_option"] == pp.BILLING_TERM_AGREEMENT,
              b["selected"]["billing_option"])
        check("it is labelled as an agreement, not as a price",
              b["selected"]["billing_option_label"] == "13-Month Agreement",
              b["selected"]["billing_option_label"])
        check("the alternative is still offered", len(b["options"]) == 2)
        check("MRR is $500", b["selected"]["mrr"] == 500.0, b["selected"]["mrr"])
        check("recurring contract value is $6,500",
              b["selected"]["recurring_contract_value"] == 6500.0,
              b["selected"]["recurring_contract_value"])

        section("SWITCHING BACK DROPS THE TERM AND THE TOTALS")
        c.patch("/sales/opportunities/opp-1",
                json={"billing_option": pp.BILLING_MONTH_TO_MONTH}, headers=rep)
        d = c.get("/sales/opportunities/opp-1", headers=rep).json()
        sel = d["billing"]["selected"]
        check("the monthly rate returns to $597", sel["monthly_rate"] == 597.0,
              sel["monthly_rate"])
        check("no contract total survives",
              sel["recurring_contract_value"] is None
              and sel["term_months"] is None,
              (sel["recurring_contract_value"], sel["term_months"]))
        check("the per-deal implementation fee still stands",
              sel["implementation_fee"] == 1500.0, sel["implementation_fee"])
        check("deal_value STILL has not moved", float(opp().deal_value) == 1497.0,
              float(opp().deal_value))

        section("A TERM CANNOT BE ASKED FOR WHERE NONE EXISTS")
        c.patch("/sales/opportunities/opp-1",
                json={"selected_package_id": "pkg-m2m"}, headers=rep)
        r = c.patch("/sales/opportunities/opp-1",
                    json={"billing_option": pp.BILLING_TERM_AGREEMENT},
                    headers=rep)
        check("the request is REFUSED, not silently downgraded",
              r.status_code == 400, (r.status_code, r.text[:120]))
        check("...and says why", "no term-agreement rate" in r.text, r.text[:160])

        section("GARBAGE FAILS CLOSED TOWARD THE REGULAR RATE")
        c.patch("/sales/opportunities/opp-1",
                json={"selected_package_id": "pkg-start"}, headers=rep)
        c.patch("/sales/opportunities/opp-1",
                json={"billing_option": "13_month_free_month_lol"}, headers=rep)
        o = opp()
        check("an unrecognised option never becomes a commitment",
              o.billing_option == pp.BILLING_MONTH_TO_MONTH, o.billing_option)

        section("RECURRING PRICING IS SETTABLE FROM THE CONTROL PLANE")
        r = c.patch("/god/ops/packages/pkg-m2m/pricing",
                    json={"monthly_price": 899, "contract_monthly_price": 750,
                          "contract_term_months": 13}, headers=god)
        check("god can set both monthly rates", r.status_code == 200,
              (r.status_code, r.text[:140]))
        got = r.json()["package"]["pricing"]
        check("...and the saving follows the two rates",
              got["savings_per_month"] == 149.0, got["savings_per_month"])
        check("THE ONE-TIME PRICE WAS NOT TOUCHED BY A PRICING CALL",
              float(pkg("pkg-m2m").price) == 999.0, float(pkg("pkg-m2m").price))

        section("A 'DISCOUNT' THAT IS NOT LOWER IS REFUSED")
        for bad, why in ((597, "equal to the regular rate"),
                         (700, "higher than the regular rate")):
            r = c.patch("/god/ops/packages/pkg-start/pricing",
                        json={"contract_monthly_price": bad}, headers=god)
            check("%s is refused" % why, r.status_code == 400,
                  (r.status_code, r.text[:140]))
        check("...and the package still prices correctly",
              float(pkg("pkg-start").contract_monthly_price) == 500.0,
              float(pkg("pkg-start").contract_monthly_price))
        r = c.patch("/god/ops/packages/pkg-start/pricing",
                    json={"contract_monthly_price": 500, "contract_term_months": 0},
                    headers=god)
        check("a zero-month term is refused", r.status_code == 400,
              (r.status_code, r.text[:140]))

        section("WITHDRAWING THE CONTRACTED RATE WITHDRAWS THE TERM")
        r = c.patch("/god/ops/packages/pkg-growth/pricing",
                    json={"contract_monthly_price": None}, headers=god)
        check("the rate can be withdrawn", r.status_code == 200, r.status_code)
        p2 = pkg("pkg-growth")
        check("...and the term goes with it",
              p2.contract_monthly_price is None and p2.contract_term_months is None,
              (p2.contract_monthly_price, p2.contract_term_months))
        check("the one-time price survived that too",
              float(p2.price) == 2495.0, float(p2.price))

        section("THE PRICE CHANGE IS AUDITED AND VISIBLE")
        a = c.get("/god/ops/audit?category=pricing&limit=20", headers=god).json()
        acts = [e["action"] for e in a["entries"]]
        check("package_pricing_changed is in the feed",
              "package_pricing_changed" in acts, acts[:6])
        check("...and is categorised, not loose",
              all(e["category"] == "pricing" for e in a["entries"]))

        section("ONLY GOD MAY REPRICE THE CATALOGUE")
        for who, hdr in (("a sales rep", rep), ("a sales manager", mgr)):
            r = c.patch("/god/ops/packages/pkg-start/pricing",
                        json={"monthly_price": 1}, headers=hdr)
            check("%s cannot reprice a package" % who,
                  r.status_code in (401, 403, 404), r.status_code)
        check("the rates survived the attempts",
              float(pkg("pkg-start").monthly_price) == 597.0
              and float(pkg("pkg-start").price) == 1497.0,
              (float(pkg("pkg-start").monthly_price), float(pkg("pkg-start").price)))

        section("A PROPOSAL QUOTES THE DEAL'S FEE, NOT THE CATALOGUE'S")
        c.patch("/sales/opportunities/opp-1",
                json={"selected_package_id": "pkg-start",
                      "implementation_fee": 1500}, headers=rep)
        pr_r = c.post("/sales/proposals",
                      json={"opportunity_id": "opp-1", "package_id": "pkg-start"},
                      headers=mgr)
        check("a proposal can be created", pr_r.status_code == 201,
              (pr_r.status_code, pr_r.text[:160]))
        prop = pr_r.json()
        check("base_amount is the DEAL's $1,500, not the catalogue's $1,497",
              prop["base_amount"] == 1500.0, prop["base_amount"])
        check("...and the total agrees with it",
              prop["final_amount"] == 1500.0, prop["final_amount"])
        check("the commercials block agrees too - one number, not two",
              prop["commercials"]["implementation_fee"] == 1500.0,
              prop["commercials"]["implementation_fee"])
        check("NO $1,497 anywhere in the quote",
              "1497" not in json.dumps(prop),
              [k for k, v in prop.items() if "1497" in json.dumps(v)])

        section("A DRAFT FOLLOWS A LATER CHANGE TO THE DEAL'S FEE")
        pid = prop["id"]
        c.patch("/sales/opportunities/opp-1",
                json={"implementation_fee": 1750}, headers=rep)
        again = c.patch("/sales/proposals/" + pid,
                        json={"billing_option": pp.BILLING_TERM_AGREEMENT},
                        headers=mgr).json()
        check("the draft picks up the new $1,750",
              again["base_amount"] == 1750.0, again["base_amount"])
        check("...and its total agrees", again["final_amount"] == 1750.0,
              again["final_amount"])

        section("A PUBLISHED-BUT-UNSENT QUOTE STILL FOLLOWS THE DEAL")
        pub = c.post("/sales/proposals/" + pid + "/publish", headers=mgr)
        check("it can be published without sending", pub.status_code == 200,
              (pub.status_code, pub.text[:120]))
        check("...and is Ready, not Sent",
              pub.json()["status"] == "ready" and pub.json()["sent_at"] is None,
              (pub.json()["status"], pub.json()["sent_at"]))
        c.patch("/sales/opportunities/opp-1",
                json={"implementation_fee": None}, headers=rep)
        back = c.patch("/sales/proposals/" + pid,
                       json={"billing_option": pp.BILLING_TERM_AGREEMENT},
                       headers=mgr).json()
        check("clearing the override falls back to the CATALOGUE's $1,497",
              back["base_amount"] == 1497.0, back["base_amount"])
        check("...and the commercials agree",
              back["commercials"]["implementation_fee"] == 1497.0
              and back["commercials"]["implementation_fee_source"]
              == "package_legacy_price",
              [back["commercials"]["implementation_fee"],
               back["commercials"]["implementation_fee_source"]])
        check("total contract value is now 6500 + 1497 = 7997",
              back["commercials"]["total_contract_value"] == 7997.0,
              back["commercials"]["total_contract_value"])
        check("NO $1,500 survives anywhere in the quote",
              "1500" not in json.dumps(back),
              [k for k, v in back.items() if "1500" in json.dumps(v)])

        section("BUT A MANAGER'S AGREED NUMBER IS NEVER OVERWRITTEN")
        adj = c.patch("/sales/proposals/" + pid,
                      json={"adjustment": -250,
                            "price_reason": "Agreed concession"}, headers=mgr)
        check("a manager may adjust", adj.status_code == 200,
              (adj.status_code, adj.text[:140]))
        c.patch("/sales/opportunities/opp-1",
                json={"implementation_fee": 1500}, headers=rep)
        held = c.patch("/sales/proposals/" + pid,
                       json={"billing_option": pp.BILLING_TERM_AGREEMENT},
                       headers=mgr).json()
        check("the adjusted quote HOLDS at 1497 - 250, not re-derived to 1500",
              held["base_amount"] == 1497.0 and held["final_amount"] == 1247.0,
              (held["base_amount"], held["final_amount"]))

        section("A SOLD DEAL CARRIES ITS AGREEMENT INTO PROVISIONING")
        from app.services.provisioning import _billing_option_sold, _term_sold

        class _Src(object):
            def __init__(self, opt=None, term=None):
                self.billing_option, self.contract_term_months = opt, term

        starter = pkg("pkg-start")
        check("the accepted proposal wins over the opportunity",
              _billing_option_sold(_Src(pp.BILLING_MONTH_TO_MONTH),
                                   _Src(pp.BILLING_TERM_AGREEMENT, 13),
                                   starter) == pp.BILLING_TERM_AGREEMENT)
        check("...and its term travels with it",
              _term_sold(_Src(pp.BILLING_MONTH_TO_MONTH),
                         _Src(pp.BILLING_TERM_AGREEMENT, 13), starter) == 13)
        check("a month-to-month sale carries NO term",
              _term_sold(_Src(pp.BILLING_MONTH_TO_MONTH), None, starter) is None)
        check("the recurring amount follows the agreement, not the regular rate",
              float(pp.monthly_rate(starter, pp.BILLING_TERM_AGREEMENT)) == 500.0,
              float(pp.monthly_rate(starter, pp.BILLING_TERM_AGREEMENT)))
        check("...and month-to-month still bills the regular rate",
              float(pp.monthly_rate(starter, pp.BILLING_MONTH_TO_MONTH)) == 597.0)
        check("the implementation fee is NOT a recurring amount",
              float(pp.implementation_fee(starter)) == 1497.0,
              float(pp.implementation_fee(starter)))
        check("no package means no invented option",
              _billing_option_sold(_Src(pp.BILLING_TERM_AGREEMENT, 13), None, None)
              is None)

    # ── the per-deal custom rate ───────────────────────────────────────────
    # A "Custom" package has no catalogue rate on purpose. These prove the DEAL
    # can supply one, that it wins over the catalogue, that it carries its own
    # term, and that it never manufactures a saving against a price nobody quoted.
    section("CUSTOM IS ACTUALLY CUSTOMISABLE - the rate lives on the deal")

    class _Deal(object):
        def __init__(self, unit=None, label=None, mn=None, term=None):
            self.custom_unit_price = unit
            self.custom_unit_label = label
            self.custom_min_units = mn
            self.custom_term_months = term

    custom_pkg = pkg("pkg-multi")
    daniel = _Deal("250", "active paying customer", 15, 13)
    c = pp.custom_rate(daniel)

    check("a deal with no custom rate reports none",
          pp.custom_rate(_Deal()) is None)
    check("the monthly rate is unit x minimum, not a typed total",
          float(c["monthly_rate"]) == 3750.0, float(c["monthly_rate"]))
    check("the BASIS survives, not just the total",
          pp.custom_basis(c) == "$250 per active paying customer per month, 15 minimum",
          pp.custom_basis(c))
    check("a flat custom rate has no basis to explain",
          pp.custom_basis(pp.custom_rate(_Deal("5000"))) is None)
    check("a missing minimum means one unit, never zero",
          float(pp.custom_rate(_Deal("5000"))["monthly_rate"]) == 5000.0)

    check("a custom package alone still offers NO term",
          pp.has_term_option(custom_pkg) is False)
    check("...but a custom rate WITH a term does",
          pp.has_term_option(custom_pkg, c) is True)
    check("a custom rate with no term does not invent one",
          pp.has_term_option(custom_pkg,
                             pp.custom_rate(_Deal("250", None, 15))) is False)
    check("the term agreement is reachable once the deal states a term",
          pp.normalize_option(pp.BILLING_TERM_AGREEMENT, custom_pkg, c)
          == pp.BILLING_TERM_AGREEMENT)
    check("...and still fails closed without one",
          pp.normalize_option(pp.BILLING_TERM_AGREEMENT, custom_pkg, None)
          == pp.BILLING_MONTH_TO_MONTH)

    q = pp.quote(custom_pkg, pp.BILLING_TERM_AGREEMENT, None, custom=c)
    check("the quote bills the custom rate", q["monthly_rate"] == 3750.0,
          q["monthly_rate"])
    check("the quote uses the DEAL's term, not the catalogue default",
          q["term_months"] == 13, q["term_months"])
    check("all 13 months are required - no free month",
          q["payments_required"] == 13 and q["has_free_month"] is False)
    check("recurring contract value is rate x term",
          q["recurring_contract_value"] == 48750.0, q["recurring_contract_value"])
    check("A CUSTOM RATE MANUFACTURES NO SAVING - there is no price to beat",
          q["savings_per_month"] is None, q["savings_per_month"])
    check("the quote reports the basis for the document",
          q["custom_basis"] == pp.custom_basis(c))

    # The catalogue must stay untouched. The whole reason this lives on the deal
    # is that editing the package would move the number for every other deal.
    check("THE CATALOGUE IS UNCHANGED BY A DEAL'S RATE",
          getattr(custom_pkg, "monthly_price", None) is None,
          getattr(custom_pkg, "monthly_price", None))

    # A deal can be agreed before its economics are. The document must then say
    # nothing about money — not "$0", not "TBD" beside a dollar sign.
    section("A PROPOSAL MAY DELIBERATELY QUOTE NOTHING")

    class _Prop(object):
        def __init__(self, withhold=False, final=None, pkgid=None):
            self.withhold_pricing = withhold
            self.final_amount = final
            self.package_id = pkgid
            self.currency = "USD"
            self.contract_term_months = None
            self.billing_option = None
            self.opportunity_id = None
            self.custom_unit_price = None
            self.custom_unit_label = None
            self.custom_min_units = None
            self.custom_term_months = None

    from app.services import proposal_service as _ps
    check("withholding removes the Investment section entirely",
          _ps._investment_markdown(None, _Prop(True, 7500, "pkg-multi")) is None)
    check("...and NOTHING is rendered in its place",
          not (_ps._investment_markdown(None, _Prop(True, 7500, "pkg-multi")) or ""))
    check("an unpriced proposal is not a zero-priced one",
          _ps._investment_markdown(None, _Prop(False, None, None)) is None)

    opts = pp.options_for(custom_pkg, None, c)
    check("a custom agreement offers ONE option, not a false choice",
          len(opts) == 1, len(opts))
    check("...and that option is the agreement itself",
          opts[0]["billing_option"] == pp.BILLING_TERM_AGREEMENT)

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
    else:
        print("\nTHE LOWER RATE IS EARNED, NEVER THE DEFAULT - and all 13 months bill.")
    print("=" * 78)
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
