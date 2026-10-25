"""Who gets a PAID owner lookup: every ownership shape, synthetic owners only.
Nothing is called - this is the preflight's decision."""
import pytest

from app.models.evosense_models import EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.services.evosense import contact_batch as CB
from app.services.evosense.ingest import owner_type_of

LIST = "list:ownertypes"


def _prop(db, org, n, owner, *, mailing="1 Mail St", source="dcad"):
    p = EvoSenseProperty(organization_id=org, street_address="%d Owner Type St" % n, city="Dallas", state="TX",
                         zip_code="75216", county="Dallas", is_test=True, status="new", priority_score=90 - n)
    db.add(p)
    db.flush()
    db.add(EvoSenseObservation(organization_id=org, property_id=p.id, provider_key="csv_import",
                               connector_kind="import", capability="PROPERTY_SEARCH",
                               source_reference="csv:ownertypes:%d" % n, is_test=True))
    o = (db.query(EvoSenseOwner).filter(EvoSenseOwner.organization_id == org,
                                         EvoSenseOwner.display_name == owner).first())
    if o is None:
        o = EvoSenseOwner(organization_id=org, display_name=owner, owner_type=owner_type_of(owner),
                          is_test=True, mailing_street=mailing)
        db.add(o)
        db.flush()
    db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True, source=source))
    db.flush()
    return p


@pytest.mark.parametrize("name,state", [
    ("SYNTH PAT INDIVIDUAL", "ready"),
    ("SYNTH JOHN & MARY JOINT", "ready"),
    ("ESTATE OF SYNTH SAM", "review"),             # typed "estate" - who can sell is unknown
    ("SYNTH SAM EST", "review"),                   # estate indicated by the name
    ("SYNTH MARY LIFE ESTATE", "review"),
    ("SYNTH FAMILY TRUST", "review"),
    ("SYNTH HOLDINGS LLC", "review"),
    ("Q", "review"),                                # unknown owner type
])
def test_owner_shapes(db_session, sample_org, name, state):
    _prop(db_session, sample_org.id, 1, name)
    rows = CB.preflight(db_session, sample_org.id, LIST)["rows"]
    assert rows[0]["state"] == state, (name, rows[0]["reason"])


def test_one_owner_with_several_properties_is_looked_up_once(db_session, sample_org):
    db, org = db_session, sample_org.id
    for n in (1, 2, 3):
        _prop(db, org, n, "SYNTH LANDLORD MANY")
    pf = CB.preflight(db, org, LIST)
    assert pf["summary"]["ready"] == 1 and pf["summary"]["duplicate_owners"] == 2
    assert pf["summary"]["max_spend_cents"] == pf["summary"]["provider"]["unit_cost_cents"]


def test_an_unconfirmed_owner_waits_for_a_person(db_session, sample_org):
    _prop(db_session, sample_org.id, 1, "SYNTH LIST ONLY", source="csv_import")
    rows = CB.preflight(db_session, sample_org.id, LIST)["rows"]
    assert rows[0]["state"] == "review" and "official county record" in rows[0]["reason"]
