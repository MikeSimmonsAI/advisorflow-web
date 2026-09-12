"""
THE EXPERIENCE PASS — one severity vocabulary, owner-facing words, and an
attention panel that can only report conditions that actually exist.

WHAT THIS FILE DEFENDS
======================

  1. FIVE STATES, NOT THREE. "We cannot see this" and "nothing has happened
     here" are different from each other and neither is healthy. Every test
     below that touches severity exists because collapsing those two into
     green is how a status board starts lying.

  2. AN UNKNOWN NEVER RESOLVES TO HEALTHY. A typo, an older API, a status
     string nobody mapped — all of them land on "can't check". A green tile is
     something the system has to earn.

  3. THE ATTENTION PANEL IS COUNTED, NOT COMPOSED. Every item corresponds to
     rows in the ledger. Give it a clean ledger and it must return nothing;
     create one real condition and exactly that item must appear.

  4. NO DEAD BUTTONS. Every `action_route` an attention item offers is asserted
     against the routes actually registered in App.jsx. A button that lands on
     the /god/* catch-all renders the Command Center and looks like the click
     did nothing, which is worse than no button — this is the same class of
     defect as the missing /god/customer-app route.

  5. THE OWNER'S WORDS, NOT OURS. Platform Health used to answer "no source"
     over "needs: invoices + payments tables". Both true; neither says anything
     about his business. The prose assertions here are what stops that
     vocabulary coming back.

  6. THE TWO COPIES OF THE VOCABULARY CANNOT DRIFT. severity.py is
     authoritative and severity.js mirrors it for surfaces whose backend still
     answers in booleans. A test compares them literally.
"""

import itertools
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.compensation_models import (BASIS_FIXED, COMP_EARNED, COMP_VOID,
                                            PAYEE_OVERRIDE, PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPackageCap,
                                            CompensationPlan, CompensationRule)
from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity)
from app.services import compensation as comp
from app.services import compensation_ledger as ledger
from app.services import severity as sev
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(9000)

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


# ═════════════════════════════════════════════════════════════════════════════
# fixtures — one brand, configured exactly like the worked EvoSys example
# ═════════════════════════════════════════════════════════════════════════════

def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="sev%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _brand(db, label="Sev", *, seller=Decimal("500.00"),
           override=Decimal("100.00"), cap=None, holdback=14, with_plan=True):
    plat = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(plat); db.commit()
    org = BrandSalesOrg(platform_id=plat.id, name=label + " Sales",
                        slug="%s-s-%d" % (label.lower(), next(_SEQ)))
    db.add(org); db.commit()
    pkg = BrandPackage(platform_id=plat.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    db.add(pkg); db.commit()

    manager = _user(db, label + " Manager")
    db.add(Membership(user_id=manager.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()
    rep = _user(db, label + " Rep")
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_REP, is_active=True,
                      reports_to_user_id=manager.id))
    db.commit()

    # NO PLAN AT ALL is what `compute()` calls unconfigured — see its early
    # return. A plan that exists but prices nothing is a different (and
    # legitimate) thing: it projects zero, which the engine reports as a real
    # projection rather than a gap. The attention item is about the first case.
    if not with_plan:
        return dict(platform=plat, org=org, pkg=pkg, manager=manager, rep=rep,
                    plan=None)

    plan = CompensationPlan(brand_sales_org_id=org.id, name=label + " Plan",
                            effective_from=date(2026, 1, 1),
                            holdback_days=holdback, max_override_levels=1,
                            is_active=True)
    db.add(plan); db.commit()
    if seller is not None:
        db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                                payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                                amount=seller, sort_order=1))
    if override is not None:
        db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                                payee_kind=PAYEE_OVERRIDE, override_level=1,
                                basis=BASIS_FIXED, amount=override, sort_order=2))
    if cap is not None:
        db.add(CompensationPackageCap(plan_id=plan.id, package_id=pkg.id,
                                      max_total_payout=cap))
    db.commit()
    return dict(platform=plat, org=org, pkg=pkg, manager=manager, rep=rep,
                plan=plan)


