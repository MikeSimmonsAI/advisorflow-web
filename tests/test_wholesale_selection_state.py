"""Buyer selection state consistency (endpoint + DB). Needs a provisioned runner.

One current-selected buyer per deal: replacing the selection must move the
previous buyer off status "selected", leave an audit row, be retry-safe, and
generic response/update calls must not bypass the rule or cross tenants.
"""
from app.models.wholesale_models import WholesaleBuyerOutreach, WholesaleEvent
from tests.test_wholesale_cross_tenant import other_headers, other_org  # noqa: F401
from tests.test_wholesale_workflow import ok, with_buyers  # noqa: F401  (fixtures)


def _offer(client, headers, outreach_id, amount):
    return ok(client.post("/wholesale/outreach/%s/response" % outreach_id,
                          headers=headers,
                          json={"status": "offer_submitted", "offer_amount": amount}))


def _select(client, headers, deal_id, outreach_id):
    return client.post("/wholesale/deals/%s/select-buyer" % deal_id,
                       headers=headers, json={"outreach_id": outreach_id})


def _both(with_buyers, client, headers):  # noqa: F811
    d = with_buyers["deal_id"]
    hi, so = with_buyers["buyers"]["high"], with_buyers["buyers"]["solid"]
    _offer(client, headers, hi["outreach_id"], hi["offer"])
    _offer(client, headers, so["outreach_id"], so["offer"])
    return d, hi, so


def test_replaced_buyer_is_no_longer_selected(client, auth_headers, with_buyers,
                                              db_session):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    result = ok(_select(client, auth_headers, d, so["outreach_id"]))
    assert result["replaced_outreach_ids"] == [hi["outreach_id"]]
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d, headers=auth_headers))
    by_id = {b["outreach_id"]: b for b in board["buyers"]}
    assert by_id[hi["outreach_id"]]["status"] == "offer_submitted"
    assert by_id[hi["outreach_id"]]["is_selected"] is False
    assert by_id[so["outreach_id"]]["status"] == "selected"
    assert sum(1 for b in board["buyers"] if b["is_selected"]) == 1
    assert sum(1 for b in board["buyers"] if b["status"] == "selected") == 1
    assert board["selected_buyer_id"] == so["buyer_id"]
    events = db_session.query(WholesaleEvent).filter_by(
        deal_id=d, action="buyer.selection_replaced").all()
    assert len(events) == 1


def test_select_retry_is_idempotent(client, auth_headers, with_buyers, db_session):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    again = ok(_select(client, auth_headers, d, hi["outreach_id"]))
    assert again["already_selected"] is True
    count = db_session.query(WholesaleEvent).filter_by(
        deal_id=d, action="buyer.selected").count()
    assert count == 1


def test_selected_row_cannot_be_overwritten_by_generic_status_write(
        client, auth_headers, with_buyers):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    for status in ("sent", "replied", "interested"):
        r = client.post("/wholesale/outreach/%s/response" % hi["outreach_id"],
                        headers=auth_headers, json={"status": status})
        assert r.status_code == 409
        assert "[selected_row_is_locked]" in r.json()["detail"]
    r = client.patch("/wholesale/outreach/%s" % hi["outreach_id"],
                     headers=auth_headers, json={"status": "queued"})
    assert r.status_code == 409


def test_status_selected_cannot_be_written_directly(client, auth_headers, with_buyers):
    d, hi, so = _both(with_buyers, client, auth_headers)
    r = client.post("/wholesale/outreach/%s/response" % so["outreach_id"],
                    headers=auth_headers, json={"status": "selected"})
    assert r.status_code == 409
    assert "[select_via_select_buyer]" in r.json()["detail"]


def test_selected_buyer_passing_releases_selection(client, auth_headers, with_buyers,
                                                   db_session):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    ok(client.post("/wholesale/outreach/%s/response" % hi["outreach_id"],
                   headers=auth_headers, json={"status": "passed"}))
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d, headers=auth_headers))
    assert not any(b["is_selected"] for b in board["buyers"])
    assert board["selected_buyer_id"] is None
    assert db_session.query(WholesaleEvent).filter_by(
        deal_id=d, action="buyer.selection_released").count() == 1


def test_offer_update_keeps_selected_status(client, auth_headers, with_buyers):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    ok(client.post("/wholesale/outreach/%s/response" % hi["outreach_id"],
                   headers=auth_headers, json={"offer_amount": hi["offer"] + 1000}))
    board = ok(client.get("/wholesale/deals/%s/buyer-board" % d, headers=auth_headers))
    row = [b for b in board["buyers"] if b["outreach_id"] == hi["outreach_id"]][0]
    assert row["status"] == "selected" and row["is_selected"] is True


def test_dnc_and_passed_refused_with_codes_and_no_mutation(
        client, auth_headers, with_buyers, db_session):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    ok(client.post("/wholesale/outreach/%s/response" % so["outreach_id"],
                   headers=auth_headers, json={"status": "passed"}))
    r = _select(client, auth_headers, d, so["outreach_id"])
    assert r.status_code == 409 and "[buyer_passed]" in r.json()["detail"]
    ok(client.patch("/wholesale/buyers/%s" % hi["buyer_id"], headers=auth_headers,
                    json={"do_not_contact": True}))
    r = _select(client, auth_headers, d, hi["outreach_id"])
    assert r.status_code == 409 and "[buyer_opted_out]" in r.json()["detail"]
    row = db_session.query(WholesaleBuyerOutreach).get(hi["outreach_id"])
    assert row.is_selected is True       # refusal left current state untouched


def test_cross_tenant_select_and_response_denied(client, auth_headers, with_buyers,
                                                 other_headers, db_session):
    d, hi, so = _both(with_buyers, client, auth_headers)
    ok(_select(client, auth_headers, d, hi["outreach_id"]))
    assert _select(client, other_headers, d, so["outreach_id"]).status_code in (403, 404)
    r = client.post("/wholesale/outreach/%s/response" % hi["outreach_id"],
                    headers=other_headers, json={"status": "passed"})
    assert r.status_code in (403, 404)
    row = db_session.query(WholesaleBuyerOutreach).get(hi["outreach_id"])
    assert row.is_selected is True and row.status == "selected"
