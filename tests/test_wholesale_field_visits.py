"""The visit record: the seller interview and the walkthrough (repairs and
photos) kept with each appointment. Who may read it, who may write it, that
answers are cleaned, and that photos never reach another company."""
import pytest

from app.models.models import User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))


@pytest.fixture
def visit(client, admin_auth_headers, sample_advisor, second_advisor):
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": second_advisor.id, "role": "manager", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist",
                         "reports_to_user_id": second_advisor.id}))
    return ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-10-12T10:00:00-05:00", "address": "12 Visit Ln, Dallas, TX",
        "assigned_to_id": sample_advisor.id}))


def test_the_rep_fills_the_interview_and_the_office_reads_it(client, auth_headers, admin_auth_headers, visit):
    path = "/wholesale/appointments/%s/visit" % visit["id"]
    v = ok(client.put(path, headers=auth_headers, json={"interview": {
        "motivation": "Inherited it, lives in Houston", "timeline": "30_days", "occupancy": "vacant",
        "owed": "$62,500", "price_wanted": "145000", "on_deed": "Two siblings", "bogus": "dropped"}}))
    i = v["interview"]
    assert i["owed"] == 62500 and i["price_wanted"] == 145000 and i["timeline"] == "30_days"
    assert "bogus" not in i
    office = ok(client.get(path, headers=admin_auth_headers))
    assert office["interview"]["motivation"].startswith("Inherited") and office["updated_by_name"]
    assert client.put(path, headers=auth_headers, json={"interview": {"timeline": "someday"}}).status_code == 422


def test_repairs_room_by_room_add_up(client, auth_headers, visit):
    path = "/wholesale/appointments/%s/visit" % visit["id"]
    v = ok(client.put(path, headers=auth_headers, json={"repairs": {"condition": "poor", "items": {
        "Roof": {"needed": True, "amount": "12,000", "note": "Tarp on back slope"},
        "Kitchen": {"needed": True, "amount": 18000},
        "Flooring": {"needed": False},
        "Spaceship": {"needed": True, "amount": 1}}}}))
    r = v["repairs"]
    assert r["condition"] == "poor" and r["total"] == 30000
    assert set(r["items"]) == {"Roof", "Kitchen"} and r["items"]["Roof"]["note"] == "Tarp on back slope"


def test_walkthrough_photos(client, auth_headers, admin_auth_headers, visit, storage):
    base = "/wholesale/appointments/%s/photos" % visit["id"]
    p = ok(client.post(base, headers=auth_headers, files={"file": ("k.png", PNG, "image/png")},
                       data={"room": "Kitchen", "caption": "Water damage under sink"}))
    assert p["room"] == "Kitchen"
    got = client.get(base + "/" + p["id"], headers=admin_auth_headers)
    assert got.status_code == 200 and got.content == PNG
    listed = ok(client.get("/wholesale/appointments/%s/visit" % visit["id"], headers=auth_headers))["photos"]
    assert [x["id"] for x in listed] == [p["id"]]
    bad = client.post(base, headers=auth_headers, files={"file": ("x.exe", b"MZ\x90\x00" * 50, "image/png")})
    assert bad.status_code in (400, 415, 422)
    assert ok(client.delete(base + "/" + p["id"], headers=auth_headers))["ok"] is True


def test_another_rep_and_another_company_cannot_touch_it(client, admin_auth_headers, visit, db_session, storage):
    from app.models.models import Organization
    from app.services.auth_service import create_access_token
    org = db_session.query(User).filter(User.role == "org_admin").first().organization_id
    rep2 = User(email="rep2@field.test", full_name="Rep Two", role="advisor", organization_id=org,
                password_hash="x", must_change_password=False)
    rival_org = Organization(name="Rival", slug="rival-visits", plan="standard", industry="real_estate")
    db_session.add_all([rep2, rival_org]); db_session.commit()
    rival = User(email="x@rival-visits.test", full_name="X", role="org_admin", organization_id=rival_org.id,
                 password_hash="x", must_change_password=False)
    db_session.add(rival); db_session.commit()
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": rep2.id, "role": "canvasser"}))
    path = "/wholesale/appointments/%s/visit" % visit["id"]
    for user in (rep2, rival):
        h = {"Authorization": "Bearer %s" % create_access_token(user, db_session)}
        assert client.get(path, headers=h).status_code in (403, 404)
        assert client.put(path, headers=h, json={"interview": {"motivation": "x"}}).status_code in (403, 404)
