"""A statewide source covers a county only where it has returned data for this
workspace - declaring 'statewide' is not coverage."""
from app.models.evosense_models import EvoSenseObservation, EvoSenseProperty
from app.services.evosense import providers as PV


def test_verified_counties_come_from_records_not_declared_scope(db_session, sample_org):
    db = db_session
    for county, key in (("HARRIS", "lgbs_tax_sales"), ("McLennan", "lgbs_tax_sales"), ("Dallas", "dcad")):
        p = EvoSenseProperty(organization_id=sample_org.id, street_address="1 A ST", city="X", state="TX",
                             zip_code="75001", county=county, address_key="k-%s" % county)
        db.add(p)
        db.flush()
        db.add(EvoSenseObservation(organization_id=sample_org.id, property_id=p.id, provider_key=key,
                                   source_reference="r-%s" % county, connector_kind="real", capability="TAX"))
    db.commit()
    ver = PV.verified_counties(db, sample_org.id, ["lgbs_tax_sales", "txgio_roll"])
    assert ver == {"lgbs_tax_sales": {"Harris", "McLennan"}}
    rep = PV.status_report(db, sample_org.id)
    row = next(r for r in rep["providers"] if r["key"] == "lgbs_tax_sales")
    assert row["coverage_area"]["verified_counties"] == ["Harris", "McLennan"]
    roll = next(r for r in rep["providers"] if r["key"] == "txgio_roll")
    assert roll["coverage_area"]["verified_counties"] == []
