"""CUSTOMER LOCATIONS, from the surface that now has controls for them.

WHY THIS FILE EXISTS

  The endpoints under /god/customers/{org_id}/locations were implemented and
  authorized long before anything in the UI called them. The God customer
  detail page could report "No locations yet" and nothing else, so a customer
  could be blocked from activation by a missing location that no supported
  screen could add. The Locations tab now has Add Location and Edit, and these
  tests pin the contract that tab depends on:

  1. THE PAYLOAD THE TAB SENDS IS THE PAYLOAD THE MODEL ACCEPTS. The editor
     posts exactly the LocationIn field names; a rename on either side must
     fail here rather than in production.

  2. THE FIRST LOCATION IS PRIMARY. Bookings route to the primary. A customer
     with a location but no primary would satisfy the blocker while still
     having nowhere to route.

  3. A SECOND CLICK DOES NOT CREATE A SECOND LOCATION. The button disables
     itself while saving, but the durable guarantee is the server's: the same
     name is refused.

  4. THE BLOCKER CLEARS FROM THE SAME PAYLOAD THE PAGE RE-READS. The tab shows
     readiness from GET /god/customers/{org_id}; if adding a location did not
     move that, the operator would fix the problem and still be told it was
     there.

  5. IT IS STILL GOD-ONLY. Giving a customer admin a way to add their own
     location was never part of closing this gap.
"""

import itertools

import pytest

from app.models.models import Organization, Platform, User
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)

