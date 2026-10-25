"""ATTOM adapter - mocked HTTP only. Nothing reaches ATTOM, nothing is billed."""
import pytest

from app.services import comp_rules as CR
from app.services.evosense import common as C
from app.services.evosense import provider_eval as PE
from app.services.evosense import providers as PV
from app.services.evosense import vendors as V

SALE = {"property": [{
    "identifier": {"attomId": 111},
    "address": {"line1": "12 SYNTH COMP LN", "locality": "DALLAS", "countrySubd": "TX", "postal1": "75216"},
    "location": {"latitude": "32.7", "longitude": "-96.8", "distance": 0.4},
    "summary": {"proptype": "SFR", "yearbuilt": 1984},
    "building": {"size": {"livingsize": 1500}, "rooms": {"beds": 3, "bathstotal": 2}},
    "sale": {"saleTransDate": "2026-05-01", "amount": {"saleamt": 250000, "saletranstype": "Resale"}},
}]}


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def fake(method, url, *, headers=None, json=None, params=None, auth=None):
        seen.append({"url": url, "headers": headers, "params": params})
        if url.endswith("sale/snapshot"):
            return dict(SALE, _status=200)
        if url.endswith("attomavm/detail"):
            return {"_status": 404}
        if url.endswith("property/detailmortgageowner"):
            return {"_status": 200, "property": [{
                "identifier": {"attomId": 5, "apn": "000-1", "fips": "48113"},
                "summary": {"proptype": "SFR", "yearbuilt": 1990},
                "building": {"size": {"livingsize": 1400}, "rooms": {"beds": 3, "bathstotal": 2}},
                "owner": {"owner1": {"fullname": "SYNTH OWNER"}},
                "mortgage": {"amount": 120000, "date": "2015-03-01", "lender": {"lastname": "SYNTH BANK"}}}]}
        if url.endswith("assessment/detail"):
            return {"_status": 200, "property": [{"assessment": {"assessed": {"assdttlvalue": 200000},
                                                                 "market": {"mktttlvalue": 210000},
                                                                 "tax": {"taxamt": 5000, "taxyear": 2026}}}]}
        if url.endswith("saleshistory/detail"):
            return {"_status": 200, "property": [{"salehistory": [
                {"saleTransDate": "2015-03-01", "amount": {"saleamt": 150000, "saledoctype": "WD"}},
                {"saleTransDate": "2004-01-01", "amount": {"saleamt": 0}}]}]}
        raise AssertionError(url)
    monkeypatch.setattr(V, "_http", fake)
    monkeypatch.setenv("ATTOM_API_KEY", "fake-not-real")
    return seen


def test_texas_sale_amounts_are_unverified_never_closed_sales(calls):
    a = PV.PROVIDERS["attom_comps"]
    comps = a.search(C.COMPS, {"latitude": 32.7, "longitude": -96.8, "state": "TX",
                               "property_type": "single_family"})
    assert comps[0]["price_source"] == CR.PRICE_UNVERIFIED_RECORD
    assert comps[0]["sale_price"] == 250000 and comps[0]["square_feet"] == 1500
    assert calls[0]["headers"]["APIKey"] == "fake-not-real"
    assert calls[0]["params"]["radius"] == 1 and "startSaleSearchDate" in calls[0]["params"]


def test_outside_non_disclosure_states_the_amount_is_a_public_record(calls):
    row = dict(SALE["property"][0], address={"countrySubd": "FL", "line1": "1 X"})
    assert V.attom_comp(row, "FL")["price_source"] == CR.PRICE_PUBLIC_RECORD


def test_profile_labels_every_value_and_reports_missing_packages(calls):
    prof = PV.PROVIDERS["attom_comps"].profile({"street_address": "1 Synth St", "city": "Dallas", "state": "TX",
                                                 "zip_code": "75216"})
    assert prof["missing"] == ["avm"] and prof["avm"]["value"] is None          # nothing filled in
    assert "not today's payoff" in prof["mortgage"]["truth"]
    assert "not a sale price" in prof["assessment"]["truth"]
    assert prof["sales_history"][0]["kind"] == CR.PRICE_UNVERIFIED_RECORD
    assert prof["sales_history"][1]["kind"] == "TRANSFER_NO_PRICE"
    assert prof["texas_rule"]
    assert calls[0]["params"] == {"address1": "1 Synth St", "address2": "Dallas, TX 75216"}


def test_attom_is_evaluation_only_and_never_routed(db_session, sample_org, monkeypatch):
    monkeypatch.setenv("ATTOM_API_KEY", "fake-not-real")
    a = PV.PROVIDERS["attom_comps"]
    assert a.evaluation_only is True
    from app.services import wholesale_auto_comps as AC
    src = [x for x in AC.ADAPTERS if x.key == "attom_comps"][0]
    ok, why = src.available(db_session, sample_org.id, {})
    assert ok is False and "evaluation-only" in why and "truth gate" in why


def test_a_paid_vendor_with_no_price_cannot_be_planned(db_session, sample_org):
    from app.models.evosense_models import EvoSenseProperty
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="1 Synth St", city="Dallas", state="TX",
                         is_test=False, status="new")
    db_session.add(p)
    db_session.flush()
    with pytest.raises(PE.EvaluationRefused) as e:
        PE.plan(db_session, sample_org.id, name="attom", provider_keys=["attom_comps"], property_ids=[p.id],
                mode=PE.MODE_COMPS)
    assert "no price on file" in str(e.value)
