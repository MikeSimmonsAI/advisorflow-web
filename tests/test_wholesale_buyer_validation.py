"""PATCH-time validation on buyers and buy boxes (2026-10-07).

A PATCH that moves one end of a range is checked against the stored other end;
negatives and an unreachable buyer are refused with a 400 and a reason.
"""
import pytest


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


@pytest.fixture()
def buyer(client, auth_headers):
    return ok(client.post("/wholesale/buyers", headers=auth_headers,
                          json=dict(company_name="Validation LLC", email="v@example.test")))


def test_patch_max_price_below_stored_min_is_refused(client, auth_headers, buyer):
    box = ok(client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"], headers=auth_headers,
                         json=dict(min_price=200000, max_price=400000)))
    r = client.patch("/wholesale/buy-boxes/%s" % box["id"], headers=auth_headers,
                     json=dict(max_price=100000))
    assert r.status_code == 400
    assert ok(client.patch("/wholesale/buy-boxes/%s" % box["id"], headers=auth_headers,
                           json=dict(max_price=300000)))["max_price"] == 300000


@pytest.mark.parametrize("body", [dict(min_price=-1), dict(min_beds=4, max_beds=2),
                                  dict(min_sqft=3000, max_sqft=1000)])
def test_bad_box_values_refused(client, auth_headers, buyer, body):
    r = client.post("/wholesale/buyers/%s/buy-boxes" % buyer["id"], headers=auth_headers, json=body)
    assert r.status_code == 400


def test_patch_cannot_leave_buyer_unreachable(client, auth_headers, buyer):
    r = client.patch("/wholesale/buyers/%s" % buyer["id"], headers=auth_headers,
                     json=dict(email=""))
    assert r.status_code == 400
    assert client.patch("/wholesale/buyers/%s" % buyer["id"], headers=auth_headers,
                        json=dict(notes="fine")).status_code == 200


def test_patch_rejects_negative_counts_and_bad_rating(client, auth_headers, buyer):
    for body in (dict(past_deals_count=-1), dict(reliability_rating=9)):
        assert client.patch("/wholesale/buyers/%s" % buyer["id"], headers=auth_headers,
                            json=body).status_code == 400
