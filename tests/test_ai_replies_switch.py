"""Owner, Nov 17: an on/off switch for AI replies to families. Only the
owner's logins (god account, super admin) may switch it ON; a manager may
switch it OFF. The page is told who may switch it."""
from app.models.models import User
from app.services.auth_service import hash_password
from tests.test_outreach_program import _h, program  # noqa: F401 (fixture)


def _user(db, org, role, email):
    u = User(email=email, full_name="Someone", password_hash=hash_password("x" * 12), organization_id=org.id,
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def test_owner_turns_ai_replies_on_and_off(client, program):
    db, org = program["db"], program["org"]
    owner = _user(db, org, "super_admin", "owner@example.com")
    me = client.get("/program/me", headers=_h(db, owner)).json()
    assert me["ai_setter_can_turn_on"] is True
    r = client.patch("/program/settings", headers=_h(db, owner), json={"ai_setter_mode": "auto"})
    assert r.status_code == 200, r.text
    assert client.get("/program/me", headers=_h(db, owner)).json()["ai_setter_mode"] == "auto"
    dash = client.get("/program/dashboard", headers=_h(db, owner)).json()["program"]
    assert dash["ai_setter_mode"] == "auto" and dash["ai_setter_can_turn_on"] is True
    assert client.patch("/program/settings", headers=_h(db, owner),
                        json={"ai_setter_mode": "off"}).status_code == 200
    assert client.get("/program/me", headers=_h(db, owner)).json()["ai_setter_mode"] == "off"


def test_a_manager_can_switch_off_but_not_on(client, program):
    db = program["db"]
    admin = program["admin"]
    admin.must_change_password = False
    db.commit()
    assert client.get("/program/me", headers=_h(db, admin)).json()["ai_setter_can_turn_on"] is False
    r = client.patch("/program/settings", headers=_h(db, admin), json={"ai_setter_mode": "auto"})
    assert r.status_code == 403
    program["prog"].ai_setter_mode = "auto"
    db.commit()
    assert client.patch("/program/settings", headers=_h(db, admin),
                        json={"ai_setter_mode": "off"}).status_code == 200
    assert client.get("/program/me", headers=_h(db, admin)).json()["ai_setter_mode"] == "off"


def test_bad_mode_is_refused(client, program):
    db, org = program["db"], program["org"]
    owner = _user(db, org, "super_admin", "owner2@example.com")
    assert client.patch("/program/settings", headers=_h(db, owner),
                        json={"ai_setter_mode": "sometimes"}).status_code == 422


def test_the_owners_everyday_platform_login_can_turn_it_on(client, program):
    from app.services.project_checklist import PLATFORM_LOGIN
    db, org = program["db"], program["org"]
    plat = _user(db, org, "org_admin", PLATFORM_LOGIN)
    assert client.get("/program/me", headers=_h(db, plat)).json()["ai_setter_can_turn_on"] is True
    assert client.patch("/program/settings", headers=_h(db, plat),
                        json={"ai_setter_mode": "auto"}).status_code == 200
    other = _user(db, org, "org_admin", "other.manager@example.com")
    assert client.patch("/program/settings", headers=_h(db, other),
                        json={"ai_setter_mode": "auto"}).status_code == 403
