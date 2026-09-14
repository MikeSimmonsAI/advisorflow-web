"""A TYPO MUST NOT GRANT EVERY MODULE.

WHAT HAPPENED. `PUT /god/customers/{org}/features` took
`enabled: Optional[List[str]] = None`, and `None` on this endpoint means
RESTORE THE LEGACY ALL-ENABLED STATE. So a request that misspelled the field —
`{"enabled_features": [...]}` rather than `{"enabled": [...]}` — was not
rejected. The unknown key was ignored, `enabled` took its maximal default, and
the endpoint answered 200 having switched all twenty-two modules on.

I did exactly that to two live customers. It was caught only because the
response echoed `"mode": "all"`; a caller that did not read the body would have
left two organizations fully entitled and never known.

The intent — "grant everything" — is still expressible. It just has to be said:
`{"enabled": null}`. What is no longer possible is arriving there by accident.
"""

import json
import uuid

import pytest

from app.models.models import Organization, User
from app.services import entitlements as ent
from app.services.auth_service import create_access_token, hash_password

START = ["leads", "users", "reports"]


@pytest.fixture()
def org(db_session):
    o = Organization(name="Grant Co", slug="g-" + uuid.uuid4().hex[:8],
                     plan="trial", is_active=True,
                     enabled_features=json.dumps(START))
    db_session.add(o)
    db_session.commit()
    return o


@pytest.fixture()
def god(db_session):
    u = User(organization_id=None, email="god+%s@example.com" % uuid.uuid4().hex[:6],
             password_hash=hash_password("TestPass123!"), full_name="Operator",
             role="god_admin", is_active=True, must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _auth(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def _enabled(db, org):
    db.refresh(org)
    return ent.enabled_for(org)


def _unchanged(db, org):
    """Nothing was written. Compared as a set: `enabled_for` returns the list
    as stored and this assertion is about WHICH modules are on, not their
    order."""
    return set(_enabled(db, org)) == set(START)


# ── the mistake ─────────────────────────────────────────────────────────────

def test_the_misspelled_field_is_refused_and_changes_nothing(client, db_session,
                                                             org, god):
    """THE EXACT PAYLOAD THAT OVER-GRANTED TWO CUSTOMERS."""
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled_features": ["leads", "users"]})
    assert r.status_code == 422, r.text
    assert _unchanged(db_session, org), \
        "a refused request must not have written anything"


def test_an_empty_body_is_refused(client, db_session, org, god):
    r = client.put("/god/customers/%s/features" % org.id,
                   headers=_auth(db_session, god), json={})
    assert r.status_code == 422, r.text
    assert _unchanged(db_session, org)


def test_any_unknown_key_is_refused_even_alongside_a_valid_one(client, db_session,
                                                               org, god):
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled": ["leads"], "plan": "enterprise"})
    assert r.status_code == 422, r.text
    assert _unchanged(db_session, org)


# ── what must still work ────────────────────────────────────────────────────

def test_a_correct_allow_list_is_stored_exactly(client, db_session, org, god):
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled": ["leads", "campaigns"]})
    assert r.status_code == 200, r.text
    assert set(_enabled(db_session, org)) == {"campaigns", "leads"}
    assert r.json()["mode"] == "allow_list"


def test_granting_everything_is_still_possible_but_must_be_explicit(
        client, db_session, org, god):
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled": None})
    assert r.status_code == 200, r.text
    assert _enabled(db_session, org) is None
    assert r.json()["mode"] == "all"


def test_switching_everything_off_is_distinct_from_granting_everything(
        client, db_session, org, god):
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled": []})
    assert r.status_code == 200, r.text
    assert _enabled(db_session, org) == []
    assert r.json()["mode"] == "allow_list"


def test_an_unregistered_feature_key_is_still_refused(client, db_session, org, god):
    r = client.put("/god/customers/%s/features" % org.id, headers=_auth(db_session, god),
                   json={"enabled": ["leads", "not_a_feature"]})
    assert r.status_code == 400, r.text
    assert _unchanged(db_session, org)