@pytest.fixture()
def brand(db_session):
    return _brand(db_session)


@pytest.fixture()
def god(db_session):
    return _user(db_session, "Owner", role="god_admin")


def _won(db, b, *, owner=None, status="won"):
    o = Opportunity(brand_sales_org_id=b["org"].id,
                    owner_user_id=(owner or b["rep"]).id,
                    company_name="Deal %d" % next(_SEQ),
                    selected_package_id=b["pkg"].id,
                    stage="closing", status=status,
                    billing_option="term_agreement", contract_term_months=13)
    db.add(o); db.commit()
    return o


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _keys(result):
    return {i["key"] for i in result["items"]}


def _item(result, key):
    for i in result["items"]:
        if i["key"] == key:
            return i
    raise AssertionError("no %r item in %s" % (key, sorted(_keys(result))))


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE VOCABULARY ITSELF
# ═════════════════════════════════════════════════════════════════════════════

def test_there_are_exactly_five_states_and_every_one_has_words():
    assert len(sev.ALL) == 5
    for s in sev.ALL:
        assert sev.LABELS[s] and sev.MEANINGS[s]


def test_every_legacy_status_maps_somewhere_deliberate():
    assert sev.normalize("ok") == sev.HEALTHY
    assert sev.normalize("warn") == sev.ATTENTION
    assert sev.normalize("bad") == sev.ACTION_REQUIRED
    # THE TWO THAT MATTER. "off" means nothing has happened; "no_source" means
    # we cannot see it. Collapsing either into the other loses the distinction
    # this vocabulary exists for.
    assert sev.normalize("off") == sev.NO_DATA
    assert sev.normalize("no_source") == sev.UNAVAILABLE


def test_an_unknown_status_can_never_paint_something_green():
    for junk in ("fine", "OKAY", "", None, "healthy-ish", "green"):
        assert sev.normalize(junk) != sev.HEALTHY


def test_cannot_check_ranks_worse_than_nothing_yet():
    """Not knowing is worse than knowing there is nothing."""
    assert sev.rank(sev.UNAVAILABLE) < sev.rank(sev.NO_DATA)
    assert sev.rank(sev.ACTION_REQUIRED) < sev.rank(sev.ATTENTION)
    assert sev.rank(sev.HEALTHY) == max(sev.rank(s) for s in sev.ALL)


def test_one_unseeable_subsystem_stops_the_page_reporting_healthy():
    assert sev.worst(["ok", "ok", "no_source"]) == sev.UNAVAILABLE
    assert sev.worst(["ok", "ok", "off"]) == sev.NO_DATA
    assert sev.worst(["ok", "ok"]) == sev.HEALTHY


def test_an_empty_set_is_nothing_yet_not_healthy():
    assert sev.worst([]) == sev.NO_DATA
    assert sev.summarize([])["overall"] == sev.NO_DATA


def test_summarize_always_reports_every_key():
    s = sev.summarize([{"severity": "ok"}, {"severity": "bad"}])
    assert set(s["counts"]) == set(sev.ALL)
    assert s["counts"][sev.ACTION_REQUIRED] == 1
    assert s["overall"] == sev.ACTION_REQUIRED
    assert s["total"] == 2


# ═════════════════════════════════════════════════════════════════════════════
# 2. THE TWO COPIES CANNOT DRIFT
# ═════════════════════════════════════════════════════════════════════════════

def test_the_browser_copy_of_the_vocabulary_matches_the_server_exactly():
    """severity.js exists for surfaces whose backend still answers in booleans.

    It is a MIRROR, not a second opinion. If the two ever disagree, an advisor's
    page and the owner's page describe the same condition with different words,
    which is precisely the drift the server module was written to end.
    """
    js = (FRONTEND / "severity.js").read_text(encoding="utf-8")
    for key in sev.ALL:
        # each constant is exported with the server's own string value
        assert "'%s'" % key in js, "severity.js is missing the %r value" % key
        # and carries the identical owner-facing label
        assert sev.LABELS[key] in js, (
            "severity.js label for %r does not match the server's %r"
            % (key, sev.LABELS[key]))


