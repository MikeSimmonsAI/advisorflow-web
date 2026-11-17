"""Acquisition Command's work queue (My Deal Room redesign).

Pins: the four views are filters over existing work states, a property is
listed once per view with all of its open tasks, results are bounded and
paginated, tenants are isolated, and a sale date written into an older
hand-off never replaces the current sale event - it is history, or, when it
cannot be reconciled, "Needs verification" with both sources.
"""
import json
from datetime import date, datetime

import pytest

from app.models.evosense_models import EvoSenseHandoff
from app.services.evosense import sandbox_seed as SS
from app.services.evosense import work_priority as WP
from app.services.evosense import work_queue as WQ

TODAY = date(2026, 11, 16)


def ok(r):
    assert r.status_code == 200, "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def _work(sale, *, status="notice_verified", kind="mortgage"):
    au = WP.auction_urgency(status, sale, kind=kind, today=TODAY)
    wp = WP.work_priority(au, workflow_state="DISCOVERED", contact_ready=False, owner_review=False, deal_level=None)
    return WP.summary(au, wp)


def _handoff(sale, note):
    return EvoSenseHandoff(organization_id="o", property_id="p", status="open", priority=95,
                           reasons=json.dumps([{"code": "AUCTION_DEADLINE", "label": "x", "sale_date": sale}]),
                           next_action=note, created_at=datetime(2026, 10, 22, 14, 0))


# ── the date rule ───────────────────────────────────────────────────────────

def test_the_reported_case_a_moved_sale_is_history_not_the_current_date():
    """Screenshots: a Dec 1 auction beside task text citing Nov 3. The hand-off
    was opened for the Nov 3 trustee sale; the sale re-posted to Dec 1, 15 days
    out (not critical), so no new escalation replaced the note."""
    h = _handoff("2026-11-03", "Foreclosure auction 2026-11-03 - Find contact")
    ctx = WQ.sale_context(_work("2026-12-01"), h, today=TODAY)
    assert ctx["status"] == "historical"
    assert ctx["current"]["date"] == "2026-12-01" and ctx["current"]["verified"] is True
    assert [n["date"] for n in ctx["history"]] == ["2026-11-03"]
    assert ctx["history"][0]["historical"] is True and "Hand-off opened" in ctx["history"][0]["source"]
    assert "Dec 1, 2026" in ctx["message"] and "Nov 3, 2026" in ctx["message"]


def test_two_future_dates_that_disagree_need_verification_with_both_sources():
    h = _handoff("2026-12-08", "Foreclosure auction 2026-12-08 - Verify the notice")
    ctx = WQ.sale_context(_work("2026-12-01"), h, today=TODAY)
    assert ctx["status"] == "needs_verification"
    assert ctx["current"]["date"] == "2026-12-01" and ctx["history"][0]["date"] == "2026-12-08"
    assert "Dec 1, 2026" in ctx["message"] and "Dec 8, 2026" in ctx["message"]


def test_a_task_date_with_no_current_sale_needs_verification():
    h = _handoff("2026-12-01", "Foreclosure auction 2026-12-01 - Find contact")
    ctx = WQ.sale_context(_work(None, status=None), h, today=TODAY)
    assert ctx["status"] == "needs_verification" and ctx["current"] is None
    assert ctx["history"][0]["date"] == "2026-12-01"


def test_an_unverified_current_sale_needs_verification():
    ctx = WQ.sale_context(_work("2026-12-01", status="claimed"), None, today=TODAY)
    assert ctx["status"] == "needs_verification" and ctx["current"]["verified"] is False


def test_matching_dates_and_no_dates_are_quiet():
    h = _handoff("2026-11-24", "Foreclosure auction 2026-11-24 - Find contact")
    assert WQ.sale_context(_work("2026-11-24"), h, today=TODAY)["status"] == "current"
    assert WQ.sale_context(_work(None, status=None), None, today=TODAY)["status"] == "none"


def test_the_note_is_never_rewritten():
    h = _handoff("2026-11-03", "Foreclosure auction 2026-11-03 - Find contact")
    WQ.sale_context(_work("2026-12-01"), h, today=TODAY)
    assert h.next_action == "Foreclosure auction 2026-11-03 - Find contact"


# ── the endpoint ────────────────────────────────────────────────────────────

@pytest.fixture()
def seeded(db_session, sample_org, sample_advisor):
    out = SS.seed_review(db_session, sample_org.id, sample_advisor, replies=True)
    db_session.commit()
    return out


def test_views_counts_and_one_row_per_property(client, auth_headers, seeded):
    first = ok(client.get("/wholesale/evosense/work-queue", headers=auth_headers))
    assert first["view"] == "needs_decision"
    assert [v["key"] for v in first["views"]] == ["needs_decision", "auctions", "handoffs", "all"]
    for v in WQ.VIEWS:
        d = ok(client.get("/wholesale/evosense/work-queue?view=%s&limit=100" % v, headers=auth_headers))
        ids = [i["id"] for i in d["items"]]
        assert len(ids) == len(set(ids)), "a property is listed once per view (%s)" % v
        assert d["total"] == first["counts"][v] == len(ids)
        for i in d["items"]:
            assert set(i["values"]) == {"tax_value", "market_estimate", "arv"}
            assert i["sale"]["status"] in ("none", "current", "historical", "needs_verification")
    nd = ok(client.get("/wholesale/evosense/work-queue?view=needs_decision&limit=100", headers=auth_headers))
    assert any(i["address"] == "1418 Cedar Springs Rd" for i in nd["items"])
    hand = [t for i in nd["items"] for t in i["tasks"] if t["kind"] == "handoff"]
    assert hand and all(t["status"] == "open" for t in hand)
    allv = ok(client.get("/wholesale/evosense/work-queue?view=all&limit=100", headers=auth_headers))
    assert {i["id"] for i in nd["items"]} <= {i["id"] for i in allv["items"]}


def test_bounded_and_paginated(client, auth_headers, seeded):
    a = ok(client.get("/wholesale/evosense/work-queue?view=all&limit=2", headers=auth_headers))
    assert len(a["items"]) <= 2 and a["limit"] == 2
    if a["total"] > 2:
        b = ok(client.get("/wholesale/evosense/work-queue?view=all&limit=2&offset=2", headers=auth_headers))
        assert not {i["id"] for i in a["items"]} & {i["id"] for i in b["items"]}
    assert client.get("/wholesale/evosense/work-queue?limit=1000", headers=auth_headers).status_code == 422


def test_search_narrows_the_queue(client, auth_headers, seeded):
    d = ok(client.get("/wholesale/evosense/work-queue?view=all&q=cedar%20springs", headers=auth_headers))
    assert d["items"] and all("cedar springs" in i["address"].lower() for i in d["items"])


def test_another_workspace_sees_none_of_it(client, db_session, seeded):
    from app.models.models import Organization, User
    from app.services.auth_service import create_access_token, hash_password
    org = Organization(name="Other WQ", slug="other-wq", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="wq@other.test", password_hash=hash_password("TestPass123!"),
             full_name="Other", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    h = {"Authorization": "Bearer %s" % create_access_token(u, db_session)}
    d = ok(client.get("/wholesale/evosense/work-queue?view=all", headers=h))
    assert d["total"] == 0 and d["items"] == []


def test_refused_without_the_wholesale_module(client, auth_headers, db_session, sample_org):
    sample_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    assert client.get("/wholesale/evosense/work-queue", headers=auth_headers).status_code == 402
