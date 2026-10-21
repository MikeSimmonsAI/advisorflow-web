# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST P3 (#272): BookaBoost Wholesale versus EvoSys Wholesale,
attacked both ways, configured exactly like production on 2026-10-21:

  * EvoSys's Wholesale org (built-in host slug), its named-seat Wholesale login,
    the named-login lock ON;
  * "BookaBoost Wholesale" on the BookaBoost brand with the provisioned feature
    list (+ `wholesale_workspace`), entered by its org admin WITHOUT a seat
    (owner rule: GOD + org admin or above);
  * an EvoSys operator (home org empty, EvoSys membership + named seat).

The victim creates one of EVERY Wholesale/EvoSense object (the shared fixture
from test_wholesale_cross_tenant), then the attacker calls every id-bearing
route with the victim's ids - plain, and again with a FORGED X-Workspace-Id
naming the victim's workspace. Not one 2xx is allowed.

Then the parts that file never covered: list/report/search screens must not
carry a victim marker, imports (intake batches) by id, background sweeps, a
real share-link revoke, and team changes in one tenant must not change access
in the other. All records synthetic.
"""
import io
import itertools
import json

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

# The attack lists and the victim world are the existing suite's, unchanged -
# importing the FIXTURES (not the tests) so this file attacks the same surface.
import test_wholesale_cross_tenant as XT
from test_wholesale_cross_tenant import theirs, local_storage  # noqa: F401  (fixtures)

_SEQ = itertools.count(1)
EVO_SLUG = "evo-integrated-solutions-llc"
EVO_FEATURES = ["ai_assist", "audit_log", "availability", "booking", "branding_settings", "cadences",
                "calendar", "campaigns", "case_files", "compliance", "crm", "crm_connectors", "email",
                "imports", "lead_cleanup", "leads", "master_dashboard", "reports", "sms", "tier_config",
                "users", "voice", "wholesale_real_estate"]                      # production, 2026-10-21
BAB_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                "calendar", "availability", "booking", "wholesale_real_estate", "wholesale_workspace"]
# Strings only a victim's records contain (from the shared fixture + this file).
# (No bare "victim": a search screen echoes its own query back.)
VICTIM_MARKERS = ("1 Victim Ln", "Victim Capital", "Victim Funding", "Victim exception", "victim note",
                  "Victim purchase agreement", "victim's private reasons", "1 Victim Ave",
                  "1418 Cedar Springs", "7302 Ferguson", "9 Canvass Ct", "A's promise",
                  "buyer@example.com", "vf@example.com", "INTAKE-VICTIM-ROW", "77 Intake Victim Way")


def _platform(db, slug, name):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p is None:
        p = Platform(id="plt-%s" % slug, name=name, slug=slug, is_active=True)
        db.add(p)
        db.commit()
    return p


def _org(db, name, slug, feats, platform):
    o = Organization(name=name, slug=slug, plan="standard", industry="generic", is_active=True,
                     platform_id=platform.id, enabled_features=json.dumps(feats))
    db.add(o)
    db.commit()
    from app.services.tier_config_service import seed_default_tier_definitions
    seed_default_tier_definitions(db, o.id, industry="generic")
    return o


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0],
             role=role, password_hash=hash_password("Pass12345!"), is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role="org_admin"):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


@pytest.fixture()
def world(db_session):
    db = db_session
    evp, bbp = _platform(db, "evosyspro", "EvoSys Pro"), _platform(db, "bookaboost", "BookaBoost")
    evo = _org(db, "EVO Integrated Solutions LLC", EVO_SLUG, EVO_FEATURES, evp)
    bab = _org(db, "BookaBoost Wholesale", "bookaboost-wholesale", BAB_FEATURES, bbp)
    evo_user = _user(db, evo, "wholesale-%d@evo.test" % next(_SEQ))
    _m(db, evo_user, "customer_org", evo.id)
    _m(db, evo_user, "product", "wholesale", "member")                      # lock ON
    bab_admin = _user(db, bab, "admin-%d@bab.test" % next(_SEQ))
    _m(db, bab_admin, "customer_org", bab.id)                                # no seat: admin rule
    operator = _user(db, None, "operator-%d@evo.test" % next(_SEQ), role="advisor")
    _m(db, operator, "customer_org", evo.id)
    _m(db, operator, "product", "wholesale", "member")
    # Attackers NOT locked to Wholesale, so the general screens (intake, leads,
    # reports) answer them and their own org checks are what is tested.
    evo_plain = _user(db, evo, "evo-admin-%d@evo.test" % next(_SEQ))       # EvoSys admin, no seat
    _m(db, evo_plain, "customer_org", evo.id)
    bab_plain = _user(db, bab, "bab-advisor-%d@bab.test" % next(_SEQ), role="advisor")  # no seat
    _m(db, bab_plain, "customer_org", bab.id, role="advisor")
    return dict(db=db, evo=evo, bab=bab, evo_user=evo_user, bab_admin=bab_admin, operator=operator,
                evo_plain=evo_plain, bab_plain=bab_plain)


# ── Victim / attacker per scenario. The shared `theirs` fixture builds the
#    victim's world through these overridden names. ──────────────────────────

SCENARIOS = {
    # name: (victim org key, victim user key, attacker user key)
    "bookaboost_attacks_evosys": ("evo", "evo_user", "bab_admin"),
    "evosys_attacks_bookaboost": ("bab", "bab_admin", "evo_user"),
    "operator_attacks_bookaboost": ("bab", "bab_admin", "operator"),
}


@pytest.fixture(params=sorted(SCENARIOS))
def scenario(request, world):
    v_org, v_user, a_user = SCENARIOS[request.param]
    plain = world["bab_plain"] if v_org == "evo" else world["evo_plain"]
    return dict(name=request.param, victim_org=world[v_org], victim=world[v_user], attacker=world[a_user],
                plain_attacker=plain)


@pytest.fixture()
def sample_org(scenario):
    return scenario["victim_org"]


@pytest.fixture()
def sample_advisor(scenario):
    return scenario["victim"]


@pytest.fixture()
def auth_headers(db_session, scenario):
    return _h(db_session, scenario["victim"], ws=scenario["victim_org"].id)


def _attacker_headers(db, scenario, forged):
    return _h(db, scenario["attacker"], ws=scenario["victim_org"].id if forged else None)


# ── 1. Every id-bearing Wholesale / EvoSense route, both ways, plain + forged ──

@pytest.mark.parametrize("forged", [False, True], ids=["own_workspace", "forged_header"])
def test_no_route_answers_the_other_tenant(client, db_session, theirs, scenario, forged):
    h = _attacker_headers(db_session, scenario, forged)
    failures = []
    for method, path, payload in XT.attacks(theirs):
        kw = {"headers": h}
        if payload is not None:
            kw["json"] = payload
        r = getattr(client, method)(path, **kw)
        if r.status_code < 400:
            failures.append("%s %s -> %s %s" % (method.upper(), path, r.status_code, r.text[:160]))
    for path, (data, name, ctype), form, field in XT.upload_attacks(theirs):
        r = client.post(path, headers=h, files={field: (name, data, ctype)}, data=form)
        if r.status_code < 400:
            failures.append("UPLOAD %s -> %s %s" % (path, r.status_code, r.text[:160]))
    assert not failures, "[%s] CROSS-TENANT ACCESS:\n%s" % (scenario["name"], "\n".join(failures))


def test_victim_world_unchanged_after_attack(client, db_session, theirs, scenario, auth_headers):
    h = _attacker_headers(db_session, scenario, True)
    for method, path, payload in XT.attacks(theirs):
        kw = {"headers": h}
        if payload is not None:
            kw["json"] = payload
        getattr(client, method)(path, **kw)
    room = XT.ok(client.get("/wholesale/deals/%s" % theirs["deal_id"], headers=auth_headers))
    assert room["property"]["city"] == "Dallas"
    assert room["deal"]["stage"] not in ("dead", "closed")
    buyers = XT.ok(client.get("/wholesale/buyers", headers=auth_headers, params={"include_test": True}))
    assert [b["display_name"] for b in buyers["buyers"]] == ["Victim Capital"]


def test_a_real_share_link_cannot_be_revoked_by_the_other_tenant(client, db_session, theirs, scenario,
                                                                 auth_headers):
    """The shared suite attacks /share-links/{id}/revoke with a DEAL id; this
    uses a real link of the victim's. Proof the attack did nothing: the
    victim's own revoke afterwards still finds a live link to revoke."""
    r = client.post("/wholesale/deals/%s/share-links" % theirs["deal_id"], headers=auth_headers,
                    json={"audience": "buyer", "buyer_id": theirs["buyer_id"]})
    assert r.status_code in (200, 201), r.text[:200]
    link_id = XT.an_id(r.json(), "link", "share_link")
    for forged in (False, True):
        h = _attacker_headers(db_session, scenario, forged)
        assert client.post("/wholesale/share-links/%s/revoke" % link_id, headers=h).status_code >= 400
    from app.models.wholesale_share_models import WholesaleShareLink
    db_session.expire_all()
    link = db_session.query(WholesaleShareLink).filter(WholesaleShareLink.id == link_id).one()
    assert link.revoked_at is None


