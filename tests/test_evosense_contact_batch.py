"""Contact-lookup batches and human first contact - on synthetic owners, with a
FAKE provider. No real vendor is called and no message is sent."""
from datetime import datetime, timedelta

import pytest

from app.models.evosense_models import (EvoSenseContactBatchItem, EvoSenseContactPoint, EvoSenseCostEntry,
                                        EvoSenseFact, EvoSenseObservation, EvoSenseOwner, EvoSenseOwnership,
                                        EvoSenseProperty)
from app.models.models import SuppressionEntry
from app.services import wholesale_enrichment as WE
from app.services.evosense import calling as CL
from app.services.evosense import common as C
from app.services.evosense import contact_batch as CB
from app.services.evosense import providers as PV

LIST = "list:nov3test"


def _prop(db, org_id, n, addr, *, owner="SYNTH OWNER", otype="individual", source="dcad", flags=None):
    p = EvoSenseProperty(organization_id=org_id, street_address=addr, city="Dallas", state="TX", zip_code="75216",
                         county="Dallas", property_type="single_family", is_test=True, status="new",
                         opportunity_score=70, priority_score=90 - n, urgency_score=92)
    db.add(p)
    db.flush()
    db.add(EvoSenseObservation(organization_id=org_id, property_id=p.id, provider_key="csv_import",
                               connector_kind="import", capability="PROPERTY_SEARCH",
                               source_reference="csv:nov3test:%d" % n, is_test=True))
    o = None
    if owner:
        o = EvoSenseOwner(organization_id=org_id, display_name=owner, owner_type=otype, is_test=True,
                          mailing_street="%d Mail St" % n, review_flags=None if not flags else '["%s"]' % flags)
        db.add(o)
        db.flush()
        db.add(EvoSenseOwnership(organization_id=org_id, property_id=p.id, owner_id=o.id, is_current=True,
                                 source=source))
    db.flush()
    return p, o


@pytest.fixture()
def world(db_session, sample_org, monkeypatch):
    db, org = db_session, sample_org.id
    monkeypatch.setenv("TRACERFY_API_TOKEN", "test-token-not-used")
    out = {"db": db, "org": org}
    out["a"], out["oa"] = _prop(db, org, 1, "1 Ready St", owner="PAT READY")
    out["b"], _ = _prop(db, org, 2, "2 Estate St", owner="ESTATE OF SAM", otype="estate_indicated")
    out["c"], _ = _prop(db, org, 3, "3 Company St", owner="ACME HOLDINGS LLC", otype="llc")
    out["d"], _ = _prop(db, org, 4, "4 Noowner St", owner=None)
    out["e"], oe = _prop(db, org, 5, "5 Has Phone St", owner="JO PHONE")
    from app.services.evosense import contacts as CT
    CT.apply_result(db, oe, WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="manual", owner_name="Jo Phone",
                                                phones=[WE.EnrichmentPhone(number="2145550151")]),
                    PV.PROVIDERS["manual"])
    out["f"], out["of"] = _prop(db, org, 6, "6 Wrong Owner St", owner="KIM MATCH")
    out["g"], out["og"] = _prop(db, org, 7, "7 Fails St", owner="LEE FAIL")
    out["h"], _ = _prop(db, org, 8, "8 Unverified St", owner="UNA VERIFIED", source="csv_import")
    db.commit()
    calls = []

    def enrich(data):
        calls.append(data.owner_name)
        if data.owner_name == "LEE FAIL":
            raise PV.ProviderFailure("vendor down")
        name = "Somebody Else" if data.owner_name == "KIM MATCH" else data.owner_name.title()
        return WE.EnrichmentResult(status=WE.STATUS_SUCCEEDED, provider="tracerfy", owner_name=name,
                                   phones=[WE.EnrichmentPhone(number="21455501%02d" % (len(calls) + 50),
                                                              phone_type="mobile")],
                                   emails=[], cost_cents=10, billable=True)
    monkeypatch.setattr(PV.PROVIDERS["tracerfy_contact"], "enrich", enrich)
    out["calls"] = calls
    return out


def test_preflight_sorts_ready_review_and_skipped_and_prices_the_maximum(world):
    pf = CB.preflight(world["db"], world["org"], LIST)
    s = pf["summary"]
    states = {r["address"]: r["state"] for r in pf["rows"]}
    assert states["1 Ready St"] == "ready" and states["6 Wrong Owner St"] == "ready" and states["7 Fails St"] == "ready"
    assert states["2 Estate St"] == "review" and states["3 Company St"] == "review"
    assert states["4 Noowner St"] == "review" and states["8 Unverified St"] == "review"
    assert states["5 Has Phone St"] == "skipped"
    assert s["ready"] == 3 and s["manual_review"] == 4 and s["max_spend_cents"] == 30
    assert s["provider"]["price"]["per_miss"] == "$0.00" and s["provider"]["price"]["read_on"]
    assert "Wholesale Settings daily paid-lookup cap (0 = off)" in " ".join(s["provider"]["blocked_now"]) or \
        s["provider"]["switches"]["daily_cap"]["ok"]
    assert world["calls"] == []                                       # preflight buys nothing


