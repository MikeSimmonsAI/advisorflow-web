"""GOD MODE -> MANAGE ACCESS. What this file defends, in order of how badly.

  1. A PERSON IS ONE IDENTITY. Correcting somebody's placement never deletes
     and recreates them: same user id, same login, same history, same
     attribution afterwards. This is the Christina case and it is the reason
     the surface exists.

  2. NOTHING BELOW GOD REACHES IT. Not an executive, not a sales manager, not
     an org admin, not a super_admin, not somebody holding Demo Suite access.
     There is one root authority and a provisioning screen must not become a
     second one.

  3. A PREVIEW WRITES NOTHING. The operator has to be able to ask the question
     as many times as they like before answering it.

  4. ADDITIONS HAPPEN BEFORE REMOVALS. A correction that fails halfway must
     leave the person holding what they held this morning.
"""

import itertools

import pytest

from app.models.models import AuditLogEntry, Organization, Platform, User
from app.models.sales_models import (ROLE_BRAND_EXECUTIVE, ROLE_SALES_MANAGER,
                                     ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     SCOPE_CUSTOMER_ORG, SCOPE_PLATFORM,
                                     BrandSalesOrg, Membership)
from app.services import capabilities
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, name="Person"):
    u = User(organization_id=org_id, email="u%d@example.com" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db_session):
    """Two brands, each with a sales organization and a customer.

    Two of everything on purpose: an isolation claim that is only ever tested
    against one brand is not tested at all.
    """
    evo = Platform(name="EvoSys Pro", slug="evo-%d" % next(_SEQ))
    boost = Platform(name="BookaBoost", slug="boost-%d" % next(_SEQ))
    db_session.add_all([evo, boost])
    db_session.commit()

    evo_sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Pro Sales",
                              slug="evo-s-%d" % next(_SEQ))
    boost_sales = BrandSalesOrg(platform_id=boost.id, name="BookaBoost Sales",
                                slug="boost-s-%d" % next(_SEQ))
    db_session.add_all([evo_sales, boost_sales])
    db_session.commit()

    evo_cust = Organization(name="Cordova Dental", slug="cordova-%d" % next(_SEQ),
                            plan="standard", platform_id=evo.id)
    boost_cust = Organization(name="Halverson Roofing",
                              slug="halverson-%d" % next(_SEQ),
                              plan="standard", platform_id=boost.id)
    db_session.add_all([evo_cust, boost_cust])
    db_session.commit()

    god = _user(db_session, role="god_admin", name="Owner")
    return dict(db=db_session, evo=evo, boost=boost, evo_sales=evo_sales,
                boost_sales=boost_sales, evo_cust=evo_cust,
                boost_cust=boost_cust, god=god)


def _christina(db, world):
    """Provisioned WRONG, exactly as the brief describes.

    EvoSys Pro, org admin, sitting inside the wrong customer organization —
    with a piece of history attached so the test can prove the history
    survives.
    """
    c = _user(db, role="org_admin", org_id=world["evo_cust"].id,
              name="Christina Torres")
    db.add(Membership(user_id=c.id, scope_type=SCOPE_CUSTOMER_ORG,
                      scope_id=world["evo_cust"].id, role="org_admin",
                      is_active=True))
    db.add(AuditLogEntry(organization_id=world["evo_cust"].id,
                         actor_user_id=c.id, action="lead_reassigned",
                         target_type="lead", target_id="lead-123"))
    db.commit()
    return c


# ═════════════════════════════════════════════════════════════════════════════
# 1. Nobody below god reaches this surface
# ═════════════════════════════════════════════════════════════════════════════

ROUTES = [
    ("get", "/god/access/directory", None),
    ("get", "/god/access/users/{uid}", None),
    ("post", "/god/access/users/{uid}/preview", {"operations": []}),
    ("post", "/god/access/users/{uid}/apply", {"operations": []}),
    ("get", "/god/access/users/{uid}/audit", None),
]