# ═════════════════════════════════════════════════════════════════════════════
# 3. ATTENTION IS COUNTED FROM REAL ROWS
# ═════════════════════════════════════════════════════════════════════════════

def test_a_clean_ledger_reports_nothing_outstanding(db_session, brand):
    """The panel must be able to say there is nothing to do.

    A finance screen that only ever surfaces problems trains its reader to
    assume something is always wrong; one that can report all-clear is worth
    believing when it does raise something.
    """
    comp.earn(db_session, _won(db_session, brand), collection_reference="clean-1",
              collected_amount=Decimal("1497.00"))
    # give the entries a provisioned customer's absence no chance to fire by
    # checking only for the conditions this test is about
    res = ledger.attention(db_session, brand["manager"],
                           brand_sales_org_id=brand["org"].id, can_settle=True)
    assert "unconfigured_commission" not in _keys(res)
    assert "capped" not in _keys(res)
    assert "voided" not in _keys(res)
    assert "payable_without_authority" not in _keys(res)


def test_an_unrated_package_is_action_required_not_a_quiet_zero(db_session):
    """A deal excluded from projected must announce itself.

    Unconfigured deals are held OUT of the projected figure rather than counted
    as $0 — which is correct, and completely silent. Without this item the only
    symptom is a forecast that looks smaller than it should.
    """
    b = _brand(db_session, "NoRate", with_plan=False)
    _won(db_session, b, status="open")
    res = ledger.attention(db_session, b["manager"],
                           brand_sales_org_id=b["org"].id, can_settle=True)
    it = _item(res, "unconfigured_commission")
    assert it["severity"] == sev.ACTION_REQUIRED
    assert it["count"] == 1
    assert res["overall"] == sev.ACTION_REQUIRED


def test_a_cap_that_reduced_somebody_s_commission_is_surfaced(db_session):
    """Real money a seller did not get, decided by a rule rather than a person."""
    b = _brand(db_session, "Capped", seller=Decimal("500.00"),
               override=Decimal("100.00"), cap=Decimal("400.00"))
    comp.earn(db_session, _won(db_session, b), collection_reference="cap-1",
              collected_amount=Decimal("1497.00"))
    res = ledger.attention(db_session, b["manager"],
                           brand_sales_org_id=b["org"].id, can_settle=True)
    it = _item(res, "capped")
    assert it["severity"] == sev.ATTENTION
    assert it["count"] >= 1
    # the DETAIL names what was withheld, not just that a cap applied
    assert "$" in it["detail"]


def test_an_entry_whose_plan_was_deleted_is_surfaced_but_not_recalculated(
        db_session, brand):
    """The amount is safe; the audit trail is what degraded.

    Each entry stores its own rate snapshot, so removing a plan cannot change
    what anybody is paid. What it does break is the ledger's ability to name
    the rule — the question an audit asks years later.
    """
    made = comp.earn(db_session, _won(db_session, brand),
                     collection_reference="orphan-1",
                     collected_amount=Decimal("1497.00"))
    before = [e.amount for e in made]
    db_session.query(CompensationPlan).filter(
        CompensationPlan.id == brand["plan"].id).delete()
    db_session.commit()

    res = ledger.attention(db_session, brand["manager"],
                           brand_sales_org_id=brand["org"].id, can_settle=True)
    it = _item(res, "plan_snapshot_missing")
    assert it["severity"] == sev.ATTENTION
    # NOT recalculated, not voided, not changed.
    rows = db_session.query(CompensationEntry).all()
    assert sorted(r.amount for r in rows) == sorted(before)


def test_a_voided_entry_is_reported_and_excluded_from_the_totals(
        db_session, brand):
    made = comp.earn(db_session, _won(db_session, brand),
                     collection_reference="void-1",
                     collected_amount=Decimal("1497.00"))
    made[0].state = COMP_VOID
    db_session.commit()

    res = ledger.attention(db_session, brand["manager"],
                           brand_sales_org_id=brand["org"].id, can_settle=True)
    assert _item(res, "voided")["count"] == 1

    s = ledger.summary(db_session, brand["manager"],
                       brand_sales_org_id=brand["org"].id)
    # earned is every row that became money EXCEPT the reversed one
    assert s["earned_count"] == len(made) - 1


