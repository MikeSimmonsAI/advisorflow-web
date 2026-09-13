"""THE IMPORTS SCREEN MUST ACTUALLY ANSWER.

Lead Imports showed "Failed to load import batches." inside a customer during
live verification, with `GET /import-batches` answering 503 while every other
call on the same screen answered 200.

503 is not a status this application produces on that path — its own refusals
are 401, 402 and 403, and an unhandled exception is mapped to 500 — so these
tests pin the statuses the ROUTE itself returns, in every access shape, so that
a 503 seen in production can be attributed to the platform in front of it
rather than to this code.
"""

import json
import uuid

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.workspace_access import SCOPE_CUSTOMER_ORG, WORKSPACE_HEADER


def _org(db, name, features=None):
    org = Organization(name=name, slug="o-" + uuid.uuid4().hex[:8], plan="standard",
                       industry="energy", is_active=True)
    if features is not None:
        org.enabled_features = json.dumps(features)
    db.add(org)
    db.commit()
    return org


def _user(db, org, role):
    u = User(organization_id=(org.id if org else None),
             email="imp+%s@example.com" % uuid.uuid4().hex[:6],
             password_hash=hash_password("TestPass123!"), full_name="Person",
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _auth(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def test_an_entitled_admin_gets_a_list(client, db_session):
    org = _org(db_session, "Entitled Co", features=["imports", "leads"])
    admin = _user(db_session, org, "org_admin")
    db_session.add(Membership(user_id=admin.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=org.id, role="org_admin", is_active=True))
    db_session.commit()

    r = client.get("/import-batches", headers=_auth(db_session, admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batches"] == []
    assert body["total"] == 0


def test_god_gets_a_list(client, db_session):
    god = _user(db_session, None, "god_admin")
    r = client.get("/import-batches", headers=_auth(db_session, god))
    assert r.status_code == 200, r.text


def test_an_unentitled_customer_is_refused_with_402_not_503(client, db_session):
    """The feature gate's refusal, stated exactly."""
    org = _org(db_session, "No Imports Co", features=[])
    admin = _user(db_session, org, "org_admin")
    db_session.add(Membership(user_id=admin.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=org.id, role="org_admin", is_active=True))
    db_session.commit()

    r = client.get("/import-batches", headers=_auth(db_session, admin))
    assert r.status_code == 402, r.text


def test_no_auth_is_401(client):
    r = client.get("/import-batches")
    assert r.status_code == 401


def test_the_query_string_the_browser_sends_is_accepted(client, db_session):
    """`?page=1&per_page=25` is what the Imports screen actually sends."""
    org = _org(db_session, "Entitled Co", features=["imports", "leads"])
    admin = _user(db_session, org, "org_admin")
    db_session.add(Membership(user_id=admin.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=org.id, role="org_admin", is_active=True))
    db_session.commit()

    r = client.get("/import-batches?page=1&per_page=25",
                   headers=_auth(db_session, admin))
    assert r.status_code == 200, r.text


def test_per_page_above_the_cap_is_a_validation_error_not_a_server_error(
        client, db_session):
    org = _org(db_session, "Entitled Co", features=["imports", "leads"])
    admin = _user(db_session, org, "org_admin")
    db_session.add(Membership(user_id=admin.id, scope_type=SCOPE_CUSTOMER_ORG,
                              scope_id=org.id, role="org_admin", is_active=True))
    db_session.commit()

    r = client.get("/import-batches?page=1&per_page=500",
                   headers=_auth(db_session, admin))
    assert r.status_code == 422, r.text
