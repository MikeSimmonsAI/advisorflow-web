"""The Wholesale team: roles, the reports-to tree, and who may change it.

What matters most: nobody's access changes until an admin acts, the first
team member cannot silently lock the admin out, a field login is
wholesale-only, and one company never sees another company's people.
"""
from app.models.models import User


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def _admin(db):
    return db.query(User).filter(User.role == "org_admin").first()


def test_only_an_admin_sees_or_changes_the_team(client, auth_headers, admin_auth_headers, sample_advisor):
    assert client.get("/wholesale/team", headers=auth_headers).status_code == 403
    r = client.post("/wholesale/team/members", headers=auth_headers,
                    json={"user_id": sample_advisor.id, "role": "canvasser", "confirm_lock": True})
    assert r.status_code == 403
    t = ok(client.get("/wholesale/team", headers=admin_auth_headers))
    assert t["members"] == [] and {r["key"] for r in t["roles"]} >= {"member", "canvasser", "field_specialist"}


def test_first_member_needs_confirmation_and_keeps_the_admin_in(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, db_session):
    body = {"user_id": sample_advisor.id, "role": "field_specialist"}
    r = client.post("/wholesale/team/members", headers=admin_auth_headers, json=body)
    assert r.status_code == 409 and "Confirm" in r.json()["detail"]

    t = ok(client.post("/wholesale/team/members", headers=admin_auth_headers, json={**body, "confirm_lock": True}))
    roles = {m["email"]: m["role"] for m in t["members"]}
    assert roles[sample_advisor.email] == "field_specialist"
    assert roles[_admin(db_session).email] == "member"          # the admin kept access
    assert t["lock_on"] is True
    # The rep's own view of themselves
    me = ok(client.get("/wholesale/team/me", headers=auth_headers))
    assert me["role"] == "field_specialist" and me["wholesale_only"] and me["field"]
    # Someone not on the team can no longer open Wholesale
    from app.services.auth_service import create_access_token
    other = {"Authorization": "Bearer %s" % create_access_token(second_advisor, db_session)}
    assert client.get("/wholesale/team/me", headers=other).status_code == 403


def test_reports_to_tree_refuses_loops_and_strangers(
        client, admin_auth_headers, sample_advisor, second_advisor, db_session):
    post = lambda b: client.post("/wholesale/team/members", headers=admin_auth_headers,
                                 json={**b, "confirm_lock": True})
    ok(post({"user_id": second_advisor.id, "role": "manager"}))
    t = ok(post({"user_id": sample_advisor.id, "role": "canvasser", "reports_to_user_id": second_advisor.id}))
    rep = [m for m in t["members"] if m["user_id"] == sample_advisor.id][0]
    assert rep["reports_to_user_id"] == second_advisor.id
    # loop: manager reporting to their own canvasser
    r = post({"user_id": second_advisor.id, "role": "manager", "reports_to_user_id": sample_advisor.id})
    assert r.status_code == 400 and "loop" in r.json()["detail"]
    # removing the manager moves the canvasser up, keeps their login
    t = ok(client.delete("/wholesale/team/members/%s" % second_advisor.id, headers=admin_auth_headers))
    rep = [m for m in t["members"] if m["user_id"] == sample_advisor.id][0]
    assert rep["reports_to_user_id"] is None
    assert second_advisor.email in {p["email"] for p in t["not_on_team"]}


def test_admins_cannot_be_locked_down_and_other_companies_are_invisible(
        client, admin_auth_headers, db_session):
    from app.models.models import Organization
    me = _admin(db_session)
    r = client.post("/wholesale/team/members", headers=admin_auth_headers,
                    json={"user_id": me.id, "role": "canvasser", "confirm_lock": True})
    assert r.status_code == 400
    other_org = Organization(name="Someone Else LLC", slug="someone-else", plan="standard")
    db_session.add(other_org); db_session.commit()
    stranger = User(email="stranger@else.test", full_name="Stranger", role="advisor",
                    organization_id=other_org.id, password_hash="x")
    db_session.add(stranger); db_session.commit()
    r = client.post("/wholesale/team/members", headers=admin_auth_headers,
                    json={"user_id": stranger.id, "role": "canvasser", "confirm_lock": True})
    assert r.status_code == 404
    t = ok(client.get("/wholesale/team", headers=admin_auth_headers))
    assert "stranger@else.test" not in {p["email"] for p in t["members"] + t["not_on_team"]}


def test_branding_tells_the_app_shell_the_role(client, admin_auth_headers, auth_headers, sample_advisor):
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "canvasser", "confirm_lock": True}))
    b = ok(client.get("/branding/org", headers=auth_headers))
    assert b["wholesale_team"] == {"role": "canvasser", "wholesale_only": True, "field": True}


# ── the server fence ────────────────────────────────────────────────────────

