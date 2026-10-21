# -*- coding: utf-8 -*-
"""LANE-E-BOOKABOOST item 4 (#272): inviting BookaBoost Wholesale's admins and
team, end to end through the real routes - invite, one-time setup link, choose a
password, sign in through the normal front door, land in BookaBoost Wholesale.

Owner rule: GOD + org admins and above; no EvoSys operators. So:
  * GOD invites an org admin -> that person opens BookaBoost Wholesale (and is
    locked to it), and never EvoSys;
  * that admin invites another admin -> same; invites an advisor -> no
    Wholesale until the admin puts them on BookaBoost's own team;
  * a forged workspace header cannot make the invite land in EvoSys;
  * nobody from EvoSys is added by any of it.
All records synthetic.
"""
import itertools
import json
import re
from urllib.parse import parse_qs, urlparse

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
EVO_SLUG = "evo-integrated-solutions-llc"
BAB_FEATURES = ["leads", "users", "reports", "imports", "compliance", "sms", "email", "ai_assist",
                "calendar", "availability", "booking", "wholesale_real_estate", "wholesale_workspace"]
PASSWORD = "Bab-Setup-2026!x"


def _platform(db, slug, name):
    p = db.query(Platform).filter(Platform.slug == slug).first()
    if p is None:
        p = Platform(id="plt-%s" % slug, name=name, slug=slug, is_active=True)
        db.add(p)
        db.commit()
    return p


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


def _token(setup_url):
    return parse_qs(urlparse(setup_url).query)["token"][0]


def _activate_and_login(client, setup_url, email):
    r = client.post("/auth/staff-activation/accept", json={"token": _token(setup_url), "new_password": PASSWORD})
    assert r.status_code == 200, r.text[:300]
    r = client.post("/auth/login", data={"username": email, "password": PASSWORD})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    return {"Authorization": "Bearer %s" % (body.get("access_token") or body.get("token"))}


@pytest.fixture()
def w(db_session):
    db = db_session
    evp, bbp = _platform(db, "evosyspro", "EvoSys Pro"), _platform(db, "bookaboost", "BookaBoost")
    evo = Organization(name="EVO Integrated Solutions LLC", slug=EVO_SLUG, plan="standard", industry="generic",
                       is_active=True, platform_id=evp.id,
                       enabled_features=json.dumps(["leads", "users", "wholesale_real_estate"]))
    bab = Organization(name="BookaBoost Wholesale", slug="bookaboost-wholesale", plan="trial", industry="generic",
                       is_active=True, platform_id=bbp.id, enabled_features=json.dumps(BAB_FEATURES))
    db.add_all([evo, bab])
    db.commit()
    god = User(organization_id=None, email="god@platform.example.com", full_name="God", role="god_admin", is_active=True,
               password_hash=hash_password("Pass12345!"), must_change_password=False)
    evo_seat = User(organization_id=evo.id, email="wholesale@evo.example.com", full_name="Evo Seat", role="org_admin",
                    is_active=True, password_hash=hash_password("Pass12345!"), must_change_password=False)
    db.add_all([god, evo_seat])
    db.commit()
    db.add_all([Membership(user_id=evo_seat.id, scope_type="customer_org", scope_id=evo.id, role="org_admin",
                           is_active=True),
                Membership(user_id=evo_seat.id, scope_type="product", scope_id="wholesale", role="member",
                           is_active=True)])
    db.commit()
    return dict(db=db, evo=evo, bab=bab, god=god, evo_seat=evo_seat)


def _bab_members(db, bab):
    return {m.user_id: m.role for m in db.query(Membership).filter(
        Membership.scope_type == "customer_org", Membership.scope_id == bab.id, Membership.is_active.is_(True)).all()}


def _opens(client, h, org):
    """Wholesale answers AND the server resolved THIS workspace. A selection the
    caller has no membership behind is discarded and the request falls back to
    their own workspace - that answer is theirs, not `org`'s."""
    hh = {**h, "X-Workspace-Id": org.id}
    if client.get("/wholesale/settings", headers=hh).status_code != 200:
        return False
    return client.get("/branding/org", headers=hh).json().get("organization_id") == org.id


