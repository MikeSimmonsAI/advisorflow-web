"""The customer front door after the switchboard: all registered jobs visible,
offers derived from test_mode.VERIFIED_JOBS (no second list to drift)."""

import json

from app.models.models import Organization, Platform, User
from app.services.ai_deployment import switchboard, test_mode
from app.services.auth_service import create_access_token, hash_password
from app.services.workforce import registry as wf_registry


def _brand(db, name, slug, org_name, org_slug, email):
    brand = Platform(name=name, slug=slug, is_active=True)
    db.add(brand)
    db.flush()
    org = Organization(name=org_name, slug=org_slug, platform_id=brand.id,
                       plan="trial", is_demo=False,
                       enabled_features=json.dumps(
                           ["leads", "sms", "email", "booking", "calendar",
                            "crm"]))
    db.add(org)
    db.flush()
    admin = User(organization_id=org.id, email=email,
                 password_hash=hash_password("OwnerPass123!"),
                 full_name="Owner", role="org_admin",
                 must_change_password=False, is_active=True)
    db.add(admin)
    db.commit()
    return brand, org, admin


def test_front_door_shows_all_jobs_and_offers_only_proven(client, db_session):
    brand, org, admin = _brand(db_session, "Front Door Brand", "fd-a",
                               "Front Door Account", "fd-acct-a",
                               "a@fd.invalid")
    other, other_org, stranger = _brand(db_session, "Unswitched Brand",
                                        "fd-b", "Unswitched Account",
                                        "fd-acct-b", "b@fd.invalid")
    god = User(organization_id=org.id, email="god@fd.invalid",
               password_hash=hash_password("GodPass123!"),
               full_name="Operator", role="god_admin",
               must_change_password=False, is_active=True)
    db_session.add(god)
    db_session.commit()

    first = switchboard.enable(db_session, platform_id=brand.id,
                               actor_user_id=god.id)
    db_session.commit()
    again = switchboard.enable(db_session, platform_id=brand.id,
                               actor_user_id=god.id)
    db_session.commit()
    assert first["changed"] and again["changed"] is False

    def home(user):
        token = create_access_token(user, db_session)
        r = client.get("/ai-workforce/home",
                       headers={"Authorization": "Bearer %s" % token})
        assert r.status_code == 200, r.text
        return r.json()

    mine = home(admin)
    cards = {c["key"]: c for c in mine["jobs"]}
    assert sorted(cards) == sorted(wf_registry.ALL_TEMPLATE_KEYS)
    assert len(cards) == 11
    for key, c in cards.items():
        if key in test_mode.VERIFIED_JOBS:
            assert c["proven"] and c["can_set_up"], (key, c)
        else:
            assert not c["proven"] and not c["can_set_up"], (key, c)
            assert c["availability_label"] == "Not yet proven"

    theirs = home(stranger)
    assert len(theirs["jobs"]) == 11
    assert not any(c["can_set_up"] for c in theirs["jobs"])
    assert "Front Door Account" not in json.dumps(theirs)
    assert "Unswitched Account" not in json.dumps(mine)

    assert client.get("/ai-workforce/home").status_code in (401, 403)