@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin"])
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_platform_roles_below_god_are_refused(client, db_session, world,
                                              role, method, path, body):
    actor = _user(db_session, role=role, org_id=world["evo_cust"].id)
    url = path.format(uid=actor.id)
    fn = getattr(client, method)
    r = fn(url, headers=_h(db_session, actor)) if body is None else \
        fn(url, json=body, headers=_h(db_session, actor))
    assert r.status_code == 403, (method, path, role, r.text)


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_sales_manager_is_refused(client, db_session, world, method, path, body):
    """Running a sales team is not root provisioning authority."""
    mgr = _user(db_session, role="advisor")
    db_session.add(Membership(user_id=mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=world["evo_sales"].id,
                              role=ROLE_SALES_MANAGER, is_active=True))
    db_session.commit()
    url = path.format(uid=mgr.id)
    fn = getattr(client, method)
    r = fn(url, headers=_h(db_session, mgr)) if body is None else \
        fn(url, json=body, headers=_h(db_session, mgr))
    assert r.status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_executive_is_refused(client, db_session, world, method, path, body):
    """The Executive Suite CONSUMES authority. It does not provision it."""
    ex = _user(db_session, role="advisor")
    db_session.add(Membership(user_id=ex.id, scope_type=SCOPE_PLATFORM,
                              scope_id=world["evo"].id,
                              role=ROLE_BRAND_EXECUTIVE, is_active=True))
    db_session.commit()
    url = path.format(uid=ex.id)
    fn = getattr(client, method)
    r = fn(url, headers=_h(db_session, ex)) if body is None else \
        fn(url, json=body, headers=_h(db_session, ex))
    assert r.status_code == 403


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_demo_entitlement_is_not_god(client, db_session, world, method, path,
                                     body):
    """Holding Demo Suite access grants nothing on the control plane."""
    presenter = _user(db_session, role="advisor")
    capabilities.set_platform_grants(db_session, presenter, world["evo"].id,
                                     "EvoSys Pro", world["god"],
                                     ["demo_suite", "demo_admin"], commit=True)
    url = path.format(uid=presenter.id)
    fn = getattr(client, method)
    r = fn(url, headers=_h(db_session, presenter)) if body is None else \
        fn(url, json=body, headers=_h(db_session, presenter))
    assert r.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# 2. The footprint reads as sentences, not as UUIDs
# ═════════════════════════════════════════════════════════════════════════════

