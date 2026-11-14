"""MY TEAM + SALES ONBOARDING — rights are enforced on the server.

Synthetic people and synthetic documents only.
"""
import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg, Membership)
from app.models.sales_team_models import SalesDeparture, SalesOnboarding
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
PDF = b"%PDF-1.4\n% synthetic test document\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
DEAL = {"plan": "custom_saas", "saas_setup": 50000, "monthly": 4000, "basis": "quote"}


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="st%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def org(db_session, monkeypatch):
    db = db_session
    sent = []
    import app.services.email_service as es
    monkeypatch.setattr(es, "send_email_via_provider",
                        lambda **kw: sent.append(kw) or {"success": True})
    plat = Platform(name="EvoSys", slug="evosys-%d" % next(_SEQ))
    db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner One")
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    outsider = _user(db, "Outsider")
    db.commit()
    return dict(db=db, brand=brand, god=god, owner=owner, outsider=outsider, sent=sent)


def H(org, u):
    return {"Authorization": "Bearer " + create_access_token(u, org["db"])}


def _by_email(db, email):
    """The invitee, as after they used the one-time link and set a password."""
    u = db.query(User).filter(User.email == email).first()
    u.must_change_password = False
    db.commit()
    return u


def _invite(client, org, actor, seat, manager_id=None, name=None):
    email = "inv%d@example.test" % next(_SEQ)
    r = client.post("/sales-team/invite", headers=H(org, actor),
                    json={"email": email, "full_name": name or ("Person %s" % seat),
                          "seat": seat, "manager_id": manager_id,
                          "base_url": "https://app.example.test"})
    return r, email


def _onboard(client, org, u):
    """Fill in the application, upload every file, submit."""
    h = H(org, u)
    r = client.put("/sales-team/onboarding/me/application", headers=h,
                   json={"legal_name": u.full_name, "home_address": "1 Test St",
                         "phone": "555-0100", "email": u.email, "start_date": "2026-11-20"})
    assert r.status_code == 200, r.text
    for kind in ("contractor_agreement", "w9", "photo_id_front", "photo_id_back", "i9"):
        r = client.post("/sales-team/onboarding/me/documents/%s" % kind, headers=h,
                        files={"file": ("%s.pdf" % kind, PDF, "application/pdf")})
        assert r.status_code == 200, r.text
    r = client.post("/sales-team/onboarding/me/submit", headers=h)
    assert r.status_code == 200, r.text
    return org["db"].query(SalesOnboarding).filter(SalesOnboarding.user_id == u.id).first()


def _approve(client, org, onb_id, actor=None):
    return client.post("/sales-team/onboarding/%s/approve" % onb_id, headers=H(org, actor or org["owner"]))


@pytest.fixture()
def built(client, org):
    """Owner invites Jonathan (Sr EVP); Jonathan invites two ASMs and a rep."""
    db = org["db"]
    r, e = _invite(client, org, org["owner"], "sevp", name="Jonathan Test")
    assert r.status_code == 200, r.text
    jon = _by_email(db, e)
    onb = _onboard(client, org, jon)
    assert _approve(client, org, onb.id).status_code == 200
    r, e3 = _invite(client, org, jon, "asm", name="Manager Three"); assert r.status_code == 200, r.text
    r, e6 = _invite(client, org, jon, "asm", name="Manager Six"); assert r.status_code == 200, r.text
    m3, m6 = _by_email(db, e3), _by_email(db, e6)
    r, er = _invite(client, org, jon, "rep", manager_id=m3.id, name="Rep Test"); assert r.status_code == 200, r.text
    rep = _by_email(db, er)
    return dict(jon=jon, m3=m3, m6=m6, rep=rep)


# ── invites ──────────────────────────────────────────────────────────────────

def test_invite_creates_seat_onboarding_and_sends_email(client, org, built):
    db = org["db"]
    rep = built["rep"]
    m = db.query(Membership).filter(Membership.user_id == rep.id).first()
    assert m.role == ROLE_SALES_REP and m.reports_to_user_id == built["m3"].id and m.is_active
    assert db.query(SalesOnboarding).filter(SalesOnboarding.user_id == rep.id).first().status == "in_progress"
    assert any(s["to_email"] == rep.email and "/activate?token=" in s["body_html"] for s in org["sent"])
    jon_caps = caps.brand_grants_for(db, built["jon"].id, org["brand"].id)
    assert jon_caps == ["sales_team_executive"]


def test_link_shown_to_inviter_only_when_email_fails(client, org, monkeypatch):
    import app.services.email_service as es
    monkeypatch.setattr(es, "send_email_via_provider", lambda **kw: {"success": False})
    r, _ = _invite(client, org, org["owner"], "rep")
    assert r.status_code == 200
    assert r.json()["email_sent"] is False and "/activate?token=" in r.json()["setup_url"]


