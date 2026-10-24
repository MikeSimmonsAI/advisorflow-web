"""Disposition follow-ups come from real rows: a quiet buyer, a buyer who viewed the page
but did not answer, a near deadline. Nothing is counted that did not happen."""
from datetime import datetime, timedelta

from app.models.wholesale_models import WholesaleBuyerOutreach, WholesaleDeal
from app.models.wholesale_share_models import WholesaleShareLink, WholesaleShareView
from app.services import wholesale_disposition_intel as DI

ES = "/wholesale/evosense"


def ok(r):
    assert r.status_code in (200, 201), r.text[:300]
    return r.json()


def test_follow_ups_from_real_rows(client, auth_headers, db_session):
    H = auth_headers
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": "5 Follow Up Ct", "city": "Dallas", "state": "TX", "zip_code": "75216",
        "county": "Dallas", "property_type": "single_family", "is_test": True}))["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Follow Test Owner"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}))["deal_id"]
    quiet = ok(client.post("/wholesale/buyers", headers=H, json={"company_name": "Quiet Co", "email": "quiet@example.test", "is_test": True}))
    looker = ok(client.post("/wholesale/buyers", headers=H, json={"company_name": "Looker Co", "email": "looker@example.test", "is_test": True}))
    deal = db_session.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    now = datetime.utcnow()
    for b in (quiet, looker):
        db_session.add(WholesaleBuyerOutreach(organization_id=deal.organization_id, deal_id=deal_id,
                                              buyer_id=b["id"], channel="email", status="sent",
                                              sent_at=now - timedelta(days=3)))
    link = WholesaleShareLink(organization_id=deal.organization_id, deal_id=deal_id, audience="buyer",
                              buyer_id=looker["id"], token="tok-follow-test")
    db_session.add(link)
    db_session.flush()
    db_session.add(WholesaleShareView(organization_id=deal.organization_id, share_link_id=link.id,
                                      deal_id=deal_id, action="view"))
    deal.inspection_deadline = now + timedelta(days=1)
    db_session.commit()

    s = DI.summary(db_session, deal.organization_id, deal, now=now)
    whys = {(x["buyer"], x["why"].split(" ")[0]) for x in s["follow_ups"]}
    assert ("Looker Co", "Opened") in whys and ("Quiet Co", "No") in whys
    assert s["follow_ups"][0]["why"].startswith("Inspection deadline in")      # deadlines first
    f = {x["key"]: x["count"] for x in s["funnel"]}
    assert f["sent"] == 2 and f["viewed"] == 1 and f["replied"] == 0 and f["offers"] == 0
    assert s["offers"] == [] and s["highlights"] == []
