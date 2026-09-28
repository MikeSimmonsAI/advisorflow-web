"""Pipeline stage moves use the org's own tiers; the lead list filters on the
server (search / assignee / source / sort) inside the authorized scope."""
from app.models.models import AuditLogEntry, Lead
from tests.test_contacts_workspace_api import _h, _org, _user


def _lead(db, org, owner=None, **kw):
    lead = Lead(organization_id=org.id, first_name=kw.pop("first_name", "Ana"),
                last_name=kw.pop("last_name", "Lopez"), phone=kw.pop("phone", "12145550101"),
                email=kw.pop("email", None), status=kw.pop("status", "new"),
                tier=kw.pop("tier", "new_inquiry"), assigned_to_id=owner.id if owner else None, **kw)
    db.add(lead)
    db.commit()
    return lead


def test_stage_move_accepts_the_orgs_tier_and_keeps_status(client, db_session):
    org = _org(db_session, "Energy Co")
    admin = _user(db_session, org, "org_admin", "adm")
    lead = _lead(db_session, org, admin, status="replied")
    r = client.patch("/leads/%s/stage" % lead.id, params={"tier": "rate_review"},
                     headers=_h(db_session, admin))
    assert r.status_code == 200, r.text
    db_session.expire_all()
    lead = db_session.get(Lead, lead.id)
    assert lead.tier == "rate_review" and lead.status == "replied"
    assert db_session.query(AuditLogEntry).filter_by(action="lead.stage_moved",
                                                     organization_id=org.id).count() == 1


def test_stage_move_refuses_a_stage_the_pipeline_does_not_have(client, db_session):
    org = _org(db_session, "Energy Co 2")
    admin = _user(db_session, org, "org_admin", "adm")
    lead = _lead(db_session, org, admin)
    r = client.patch("/leads/%s/stage" % lead.id, params={"tier": "imminent"},
                     headers=_h(db_session, admin))
    assert r.status_code == 400
    assert "rate_review" in r.json()["detail"]["valid_stages"]


def test_stage_move_on_another_tenants_lead_is_404(client, db_session):
    a, b = _org(db_session, "A Energy"), _org(db_session, "B Energy")
    admin_a = _user(db_session, a, "org_admin", "a")
    lead_b = _lead(db_session, b)
    r = client.patch("/leads/%s/stage" % lead_b.id, params={"tier": "rate_review"},
                     headers=_h(db_session, admin_a))
    assert r.status_code == 404
    db_session.expire_all()
    assert db_session.get(Lead, lead_b.id).tier == "new_inquiry"


def test_list_search_assignee_source_and_sort_are_server_side(client, db_session):
    org = _org(db_session, "Energy Co 3")
    admin = _user(db_session, org, "org_admin", "adm")
    other = _user(db_session, org, "advisor", "adv")
    _lead(db_session, org, admin, first_name="Zed", last_name="Alpha", phone="12145559999",
          source="website")
    _lead(db_session, org, other, first_name="Amy", last_name="Beta", email="amy@x.com")
    h = _h(db_session, admin)
    names = lambda r: sorted(i["first_name"] for i in r.json()["items"])  # noqa: E731
    assert names(client.get("/leads/", params={"search": "amy@"}, headers=h)) == ["Amy"]
    assert names(client.get("/leads/", params={"search": "555-9999"}, headers=h)) == ["Zed"]
    assert names(client.get("/leads/", params={"search": "amy beta"}, headers=h)) == ["Amy"]
    assert names(client.get("/leads/", params={"search": "ZED ALPHA"}, headers=h)) == ["Zed"]
    assert names(client.get("/leads/", params={"assigned_to_id": other.id}, headers=h)) == ["Amy"]
    assert names(client.get("/leads/", params={"source": "website"}, headers=h)) == ["Zed"]
    r = client.get("/leads/", params={"sort": "name"}, headers=h)
    assert [i["last_name"] for i in r.json()["items"]] == ["Alpha", "Beta"]
    assert client.get("/leads/", params={"sort": "bogus"}, headers=h).status_code == 422
    # A literal % is not a wildcard.
    assert client.get("/leads/", params={"search": "%"}, headers=h).json()["total"] == 0


def test_an_advisor_searching_still_only_sees_their_own_leads(client, db_session):
    org = _org(db_session, "Energy Co 4")
    admin = _user(db_session, org, "org_admin", "adm")
    adv = _user(db_session, org, "advisor", "adv")
    _lead(db_session, org, admin, first_name="Hidden")
    _lead(db_session, org, adv, first_name="Mine")
    r = client.get("/leads/", params={"search": "i"}, headers=_h(db_session, adv))
    assert [i["first_name"] for i in r.json()["items"]] == ["Mine"]