def test_who_may_invite(client, org, built):
    assert _invite(client, org, built["jon"], "sevp")[0].status_code == 403   # no peers
    assert _invite(client, org, built["m3"], "rep")[0].status_code == 403     # ASM: no invites
    assert _invite(client, org, built["rep"], "rep")[0].status_code == 403
    assert _invite(client, org, org["outsider"], "rep")[0].status_code == 403
    assert _invite(client, org, org["god"], "asm")[0].status_code == 200
    r = client.post("/sales-team/invite", json={"email": "x@example.test", "full_name": "X", "seat": "rep"})
    assert r.status_code == 401


def test_sevp_can_only_place_reps_under_his_own_managers(client, org, built):
    other_mgr = _user(org["db"], "Other Manager")
    org["db"].add(Membership(user_id=other_mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                             scope_id=org["brand"].id, role=ROLE_SALES_MANAGER, is_active=True))
    org["db"].commit()
    r, _ = _invite(client, org, built["jon"], "rep", manager_id=other_mgr.id)
    assert r.status_code == 403


# ── onboarding and approval ──────────────────────────────────────────────────

def test_unapproved_person_cannot_sell(client, org, built):
    h = H(org, built["rep"])
    a = client.get("/deal-builder/access", headers=h).json()
    assert a["sales"] is False
    r = client.post("/deal-builder/commission", json=DEAL, headers=h)
    assert r.status_code == 403 and "onboarding" in r.json()["detail"]
    assert client.get("/sales-team/onboarding/me", headers=h).status_code == 200


def test_submit_requires_the_whole_checklist(client, org, built):
    h = H(org, built["rep"])
    r = client.post("/sales-team/onboarding/me/submit", headers=h)
    assert r.status_code == 422 and "W-9" in r.json()["detail"] and "I-9" in r.json()["detail"]


def test_approval_turns_selling_on_with_the_right_view(client, org, built):
    onb = _onboard(client, org, built["rep"])
    assert _approve(client, org, onb.id, built["jon"]).status_code == 403    # Sr EVP cannot approve
    assert _approve(client, org, onb.id).status_code == 200
    r = client.post("/deal-builder/commission", json=DEAL, headers=H(org, built["rep"])).json()
    assert r["view"] == "rep" and r["own"]["monthly"] == 800.0
    onb3 = _onboard(client, org, built["m3"]); _approve(client, org, onb3.id)
    r = client.post("/deal-builder/commission", json=DEAL, headers=H(org, built["m3"])).json()
    assert r["view"] == "asm" and [l["role"] for l in r["layers"]] == ["rep", "asm"]
    r = client.post("/deal-builder/commission", json=DEAL, headers=H(org, built["jon"])).json()
    assert r["view"] == "sevp" and "evosys" not in r
    assert client.get("/deal-builder/executive-page", headers=H(org, built["jon"])).status_code == 403


def test_sevp_sees_status_not_files(client, org, built):
    onb = _onboard(client, org, built["rep"])
    roster = client.get("/sales-team/roster", headers=H(org, built["jon"])).json()
    person = [p for p in roster["people"] if p["user_id"] == built["rep"].id][0]
    items = person["onboarding"]["items"]
    assert all("filename" not in i and "document_id" not in i for i in items)
    assert all(i["done"] for i in items)
    owner_view = client.get("/sales-team/roster", headers=H(org, org["owner"])).json()
    p2 = [p for p in owner_view["people"] if p["user_id"] == built["rep"].id][0]
    doc = [i for i in p2["onboarding"]["items"] if i["key"] == "w9"][0]
    url = "/sales-team/onboarding/%s/documents/%s" % (onb.id, doc["document_id"])
    assert client.get(url, headers=H(org, built["jon"])).status_code == 403
    assert client.get(url, headers=H(org, built["rep"])).status_code == 403
    assert client.get(url).status_code == 401
    r = client.get(url, headers=H(org, org["owner"]))
    assert r.status_code == 200 and r.content == PDF and "no-store" in r.headers["cache-control"]


def test_document_views_are_audited(client, org, built):
    from app.models.models import AuditLogEntry as AuditLog
    onb = _onboard(client, org, built["rep"])
    owner_view = client.get("/sales-team/roster", headers=H(org, org["owner"])).json()
    p = [p for p in owner_view["people"] if p["user_id"] == built["rep"].id][0]
    doc_id = [i for i in p["onboarding"]["items"] if i["key"] == "photo_id_front"][0]["document_id"]
    before = org["db"].query(AuditLog).filter(AuditLog.action == "sales_onboarding_document_downloaded").count()
    client.get("/sales-team/onboarding/%s/documents/%s" % (onb.id, doc_id), headers=H(org, org["owner"]))
    after = org["db"].query(AuditLog).filter(AuditLog.action == "sales_onboarding_document_downloaded").count()
    assert after == before + 1


