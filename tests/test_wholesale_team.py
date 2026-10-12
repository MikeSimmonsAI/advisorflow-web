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
