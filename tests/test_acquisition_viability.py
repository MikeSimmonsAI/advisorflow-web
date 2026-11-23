# -*- coding: utf-8 -*-
"""#452 Harvey viability score, attack groups and search queue. No network."""
import json
import uuid
from datetime import datetime, timedelta

from app.models.acquisition_models import AcquisitionDiscoveryRun
from app.models.lead_intel_models import LeadIntelProspect
from app.models.models import Organization
from app.services.acquisition import viability as V

import pytest


@pytest.fixture()
def db(db_session):
    return db_session


def _p(db, **kw):
    base = dict(source="openstreetmap", name="Biz %s" % uuid.uuid4().hex[:6], city="Dallas", state="TX",
                stage="qualified")
    base.update(kw)
    p = LeadIntelProspect(**base)
    db.add(p)
    db.commit()
    return p


def _org(db):
    o = Organization(name="Viab Co", slug="vb-%s" % uuid.uuid4().hex[:8], plan="enterprise", industry="cleaning",
                     is_active=True, enabled_features=json.dumps(["leads"]))
    db.add(o)
    db.commit()
    return o


def test_reachable_apartment_is_group_a(db):
    p = _p(db, industry="apartments", email="manager@oakapts.example", email_verified=True,
           phone_raw="(214) 555-0100", contact_name="Pat Lee", contact_title="Property Manager")
    r = V.score_prospect(p)
    assert r["group"] == "A"
    assert r["score"] == 30 + 25 + 16 + 15 + 0
    assert any("verified email" in x for x in r["reasons"])
    assert r["parts"]["timing"]["points"] == 0          # no invented timing signal


def test_apartment_with_phone_only_needs_contact_info(db):
    r = V.score_prospect(_p(db, industry="apartments", phone_raw="(214) 555-0101"))
    assert r["group"] == "B" and r["score"] == 30 + 8 + 16 + 15


def test_restaurant_without_contact_is_parked(db):
    r = V.score_prospect(_p(db, industry="restaurants"))
    assert r["group"] == "C" and r["score"] == 6 + 0 + 6 + 15


def test_suppressed_is_dropped_with_reason(db):
    r = V.score_prospect(_p(db, industry="office", suppressed_reason="Not a buyer: office=estate_agent"))
    assert r["group"] == "D" and r["score"] == 0 and "estate_agent" in r["reasons"][0]


def test_unverified_email_counts_less_and_size_signal(db):
    r = V.score_prospect(_p(db, industry="medical_dental", email="info@x.example", email_verified=False,
                            reviews_count=150))
    assert r["parts"]["reachable"]["points"] == 4
    assert r["parts"]["job_size"]["points"] == 16


def test_rescore_writes_only_viability_columns(db):
    p = _p(db, industry="apartments", phone_raw="1", score=42, bucket="READY_TO_SEND", stage="qualified")
    out = V.rescore_pool(db)
    db.refresh(p)
    assert out["scored"] >= 1 and p.viability_group == "B" and p.viability_at is not None
    assert p.score == 42 and p.bucket == "READY_TO_SEND" and p.stage == "qualified"
    assert V.group_counts(db)["B"] >= 1
    assert V.ranked(db, "B")[0]["reasons"]


def test_attack_order_runs_first_pair_side_by_side():
    order = V.attack_order(["office", "short_term_rental", "apartments"], ["Dallas", "Plano"])
    assert order[:4] == [("apartments", "Dallas"), ("short_term_rental", "Dallas"),
                         ("apartments", "Plano"), ("short_term_rental", "Plano")]
    assert order[4:] == [("office", "Dallas"), ("office", "Plano")]


def test_queue_marks_done_next_and_needs_places(db):
    o = _org(db)
    db.add(AcquisitionDiscoveryRun(organization_id=o.id, provider="openstreetmap", segment="apartments,office",
                                   city="Dallas", query="q", max_results=25, status="done"))
    db.commit()
    q = V.queue(db, o.id, ["apartments", "short_term_rental", "office"], ["Dallas", "Plano"])
    st = {(r["segment"], r["city"]): r["status"] for r in q["rows"]}
    assert st[("apartments", "Dallas")] == "done"
    assert st[("short_term_rental", "Dallas")] == "needs_google_places"
    assert st[("apartments", "Plano")] == "next" and q["next"] == {"segment": "apartments", "city": "Plano"}
    assert st[("office", "Dallas")] == "done"
    q2 = V.queue(db, o.id, ["short_term_rental"], ["Dallas"], places_ready=True)
    assert q2["next"] == {"segment": "short_term_rental", "city": "Dallas"}


