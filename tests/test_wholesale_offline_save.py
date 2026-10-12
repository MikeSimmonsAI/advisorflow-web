"""Build list #28: the field app keeps work on the phone through a dead zone
and sends it when signal comes back. A change sent that way can arrive twice
(the first try got through, the answer never came back). The server counts it
once: the same photo is not saved twice and the same visit result does not
make a second call-back."""
import pytest

from app.models.models import User

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
PNG2 = b"\x89PNG\r\n\x1a\n" + b"\x01" * 256


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("WHOLESALE_LOCAL_MEDIA_ROOT", str(tmp_path / "media"))


@pytest.fixture
def team(client, admin_auth_headers, sample_advisor, second_advisor):
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": second_advisor.id, "role": "manager", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist",
                         "reports_to_user_id": second_advisor.id}))


def _book(client, admin_auth_headers, rep_id, **extra):
    return ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-10-20T10:00:00-05:00", "address": "28 Signal Ln, Dallas, TX",
        "assigned_to_id": rep_id, **extra}))


def test_the_same_photo_sent_twice_is_saved_once(client, auth_headers, admin_auth_headers, sample_advisor,
                                                  team, storage):
    a = _book(client, admin_auth_headers, sample_advisor.id)
    base = "/wholesale/appointments/%s/photos" % a["id"]
    send = lambda b: ok(client.post(base, headers=auth_headers, files={"file": ("k.png", b, "image/png")},
                                    data={"room": "Kitchen"}))
    first, again, other = send(PNG), send(PNG), send(PNG2)
    assert again["id"] == first["id"] and other["id"] != first["id"]
    listed = ok(client.get("/wholesale/appointments/%s/visit" % a["id"], headers=auth_headers))["photos"]
    assert sorted(x["id"] for x in listed) == sorted([first["id"], other["id"]])


def test_a_visit_result_sent_twice_makes_one_call_back(client, auth_headers, admin_auth_headers, sample_advisor,
                                                       team, sample_org, db_session):
    from app.models.evosense_models import EvoSenseFollowUp, EvoSenseProperty
    prop = EvoSenseProperty(organization_id=sample_org.id, street_address="28 Signal Ln", city="Dallas",
                            state="TX", zip_code="75201", is_test=True)
    db_session.add(prop); db_session.commit()
    a = _book(client, admin_auth_headers, sample_advisor.id, evosense_property_id=prop.id)
    path = "/wholesale/appointments/%s" % a["id"]
    body = {"outcome": "follow_up", "outcome_note": "Call Thursday", "follow_up_at": "2026-10-22T10:00:00-05:00",
            "client_ref": "phone-ref-1"}
    one, two = ok(client.patch(path, headers=auth_headers, json=body)), ok(client.patch(path, headers=auth_headers, json=body))
    assert one["outcome"] == two["outcome"] == "follow_up"
    calls = lambda: db_session.query(EvoSenseFollowUp).filter(EvoSenseFollowUp.property_id == prop.id).count()
    assert calls() == 1
    # A NEW change from the phone (a later follow-up) is a real one.
    ok(client.patch(path, headers=auth_headers, json={**body, "client_ref": "phone-ref-2",
                                                       "follow_up_at": "2026-10-29T10:00:00-05:00"}))
    assert calls() == 2


def test_a_repeat_never_skips_the_permission_check(client, admin_auth_headers, sample_advisor, team, db_session):
    from app.services.auth_service import create_access_token
    org = db_session.query(User).filter(User.role == "org_admin").first().organization_id
    rep2 = User(email="rep2@signal.test", full_name="Rep Two", role="advisor", organization_id=org,
                password_hash="x", must_change_password=False)
    db_session.add(rep2); db_session.commit()
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": rep2.id, "role": "field_specialist"}))
    a = _book(client, admin_auth_headers, sample_advisor.id)
    h = {"Authorization": "Bearer %s" % create_access_token(rep2, db_session)}
    r = client.patch("/wholesale/appointments/%s" % a["id"], headers=h,
                     json={"outcome": "dead", "client_ref": "x"})
    assert r.status_code in (403, 404)