# ── 2. Lists, reports, dashboards, search: nothing of the victim's shows up ──

LISTING_ROUTES = [
    "/wholesale/dashboard", "/wholesale/properties", "/wholesale/deals", "/wholesale/approvals",
    "/wholesale/buyers?include_test=true", "/wholesale/command-center", "/wholesale/needs-you",
    "/wholesale/events", "/wholesale/operating-board", "/wholesale/settings/assignees",
    "/wholesale/exceptions", "/wholesale/exceptions/summary", "/wholesale/ops/callbacks",
    "/wholesale/appointments", "/wholesale/appointments/board", "/wholesale/appointments/reps",
    "/wholesale/appointments/offers/pending", "/wholesale/canvass/routes",
    "/wholesale/contract-templates", "/wholesale/documents/lifecycle", "/wholesale/funding/partners",
    "/wholesale/team", "/wholesale/sms/consents", "/wholesale/skip-trace/compare",
    "/wholesale/evosense/command-center", "/wholesale/evosense/inbox",
    "/wholesale/evosense/opportunity-queue", "/wholesale/evosense/funnel",
    "/wholesale/evosense/strategies", "/wholesale/evosense/identity-reviews",
    "/wholesale/evosense/permission-links", "/wholesale/evosense/events",
    "/wholesale/evosense/reprocess", "/wholesale/evosense/provider-evaluations",
    "/wholesale/evosense/ground-truth", "/wholesale/evosense/providers",
    "/wholesale/evosense/controls", "/intake/batches", "/intake/contacts", "/intake/contacts/summary",
    "/import-batches", "/leads/", "/notifications/", "/conversation-intel/queue",
    "/conversation-intel/search?q=Cedar",
]


