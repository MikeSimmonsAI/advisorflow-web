"""The team board counts what happened - booked, ran, no-show, signed - by
rep and by week, and shows each person only the part of the team they may see."""
from datetime import datetime

from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:300])
    return r.json()


def _today(h):
    return datetime.now(CT).replace(hour=h, minute=0, second=0, microsecond=0).isoformat()


def test_board_counts_by_rep(client, admin_auth_headers, auth_headers, sample_advisor, second_advisor):
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": second_advisor.id, "role": "field_specialist", "confirm_lock": True}))
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist"}))
    book = lambda rep, h: ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today(h), "address": "%d Board St" % h, "assigned_to_id": rep}))
    a1, a2, a3 = book(sample_advisor.id, 9), book(sample_advisor.id, 11), book(second_advisor.id, 13)
    ok(client.patch("/wholesale/appointments/%s" % a1["id"], headers=auth_headers, json={"outcome": "signed"}))
    ok(client.patch("/wholesale/appointments/%s" % a2["id"], headers=auth_headers, json={"status": "no_show"}))
    b = ok(client.get("/wholesale/appointments/board?weeks=2", headers=admin_auth_headers))
    rows = {r["user_id"]: r["total"] for r in b["rows"]}
    me = rows[sample_advisor.id]
    assert (me["booked"], me["ran"], me["no_show"], me["signed"], me["signed_rate"]) == (2, 1, 1, 1, 100)
    assert rows[second_advisor.id]["booked"] == 1 and b["team"]["booked"] == 3 and len(b["weeks"]) == 2
    # A field login sees only their own row
    mine = ok(client.get("/wholesale/appointments/board", headers=auth_headers))
    assert {r["user_id"] for r in mine["rows"]} == {sample_advisor.id}


def test_commission_is_five_percent_of_the_assignment_fee(
        client, admin_auth_headers, auth_headers, sample_advisor, second_advisor, sample_org, db_session):
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty
    from app.models.wholesale_ops_models import WholesaleFieldAppointment
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist", "confirm_lock": True}))
    assert ok(client.get("/wholesale/team/rules", headers=admin_auth_headers))["commission_pct"] == 5

    def deal(fee=None, collected=None):
        p = WholesaleProperty(organization_id=sample_org.id, street_address="5 Fee St", city="Dallas",
                              state="TX", zip_code="75201")
        db_session.add(p); db_session.flush()
        d = WholesaleDeal(organization_id=sample_org.id, property_id=p.id, stage="new_property",
                          assignment_fee=fee, wholesale_fee_collected=collected)
        db_session.add(d); db_session.commit()
        return d.id

    book = lambda h: ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": _today(h), "address": "%d Fee St" % h, "assigned_to_id": sample_advisor.id}))
    closed, signed = book(9), book(11)
    for appt, d in ((closed, deal(collected=23000)), (signed, deal(fee=10000))):
        ok(client.patch("/wholesale/appointments/%s" % appt["id"], headers=auth_headers, json={"outcome": "signed"}))
        db_session.query(WholesaleFieldAppointment).filter_by(id=appt["id"]).update({"deal_id": d})
    db_session.commit()

    b = ok(client.get("/wholesale/appointments/board", headers=admin_auth_headers))
    me = next(r for r in b["rows"] if r["user_id"] == sample_advisor.id)
    assert me["commission"] == {"earned": 1150, "coming": 500}      # 5% of 23k closed, 5% of 10k signed
    assert b["team"]["commission"] == {"earned": 1150, "coming": 500} and b["commission_pct"] == 5
    # The rep sees their own
    mine = ok(client.get("/wholesale/appointments/board", headers=auth_headers))
    assert mine["rows"][0]["commission"]["earned"] == 1150
    # Mike can change the rate
    ok(client.put("/wholesale/team/rules", headers=admin_auth_headers,
                  json={"offer_limit_pct": 90, "commission_pct": 7.5}))
    b = ok(client.get("/wholesale/appointments/board", headers=admin_auth_headers))
    assert next(r for r in b["rows"] if r["user_id"] == sample_advisor.id)["commission"]["earned"] == 1725
    assert client.put("/wholesale/team/rules", headers=admin_auth_headers,
                      json={"offer_limit_pct": 90, "commission_pct": 80}).status_code == 422
