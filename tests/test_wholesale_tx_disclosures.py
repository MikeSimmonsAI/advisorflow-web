"""Texas Occupations Code 1101.0045: a wholesaler must tell BOTH the seller and
any potential buyer, in writing, that it holds only an equitable interest
(a contract), not the property. The purchase agreement carries the seller
notice; every investor page carries the buyer notice."""
from app.services import wholesale_contract_docs as D
from app.services import wholesale_publication as P


def _values():
    return {"seller_name": "Maria Lopez", "buyer_entity": "EVO Integrated Properties", "address": "2620 Kirby St",
            "city": "Dallas", "state": "TX", "zip_code": "75203", "county": "Dallas", "price": "$150,000",
            "earnest_money": "$1,000", "title_company": "Sample Title", "option_days": "10",
            "closing_date": "Nov 30, 2026", "closing_costs": "Each side pays its usual costs.",
            "possession": "At closing."}


def test_purchase_agreement_carries_the_seller_notice():
    html = D.render("purchase_agreement", _values())
    assert "1101.0045" in html
    assert "Notice to Seller" in html and "equitable" in html and "interest" in html
    assert "not acting" in html and "broker or agent" in html


def test_buyer_notice_names_the_operator_and_is_always_present():
    d = P.buyer_disclosure("EVO Integrated Properties")
    assert d["law"].endswith("1101.0045")
    assert d["text"].startswith("EVO Integrated Properties does not own this property")
    assert "equitable interest" in d["text"] and "not acting as your real estate broker" in d["text"]
    blank = P.buyer_disclosure(None)
    assert blank["text"].startswith("The party offering this deal does not own")
