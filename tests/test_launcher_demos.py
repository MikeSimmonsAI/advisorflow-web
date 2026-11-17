# -*- coding: utf-8 -*-
"""Program Access Demos page (/launcher/demos).

Pins:
  * a brand's sales manager sees EVERY deal that has a demo link - open, won,
    lost or linked to a customer - one entry per deal;
  * a live link is returned (platform demo preferred); an expired or revoked
    deal is listed as not live and carries NO link;
  * a rep, a customer-only user and another brand's manager see none of this
    brand's demos (organization boundary);
  * the endpoint needs a signed-in user;
  * nothing is created or changed by reading it.
"""
import pytest

from app.models.demo_site_models import DemoSite
from tests.test_program_access_previews import _h, w  # noqa: F401  (shared fixture)


def _demos(client, db, u):
    r = client.get("/launcher/demos", headers=_h(db, u))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_manager_sees_every_deal_with_a_demo_including_lost_and_won(client, w):
    items = _demos(client, w["db"], w["mgr"])
    names = sorted(i["company_name"] for i in items)
    assert names == sorted([
        "Commercial Cleaning Blueprint — Dan Example", "Countryside Land Partners", "Expired Co",
        "Linked Co", "Lost Co", "Onboarding Co", "Revoked Co",
    ])
    assert "Prospect Only Co" not in names          # no demo built
    assert "Other Brand Deal" not in names          # another brand
    lost = next(i for i in items if i["company_name"] == "Lost Co")
    assert lost["status"] == "lost" and lost["live"] is True and lost["url"]


def test_best_link_and_dead_links_have_no_url(client, w):
    items = {i["company_name"]: i for i in _demos(client, w["db"], w["mgr"])}
    cs = items["Countryside Land Partners"]
    assert cs["demo_slot"] == "platform" and "countryside" in cs["url"] and cs["url"].endswith("?staff=1")
    for dead in ("Expired Co", "Revoked Co"):
        assert items[dead]["live"] is False
        assert items[dead]["url"] is None
    assert items["Linked Co"]["customer"] is True
    # live first, then open deals, then the rest
    order = [i["live"] for i in _demos(client, w["db"], w["mgr"])]
    assert order == sorted(order, reverse=True)


@pytest.mark.parametrize("who", ["rep", "cleaner", "other_mgr"])
def test_no_demos_for_rep_customer_or_other_brand(client, w, who):
    names = [i["company_name"] for i in _demos(client, w["db"], w[who])]
    assert not any(n in names for n in ("Countryside Land Partners", "Lost Co", "Expired Co"))
    if who != "other_mgr":
        assert names == []


def test_requires_sign_in(client):
    assert client.get("/launcher/demos").status_code in (401, 403)


def test_reading_demos_changes_nothing(client, w):
    db = w["db"]
    before = sorted((d.id, d.is_active, d.expires_at, d.revoked_at) for d in db.query(DemoSite).all())
    _demos(client, db, w["mgr"])
    db.expire_all()
    after = sorted((d.id, d.is_active, d.expires_at, d.revoked_at) for d in db.query(DemoSite).all())
    assert before == after
