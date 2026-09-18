"""The default inbound owner is CONFIGURATION, and this proves it.

Generic website traffic - a visitor who arrives with no salesperson `?code=` -
has to reach somebody. Before this endpoint existed there was no way to say who
without writing a row by hand, and the temptation in that situation is to put a
name in the booking code. These tests exist to make that unnecessary and to keep
it that way: the last test resolves an inbound owner through the real booking
resolver and asserts it came back from the column, for a person the test itself
chose at runtime.

Nothing here books, emails or contacts anyone. Synthetic identities only.
"""
import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    INBOUND_DEFAULT_OWNER,
)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
URL = "/god/ops/brands/%s/inbound-owner"


# ── factories ───────────────────────────────────────────────────────────────

def _user(db, role="advisor", name=None):
    n = next(_SEQ)
    u = User(organization_id=None,
             email="inbound%d@example.invalid" % n,
             password_hash=hash_password("x"),
             full_name=name or ("Synthetic Person %d" % n),
             role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


def _god(db):
    return _user(db, role="god_admin", name="Synthetic Operator")


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


def _brand(db):
    n = next(_SEQ)
    p = Platform(name="Platform %d" % n, slug="plat-inbound-%d" % n)
    db.add(p); db.commit()
    b = BrandSalesOrg(platform_id=p.id, name="Brand %d" % n,
                      slug="brand-inbound-%d" % n)
    db.add(b); db.commit()
    return b


def _seat(db, bso, user, role=ROLE_SALES_MANAGER, active=True):
    m = Membership(user_id=user.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=bso.id, role=role, is_active=active)
    db.add(m); db.commit()
    return m


# ── the happy path ──────────────────────────────────────────────────────────

def test_sets_the_owner_when_the_seat_is_active(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    person = _user(db_session)
    _seat(db_session, bso, person)

    r = client.patch(URL % bso.id, json={"user_id": person.id},
                     headers=_h(db_session, god))
    assert r.status_code == 200, r.text
    owner = r.json()["inbound_owner"]
    assert owner["user_id"] == person.id
    assert owner["configured"] is True
    assert owner["seat_is_active"] is True
    assert owner["role"] == ROLE_SALES_MANAGER

    db_session.refresh(bso)
    assert bso.default_inbound_owner_user_id == person.id


def test_stamps_the_one_supported_assignment_mode(client, db_session):
    """A brand that names an owner while the mode says something this build
    cannot resolve would look configured and still refuse every visitor."""
    god = _god(db_session)
    bso = _brand(db_session)
    bso.inbound_assignment_mode = None
    db_session.commit()
    person = _user(db_session)
    _seat(db_session, bso, person)

    client.patch(URL % bso.id, json={"user_id": person.id},
                 headers=_h(db_session, god))
    db_session.refresh(bso)
    assert bso.inbound_assignment_mode == INBOUND_DEFAULT_OWNER


# ── the refusals, which are the point ───────────────────────────────────────

def test_refuses_somebody_with_no_seat_on_this_brand(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    outsider = _user(db_session, name="No Seat Person")

    r = client.patch(URL % bso.id, json={"user_id": outsider.id},
                     headers=_h(db_session, god))
    assert r.status_code == 400
    assert "not on this brand's sales team" in r.json()["detail"]
    db_session.refresh(bso)
    assert bso.default_inbound_owner_user_id is None


def test_refuses_a_seat_that_belongs_to_a_different_brand(client, db_session):
    """The check is scoped to THIS brand. A manager of another brand holding a
    perfectly good membership elsewhere must not become this brand's inbound
    owner."""
    god = _god(db_session)
    here, elsewhere = _brand(db_session), _brand(db_session)
    person = _user(db_session)
    _seat(db_session, elsewhere, person)

    r = client.patch(URL % here.id, json={"user_id": person.id},
                     headers=_h(db_session, god))
    assert r.status_code == 400
    db_session.refresh(here)
    assert here.default_inbound_owner_user_id is None


def test_refuses_a_deactivated_seat(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    person = _user(db_session)
    _seat(db_session, bso, person, active=False)

    r = client.patch(URL % bso.id, json={"user_id": person.id},
                     headers=_h(db_session, god))
    assert r.status_code == 400
    assert "deactivated" in r.json()["detail"]


def test_unknown_brand_and_unknown_user_are_404(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    person = _user(db_session)
    _seat(db_session, bso, person)

    assert client.patch(URL % "no-such-brand", json={"user_id": person.id},
                        headers=_h(db_session, god)).status_code == 404
    assert client.patch(URL % bso.id, json={"user_id": "no-such-user"},
                        headers=_h(db_session, god)).status_code == 404


def test_only_god_may_set_it(client, db_session):
    bso = _brand(db_session)
    person = _user(db_session)
    _seat(db_session, bso, person)
    rep = _user(db_session)
    _seat(db_session, bso, rep, role=ROLE_SALES_REP)

    r = client.patch(URL % bso.id, json={"user_id": person.id},
                     headers=_h(db_session, rep))
    assert r.status_code == 403


# ── clearing, and going stale ───────────────────────────────────────────────

def test_null_clears_it_and_returns_the_brand_to_refusing(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    person = _user(db_session)
    _seat(db_session, bso, person)
    client.patch(URL % bso.id, json={"user_id": person.id},
                 headers=_h(db_session, god))

    r = client.patch(URL % bso.id, json={"user_id": None},
                     headers=_h(db_session, god))
    assert r.status_code == 200
    assert r.json()["inbound_owner"]["configured"] is False
    db_session.refresh(bso)
    assert bso.default_inbound_owner_user_id is None


def test_a_seat_deactivated_afterwards_shows_as_inactive(client, db_session):
    """The column keeps pointing at them; the page has to say it is now broken
    rather than read as configured."""
    god = _god(db_session)
    bso = _brand(db_session)
    person = _user(db_session)
    seat = _seat(db_session, bso, person)
    client.patch(URL % bso.id, json={"user_id": person.id},
                 headers=_h(db_session, god))

    seat.is_active = False
    db_session.commit()

    detail = client.get("/god/ops/brands/%s" % bso.id,
                        headers=_h(db_session, god)).json()
    owner = detail["configuration"]["inbound_owner"]
    assert owner["user_id"] == person.id
    assert owner["configured"] is True
    assert owner["seat_is_active"] is False


def test_brand_detail_surfaces_an_unset_owner(client, db_session):
    god = _god(db_session)
    bso = _brand(db_session)
    detail = client.get("/god/ops/brands/%s" % bso.id,
                        headers=_h(db_session, god)).json()
    assert detail["configuration"]["inbound_owner"]["configured"] is False


# ── the booking path reads CONFIGURATION, not a name in code ────────────────

def test_the_real_resolver_returns_whoever_was_configured(client, db_session):
    """The test that makes hard-coding unnecessary.

    Two different people, chosen here at runtime. The resolver the public
    booking endpoint actually calls returns whichever one the setting names, and
    follows it when it changes.
    """
    from app.services import sales_booking_codes as codes

    god = _god(db_session)
    bso = _brand(db_session)
    first = _user(db_session, name="First Configured Person")
    second = _user(db_session, name="Second Configured Person")
    _seat(db_session, bso, first)
    _seat(db_session, bso, second, role=ROLE_SALES_REP)

    # Nobody configured: the brand refuses rather than picking someone.
    refused = codes.resolve_inbound_owner(db_session, bso)
    assert refused["ok"] is False
    assert refused["owner"] is None

    client.patch(URL % bso.id, json={"user_id": first.id},
                 headers=_h(db_session, god))
    db_session.refresh(bso)
    resolved = codes.resolve_inbound_owner(db_session, bso)
    assert resolved["ok"] is True
    assert resolved["owner"].id == first.id

    client.patch(URL % bso.id, json={"user_id": second.id},
                 headers=_h(db_session, god))
    db_session.refresh(bso)
    assert codes.resolve_inbound_owner(db_session, bso)["owner"].id == second.id