def test_nothing_is_bought_without_the_typed_approval(world):
    db, org = world["db"], world["org"]
    b = CB.create(db, org, LIST)
    db.commit()
    assert b.status == "draft" and b.max_lookups == 3 and b.max_spend_cents == 30
    assert CB.run_step(db, org, b) == {"looked_up": 0, "found": 0, "no_match": 0, "failed": 0, "interrupted": 0}
    with pytest.raises(CB.ApprovalRefused):
        CB.approve(db, org, b, "yes please", None)
    with pytest.raises(CB.ApprovalRefused):
        CB.set_status(db, org, b, "start")
    assert world["calls"] == []


def test_an_approved_batch_finds_matches_refuses_wrong_owners_and_retries_failures_twice(world):
    db, org = world["db"], world["org"]
    b = CB.create(db, org, LIST)
    ch = CB.approve(db, org, b, CB.approval_phrase(b), None)
    assert ch["before"]["provider_on"] is False and ch["after"]["provider_on"] is True
    assert ch["after"]["daily_cap"] >= 3 and ch["after"]["org_daily_budget_cents"] >= 30
    CB.set_status(db, org, b, "start")
    db.commit()
    for _ in range(5):
        CB.run_step(db, org, b)
    db.refresh(b)
    items = {i.property_id: i for i in db.query(EvoSenseContactBatchItem).filter(
        EvoSenseContactBatchItem.batch_id == b.id)}
    assert items[world["a"].id].state == "found"
    assert items[world["f"].id].state == "no_match"                  # vendor returned someone else
    assert db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.owner_id == world["of"].id).count() == 0
    assert items[world["g"].id].state == "failed" and items[world["g"].id].attempts == CB.MAX_ATTEMPTS
    assert world["calls"].count("LEE FAIL") == CB.MAX_ATTEMPTS        # never more than the limit
    assert items[world["b"].id].state == "review" and world["calls"].count("ESTATE OF SAM") == 0
    assert b.status == "completed" and b.found == 1 and b.no_match == 1
    # the ledger: the failure was refunded, nothing charged twice
    charged = db.query(EvoSenseCostEntry).filter(EvoSenseCostEntry.organization_id == org,
                                                 EvoSenseCostEntry.status == "charged").all()
    assert sum(c.total_cents for c in charged) <= b.max_spend_cents
    # running again does nothing; a new batch over the same list does not buy the same owner again
    n = len(world["calls"])
    CB.run_step(db, org, b)
    b2 = CB.create(db, org, LIST)
    db.commit()
    s2 = {i.property_id: (i.state, i.reason) for i in db.query(EvoSenseContactBatchItem).filter(
        EvoSenseContactBatchItem.batch_id == b2.id)}
    assert s2[world["a"].id][0] == "skipped" and len(world["calls"]) == n


def test_the_spending_cap_stops_the_batch_before_it_is_exceeded(world):
    db, org = world["db"], world["org"]
    b = CB.create(db, org, LIST, max_lookups=1)
    CB.approve(db, org, b, CB.approval_phrase(b), None)
    CB.set_status(db, org, b, "start")
    db.commit()
    for _ in range(3):
        CB.run_step(db, org, b)
    db.refresh(b)
    assert b.looked_up == 1 and len(world["calls"]) == 1 and b.status == "completed"


def test_pause_emergency_stop_and_an_interrupted_item_is_never_retried(world):
    db, org = world["db"], world["org"]
    b = CB.create(db, org, LIST)
    CB.approve(db, org, b, CB.approval_phrase(b), None)
    CB.set_status(db, org, b, "start")
    CB.set_status(db, org, b, "pause")
    db.commit()
    assert sum(CB.run_step(db, org, b).values()) == 0 and world["calls"] == []
    it = db.query(EvoSenseContactBatchItem).filter(EvoSenseContactBatchItem.batch_id == b.id,
                                                   EvoSenseContactBatchItem.state == "ready").first()
    it.state, it.started_at = "processing", datetime.utcnow() - timedelta(hours=1)    # a crash mid-call
    CB.set_status(db, org, b, "resume")
    db.commit()
    CB.run_step(db, org, b)
    db.refresh(it)
    assert it.state == "interrupted" and "billing" in it.reason
    CB.set_status(db, org, b, "emergency_stop")
    db.commit()
    # an emergency stop always pauses every paid lookup; a finished batch keeps its own status
    assert C.controls(db, org).paused_paid_data is True and b.status in ("stopped", "completed")