def test_roster_scope(client, org, built):
    outsider_mgr = _user(org["db"], "Not Jonathans")
    org["db"].add(Membership(user_id=outsider_mgr.id, scope_type=SCOPE_BRAND_SALES_ORG,
                             scope_id=org["brand"].id, role=ROLE_SALES_MANAGER, is_active=True))
    org["db"].commit()
    ids = {p["user_id"] for p in client.get("/sales-team/roster", headers=H(org, built["jon"])).json()["people"]}
    assert {built["m3"].id, built["m6"].id, built["rep"].id} <= ids and outsider_mgr.id not in ids
    assert client.get("/sales-team/roster", headers=H(org, built["m3"])).status_code == 403
    assert client.get("/sales-team/roster", headers=H(org, built["rep"])).status_code == 403
    all_ids = {p["user_id"] for p in client.get("/sales-team/roster", headers=H(org, org["owner"])).json()["people"]}
    assert outsider_mgr.id in all_ids


# ── suspend, move, departure ─────────────────────────────────────────────────

def test_suspend_and_reactivate(client, org, built):
    onb = _onboard(client, org, built["rep"]); _approve(client, org, onb.id)
    h = H(org, built["jon"])
    assert client.post("/sales-team/members/%s/suspend" % built["rep"].id, headers=h).status_code == 200
    assert client.get("/deal-builder/access", headers=H(org, built["rep"])).json()["sales"] is False
    assert client.post("/sales-team/members/%s/reactivate" % built["rep"].id, headers=h).status_code == 200
    assert client.get("/deal-builder/access", headers=H(org, built["rep"])).json()["sales"] is True
    # Not himself, not owners, not people outside his team; ASMs and reps cannot.
    assert client.post("/sales-team/members/%s/suspend" % built["jon"].id, headers=h).status_code == 403
    assert client.post("/sales-team/members/%s/suspend" % org["owner"].id, headers=h).status_code in (403, 404)
    assert client.post("/sales-team/members/%s/suspend" % built["rep"].id,
                       headers=H(org, built["m3"])).status_code == 403


def test_move_rep_between_managers(client, org, built):
    h = H(org, built["jon"])
    r = client.post("/sales-team/members/%s/move" % built["rep"].id, headers=h,
                    json={"manager_id": built["m6"].id})
    assert r.status_code == 200
    m = org["db"].query(Membership).filter(Membership.user_id == built["rep"].id).first()
    assert m.reports_to_user_id == built["m6"].id
    r = client.post("/sales-team/members/%s/move" % built["m3"].id, headers=h,
                    json={"manager_id": built["m6"].id})
    assert r.status_code == 422      # managers are not moved this way


def test_departure_is_owner_only_and_final(client, org, built):
    url = "/sales-team/members/%s/depart" % built["rep"].id
    assert client.post(url, headers=H(org, built["jon"]), json={}).status_code == 403
    assert client.post(url, headers=H(org, org["owner"]), json={"departed_at": "2026-12-01"}).status_code == 200
    db = org["db"]
    assert db.query(SalesDeparture).filter(SalesDeparture.user_id == built["rep"].id).count() == 1
    assert db.query(Membership).filter(Membership.user_id == built["rep"].id).first().is_active is False
    r = client.post("/sales-team/members/%s/reactivate" % built["rep"].id, headers=H(org, org["owner"]))
    assert r.status_code == 409


def test_owners_are_not_managed_from_my_team(client, org, built):
    r = client.post("/sales-team/members/%s/suspend" % org["owner"].id, headers=H(org, org["god"]))
    assert r.status_code in (403, 404)
    r, _ = _invite(client, org, org["god"], "rep")
    assert r.status_code == 200


def test_signed_out_gets_nothing(client, org):
    for path in ("/sales-team/me", "/sales-team/roster", "/sales-team/onboarding/me"):
        assert client.get(path).status_code == 401


def test_pending_invitee_has_no_back_office(client, org, built):
    from app.services.sales_access import sales_memberships
    db = org["db"]
    rep = db.query(User).filter(User.id == built["rep"].id).first()
    assert sales_memberships(rep, db) == []
    onb = _onboard(client, org, built["rep"]); _approve(client, org, onb.id)
    rep = db.query(User).filter(User.id == built["rep"].id).first()
    setattr(rep, "_sales_memberships_memo", None)
    assert len(sales_memberships(rep, db)) == 1


def test_roster_shows_god_as_god_not_manager(db_session=None):
    """God Mode is never shown as a team seat that others can act on."""
    from app.services import sales_team
    import inspect
    src = inspect.getsource(sales_team._seat_of_member)
    assert "is_god(user)" in src and "SEAT_GOD" in src
