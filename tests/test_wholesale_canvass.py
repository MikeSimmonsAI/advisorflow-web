"""Canvassing: a route of doors from EvoSense's findings in a ZIP (best first,
opted-out owners skipped), worked by one canvasser, and a hot door handed to
the canvasser's manager as a call-back."""
from app.models.models import User


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def _props(db, org_id):
    from app.models.evosense_models import EvoSenseProperty as P
    rows = [P(organization_id=org_id, street_address="%d Elm St" % n, city="Dallas", state="TX",
              zip_code="75208", opportunity_score=score, status=status, is_test=True)
            for n, score, status in ((10, 90, "high_opportunity"), (20, 40, "new"), (30, 99, "suppressed"),
                                     (40, 70, "needs_you"))]
    rows.append(P(organization_id=org_id, street_address="1 Far Rd", city="Fort Worth", state="TX",
                  zip_code="76104", opportunity_score=95, is_test=True))
    db.add_all(rows)
    db.commit()
    return rows


def _team(client, admin_h, canvasser, manager):
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": manager.id, "role": "manager", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_h,
                   json={"user_id": canvasser.id, "role": "canvasser", "reports_to_user_id": manager.id}))


def test_route_by_zip_best_first_and_opted_out_skipped(client, admin_auth_headers, auth_headers, sample_advisor,
                                                      second_advisor, sample_org, db_session):
    _props(db_session, sample_org.id)
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    pv = ok(client.get("/wholesale/canvass/preview?zips=75208&include_test=true", headers=admin_auth_headers))
    assert pv["doors"] == 3
    r = ok(client.post("/wholesale/canvass/routes", headers=admin_auth_headers, json={
        "zips": "75208", "assigned_to_id": sample_advisor.id, "route_date": "2026-10-13", "include_test": True}))
    assert [d["address"].split(",")[0] for d in r["doors"]] == ["10 Elm St", "40 Elm St", "20 Elm St"]
    assert r["route_date"] and r["assigned_to_name"]
    mine = ok(client.get("/wholesale/canvass/routes", headers=auth_headers))
    assert [x["id"] for x in mine["items"]] == [r["id"]] and mine["can_build"] is False
    assert client.post("/wholesale/canvass/routes", headers=auth_headers,
                       json={"zips": "75208", "include_test": True}).status_code == 403
    assert client.post("/wholesale/canvass/routes", headers=admin_auth_headers,
                       json={"zips": "7520"}).status_code == 422


def test_hot_door_goes_to_the_manager(client, admin_auth_headers, auth_headers, sample_advisor, second_advisor,
                                      sample_org, db_session):
    from app.models.evosense_models import EvoSenseFollowUp
    _props(db_session, sample_org.id)
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    r = ok(client.post("/wholesale/canvass/routes", headers=admin_auth_headers, json={
        "zips": "75208", "assigned_to_id": sample_advisor.id, "include_test": True}))
    first, second = r["doors"][0], r["doors"][1]
    ok(client.patch("/wholesale/canvass/doors/%s" % second["id"], headers=auth_headers, json={"result": "not_home"}))
    hot = ok(client.patch("/wholesale/canvass/doors/%s" % first["id"], headers=auth_headers, json={
        "result": "hot", "talked_to": "Linda (owner)", "phone": "214-555-0199", "permission": "yes",
        "notes": "Wants out before winter"}))
    assert hot["handed_off"] is True and hot["interest"] == "hot"
    fu = db_session.query(EvoSenseFollowUp).one()
    assert fu.assigned_to_id == second_advisor.id and "Linda" in fu.note and "214-555-0199" in fu.note
    route = ok(client.get("/wholesale/canvass/routes/%s" % r["id"], headers=auth_headers))
    assert route["doors_done"] == 2 and route["hot"] == 1
    # marking it hot again does not send a second hand-off
    ok(client.patch("/wholesale/canvass/doors/%s" % first["id"], headers=auth_headers, json={"notes": "x"}))
    assert db_session.query(EvoSenseFollowUp).count() == 1


def test_another_canvasser_cannot_see_or_work_it(client, admin_auth_headers, sample_advisor, second_advisor,
                                                 sample_org, db_session):
    from app.services.auth_service import create_access_token
    _props(db_session, sample_org.id)
    _team(client, admin_auth_headers, sample_advisor, second_advisor)
    other = User(email="c2@canvass.test", full_name="Canvasser Two", role="advisor",
                 organization_id=sample_org.id, password_hash="x", must_change_password=False)
    db_session.add(other); db_session.commit()
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers, json={"user_id": other.id, "role": "canvasser"}))
    r = ok(client.post("/wholesale/canvass/routes", headers=admin_auth_headers, json={
        "zips": "75208", "assigned_to_id": sample_advisor.id, "include_test": True}))
    h = {"Authorization": "Bearer %s" % create_access_token(other, db_session)}
    assert client.get("/wholesale/canvass/routes/%s" % r["id"], headers=h).status_code == 404
    assert client.patch("/wholesale/canvass/doors/%s" % r["doors"][0]["id"], headers=h,
                        json={"result": "hot"}).status_code == 404
    assert ok(client.get("/wholesale/canvass/routes", headers=h))["items"] == []
