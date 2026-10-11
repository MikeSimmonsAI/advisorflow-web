"""A person's own beds / baths / size / year on an EvoSense property: kept
apart from the county record, never overwritten by a county refresh, and
carried (with where it came from) to the deal."""
import pytest

from app.models.evosense_models import EvoSenseProperty
from app.services.evosense import common as C
from app.services.evosense import ingest as IN
from app.services.evosense import providers as PV
from app.services.evosense.promotion import _entered_note


@pytest.fixture
def prop(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="2620 Kirby St", city="Dallas",
                         state="TX", zip_code="75203", county="Dallas", parcel_apn="00000123",
                         bedrooms=3, bathrooms=1, square_feet=1100, year_built=1950,
                         fact_ranks=C.jdump({"bedrooms": 60, "bathrooms": 60, "square_feet": 60, "year_built": 60}))
    db_session.add(p)
    db_session.commit()
    return p


def url(p):
    return "/wholesale/evosense/properties/%s/facts" % p.id


def test_entry_is_kept_apart_and_survives_a_county_refresh(client, auth_headers, db_session, prop):
    r = client.patch(url(prop), headers=auth_headers, json={"bedrooms": 4, "bathrooms": 2.5})
    assert r.status_code == 200, r.text
    mine = r.json()["entered_by_you"]
    assert mine["bedrooms"]["value"] == 4 and mine["bedrooms"]["county_value"] == 3
    assert mine["bathrooms"]["value"] == 2.5 and mine["bathrooms"]["county_value"] == 1
    db_session.refresh(prop)
    assert float(prop.bedrooms) == 4 and float(prop.bathrooms) == 2.5

    # the county publishes a refresh: the person's entry stands, the county value is remembered
    IN.attach_to(db_session, prop, PV.PROVIDERS["dcad"], C.ASSESSOR,
                 {"bedrooms": 3, "bathrooms": 2, "square_feet": 1150}, None, IN.RANK_RECORDS)
    db_session.commit()
    db_session.refresh(prop)
    assert float(prop.bedrooms) == 4 and float(prop.bathrooms) == 2.5
    assert prop.square_feet == 1100            # same-rank county values conflict; never silently replaced
    mine = C.jload(prop.user_facts, {})
    assert mine["bathrooms"]["county_value"] == 2.0

    d = client.get("/wholesale/evosense/properties/%s" % prop.id, headers=auth_headers).json()
    assert d["facts"]["entered_by_you"]["bedrooms"]["value"] == 4

    note = _entered_note(prop)
    assert "Bedrooms 4" in note and "county record: 3" in note

    # clearing the entry goes back to the county record
    r = client.patch(url(prop), headers=auth_headers, json={"bathrooms": None})
    assert r.status_code == 200
    db_session.refresh(prop)
    assert float(prop.bathrooms) == 2.0 and "bathrooms" not in C.jload(prop.user_facts, {})


def test_bad_values_are_refused(client, auth_headers, prop):
    for body in ({"bedrooms": 2.5}, {"bathrooms": 2.3}, {"year_built": 1700}, {"square_feet": 5},
                 {"bedrooms": "x"}, {}, {"owner_name": "x"}):
        assert client.patch(url(prop), headers=auth_headers, json=body).status_code == 422, body


def test_another_organization_cannot_edit(client, db_session, prop):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    org = Organization(name="Other Co", slug="other-co-uf", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="other@uf.test", password_hash=hash_password("TestPass123!"),
             full_name="Other", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    assert client.patch(url(prop), headers=h, json={"bedrooms": 9}).status_code in (403, 404)
    db_session.refresh(prop)
    assert float(prop.bedrooms) == 3
