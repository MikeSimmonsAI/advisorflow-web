# -*- coding: utf-8 -*-
"""#452 Acquisition Hub: platform totals, email readiness, contact research,
client provisioning. Synthetic data only."""
from app.models.acquisition_models import AcquisitionEvidence, AcquisitionOpportunity
from app.models.models import EmailMessage, Lead, Organization
from tests.test_acquisition_pilot import _h, _pool_business, pilot  # noqa: F401


def _hub(p):
    r = p["c"].get("/god/acquisition/hub", headers=_h(p["db"], p["god"]))
    assert r.status_code == 200, r.text
    return r.json()


def test_phone_only_business_is_not_email_ready(pilot):
    p = pilot
    _pool_business(p["db"], industry="medical_dental", phone_e164="+12145550100", source_url="https://src.example/a")
    h = _hub(p)
    assert h["pool"]["total"] == 1 and h["pool"]["email_ready"] == 0 and h["pool"]["needs_email"] == 1
    rows = p["c"].get("/god/acquisition/master/prospects?status=all", headers=_h(p["db"], p["god"])).json()["items"]
    r = rows[0]
    assert r["email_ready"] is False and r["queue"] == "needs_email" and "bucket" not in r
    assert "No published email found yet" in r["not_ready_reasons"]


def test_contact_research_records_source_and_reaches_the_client(pilot):
    p = pilot
    item = _pool_business(p["db"], industry="medical_dental", source_url="https://src.example/b")
    p["c"].post("/god/acquisition/master/assign", json={"destination_org_id": p["shine"].id, "prospect_ids": [item.id]},
                headers=_h(p["db"], p["god"]))
    bad = p["c"].post("/god/acquisition/master/%s/contact" % item.id, json={"email": "office@biz.example"},
                      headers=_h(p["db"], p["god"]))
    assert bad.status_code == 422                                   # a source page is required
    r = p["c"].post("/god/acquisition/master/%s/contact" % item.id,
                    json={"email": "Office@Biz.example", "source_url": "https://biz.example/contact", "verified": False},
                    headers=_h(p["db"], p["god"])).json()
    assert r["readiness"]["queue"] == "verification_pending" and r["readiness"]["email_ready"] is False
    r = p["c"].post("/god/acquisition/master/%s/contact" % item.id,
                    json={"email": "office@biz.example", "source_url": "https://biz.example/contact", "verified": True},
                    headers=_h(p["db"], p["god"])).json()
    assert r["readiness"]["email_ready"] is True and r["readiness"]["queue"] == "verified_contact" and r["pushed_to_client"]
    opp = p["db"].query(AcquisitionOpportunity).filter(AcquisitionOpportunity.source == "master_pool:" + item.id).one()
    lead = p["db"].query(Lead).filter(Lead.id == opp.lead_id).one()
    assert lead.email == "office@biz.example"
    assert p["db"].query(AcquisitionEvidence).filter(AcquisitionEvidence.opportunity_id == opp.id,
                                                     AcquisitionEvidence.kind == "contact_email",
                                                     AcquisitionEvidence.verified.is_(True)).count() == 1
    h = _hub(p)
    assert h["pool"]["email_verified"] == 1 and h["pool"]["email_ready"] == 1
    shine = next(c for c in h["clients"] if c["organization_id"] == p["shine"].id)
    assert shine["assigned"] == 1 and shine["contacts_verified"] == 1 and shine["contacted"] == 0
    assert p["db"].query(EmailMessage).count() == 0


def test_hub_is_owner_only_and_practice_never_counts(pilot):
    p = pilot
    for u in (p["owner"], p["outsider"]):
        assert p["c"].get("/god/acquisition/hub", headers=_h(p["db"], u)).status_code == 403
        assert p["c"].post("/god/acquisition/clients", json={"name": "X"}, headers=_h(p["db"], u)).status_code == 403
    p["c"].post("/acquisition-ops/%s/prospects" % p["shine"].id, headers=_h(p["db"], p["god"]),
                json={"business_name": "Practice Co", "is_practice": True, "state": "TX"})
    h = _hub(p)
    shine = next(c for c in h["clients"] if c["organization_id"] == p["shine"].id)
    assert shine["practice"] == 1 and shine["real"] == 0
    assert [f["key"] for f in h["funnel"]] == ["imported", "qualified", "assigned", "email_ready", "contacted",
                                                "interested", "booked", "won"]


def test_add_cleaning_company_starts_paused_with_no_users(pilot):
    from app.models.models import Platform
    p = pilot
    if p["db"].query(Platform).filter(Platform.slug == "evosyspro").first() is None:
        p["db"].add(Platform(name="EvoSys Pro", slug="evosyspro"))
        p["db"].commit()
    r = p["c"].post("/god/acquisition/clients", json={"name": "Lucy Test Cleaning"}, headers=_h(p["db"], p["god"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["paused"] is True and out["invitation"] is None and out["sent"] == 0 and out["status"] == "created"
    org = p["db"].query(Organization).filter(Organization.id == out["organization_id"]).one()
    assert org.industry == "cleaning" and "client_acquisition" in org.enabled_features
    ov = p["c"].get("/acquisition-ops/%s/overview" % org.id, headers=_h(p["db"], p["god"])).json()
    assert ov["profile"]["paused"] is True and ov["profile"]["pilot_started_at"] is None
    dup = p["c"].post("/god/acquisition/clients", json={"name": "Lucy Test Cleaning"}, headers=_h(p["db"], p["god"]))
    assert dup.json()["status"] == "existing" and dup.json()["organization_id"] == org.id


def test_queue_filter_and_search(pilot):
    p = pilot
    _pool_business(p["db"], name="Alpha Dental", industry="medical_dental", source_url="https://s.example/1")
    _pool_business(p["db"], name="Beta Offices", industry="office", email="hi@beta.example", source_url="https://s.example/2")
    g = _h(p["db"], p["god"])
    q = p["c"].get("/god/acquisition/master/prospects?status=all&queue=verification_pending", headers=g).json()
    assert [x["name"] for x in q["items"]] == ["Beta Offices"]
    s = p["c"].get("/god/acquisition/master/prospects?status=all&search=alpha", headers=g).json()
    assert [x["name"] for x in s["items"]] == ["Alpha Dental"]
    assert p["c"].get("/god/acquisition/master/prospects?queue=bogus", headers=g).status_code == 422


def test_owner_program_access_has_one_acquisition_hub_card(pilot):
    p = pilot
    ctx = p["c"].get("/auth/my-contexts", headers=_h(p["db"], p["god"])).json()
    cards = ctx["acquisition_ops"]
    assert len(cards) == 1 and cards[0]["hub"] is True and cards[0]["path"] == "/acquisition-ops"
    # a client's own staff never get the hub card
    assert p["c"].get("/auth/my-contexts", headers=_h(p["db"], p["owner"])).json()["acquisition_ops"] == []


def test_test_workspaces_are_labelled_and_never_counted():
    from app.services.acquisition import hub as HUB
    assert HUB.is_test_org("TEST QA Onboarding 1 (synthetic)")
    assert HUB.is_test_org("test second cleaning co")
    assert not HUB.is_test_org("Divine Shine Elite Cleaning")
    assert not HUB.is_test_org("Testament Cleaning LLC")
    assert not HUB.is_test_org(None)