def test_footprint_names_every_context(client, db_session, world):
    c = _christina(db_session, world)
    r = client.get("/god/access/users/%s" % c.id,
                   headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    fp = r.json()
    assert fp["identity"]["user_id"] == c.id
    assert fp["identity"]["email"] == c.email
    ws = [w for w in fp["workspaces"] if w["is_active"]]
    assert len(ws) == 1
    assert ws[0]["name"] == "Cordova Dental"
    assert ws[0]["role_label"] == "Workspace Admin"
    # The point of the screen: an operator reads this, not a join.
    assert any("Cordova Dental" in s for s in fp["summary"])


def test_footprint_shows_revoked_memberships_labelled(client, db_session, world):
    """'There is no membership' and 'somebody switched it off' are different
    diagnoses with different fixes."""
    c = _christina(db_session, world)
    m = (db_session.query(Membership)
         .filter(Membership.user_id == c.id).first())
    m.is_active = False
    db_session.commit()
    fp = client.get("/god/access/users/%s" % c.id,
                    headers=_h(db_session, world["god"])).json()
    assert fp["workspaces"][0]["state"] == "revoked"
    assert fp["workspaces"][0]["is_active"] is False


# ═════════════════════════════════════════════════════════════════════════════
# 3. Preview writes nothing, and says what is preserved
# ═════════════════════════════════════════════════════════════════════════════

def _correction_plan(world):
    return {"operations": [
        {"op": "move_membership",
         "from": {"scope_type": SCOPE_CUSTOMER_ORG,
                  "scope_id": world["evo_cust"].id},
         "to": {"scope_type": SCOPE_PLATFORM, "scope_id": world["boost"].id,
                "role": ROLE_BRAND_EXECUTIVE}},
        {"op": "add_membership", "scope_type": SCOPE_BRAND_SALES_ORG,
         "scope_id": world["boost_sales"].id, "role": ROLE_SALES_MANAGER},
        {"op": "grant_demo", "platform_id": world["boost"].id},
        {"op": "clear" if False else "set_home_organization",
         "organization_id": None},
    ]}


def test_preview_changes_nothing(client, db_session, world):
    c = _christina(db_session, world)
    before = (db_session.query(Membership)
              .filter(Membership.user_id == c.id).count())
    r = client.post("/god/access/users/%s/preview" % c.id,
                    json=_correction_plan(world),
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert (db_session.query(Membership)
            .filter(Membership.user_id == c.id).count()) == before
    assert db_session.query(User).filter(User.id == c.id).first().organization_id \
        == world["evo_cust"].id


def test_preview_states_what_is_preserved(client, db_session, world):
    c = _christina(db_session, world)
    p = client.post("/god/access/users/%s/preview" % c.id,
                    json=_correction_plan(world),
                    headers=_h(db_session, world["god"])).json()
    assert p["adding"] and p["removing"]
    joined = " ".join(p["preserved"]).lower()
    # These four sentences are what make the confirm button pressable.
    assert "login identity" in joined
    assert "historical activity" in joined
    assert "audit" in joined
    assert p["requires_confirmation"] is True


# ═════════════════════════════════════════════════════════════════════════════
# 4. THE CHRISTINA CASE — corrected without being recreated
# ═════════════════════════════════════════════════════════════════════════════

def test_wrong_placement_is_corrected_without_deleting_the_user(
        client, db_session, world):
    c = _christina(db_session, world)
    original_id, original_email = c.id, c.email

    r = client.post("/god/access/users/%s/apply" % c.id,
                    json=_correction_plan(world),
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    db_session.expire_all()

    # SAME IDENTITY.
    still = db_session.query(User).filter(User.id == original_id).first()
    assert still is not None
    assert still.email == original_email
    assert db_session.query(User).filter(User.email == original_email).count() == 1

    # SAME HISTORY.
    assert (db_session.query(AuditLogEntry)
            .filter(AuditLogEntry.actor_user_id == original_id,
                    AuditLogEntry.action == "lead_reassigned").count()) == 1

    # NEW AUTHORITY, in the right brand.
    fp = client.get("/god/access/users/%s" % original_id,
                    headers=_h(db_session, world["god"])).json()
    brands = [b for b in fp["brand_contexts"] if b["is_active"]]
    assert [b["name"] for b in brands] == ["BookaBoost"]
    back = [b for b in fp["back_office"] if b["is_active"]]
    assert [b["role_label"] for b in back] == ["Sales Manager"]
    assert [d["platform_name"] for d in fp["demo"]["brands"]] == ["BookaBoost"]

    # OLD AUTHORITY GONE — deactivated, not deleted.
    old = (db_session.query(Membership)
           .filter(Membership.user_id == original_id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG,
                   Membership.scope_id == world["evo_cust"].id).first())
    assert old is not None and old.is_active is False
    assert still.organization_id is None


def test_the_correction_is_audited_with_before_and_after(client, db_session,
                                                         world):
    c = _christina(db_session, world)
    client.post("/god/access/users/%s/apply" % c.id,
                json=_correction_plan(world),
                headers=_h(db_session, world["god"]))
    entry = (db_session.query(AuditLogEntry)
             .filter(AuditLogEntry.action == "access.manage",
                     AuditLogEntry.target_id == c.id).first())
    assert entry is not None
    assert entry.actor_user_id == world["god"].id
    assert entry.before_state and entry.after_state
    # No secrets in an audit trail, ever.
    blob = (entry.before_state + entry.after_state).lower()
    for forbidden in ("password", "token", "secret", "hash"):
        assert forbidden not in blob


def test_audit_route_returns_the_change(client, db_session, world):
    c = _christina(db_session, world)
    client.post("/god/access/users/%s/apply" % c.id,
                json=_correction_plan(world),
                headers=_h(db_session, world["god"]))
    r = client.get("/god/access/users/%s/audit" % c.id,
                   headers=_h(db_session, world["god"]))
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["entries"]]
    assert "access.manage" in actions


# ═════════════════════════════════════════════════════════════════════════════
# 5. Dual-role and multi-context people
# ═════════════════════════════════════════════════════════════════════════════

def test_one_person_holds_several_contexts_at_once(client, db_session, world):
    p = _user(db_session, role="advisor", name="Dual Role")
    plan = {"operations": [
        {"op": "add_membership", "scope_type": SCOPE_PLATFORM,
         "scope_id": world["boost"].id, "role": ROLE_BRAND_EXECUTIVE},
        {"op": "add_membership", "scope_type": SCOPE_BRAND_SALES_ORG,
         "scope_id": world["boost_sales"].id, "role": ROLE_SALES_MANAGER},
        {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": world["boost_cust"].id, "role": "org_admin"},
        {"op": "grant_demo", "platform_id": world["boost"].id},
    ]}
    r = client.post("/god/access/users/%s/apply" % p.id, json=plan,
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    fp = r.json()["footprint"]
    assert len([b for b in fp["brand_contexts"] if b["is_active"]]) == 1
    assert len([b for b in fp["back_office"] if b["is_active"]]) == 1
    assert len([w for w in fp["workspaces"] if w["is_active"]]) == 1
    assert fp["demo"]["brands"]

    # NO AUTHORITY BLEED. Nothing about the other brand was granted.
    assert all(b["name"] != "EvoSys Pro"
               for b in fp["brand_contexts"] + fp["back_office"])
    # And no customer workspace came along with the brand roles.
    assert [w["name"] for w in fp["workspaces"] if w["is_active"]] \
        == ["Halverson Roofing"]


def test_brand_executive_does_not_gain_workspace_entry(client, db_session,
                                                       world):
    """An executive assignment is a PORTFOLIO row, not a door into the tenant."""
    p = _user(db_session, role="advisor")
    client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "add_membership", "scope_type": SCOPE_PLATFORM,
         "scope_id": world["evo"].id, "role": ROLE_BRAND_EXECUTIVE},
        {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": world["evo_cust"].id, "role": ROLE_BRAND_EXECUTIVE},
    ]}, headers=_h(db_session, world["god"]))
    fp = client.get("/god/access/users/%s" % p.id,
                    headers=_h(db_session, world["god"])).json()
    assert len(fp["executive_assignments"]) == 1
    assert fp["workspaces"] == []
    assert "NOT grant entry" in fp["executive_assignments"][0]["means"]


# ═════════════════════════════════════════════════════════════════════════════
# 6. Escalation is impossible from this surface
# ═════════════════════════════════════════════════════════════════════════════

def test_god_admin_cannot_be_granted_as_a_platform_role(client, db_session,
                                                        world):
    p = _user(db_session, role="advisor")
    r = client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "set_platform_role", "role": "god_admin"}]},
        headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    assert "root authority" in r.json()["detail"].lower()
    db_session.expire_all()
    assert db_session.query(User).filter(User.id == p.id).first().role == "advisor"