def test_fence_rules_by_role():
    from app.services.wholesale_team import fence
    # full access and no role: never fenced
    for role in (None, "member"):
        assert fence("/leads", "GET", role) is None
        assert fence("/wholesale/skip-trace/estimate", "POST", role) is None
    # managers: Wholesale side only, but all of it
    assert fence("/leads", "GET", "manager")
    assert fence("/crm/contacts", "GET", "team_lead")
    assert fence("/messages", "GET", "manager")                 # "/me" is a whole segment
    assert fence("/wholesale/buyers", "GET", "manager") is None
    assert fence("/wholesale/evosense/strategies/x/hunt", "POST", "manager") is None
    assert fence("/org-settings/", "GET", "manager") is None
    assert fence("/org-settings/", "PUT", "manager")
    # field: the shell and their own work, nothing that hunts, buys, sends or exports
    for role in ("field_specialist", "canvasser"):
        for ok_path in ("/auth/me", "/branding/org", "/notifications/", "/settings/profile",
                        "/wholesale/appointments", "/wholesale/appointments/reps",
                        "/wholesale/evosense/properties/p1", "/wholesale/evosense/properties/p1/photo.jpg",
                        "/wholesale/team/me"):
            assert fence(ok_path, "GET", role) is None, ok_path
        assert fence("/wholesale/evosense/properties/p1/facts", "PATCH", role) is None
        for only_theirs in ("/wholesale/properties", "/wholesale/ops/callbacks", "/wholesale/deals/d1",
                            "/wholesale/evosense/inbox", "/wholesale/evosense/properties",
                            "/wholesale/evosense/command-center"):
            assert fence(only_theirs, "GET", role), only_theirs
        for write in ("reply", "promote", "valuation", "comps", "feedback"):
            assert fence("/wholesale/evosense/properties/p1/%s" % write, "POST", role), write
        for bad in ("/wholesale/evosense/strategies", "/wholesale/evosense/strategies/s1/hunt",
                    "/wholesale/skip-trace/estimate", "/wholesale/contact-lookup/run",
                    "/wholesale/buyers", "/wholesale/funding/partners", "/wholesale/settings",
                    "/wholesale/ops/pilot", "/wholesale/evosense/provider-evaluations",
                    "/wholesale/evosense/controls", "/wholesale/team", "/wholesale/properties/export.csv",
                    "/leads"):
            assert fence(bad, "GET", role), bad
        assert fence("/wholesale/evosense/properties/p1/enrich", "POST", role)
        assert fence("/wholesale/evosense/properties/p1/outreach", "POST", role)


def test_a_field_login_is_refused_by_the_server_not_just_the_menu(
        client, admin_auth_headers, auth_headers, sample_advisor):
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "canvasser", "confirm_lock": True}))
    assert client.get("/leads", headers=auth_headers).status_code == 403
    r = client.get("/wholesale/evosense/strategies", headers=auth_headers)
    assert r.status_code == 403 and "Field logins" in r.json()["detail"]
    assert client.get("/wholesale/team/me", headers=auth_headers).status_code == 200
    assert client.get("/branding/org", headers=auth_headers).status_code == 200
    # Back to full access: everything they had before comes back
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "member"}))
    assert client.get("/leads", headers=auth_headers).status_code == 200


# ── hunts, lookups and strategy changes: admins and team managers only ──────

def test_admin_only_writes_are_recognised():
    from app.services.wholesale_team import admin_only_write as a
    assert a("/wholesale/evosense/strategies", "POST")
    assert a("/wholesale/evosense/strategies/s1", "PATCH")
    assert a("/wholesale/evosense/strategies/s1/hunt", "POST")
    assert a("/wholesale/evosense/strategies/s1/clone", "POST")
    assert a("/wholesale/evosense/strategy-starters", "POST")
    assert a("/wholesale/evosense/properties/p1/enrich", "POST")
    assert a("/wholesale/contact-lookup/run", "POST")
    assert a("/wholesale/evosense/sources/tad/upload", "POST")
    assert not a("/wholesale/evosense/strategies", "GET")
    assert not a("/wholesale/evosense/strategies/preview", "POST")
    assert not a("/wholesale/evosense/properties/p1/follow-up", "POST")
    assert not a("/wholesale/evosense/properties/p1/dismiss", "POST")


def test_a_plain_advisor_cannot_start_a_hunt_but_a_team_manager_can(
        client, admin_auth_headers, auth_headers, sample_advisor):
    r = client.post("/wholesale/evosense/strategies/does-not-exist/hunt", headers=auth_headers, json={})
    assert r.status_code == 403 and "admin or a team manager" in r.json()["detail"]
    r = client.post("/wholesale/contact-lookup/run", headers=auth_headers, json={})
    assert r.status_code == 403
    # The admin is not stopped by the fence (the route itself answers: no such strategy)
    assert client.post("/wholesale/evosense/strategies/does-not-exist/hunt",
                       headers=admin_auth_headers, json={}).status_code != 403
    # Made a team manager: the fence lets them through
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "manager", "confirm_lock": True}))
    assert client.post("/wholesale/evosense/strategies/does-not-exist/hunt",
                       headers=auth_headers, json={}).status_code != 403



def test_a_field_login_opens_only_properties_on_their_appointments(
        client, admin_auth_headers, auth_headers, sample_advisor, sample_org, db_session):
    from app.models.evosense_models import EvoSenseProperty
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist", "confirm_lock": True}))
    mine = EvoSenseProperty(organization_id=sample_org.id, street_address="10 Mine St", city="Dallas",
                            state="TX", zip_code="75201", is_test=True)
    other = EvoSenseProperty(organization_id=sample_org.id, street_address="20 Other St", city="Dallas",
                             state="TX", zip_code="75201", is_test=True)
    db_session.add_all([mine, other]); db_session.commit()
    ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-10-12T10:00:00-05:00", "evosense_property_id": mine.id,
        "assigned_to_id": sample_advisor.id}))
    r_other = client.get("/wholesale/evosense/properties/%s" % other.id, headers=auth_headers)
    assert r_other.status_code == 403 and "not on any of your appointments" in r_other.json()["detail"]
    assert client.get("/wholesale/evosense/properties/%s" % mine.id, headers=auth_headers).status_code != 403
    assert client.get("/wholesale/evosense/inbox", headers=auth_headers).status_code == 403
    assert client.get("/wholesale/ops/callbacks", headers=auth_headers).status_code == 403