# The exact body the Locations tab sends — see LocationEditor in
# frontend/src/pages/god/CustomerDetail.jsx. Keys here are LocationIn's.
UI_BODY = {
    "name": "Main Office",
    "address_line1": "100 Example Ave",
    "address_line2": "Suite 400",
    "city": "Dallas",
    "state": "TX",
    "postal_code": "75201",
    "phone": "214-555-0100",
    "email": "main@example.com",
    "timezone": "America/Chicago",
    "notes": "Front desk takes walk-ins.",
}


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _user(db, role="advisor", org_id=None, email=None):
    u = User(organization_id=org_id,
             email=email or ("loc%d@example.com" % next(_SEQ)),
             password_hash=hash_password("x"), full_name="Person", role=role,
             must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def world(db_session):
    plat = Platform(name="Brand %d" % next(_SEQ), slug="brand-loc-%d" % next(_SEQ))
    db_session.add(plat)
    db_session.commit()
    cust = Organization(name="Customer %d" % next(_SEQ),
                        slug="cust-loc-%d" % next(_SEQ),
                        plan="standard", platform_id=plat.id)
    db_session.add(cust)
    db_session.commit()
    god = _user(db_session, role="god_admin")
    return dict(plat=plat, cust=cust, god=god)


def _path(world, suffix=""):
    return "/god/customers/" + world["cust"].id + "/locations" + suffix


# ═════════════════════════════════════════════════════════════════════════════
# 1. The payload the UI sends
# ═════════════════════════════════════════════════════════════════════════════

def test_the_editors_payload_is_accepted_field_for_field(client, db_session, world):
    r = client.post(_path(world), json=UI_BODY, headers=_h(db_session, world["god"]))
    assert r.status_code == 201, r.text
    row = r.json()
    for key, sent in UI_BODY.items():
        assert row[key] == sent, "%s was not stored as sent" % key


def test_name_alone_is_enough(client, db_session, world):
    """Every other field is optional on LocationIn, and the editor only sends
    the ones that were filled in."""
    r = client.post(_path(world), json={"name": "Just A Name"},
                    headers=_h(db_session, world["god"]))
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Just A Name"


@pytest.mark.parametrize("bad", [{}, {"name": ""}, {"name": "   "}])
def test_a_location_without_a_name_is_refused(client, db_session, world, bad):
    r = client.post(_path(world), json=bad, headers=_h(db_session, world["god"]))
    assert r.status_code in (400, 422), r.text


# ═════════════════════════════════════════════════════════════════════════════
# 2. Primary
# ═════════════════════════════════════════════════════════════════════════════

def test_the_first_location_becomes_primary(client, db_session, world):
    r = client.post(_path(world), json={"name": "First"},
                    headers=_h(db_session, world["god"]))
    assert r.json()["is_primary"] is True


def test_a_second_location_does_not_steal_primary(client, db_session, world):
    h = _h(db_session, world["god"])
    client.post(_path(world), json={"name": "First"}, headers=h)
    second = client.post(_path(world), json={"name": "Second"}, headers=h)
    assert second.json()["is_primary"] is False

    listed = client.get(_path(world), headers=h).json()["locations"]
    assert [l["name"] for l in listed if l["is_primary"]] == ["First"]


# ═════════════════════════════════════════════════════════════════════════════
# 3. A repeated save does not duplicate
# ═════════════════════════════════════════════════════════════════════════════

def test_the_same_name_twice_is_refused_not_duplicated(client, db_session, world):
    h = _h(db_session, world["god"])
    first = client.post(_path(world), json=UI_BODY, headers=h)
    assert first.status_code == 201
    again = client.post(_path(world), json=UI_BODY, headers=h)
    assert again.status_code == 400, again.text

    listed = client.get(_path(world), headers=h).json()["locations"]
    assert len(listed) == 1, "a repeated save created a second location"


# ═════════════════════════════════════════════════════════════════════════════
# 4. What the tab re-reads
# ═════════════════════════════════════════════════════════════════════════════

def _detail(client, db_session, world):
    return client.get("/god/customers/" + world["cust"].id,
                      headers=_h(db_session, world["god"])).json()


def test_adding_a_location_clears_the_location_blocker(client, db_session, world):
    before = _detail(client, db_session, world)
    assert any("location" in b.lower() for b in before["readiness"]["blockers"]), \
        "expected a location blocker before one exists"

    client.post(_path(world), json={"name": "Main Office"},
                headers=_h(db_session, world["god"]))

    after = _detail(client, db_session, world)
    assert not any("location" in b.lower() for b in after["readiness"]["blockers"])
    assert [l["name"] for l in after["locations"]] == ["Main Office"]
    assert after["readiness"]["sections"]["locations"]["primary"] == "Main Office"


# ═════════════════════════════════════════════════════════════════════════════
# 5. Edit
# ═════════════════════════════════════════════════════════════════════════════

def test_edit_sends_only_changed_fields_and_keeps_the_rest(client, db_session, world):
    h = _h(db_session, world["god"])
    created = client.post(_path(world), json=UI_BODY, headers=h).json()

    r = client.patch(_path(world, "/" + created["id"]),
                     json={"name": "Renamed Office", "city": "Plano"}, headers=h)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "Renamed Office"
    assert row["city"] == "Plano"
    # Untouched fields are untouched, not blanked by an absent key.
    assert row["phone"] == UI_BODY["phone"]
    assert row["postal_code"] == UI_BODY["postal_code"]


def test_editing_an_unknown_location_is_not_found(client, db_session, world):
    r = client.patch(_path(world, "/does-not-exist"), json={"name": "X"},
                     headers=_h(db_session, world["god"]))
    assert r.status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# 6. Authority — unchanged by this work
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("role", ["advisor", "org_admin", "super_admin", "viewer"])
def test_location_management_is_god_only(client, db_session, world, role):
    """A customer admin still cannot add their own location. Closing a UI gap
    is not a reason to move where authority lives."""
    actor = _user(db_session, role=role, org_id=world["cust"].id)
    h = _h(db_session, actor)

    assert client.get(_path(world), headers=h).status_code in (401, 403, 404)
    assert client.post(_path(world), json={"name": "Theirs"},
                       headers=h).status_code in (401, 403, 404)
    assert client.patch(_path(world, "/whatever"), json={"name": "X"},
                        headers=h).status_code in (401, 403, 404)

    god_view = client.get(_path(world), headers=_h(db_session, world["god"]))
    assert god_view.json()["locations"] == [], \
        "a refused caller still managed to create a location"


def test_anonymous_cannot_reach_the_location_endpoints(client, world):
    assert client.get(_path(world)).status_code in (401, 403)
    assert client.post(_path(world), json={"name": "X"}).status_code in (401, 403)