def test_the_owner_is_not_administered_from_this_screen(client, db_session,
                                                        world):
    other_god = _user(db_session, role="god_admin", name="Second Owner")
    r = client.post("/god/access/users/%s/apply" % other_god.id,
                    json={"operations": [
                        {"op": "set_active", "is_active": False}]},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    assert "platform owner" in r.json()["detail"].lower()


def test_a_role_that_means_nothing_in_a_scope_is_refused(client, db_session,
                                                         world):
    p = _user(db_session, role="advisor")
    r = client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "add_membership", "scope_type": SCOPE_BRAND_SALES_ORG,
         "scope_id": world["evo_sales"].id, "role": "org_admin"}]},
        headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    assert "not a role that means anything" in r.json()["detail"]


def test_a_membership_pointing_at_nothing_is_refused(client, db_session, world):
    p = _user(db_session, role="advisor")
    r = client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": "no-such-org", "role": "advisor"}]},
        headers=_h(db_session, world["god"]))
    assert r.status_code == 400


def test_templates_expand_to_existing_authority(client, db_session, world):
    """A template is a way of typing less. It must never invent a role."""
    p = _user(db_session, role="advisor")
    r = client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "apply_template", "template": "sales_manager",
         "scope_id": world["evo_sales"].id},
        {"op": "apply_template", "template": "demo_presenter",
         "scope_id": world["evo"].id},
    ]}, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    m = (db_session.query(Membership)
         .filter(Membership.user_id == p.id,
                 Membership.scope_type == SCOPE_BRAND_SALES_ORG).first())
    assert m.role == ROLE_SALES_MANAGER          # an EXISTING role, not a new one
    assert capabilities.platform_grants_for(db_session, p.id, world["evo"].id) \
        == ["demo_suite"]


# ═════════════════════════════════════════════════════════════════════════════
# 7. Nothing is half-applied
# ═════════════════════════════════════════════════════════════════════════════