def test_god_invites_a_bookaboost_admin_who_lands_in_bookaboost_wholesale(client, w):
    db, bab, evo = w["db"], w["bab"], w["evo"]
    r = client.post("/god/customers/%s/users" % bab.id, headers=_h(db, w["god"]),
                    json={"email": "owner@bab.example.com", "full_name": "BAB Owner", "role": "org_admin",
                          "base_url": "https://app.bookaboost.live"})
    assert r.status_code == 201, r.text[:300]
    setup = r.json()["setup_url"]
    assert setup and setup.startswith("https://app.bookaboost.live/activate?token=")
    h = _activate_and_login(client, setup, "owner@bab.example.com")
    ctx = client.get("/auth/my-contexts", headers=h).json()
    assert {x["organization_id"]: x["experience"] for x in ctx["workspace_contexts"]} == {bab.id: "wholesale"}
    assert [p["organization_id"] for p in ctx.get("product_contexts") or [] if p.get("key") == "wholesale"] == [bab.id]
    assert _opens(client, h, bab)
    assert not _opens(client, h, evo)                       # forged header: no EvoSys
    owner = db.query(User).filter(User.email == "owner@bab.example.com").one()
    assert _bab_members(db, bab) == {owner.id: "org_admin"}
    assert w["evo_seat"].id not in _bab_members(db, bab)    # nobody from EvoSys was added


def test_bookaboost_admin_invites_an_admin_and_an_advisor(client, w):
    db, bab, evo = w["db"], w["bab"], w["evo"]
    r = client.post("/god/customers/%s/users" % bab.id, headers=_h(db, w["god"]),
                    json={"email": "owner2@bab.example.com", "full_name": "BAB Owner", "role": "org_admin"})
    owner_h = _activate_and_login(client, r.json()["setup_url"], "owner2@bab.example.com")
    owner_ws = {**owner_h, "X-Workspace-Id": bab.id}
    made = {}
    for email, role in (("admin2@bab.example.com", "org_admin"), ("rep@bab.example.com", "advisor")):
        r = client.post("/admin/users", headers=owner_ws, json={"email": email, "full_name": email.split("@")[0],
                                                                "role": role})
        assert r.status_code in (200, 201), r.text[:300]
        made[email] = _activate_and_login(client, r.json()["setup_url"], email)
        u = db.query(User).filter(User.email == email).one()
        assert u.organization_id == bab.id                  # landed in BookaBoost, not EvoSys
    assert _opens(client, made["admin2@bab.example.com"], bab)     # admin rule
    assert not _opens(client, made["admin2@bab.example.com"], evo)
    assert not _opens(client, made["rep@bab.example.com"], bab)    # an advisor needs a team seat first
    rep = db.query(User).filter(User.email == "rep@bab.example.com").one()
    r = client.post("/wholesale/team/members", headers=owner_ws, json={"user_id": rep.id, "role": "member"})
    assert r.status_code == 200, r.text[:300]
    assert _opens(client, made["rep@bab.example.com"], bab)        # BookaBoost's own seat
    assert not _opens(client, made["rep@bab.example.com"], evo)


def test_a_forged_header_cannot_make_a_bookaboost_invite_land_in_evosys(client, w):
    db, bab, evo = w["db"], w["bab"], w["evo"]
    r = client.post("/god/customers/%s/users" % bab.id, headers=_h(db, w["god"]),
                    json={"email": "owner3@bab.example.com", "full_name": "BAB Owner", "role": "org_admin"})
    owner_h = _activate_and_login(client, r.json()["setup_url"], "owner3@bab.example.com")
    r = client.post("/admin/users", headers={**owner_h, "X-Workspace-Id": evo.id},
                    json={"email": "sneak@bab.example.com", "full_name": "Sneak", "role": "org_admin"})
    if r.status_code in (200, 201):
        assert db.query(User).filter(User.email == "sneak@bab.example.com").one().organization_id == bab.id
    else:
        assert r.status_code in (400, 403, 404)
    evo_people = {u.id for u in db.query(User).filter(User.organization_id == evo.id).all()}
    assert evo_people == {w["evo_seat"].id}


def test_an_evosys_seat_holder_is_not_given_bookaboost_by_any_invite_path(client, w):
    """Only an explicit GOD act could add an EvoSys person to BookaBoost - and
    even then, as an advisor, their EvoSys seat does not open it."""
    db, bab = w["db"], w["bab"]
    r = client.post("/god/customers/%s/users" % bab.id, headers=_h(db, w["god"]),
                    json={"email": "wholesale@evo.example.com", "role": "advisor"})
    assert r.status_code in (200, 201, 409), r.text[:300]
    if r.status_code in (200, 201):
        assert not _opens(client, _h(db, w["evo_seat"]), bab)