def test_commission_on_an_unprovisioned_deal_is_flagged(db_session, brand):
    """Legitimate — payment can land before the workspace is built — but a
    payable entry with no customer behind it is worth seeing before it is paid.
    """
    comp.earn(db_session, _won(db_session, brand),
              collection_reference="unprov-1",
              collected_amount=Decimal("1497.00"))
    res = ledger.attention(db_session, brand["manager"],
                           brand_sales_org_id=brand["org"].id, can_settle=True)
    it = _item(res, "unprovisioned_customer")
    assert it["severity"] == sev.ATTENTION
    # NO ACTION BUTTON: provisioning is per-deal and there is no list page to
    # send anyone to. A button landing on the wrong screen is worse than none.
    assert it["action_route"] is None


# ═════════════════════════════════════════════════════════════════════════════
# 4. AUTHORITY BOUNDARIES
# ═════════════════════════════════════════════════════════════════════════════

def test_a_viewer_without_settlement_authority_is_told_why_there_is_no_button(
        db_session, brand):
    """Not a fault — the capability model working — but it has to be said.

    Somebody looking at a payable total with no way to release it needs to be
    told the reason, or they conclude the screen is broken.
    """
    e = comp.earn(db_session, _won(db_session, brand),
                  collection_reference="auth-1",
                  collected_amount=Decimal("1497.00"))[0]
    e.payable_at = datetime.utcnow() - timedelta(days=1)   # holdback elapsed
    db_session.commit()

    denied = ledger.attention(db_session, brand["manager"],
                              brand_sales_org_id=brand["org"].id,
                              can_settle=False)
    assert "payable_without_authority" in _keys(denied)

    allowed = ledger.attention(db_session, brand["manager"],
                               brand_sales_org_id=brand["org"].id,
                               can_settle=True)
    assert "payable_without_authority" not in _keys(allowed)


def test_attention_is_brand_scoped_like_every_other_read(db_session):
    """One brand's problem must never appear on another brand's panel."""
    alpha = _brand(db_session, "AttnA", with_plan=False)
    beta = _brand(db_session, "AttnB")
    _won(db_session, alpha, status="open")          # alpha has the unrated deal

    a_view = ledger.attention(db_session, alpha["manager"],
                              brand_sales_org_id=alpha["org"].id)
    b_view = ledger.attention(db_session, beta["manager"],
                              brand_sales_org_id=beta["org"].id)
    assert "unconfigured_commission" in _keys(a_view)
    assert "unconfigured_commission" not in _keys(b_view)


def test_a_rep_is_never_told_they_lack_settlement_authority(
        client, db_session, brand):
    """/me deliberately does not pass `can_settle`.

    A salesperson has no settlement authority by design. Telling them they
    cannot settle is noise about a power they were never meant to have, and it
    would make the rep screen read like a permissions failure.
    """
    e = comp.earn(db_session, _won(db_session, brand),
                  collection_reference="me-1",
                  collected_amount=Decimal("1497.00"))[0]
    e.payable_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()

    r = client.get("/sales/compensation/me", headers=_h(db_session, brand["rep"]))
    assert r.status_code == 200
    keys = {i["key"] for i in r.json()["attention"]["items"]}
    assert "payable_without_authority" not in keys