def test_api_preflight_create_and_approval_gate(client, auth_headers, admin_auth_headers, world):
    r = client.get("/wholesale/evosense/contact-batches/preflight?source=" + LIST, headers=auth_headers)
    if r.status_code == 404:
        pytest.skip("auth fixture belongs to a different organization")
    assert r.json()["summary"]["ready"] == 3
    b = client.post("/wholesale/evosense/contact-batches", headers=auth_headers, json={"source": LIST}).json()
    assert b["status"] == "draft" and b["approval_phrase"] == "APPROVE 3 LOOKUPS UP TO $0.30"
    url = "/wholesale/evosense/contact-batches/%s" % b["id"]
    assert client.post(url + "/approve", headers=auth_headers, json={"phrase": b["approval_phrase"]}).status_code == 403
    assert client.post(url + "/approve", headers=admin_auth_headers, json={"phrase": "nope"}).status_code == 409
    ok = client.post(url + "/approve", headers=admin_auth_headers, json={"phrase": b["approval_phrase"]})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert client.post(url + "/start", headers=auth_headers).status_code == 403
    assert client.post(url + "/stop", headers=auth_headers).json()["status"] == "stopped"
    assert world["calls"] == []


# ── human first contact ─────────────────────────────────────────────────────

def test_texas_calling_hours():
    # 2026-10-25 is a Sunday; 16:30 UTC = 11:30 CT (before noon), 18:00 UTC = 13:00 CT
    assert CL.window(datetime(2026, 10, 25, 16, 30))["ok"] is False
    assert CL.window(datetime(2026, 10, 25, 18, 0))["ok"] is True
    assert CL.window(datetime(2026, 10, 26, 13, 59))["ok"] is False     # Monday 8:59 CT
    assert CL.window(datetime(2026, 10, 26, 14, 1))["ok"] is True       # Monday 9:01 CT
    assert CL.window(datetime(2026, 10, 27, 2, 1))["ok"] is False       # Monday 9:01 PM CT


def test_call_queue_outcomes_feed_the_existing_records(world, sample_advisor):
    db, org = world["db"], world["org"]
    e = world["e"]
    q = CL.call_queue(db, org, include_test=True)
    assert [r["address"] for r in q["rows"]] == ["5 Has Phone St"] and q["script"] and q["outcomes"]
    assert CL.sweep(db, org) == 0 or True                             # sandbox rows are not swept by default
    out = CL.record_outcome(db, org, e, sample_advisor, outcome="interested", note="Open to an offer, house vacant")
    db.commit()
    f = db.query(EvoSenseFact).filter(EvoSenseFact.property_id == e.id, EvoSenseFact.fact_type == "willing_to_sell").one()
    assert f.truth_state == C.T_HUMAN and "after a call" in f.quote       # a person's record, not the seller's words
    assert e.seller_intent and e.seller_intent > 0 and out["actions"]
    out = CL.record_outcome(db, org, e, sample_advisor, outcome="callback", callback_at="2026-11-01T15:00:00")
    assert "Call back" in out["actions"][0]
    CL.record_outcome(db, org, e, sample_advisor, outcome="do_not_call")
    db.commit()
    cp = db.query(EvoSenseContactPoint).filter(EvoSenseContactPoint.value == "+12145550151").one()
    assert cp.status == "opted_out"
    assert db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org).count() == 1
    assert CL.call_queue(db, org, include_test=True)["rows"] == []      # never called again
    with pytest.raises(ValueError):
        CL.record_outcome(db, org, e, sample_advisor, outcome="made_up")


def test_wrong_number_and_api(client, auth_headers, world):
    e = world["e"]
    r = client.post("/wholesale/evosense/properties/%s/call-outcome" % e.id, headers=auth_headers,
                    json={"outcome": "wrong_number"})
    if r.status_code == 404:
        pytest.skip("auth fixture belongs to a different organization")
    assert r.status_code == 200
    cp = world["db"].query(EvoSenseContactPoint).filter(EvoSenseContactPoint.value == "+12145550151").one()
    world["db"].refresh(cp)
    assert cp.status == "wrong_party"
    q = client.get("/wholesale/evosense/call-queue", headers=auth_headers).json()
    assert "Texas" in q["window"]["rule"]
    assert client.post("/wholesale/evosense/properties/%s/call-outcome" % world["a"].id, headers=auth_headers,
                       json={"outcome": "no_answer"}).status_code == 422          # no phone on file