def test_old_search_comes_round_again(db):
    o = _org(db)
    r = AcquisitionDiscoveryRun(organization_id=o.id, provider="openstreetmap", segment="apartments",
                                city="Dallas", query="q", max_results=25, status="done")
    db.add(r)
    db.commit()
    r.created_at = datetime.utcnow() - timedelta(days=V.RECHECK_DAYS + 1)
    db.commit()
    assert V.queue(db, o.id, ["apartments"], ["Dallas"])["next"] == {"segment": "apartments", "city": "Dallas"}


def test_next_city_walks_the_service_area(db):
    o = _org(db)
    assert V.next_city(db, o.id, ["Dallas", "Plano"]) == "Dallas"
    db.add(AcquisitionDiscoveryRun(organization_id=o.id, provider="openstreetmap", segment="office",
                                   city="Dallas", query="q", max_results=25, status="done"))
    db.commit()
    assert V.next_city(db, o.id, ["Dallas", "Plano"]) == "Plano"


def test_daily_report_counts(db):
    _p(db, industry="apartments", email="a@b.example", email_verified=True)
    V.rescore_pool(db)
    rep = V.daily_report(db)
    assert rep["verified_emails"] >= 1 and rep["pool_total"] >= 1 and "groups" in rep


# ── endpoints: owner/admin only, real routes ────────────────────────────────
from tests.test_acquisition_pilot import _h, pilot  # noqa: E402,F401


def test_viability_endpoints_owner_only(pilot):
    p = pilot
    db, c, god = p["db"], p["c"], p["god"]
    _p(db, industry="apartments", phone_raw="(214) 555-0199")
    for who in (p["owner"], p["outsider"]):
        assert c.get("/god/acquisition/workforce/viability", headers=_h(db, who)).status_code == 403
        assert c.post("/god/acquisition/workforce/viability/rescore", headers=_h(db, who)).status_code == 403
        assert c.get("/god/acquisition/workforce/search-queue", headers=_h(db, who)).status_code == 403
    r = c.post("/god/acquisition/workforce/viability/rescore", json={}, headers=_h(db, god))
    assert r.status_code == 200, r.text
    assert r.json()["groups"]["B"] >= 1
    r = c.get("/god/acquisition/workforce/viability?group=B", headers=_h(db, god))
    assert r.status_code == 200 and r.json()["rows"][0]["group"] == "B"
    assert c.get("/god/acquisition/workforce/viability?group=Z", headers=_h(db, god)).status_code == 422
    r = c.get("/god/acquisition/workforce/search-queue", headers=_h(db, god))
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["rows"][0]["segment"] == "apartments" and q["rows"][1]["segment"] == "short_term_rental"
    assert q["rows"][1]["status"] == "needs_google_places" and "daily_report" in q
    r = c.get("/god/acquisition/workforce/search-queue?organization_id=%s" % p["shine"].id, headers=_h(db, god))
    assert r.status_code == 200, r.text


def test_auto_score_new_then_stale(db):
    a = _p(db, industry="apartments", phone_raw="1")
    b = _p(db, industry="office")
    out = V.auto_score(db, batch=1)
    assert out == {"scored_new": 1, "rescored": 0}
    out = V.auto_score(db)
    assert out["scored_new"] == 1
    db.refresh(a); db.refresh(b)
    assert a.viability_group and b.viability_group
    a.viability_at = datetime.utcnow() - timedelta(days=2)
    db.commit()
    assert V.auto_score(db)["rescored"] == 1


def test_workforce_pass_scores_the_pool(db):
    from app.services.acquisition import workforce as WF
    p = _p(db, industry="apartments", phone_raw="1")
    s = WF.state(db)
    s.status = "running"
    db.commit()
    out = WF.run_pass(db, practice_only=True)
    db.refresh(p)
    assert out["viability"]["scored_new"] >= 1 and p.viability_group == "B"