def test_a_plan_that_fails_leaves_the_person_exactly_as_they_were(
        client, db_session, world):
    """The second operation is invalid. The first must not survive it."""
    c = _christina(db_session, world)
    r = client.post("/god/access/users/%s/apply" % c.id, json={"operations": [
        {"op": "add_membership", "scope_type": SCOPE_BRAND_SALES_ORG,
         "scope_id": world["boost_sales"].id, "role": ROLE_SALES_REP},
        {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": "does-not-exist", "role": "advisor"},
    ]}, headers=_h(db_session, world["god"]))
    assert r.status_code == 400
    db_session.expire_all()
    assert (db_session.query(Membership)
            .filter(Membership.user_id == c.id,
                    Membership.scope_type == SCOPE_BRAND_SALES_ORG)
            .count()) == 0
    # And the access they had this morning is untouched.
    assert (db_session.query(Membership)
            .filter(Membership.user_id == c.id,
                    Membership.scope_type == SCOPE_CUSTOMER_ORG,
                    Membership.is_active.is_(True)).count()) == 1


def test_reinstating_access_somebody_used_to_have_actually_works(
        client, db_session, world):
    """THE BUG THAT ONLY CLICKING FOUND.

    Adding a workspace membership to somebody who never had one worked;
    RE-granting one to somebody whose membership had been revoked failed with
    "the replacement access could not be verified, so nothing was removed" and
    rolled the whole plan back.

    `grant_workspace_membership` flushes on the branch that inserts a new row
    and returns early — without flushing — on the branch that reactivates an
    existing one. With autoflush off, the verification query that follows saw
    the pre-change row.

    So the one case the screen exists for — correcting somebody who has been
    somewhere before — was the one case that did not work, and every test here
    used a fresh scope and missed it. This is that case.
    """
    c = _christina(db_session, world)
    org_id = world["evo_cust"].id

    # Revoke it, exactly as a previous correction would have.
    client.post("/god/access/users/%s/apply" % c.id, json={"operations": [
        {"op": "remove_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": org_id}]}, headers=_h(db_session, world["god"]))
    db_session.expire_all()
    row = (db_session.query(Membership)
           .filter(Membership.user_id == c.id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG).first())
    assert row.is_active is False

    # Now put her back, in a different role.
    r = client.post("/god/access/users/%s/apply" % c.id, json={"operations": [
        {"op": "apply_template", "template": "workspace_user",
         "scope_id": org_id}]}, headers=_h(db_session, world["god"]))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    rows = (db_session.query(Membership)
            .filter(Membership.user_id == c.id,
                    Membership.scope_type == SCOPE_CUSTOMER_ORG).all())
    # ONE row, reactivated and re-roled — not a second one beside the first.
    assert len(rows) == 1
    assert rows[0].is_active is True
    assert rows[0].role == "advisor"


def test_reinstating_an_executive_assignment_also_works(client, db_session,
                                                        world):
    """The same flush hazard on the executive-assignment branch."""
    p = _user(db_session, role="advisor")
    plan = {"operations": [
        {"op": "add_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": world["evo_cust"].id, "role": ROLE_BRAND_EXECUTIVE}]}
    h = _h(db_session, world["god"])
    assert client.post("/god/access/users/%s/apply" % p.id, json=plan,
                       headers=h).status_code == 200
    client.post("/god/access/users/%s/apply" % p.id, json={"operations": [
        {"op": "remove_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": world["evo_cust"].id, "role": ROLE_BRAND_EXECUTIVE}]},
        headers=h)
    r = client.post("/god/access/users/%s/apply" % p.id, json=plan, headers=h)
    assert r.status_code == 200, r.text
    fp = r.json()["footprint"]
    assert [a["is_active"] for a in fp["executive_assignments"]] == [True]


def test_removing_access_deactivates_rather_than_deletes(client, db_session,
                                                         world):
    c = _christina(db_session, world)
    client.post("/god/access/users/%s/apply" % c.id, json={"operations": [
        {"op": "remove_membership", "scope_type": SCOPE_CUSTOMER_ORG,
         "scope_id": world["evo_cust"].id}]},
        headers=_h(db_session, world["god"]))
    db_session.expire_all()
    row = (db_session.query(Membership)
           .filter(Membership.user_id == c.id,
                   Membership.scope_type == SCOPE_CUSTOMER_ORG).first())
    assert row is not None            # the history of who could enter survives
    assert row.is_active is False


def test_directory_is_named_not_uuids(client, db_session, world):
    d = client.get("/god/access/directory",
                   headers=_h(db_session, world["god"])).json()
    assert {p["name"] for p in d["platforms"]} >= {"EvoSys Pro", "BookaBoost"}
    assert all(w["name"] for w in d["workspaces"])
    assert all(t["what"] and t["not"] for t in d["templates"])