def test_a_rep_reading_their_own_attention_sees_only_their_own_rows(
        client, db_session, brand):
    """The manager's override entry must not surface on the rep's panel."""
    comp.earn(db_session, _won(db_session, brand),
              collection_reference="scope-1",
              collected_amount=Decimal("1497.00"))
    r = client.get("/sales/compensation/me", headers=_h(db_session, brand["rep"]))
    body = r.json()
    unprov = [i for i in body["attention"]["items"]
              if i["key"] == "unprovisioned_customer"]
    if unprov:
        # the rep owns exactly one of the two entries on that deal
        assert unprov[0]["count"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# 5. NO DEAD BUTTONS
# ═════════════════════════════════════════════════════════════════════════════

def _registered_routes():
    """Every path App.jsx actually registers."""
    app = (FRONTEND / "App.jsx").read_text(encoding="utf-8")
    return set(re.findall(r'path="([^"]+)"', app))


def test_every_attention_action_route_is_a_route_that_exists(db_session):
    """THE SAME DEFECT CLASS AS THE MISSING /god/customer-app ROUTE.

    `/god/pricing-compensation` is a perfectly plausible name for the pricing
    screen and is not registered — it would fall through to the /god/* catch-all
    and silently render the Command Center, so the button would look broken
    rather than error. Rather than trusting a string, every route an attention
    item can offer is checked against App.jsx.
    """
    routes = _registered_routes()
    b = _brand(db_session, "Routes", with_plan=False)
    _won(db_session, b, status="open")
    res = ledger.attention(db_session, b["manager"],
                           brand_sales_org_id=b["org"].id, can_settle=True)
    offered = [i["action_route"] for i in res["items"] if i["action_route"]]
    assert offered, "the unrated-package item should offer a way to fix it"
    for route in offered:
        assert route in routes, (
            "attention item points at %r, which App.jsx does not register" % route)


def test_every_attention_view_is_a_real_ledger_view(db_session, brand):
    """`view` is what makes an item provable: it names the exact bucket the
    count came from. A view the ledger does not accept would 400 on click."""
    comp.earn(db_session, _won(db_session, brand),
              collection_reference="views-1",
              collected_amount=Decimal("1497.00"))
    res = ledger.attention(db_session, brand["manager"],
                           brand_sales_org_id=brand["org"].id, can_settle=True)
    for i in res["items"]:
        if i["view"] is not None:
            assert i["view"] in ledger.VIEW_LABELS


# ═════════════════════════════════════════════════════════════════════════════
# 6. THE ENDPOINTS CARRY IT
# ═════════════════════════════════════════════════════════════════════════════

def test_the_command_centre_serves_the_severity_legend(
        client, db_session, brand, god):
    """Served, not retyped in React. One change to the vocabulary must not
    ship to some surfaces and not others.

    `brand` is requested so a brand exists for the owner's scope to resolve to;
    a god with no brand sales org anywhere is correctly refused, and that
    refusal is asserted elsewhere rather than being worked around here.
    """
    r = client.get("/sales/compensation/overview", headers=_h(db_session, god))
    assert r.status_code == 200
    legend = r.json()["vocabulary"]["severity"]
    assert {x["key"] for x in legend} == set(sev.ALL)
    for x in legend:
        assert x["label"] == sev.LABELS[x["key"]]


def test_the_overview_and_me_both_carry_an_attention_block(
        client, db_session, brand, god):
    for url, who in (("/sales/compensation/overview", god),
                     ("/sales/compensation/me", brand["rep"])):
        body = client.get(url, headers=_h(db_session, who)).json()
        assert "attention" in body, url
        assert "items" in body["attention"]
        assert body["attention"]["overall"] in sev.ALL


# ═════════════════════════════════════════════════════════════════════════════
# 7. PLATFORM HEALTH SPEAKS TO THE OWNER
# ═════════════════════════════════════════════════════════════════════════════

def test_platform_health_gives_every_section_a_canonical_severity(
        client, db_session, god):
    body = client.get("/god/platform-health", headers=_h(db_session, god)).json()
    assert body["sections"]
    for s in body["sections"]:
        assert s["severity"] in sev.ALL, s["key"]
        assert s["severity_label"] == sev.LABELS[s["severity"]]


def test_a_subsystem_we_cannot_see_is_never_reported_as_healthy(
        client, db_session, god):
    """Background jobs leave no durable record. That is not a clean bill of
    health, and the endpoint must not round it up to one."""
    body = client.get("/god/platform-health", headers=_h(db_session, god)).json()
    jobs = [s for s in body["sections"] if s["key"] == "jobs"][0]
    assert jobs["severity"] == sev.UNAVAILABLE
    assert body["overall"]["overall"] != sev.HEALTHY


def test_platform_health_does_not_answer_in_engineering_vocabulary(
        client, db_session, god):
    """THE COPY THIS PASS EXISTS TO REMOVE.

    "no source" and "needs: invoices + payments tables" were both true and told
    the owner nothing about his business — one does not say whether the gap is
    in his setup or our code, the other is a ticket in our repo. Prose he reads
    must not contain either.

    The raw diagnostic still has a home: `technical`, which is rendered small
    and last, and is deliberately excluded from this assertion.
    """
    body = client.get("/god/platform-health", headers=_h(db_session, god)).json()
    banned = ("no source", "table", "webhook", "endpoint", "api", "query failed",
              "null", "column")
    for s in body["sections"]:
        prose = " ".join(str(s.get(f) or "")
                         for f in ("headline", "detail", "needs")).lower()
        for word in banned:
            assert word not in prose, (
                "%s still speaks to us rather than to the owner: %r contains %r"
                % (s["key"], prose, word))


def test_platform_health_says_what_would_have_to_change_in_plain_words(
        client, db_session, god):
    """A section that cannot report must name an OUTCOME, not a schema object."""
    body = client.get("/god/platform-health", headers=_h(db_session, god)).json()
    jobs = [s for s in body["sections"] if s["key"] == "jobs"][0]
    assert jobs["needs"]
    assert "record of each scheduled run" in jobs["needs"]


def test_platform_health_ships_the_legend_it_is_rendered_with(
        client, db_session, god):
    body = client.get("/god/platform-health", headers=_h(db_session, god)).json()
    assert {x["key"] for x in body["legend"]} == set(sev.ALL)


# ═════════════════════════════════════════════════════════════════════════════
# 8. THE SCREENS THEMSELVES
# ═════════════════════════════════════════════════════════════════════════════

def _src(rel):
    return (FRONTEND / rel).read_text(encoding="utf-8")


def _stripped(text):
    """Source with its comments removed.

    EVERY ASSERTION BELOW THAT LOOKS FOR THE *ABSENCE* OF SOMETHING HAS TO RUN
    ON THIS. The explanations in this codebase quote the exact strings they
    replaced — "no source", "needs: invoices + payments tables",
    "3/4 integrations connected" — precisely so a future reader knows what the
    change was for. Matching raw text would fail against the comment describing
    the fix, and the obvious "fix" is to delete the explanation, which is the
    wrong trade every time.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


#: The dark token block for the Sales Workspace. Matched by SHAPE, not by one
#: exact selector string, because the selector legitimately grows qualifiers:
#: the God-Mode-light work narrowed it to `:not(.gm-shell *)` so the workspace
#: palette stops at the God shell's own border instead of repainting it. These
#: two tests asserted the literal `[data-appearance="dark"] .sw-scope{` and so
#: went red on main the day that qualifier landed — guarding a spelling rather
#: than the invariant. The invariant is the one below: a dark block exists on
#: .sw-scope, and the palette inside it stacks.
_SW_DARK_BLOCK = re.compile(
    r'\[data-appearance="dark"\]\s*\.sw-scope(?::not\([^)]*\)|[^\s{,])*\s*\{([^}]*)\}'
)


def _sw_dark_tokens(css):
    """The body of the Sales Workspace dark token block."""
    for body in _SW_DARK_BLOCK.findall(css):
        if "--sw-surface:" in body:
            return body
    raise AssertionError(
        "no [data-appearance=\"dark\"] .sw-scope block declaring --sw-surface"
    )


def test_the_sales_workspace_palette_follows_the_appearance_axis():
    """THE ROOT CAUSE OF THE WHITE RECTANGLES.

    The sheet was a hard-coded light theme. Correct while it only rendered on
    its own page; wrong the moment God Mode embedded the Command Center in a
    dark shell. Tokens plus a dark block is what fixes it — not a lighter
    background on one screen.
    """
    css = _src("pages/sales/SalesStyles.jsx")
    assert _sw_dark_tokens(css)
    assert "--sw-surface:" in css and "--sw-canvas:" in css


def test_no_card_in_the_sales_workspace_is_painted_white_by_a_literal():
    """A single `background:#fff` left behind is a white slab on a dark page."""
    css = _stripped(_src("pages/sales/SalesStyles.jsx"))
    assert "background:#fff" not in css
    # `#fff\b` — the SHORTHAND white, deliberately not matching the six-digit
    # `#ffffff` in the light token block, which is where white is now allowed
    # to be declared exactly once per role.
    bare_white = re.findall(r"#fff\b", css)
    # the two survivors are LABELS on a teal button, which must stay white in
    # both appearances because they sit on teal, not on the page.
    assert len(bare_white) == 2, css.count("#fff")


def test_the_dark_palette_is_designed_rather_than_inverted():
    """Surfaces must STACK. If the card is not lighter than the canvas the page
    reads as one flat sheet, which is what "just make it dark" produces."""
    css = _src("pages/sales/SalesStyles.jsx")
    dark = _sw_dark_tokens(css)
    def val(name):
        return re.search(r"--sw-%s:(#[0-9a-f]{6})" % name, dark).group(1)
    def lum(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    assert lum(val("surface")) > lum(val("canvas"))
    assert lum(val("surface3")) > lum(val("surface"))
    # a field is RECESSED — below the card it sits on
    assert lum(val("field")) < lum(val("surface"))
    # and the ink has to be legible on all of it
    assert lum(val("ink")) > 200


def test_the_command_centre_separates_the_forecast_from_the_liabilities():
    src = _src("pages/sales/CompensationCommand.jsx")
    assert "sw-cc-forecast" in src
    assert "forecast" in src            # the tile is marked as one
    assert "owed to nobody" in src


def test_the_command_centre_renders_the_attention_panel_and_its_all_clear():
    src = _src("pages/sales/CompensationCommand.jsx")
    assert "ATTENTION REQUIRED" in src
    assert "attention" in src
    assert "Nothing outstanding" in src


def test_the_command_centre_still_proves_its_own_totals():
    """The reconciliation promise, asserted in the UI as well as the API."""
    src = _src("pages/sales/CompensationCommand.jsx")
    assert "sw-proof" in src
    assert "rows?.total" in src


def test_both_compensation_screens_use_one_visual_system():
    """A rep and their manager reading the same five figures should not have to
    translate between two layouts to agree on a number."""
    mine = _src("pages/sales/MyCompensation.jsx")
    theirs = _src("pages/sales/CompensationCommand.jsx")
    for token in ("sw-tiles", "sw-tile-value", "sw-cc-forecast", "sw-becoming"):
        assert token in mine, token
        assert token in theirs, token


def test_my_compensation_offers_no_administrative_control():
    """A rep can see what they are owed. They cannot change what anyone earns."""
    src = _stripped(_src("pages/sales/MyCompensation.jsx"))
    for forbidden in ("/pay", "promote-due", "Settle", "payables"):
        assert forbidden not in src, forbidden


def test_platform_health_no_longer_renders_our_backlog():
    src = _stripped(_src("pages/god/PlatformHealth.jsx"))
    assert "NoSource" not in src
    assert "needs: " not in src
    assert "severity" in src


def test_system_health_uses_the_shared_vocabulary_not_a_boolean():
    raw = _src("pages/SystemHealth.jsx")
    src = _stripped(raw)
    assert "from '../severity'" in raw
    # the old headline was a ratio of connected integrations; the new one is a
    # verdict, because "3/4" is arithmetic and not an answer
    assert "integrations connected" not in src
    assert "All clear" in src


def test_system_health_states_what_an_unmeasured_scheduler_does_not_mean():
    """A blank timestamp with a footnote reads as a broken field. It has to say
    what it is: we cannot check this, and that is not a report of failure."""
    src = _src("pages/SystemHealth.jsx")
    assert "not a report that" in src.lower()
    assert "UNAVAILABLE" in src