@pytest.mark.parametrize("forged", [False, True], ids=["own_workspace", "forged_header"])
def test_no_list_report_or_search_screen_carries_the_victims_records(client, db_session, theirs,
                                                                      scenario, forged):
    leaks, answered = [], 0
    ws = scenario["victim_org"].id if forged else None
    for who in (scenario["attacker"], scenario["plain_attacker"]):
        h = _h(db_session, who, ws=ws)
        for path in LISTING_ROUTES:
            r = client.get(path, headers=h)
            if r.status_code != 200:
                continue                                # refused is fine; only answers can leak
            answered += 1
            hit = [m for m in VICTIM_MARKERS if m in r.text]
            if hit:
                leaks.append("%s as %s -> %s" % (path, who.email, hit))
    assert answered >= 8, "[%s] too few screens answered (%d) - the check would prove nothing" % (
        scenario["name"], answered)
    assert not leaks, "[%s] VICTIM DATA IN ATTACKER SCREENS:\n%s" % (scenario["name"], "\n".join(leaks))


# ── 3. Imports: the victim's intake batch and contacts, by id ────────────────

def test_import_batches_and_contacts_stay_in_their_tenant(client, db_session, scenario, auth_headers):
    """The Wholesale property import files its owners as ONE intake batch (the
    real path a Wholesale workspace uses; the general intake screens are locked
    away from it). Then every intake / import-batch / contact route is attacked
    by id - by the Wholesale-locked attacker AND by an unlocked admin/advisor of
    the other org, plain and with a forged workspace header."""
    from app.models.intake_models import OrgContact
    csv = ("Address,City,State,Zip,First Name,Last Name,Phone,Email\n"
           "77 Intake Victim Way,Dallas,TX,75201,INTAKE-VICTIM-ROW,Owner,2145550199,victim-row@example.com\n")
    r = client.post("/wholesale/properties/import?is_test=true", headers=auth_headers,
                    files={"file": ("victim.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert r.status_code == 200, r.text[:300]
    bid = r.json()["intake_batch_id"]
    assert bid, r.json()
    contact = db_session.query(OrgContact).filter(OrgContact.organization_id == scenario["victim_org"].id).first()
    assert contact is not None
    cid, row_id = contact.id, "row-probe"
    paths = [("get", "/intake/contacts/%s" % cid, None),
             ("post", "/intake/contacts/%s/promote" % cid, {}),
             ("delete", "/intake/contacts/%s" % cid, None),
             ("post", "/intake/contacts/bulk-delete", {"ids": [cid]}),
             ("post", "/intake/contacts/bulk-promote", {"ids": [cid]}),
             ("get", "/import-batches/%s" % bid, None), ("get", "/import-batches/%s/rows" % bid, None),
             ("post", "/import-batches/%s/ready" % bid, {}), ("post", "/import-batches/%s/commit" % bid, {}),
             ("post", "/import-batches/%s/archive" % bid, {}), ("delete", "/import-batches/%s" % bid, None),
             ("get", "/intake/batches/%s" % bid, None), ("get", "/intake/batches/%s/preview" % bid, None),
             ("get", "/intake/batches/%s/rows" % bid, None),
             ("get", "/intake/batches/%s/classification-values" % bid, None),
             ("get", "/intake/batches/%s/commit-preview" % bid, None),
             ("get", "/intake/batches/%s/rollback-plan" % bid, None),
             ("get", "/intake/batches/%s/audit" % bid, None),
             ("put", "/intake/batches/%s/mapping" % bid, {"mapping": {}}),
             ("post", "/intake/batches/%s/analyze" % bid, None),
             ("patch", "/intake/batches/%s/rows/%s" % (bid, row_id), {"values": {"first_name": "planted"}}),
             ("post", "/intake/batches/%s/rows/bulk" % bid, {"row_ids": [row_id], "action": "exclude"}),
             ("post", "/intake/batches/%s/commit" % bid, {"mode": "ready_only",
                                                          "confirm_organization_name": scenario["victim_org"].name}),
             ("post", "/intake/batches/%s/rollback" % bid, {}),
             ("post", "/intake/batches/%s/cancel" % bid, {})]
    bad = []
    for who in (scenario["attacker"], scenario["plain_attacker"]):
        for forged in (False, True):
            h = _h(db_session, who, ws=scenario["victim_org"].id if forged else None)
            for method, path, payload in paths:
                kw = {"headers": h}
                if payload is not None:
                    kw["json"] = payload
                rr = getattr(client, method)(path, **kw)
                if rr.status_code < 400 and "/bulk-" in path:
                    # Bulk routes answer per id. Denial is "not found / skipped,
                    # nothing done" - the other tenant's id simply is not there.
                    body = rr.json()
                    if not (body.get("deleted") or body.get("promoted") or body.get("deleted_ids")
                            or body.get("lead_ids")):
                        continue
                if rr.status_code < 400:
                    bad.append("%s %s as %s forged=%s -> %s %s" % (method.upper(), path, who.email, forged,
                                                                   rr.status_code, rr.text[:120]))
    assert not bad, "[%s] IMPORT DATA REACHABLE ACROSS TENANTS:\n%s" % (scenario["name"], "\n".join(bad))
    # The victim's batch and contact are still theirs and untouched.
    from app.models.import_models import ImportBatch
    db_session.expire_all()
    b = db_session.query(ImportBatch).filter(ImportBatch.id == bid).one()
    assert b.organization_id == scenario["victim_org"].id
    c = db_session.query(OrgContact).filter(OrgContact.id == cid).one()
    assert c.organization_id == scenario["victim_org"].id and (c.first_name or "") == "INTAKE-VICTIM-ROW"


# ── 4. Background work: sweeps write only into the org they read ────────────

def test_exception_sweep_keeps_each_tenants_work_in_its_own_org(client, db_session, theirs, scenario):
    from app.models.wholesale_models import WholesaleWorkException
    from app.services import wholesale_exceptions as WX
    victim_id = scenario["victim_org"].id
    before = {e.id: e.organization_id for e in db_session.query(WholesaleWorkException).all()}
    WX.sweep_all(db_session)
    db_session.expire_all()
    for e in db_session.query(WholesaleWorkException).all():
        if e.id in before:
            assert e.organization_id == before[e.id]           # nothing moved
            continue
        # Every new exception points at a subject in its own org.
        if e.subject_type == "property":
            from app.models.wholesale_models import WholesaleProperty
            p = db_session.query(WholesaleProperty).filter(WholesaleProperty.id == e.subject_id).first()
            if p is not None:
                assert p.organization_id == e.organization_id
        assert e.organization_id in (victim_id, scenario["attacker"].organization_id,
                                     *[o.id for o in db_session.query(Organization).all()])
        assert e.organization_id                                 # never an org-less row


# ── 5. Team changes in one tenant never change access in the other ───────────

def _wholesale_ok(client, db, user, org):
    return client.get("/wholesale/settings", headers=_h(db, user, ws=org.id)).status_code == 200


def test_bookaboost_team_changes_do_not_open_or_close_evosys(client, db_session, world):
    """A person whose home is BookaBoost Wholesale and who ALSO belongs to EvoSys
    Wholesale (as an advisor, no EvoSys seat). Adding them to the BookaBoost
    Wholesale team must open BookaBoost for them - and must NOT open EvoSys,
    whose rule is still named EvoSys seats only."""
    db, evo, bab = db_session, world["evo"], world["bab"]
    both = _user(db, bab, "both-%d@bab.test" % next(_SEQ), role="advisor")
    _m(db, both, "customer_org", bab.id, role="advisor")
    _m(db, both, "customer_org", evo.id, role="advisor")
    assert not _wholesale_ok(client, db, both, evo)
    admin_h = _h(db, world["bab_admin"], ws=bab.id)
    r = client.post("/wholesale/team/members", headers=admin_h,
                    json={"user_id": both.id, "role": "member"})
    assert r.status_code == 200, r.text[:300]
    assert _wholesale_ok(client, db, both, bab), "BookaBoost team member cannot open BookaBoost Wholesale"
    assert not _wholesale_ok(client, db, both, evo), "BookaBoost team grant opened EvoSys Wholesale"


def test_bookaboost_team_removal_does_not_touch_an_evosys_seat(client, db_session, world):
    """An EvoSys named-seat person who ALSO works in BookaBoost (home BookaBoost).
    Removing them from BookaBoost's team must not take EvoSys away, and setting
    a BookaBoost field role must not lock them down inside EvoSys."""
    db, evo, bab = db_session, world["evo"], world["bab"]
    p = _user(db, bab, "dual-%d@bab.test" % next(_SEQ), role="advisor")
    _m(db, p, "customer_org", bab.id, role="advisor")
    _m(db, p, "customer_org", evo.id, role="advisor")
    _m(db, p, "product", "wholesale", "member")                 # EvoSys seat, full access
    assert _wholesale_ok(client, db, p, evo)
    admin_h = _h(db, world["bab_admin"], ws=bab.id)
    assert client.post("/wholesale/team/members", headers=admin_h,
                       json={"user_id": p.id, "role": "canvasser"}).status_code == 200
    me = client.get("/wholesale/team/me", headers=_h(db, p, ws=evo.id)).json()
    assert (me.get("role") or "member") == "member", "BookaBoost field role leaked into EvoSys: %r" % me
    assert client.delete("/wholesale/team/members/%s" % p.id, headers=admin_h).status_code == 200
    assert _wholesale_ok(client, db, p, evo), "removing from BookaBoost's team closed EvoSys Wholesale"


def test_bookaboost_admin_cannot_touch_an_evosys_persons_team_row(client, db_session, world):
    db = db_session
    admin_h = _h(db, world["bab_admin"], ws=world["bab"].id)
    for who in (world["evo_user"], world["operator"]):
        assert client.post("/wholesale/team/members", headers=admin_h,
                           json={"user_id": who.id, "role": "canvasser"}).status_code >= 400
        assert client.delete("/wholesale/team/members/%s" % who.id, headers=admin_h).status_code >= 400
    assert _wholesale_ok(client, db, world["evo_user"], world["evo"])


def test_an_evosys_seat_never_opens_bookaboost(client, db_session, world):
    """Owner rule (#272): GOD + BookaBoost's org admins (+ its own team). An
    EvoSys named-seat person given an ordinary BookaBoost membership does not
    get BookaBoost Wholesale from their EvoSys seat."""
    db, bab = db_session, world["bab"]
    for who in (world["evo_user"], world["operator"]):
        _m(db, who, "customer_org", bab.id, role="advisor")
        assert not _wholesale_ok(client, db, who, bab), who.email
        c = client.get("/auth/my-contexts", headers=_h(db, who)).json()
        exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
        assert exp.get(bab.id) == "platform", exp
    assert _wholesale_ok(client, db, world["evo_user"], world["evo"])          # EvoSys unchanged


def test_bookaboost_team_seat_is_its_own_and_its_field_fence_stays_home(client, db_session, world):
    db, bab, evo = db_session, world["bab"], world["evo"]
    canv = _user(db, bab, "canvasser-%d@bab.test" % next(_SEQ), role="advisor")
    _m(db, canv, "customer_org", bab.id, role="advisor")
    admin_h = _h(db, world["bab_admin"], ws=bab.id)
    assert client.post("/wholesale/team/members", headers=admin_h,
                       json={"user_id": canv.id, "role": "canvasser"}).status_code == 200
    h = _h(db, canv, ws=bab.id)
    assert client.get("/wholesale/canvass/routes", headers=h).status_code == 200    # field kit works
    assert client.get("/wholesale/settings", headers=h).status_code == 403          # fenced in BookaBoost
    me = client.get("/wholesale/team/me", headers=h).json()
    assert me["role"] == "canvasser" and me["field"] is True
    team = client.get("/wholesale/team", headers=admin_h).json()
    assert [m["user_id"] for m in team["members"]] == [canv.id]
    # The seat row is BookaBoost's own and the platform lock list is untouched.
    from app.services import product_access as PA
    rows = db.query(Membership).filter(Membership.user_id == canv.id, Membership.scope_type == "product").all()
    assert [r.scope_id for r in rows] == ["wholesale:" + bab.id]
    assert canv.id not in {x["user_id"] for x in PA.holders(db)}
    # EvoSys's team page never lists BookaBoost's people.
    evo_admin_h = _h(db, world["evo_plain"], ws=evo.id)
    evo_team = client.get("/wholesale/team", headers=evo_admin_h)
    if evo_team.status_code == 200:
        assert canv.id not in json.dumps(evo_team.json())


# ── 6. Shared platform files never show a platform person to a tenant ──────

def test_shared_county_file_status_hides_who_uploaded_it_from_tenants(client, db_session, world, monkeypatch):
    from app.services.evosense import providers as PV
    real = PV.upload_status

    def with_upload(key):
        out = real(key)
        out["current"] = {"uploaded_at": "2026-10-20T12:00:00Z", "source_date": "2026-10-01",
                          "by": "platform-admin@evosys.test", "via": "upload", "size": 1234}
        return out
    monkeypatch.setattr(PV, "upload_status", with_upload)
    for who, org in ((world["bab_admin"], world["bab"]), (world["evo_user"], world["evo"])):
        r = client.get("/wholesale/evosense/sources/tad/upload", headers=_h(db_session, who, ws=org.id))
        assert r.status_code == 200, (who.email, r.status_code, r.text[:160])
        cur = r.json().get("current") or {}
        assert "by" not in cur and "platform-admin@evosys.test" not in r.text
        assert cur.get("uploaded_at") and cur.get("source_date")          # still useful to the tenant
    god = _user(db_session, None, "god-%d@evo.test" % next(_SEQ), role="god_admin")
    r = client.get("/wholesale/evosense/sources/tad/upload",
                   headers={**_h(db_session, god), "X-Org-Override": world["bab"].id})
    assert r.status_code == 200 and r.json()["current"]["by"] == "platform-admin@evosys.test"


# ── 7. Team page membership: BookaBoost by membership, EvoSys unchanged ─────

def test_bookaboost_team_page_includes_members_by_workspace_membership(client, db_session, world):
    """Someone GOD added to BookaBoost Wholesale who already had a login
    elsewhere (home org elsewhere, BookaBoost membership) can be listed and put
    on BookaBoost's team - and that changes nothing for them in EvoSys."""
    db, bab, evo = db_session, world["bab"], world["evo"]
    guest = _user(db, None, "guest-%d@bab.test" % next(_SEQ), role="advisor")
    _m(db, guest, "customer_org", bab.id, role="advisor")
    admin_h = _h(db, world["bab_admin"], ws=bab.id)
    t = client.get("/wholesale/team", headers=admin_h).json()
    assert guest.id in {p["user_id"] for p in t["not_on_team"]}
    assert client.post("/wholesale/team/members", headers=admin_h,
                       json={"user_id": guest.id, "role": "member"}).status_code == 200
    assert _wholesale_ok(client, db, guest, bab)
    assert not _wholesale_ok(client, db, guest, evo)
    # EvoSys's team page keeps the home-org rule: the operator (EvoSys member,
    # no home org) is still not listed there.
    evo_admin_h = _h(db, world["evo_plain"], ws=evo.id)
    r = client.get("/wholesale/team", headers=evo_admin_h)
    if r.status_code == 200:
        listed = {p["user_id"] for p in r.json()["members"] + r.json()["not_on_team"]}
        assert world["operator"].id not in listed
