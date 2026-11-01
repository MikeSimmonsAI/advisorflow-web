"""Private BookaBoost / AdvisorFlow Opportunity Radar contract — synthetic data only."""
from datetime import datetime, timedelta
import json
import uuid

import pytest

from app.models.models import Organization, User
from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
from app.models.sales_models import Membership
from app.services.auth_service import create_access_token, hash_password
from app.services.evosense import overlooked as RADAR


def org(db, name):
    o = Organization(name=name, slug="radar-" + uuid.uuid4().hex[:12],
                     plan="standard", industry="real_estate", is_active=True,
                     enabled_features=json.dumps(["wholesale_real_estate", "wholesale_workspace",
                                                  "leads", "users", "reports"]))
    db.add(o)
    db.flush()
    return o


def property_record(db, o, address, category, *, state="TX", score=68,
                    test=False, archived=False):
    p = EvoSenseProperty(organization_id=o.id, street_address=address,
                         state=state, county="Tarrant", city="Arlington",
                         property_category=category,
                         property_class_source="county_record",
                         opportunity_score=score, data_confidence="medium",
                         is_test=test,
                         archived_at=datetime.utcnow() if archived else None)
    db.add(p)
    db.flush()
    return p


def evidence(db, o, p, kind, *, source="county_record", days_ago=5):
    s = EvoSenseSignal(organization_id=o.id, property_id=p.id,
                       signal_type=kind, source=source, active=True,
                       confidence=80,
                       observed_at=datetime.utcnow() - timedelta(days=days_ago),
                       effective_at=datetime.utcnow() - timedelta(days=days_ago))
    db.add(s)
    db.flush()
    return s


def test_radar_tenant_isolation_and_source_evidence(db_session):
    db = db_session
    a = org(db, "BookaBoost Private")
    b = org(db, "EvoSys Private")
    a_p = property_record(db, a, "A Only St", "residential", state="TX")
    b_p = property_record(db, b, "B Only St", "residential", state="OK")
    evidence(db, a, a_p, "PRE_FORECLOSURE", source="county_notice")
    evidence(db, b, b_p, "TAX_DELINQUENT", source="other_county")
    db.commit()

    found = RADAR.search(db, a.id, market="residential")
    assert found["total"] == 1
    assert [v["address"] for v in found["items"]] == ["A Only St"]
    assert found["items"][0]["why_review"][0]["source"] == "county_notice"
    assert found["items"][0]["why_review"][0]["type"] == "PRE_FORECLOSURE"
    assert found["items"][0]["existing_opportunity_score"] == 68
    assert RADAR.search(db, a.id, state="OK")["total"] == 0
    assert RADAR.search(db, b.id, state="OK")["total"] == 1


def test_farmland_never_presents_vacancy_or_absentee_as_opportunity_evidence(db_session):
    db = db_session
    a = org(db, "Land Specialist")
    p = property_record(db, a, "Ranch Road 1", "agricultural", state="KS")
    evidence(db, a, p, "VACANT")
    evidence(db, a, p, "ABSENTEE_OWNER")
    db.commit()
    result = RADAR.search(db, a.id, market="land")
    assert result["total"] == 1
    item = result["items"][0]
    assert item["why_review"] == [] and item["evidence_status"] == "needs_research"
    assert RADAR.search(db, a.id, market="residential")["total"] == 0


def test_no_fabricated_score_and_unknown_property_class_requires_review(db_session):
    db = db_session
    a = org(db, "Unclassified")
    p = property_record(db, a, "Unknown 5", None, score=None, state="CA")
    evidence(db, a, p, "CODE_HISTORY")
    db.commit()
    item = RADAR.search(db, a.id, market="all")["items"][0]
    assert item["existing_opportunity_score"] is None
    assert item["why_review"] == [] and item["classification_review_required"]
    assert RADAR.search(db, a.id, market="residential")["total"] == 0
    assert RADAR.search(db, a.id, market="wholesale")["total"] == 0


def test_test_and_archived_records_hidden_without_mutations(db_session):
    db = db_session
    a = org(db, "Safety")
    property_record(db, a, "Real Ave", "commercial")
    property_record(db, a, "Test Ave", "commercial", test=True)
    property_record(db, a, "Archived Ave", "commercial", archived=True)
    db.commit()
    assert RADAR.search(db, a.id, market="commercial")["total"] == 1
    assert RADAR.search(db, a.id, market="commercial", include_test=True)["total"] == 2
    assert db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == a.id).count() == 3


def test_filters_pagination_and_invalid_scope(db_session):
    db = db_session
    a = org(db, "Nationwide")
    property_record(db, a, "Arkansas 1", "land", state="AR", score=40)
    property_record(db, a, "Arkansas 2", "land", state="AR", score=80)
    property_record(db, a, "Georgia 1", "commercial", state="GA", score=90)
    db.commit()
    first = RADAR.search(db, a.id, market="land", state="ar", min_score=50, limit=1)
    assert first["total"] == 1 and first["items"][0]["address"] == "Arkansas 2"
    second = RADAR.search(db, a.id, market="all", limit=1, offset=1)
    assert second["total"] == 3 and len(second["items"]) == 1
    for kwargs in ({"market": "bogus"}, {"limit": 10000}, {"min_score": 500},
                   {"state": "USA"}):
        with pytest.raises(ValueError):
            RADAR.search(db, a.id, **kwargs)
    with pytest.raises(ValueError):
        RADAR.search(db, "")


def test_private_radar_endpoint_rejects_anonymous(client):
    response = client.get("/wholesale/evosense/opportunity-radar")
    assert response.status_code in (401, 403), response.text[:200]


def _login(db, o, *, role="org_admin", member_of=None):
    user = User(organization_id=o.id if o else None,
                email="radar-" + uuid.uuid4().hex[:8] + "@example.com",
                full_name="Test Radar User", role=role,
                password_hash=hash_password("Pass12345!"), is_active=True,
                must_change_password=False)
    db.add(user)
    db.flush()
    for ws in ([o] if o else []) + list(member_of or []):
        db.add(Membership(user_id=user.id, scope_type="customer_org", scope_id=ws.id,
                          role=role, is_active=True))
    db.commit()
    return user


def _h(db, user, ws=None):
    h = {"Authorization": "Bearer " + create_access_token(user, db)}
    if ws:
        h["X-Workspace-Id"] = ws.id
    return h


def test_private_radar_endpoint_workspace_scoped(client, db_session):
    """The platform contract for a forged X-Workspace-Id is: the server throws
    the header away (and logs AUTHZ DENIED) and the caller stays in their OWN
    workspace (workspace_access.selected_workspace_id). So the right proof is
    not a particular status code but that NOTHING of the other tenant comes
    back - with or without the forged header."""
    db = db_session
    a = org(db, "BookaBoost Radar")
    b = org(db, "Other Radar")
    ours = property_record(db, a, "Our Only St", "land")
    theirs = property_record(db, b, "Secret Other St", "land")
    evidence(db, b, theirs, "TAX_DELINQUENT", source="secret_county_feed")
    user = _login(db, a)
    other = _login(db, b)

    r = client.get("/wholesale/evosense/opportunity-radar", headers=_h(db, user, a))
    assert r.status_code == 200, r.text[:300]
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == ours.id
    assert "Secret Other St" not in r.text

    # Forged header naming the other tenant: discarded, never honoured.
    attack = client.get("/wholesale/evosense/opportunity-radar", headers=_h(db, user, b))
    if attack.status_code == 200:
        body = attack.json()
        assert {i["id"] for i in body["items"]} <= {ours.id}, "another tenant's record was returned"
    else:
        assert attack.status_code in (403, 404), attack.text[:300]
    for leaked in ("Secret Other St", theirs.id, "secret_county_feed"):
        assert leaked not in attack.text

    # Every filter combination stays inside the caller's own workspace.
    for q in ("market=land", "market=wholesale", "state=TX", "county=Tarrant", "min_score=0",
              "market=all&limit=50", "include_test=true"):
        rr = client.get("/wholesale/evosense/opportunity-radar?" + q, headers=_h(db, user, b))
        assert theirs.id not in rr.text and "Secret Other St" not in rr.text, q

    # The other tenant's property id is refused on the research route Radar links to.
    for path in ("/wholesale/evosense/properties/%s" % theirs.id,
                 "/wholesale/evosense/properties/%s/readiness" % theirs.id):
        for ws in (None, a, b):
            assert client.get(path, headers=_h(db, user, ws)).status_code in (403, 404), (path, ws)

    # And the reverse direction: the other tenant never sees ours.
    back = client.get("/wholesale/evosense/opportunity-radar", headers=_h(db, other, a))
    assert ours.id not in back.text and "Our Only St" not in back.text


def test_private_radar_requires_the_wholesale_entitlement(client, db_session):
    db = db_session
    plain = Organization(name="No Wholesale", slug="radar-" + uuid.uuid4().hex[:12],
                         plan="standard", industry="real_estate", is_active=True,
                         enabled_features=json.dumps(["leads", "users", "reports"]))
    db.add(plain)
    db.flush()
    property_record(db, plain, "Should Not Show", "land")
    user = _login(db, plain)
    r = client.get("/wholesale/evosense/opportunity-radar", headers=_h(db, user, plain))
    assert r.status_code in (402, 403, 404), r.text[:300]
    assert "Should Not Show" not in r.text


def test_radar_reads_never_write(client, db_session):
    """Opening Radar changes nothing: no property, signal or deal rows."""
    from app.models.wholesale_models import WholesaleDeal
    db = db_session
    a = org(db, "Read Only")
    p = property_record(db, a, "Read Only Ave", "residential")
    evidence(db, a, p, "PRE_FORECLOSURE")
    user = _login(db, a)
    before = (db.query(EvoSenseProperty).count(), db.query(EvoSenseSignal).count(),
              db.query(WholesaleDeal).count())
    for q in ("", "?market=residential", "?market=wholesale&min_score=10"):
        assert client.get("/wholesale/evosense/opportunity-radar" + q, headers=_h(db, user, a)).status_code == 200
    db.expire_all()
    after = (db.query(EvoSenseProperty).count(), db.query(EvoSenseSignal).count(),
             db.query(WholesaleDeal).count())
    assert before == after


def test_coverage_next_step_and_missing_information_are_honest(db_session):
    db = db_session
    a = org(db, "Coverage")
    b = org(db, "Coverage Other")
    p = property_record(db, a, "Coverage 1", "residential", state="GA")
    property_record(db, a, "Coverage 2", "land", state="GA")
    property_record(db, a, "Coverage 3", "land", state="MT")
    property_record(db, b, "Elsewhere", "land", state="WY")
    db.commit()
    res = RADAR.search(db, a.id, market="all", state="GA")
    states = {s["state"]: s["count"] for s in res["available"]["states"]}
    assert states == {"GA": 2, "MT": 1}, states            # never WY: that is another tenant
    assert res["available"]["counties"] == [{"county": "Tarrant", "count": 2}]
    item = next(i for i in res["items"] if i["id"] == p.id)
    assert "Current supporting evidence" in item["missing_information"]
    assert "Parcel number (APN)" in item["missing_information"]
    assert "Year built" in item["missing_information"]
    assert item["recommended_next_action"]["code"] == "research"
    assert "EvoSense" not in json.dumps(res)                  # BookaBoost-facing text names no other brand
    land = RADAR.search(db, a.id, market="land")
    assert {s["state"] for s in land["available"]["states"]} == {"GA", "MT"}
    assert all("Year built" not in i["missing_information"] for i in land["items"])


# ── Buyer matching (read-only, confirmed matches only) ──────────────────────

def _buyer(db, o, name, *, states=None, counties=None, types=None, test=False):
    from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer
    b = WholesaleBuyer(organization_id=o.id, company_name=name, is_test=test, email="b-%s@example.com" % uuid.uuid4().hex[:6])
    db.add(b)
    db.flush()
    db.add(WholesaleBuyBox(organization_id=o.id, buyer_id=b.id, label=name + " box",
                           states=json.dumps(states) if states is not None else None,
                           counties=json.dumps(counties) if counties is not None else None,
                           property_types=json.dumps(types) if types is not None else None))
    db.flush()
    return b


def test_buyer_matches_are_confirmed_only_and_tenant_scoped(client, db_session):
    from app.models.wholesale_models import WholesaleBuyerMatch
    db = db_session
    a = org(db, "Match A")
    b = org(db, "Match B")
    house = property_record(db, a, "Match House", "residential", state="TX")
    land = property_record(db, a, "Match Land", "land", state="TX")
    _buyer(db, a, "Confirmed TX Houses", states=["TX"], types=["residential"])
    _buyer(db, a, "Open Box", states=None, types=None)                 # names nothing: never a confirmed match
    _buyer(db, a, "Georgia Only", states=["GA"], types=["residential"])  # outside geography
    _buyer(db, a, "Land Only", states=["TX"], types=["land"])           # wrong type for the house
    _buyer(db, a, "Sandbox Buyer", states=["TX"], types=["residential"], test=True)
    _buyer(db, b, "Other Tenant Buyer", states=["TX"], types=["residential", "land"])
    db.commit()
    before = db.query(WholesaleBuyerMatch).count()

    res = RADAR.search(db, a.id, market="all")
    counts = {i["address"]: i["buyer_matches"]["confirmed"] for i in res["items"]}
    assert counts == {"Match House": 1, "Match Land": 1}, counts
    house_item = next(i for i in res["items"] if i["id"] == house.id)
    assert house_item["buyer_matches"]["budget_checked"] is False

    detail = RADAR.buyer_matches(db, a.id, house.id)
    assert [m["name"] for m in detail["items"]] == ["Confirmed TX Houses"]
    assert detail["budget_note"] and "Other Tenant Buyer" not in json.dumps(detail)
    assert RADAR.buyer_matches(db, b.id, house.id) is None            # another tenant's property: nothing
    assert db.query(WholesaleBuyerMatch).count() == before            # read-only

    user = _login(db, a)
    other = _login(db, b)
    r = client.get("/wholesale/evosense/opportunity-radar/%s/buyer-matches" % land.id, headers=_h(db, user, a))
    assert r.status_code == 200 and [m["name"] for m in r.json()["items"]] == ["Land Only"], r.text[:300]
    for hdr in (_h(db, other, b), _h(db, other, a)):
        x = client.get("/wholesale/evosense/opportunity-radar/%s/buyer-matches" % house.id, headers=hdr)
        assert x.status_code == 404 and "Confirmed TX Houses" not in x.text
    assert client.get("/wholesale/evosense/opportunity-radar/%s/buyer-matches" % house.id).status_code in (401, 403)


def test_unknown_property_facts_never_count_as_a_buyer_match(db_session):
    db = db_session
    a = org(db, "Unknowns")
    p = property_record(db, a, "No Type", None, state="TX")
    _buyer(db, a, "Wants Houses", states=["TX"], types=["residential"])
    db.commit()
    item = RADAR.search(db, a.id, market="all")["items"][0]
    assert item["buyer_matches"]["confirmed"] == 0
    assert RADAR.buyer_matches(db, a.id, p.id)["items"] == []


def test_sort_and_whole_set_summary(db_session):
    db = db_session
    a = org(db, "Sorting")
    p1 = property_record(db, a, "B Street", "residential", state="TX", score=50)
    p2 = property_record(db, a, "A Street", "residential", state="GA", score=90)
    property_record(db, a, "C Street", "residential", state="OK", score=None)
    evidence(db, a, p1, "PRE_FORECLOSURE")
    p1.last_observed_at = datetime.utcnow()
    p2.last_observed_at = datetime.utcnow() - timedelta(days=40)
    db.commit()
    assert [i["address"] for i in RADAR.search(db, a.id, sort="score")["items"]][:2] == ["A Street", "B Street"]
    assert RADAR.search(db, a.id, sort="recent")["items"][0]["address"] == "B Street"
    assert [i["state"] for i in RADAR.search(db, a.id, sort="state")["items"]] == ["GA", "OK", "TX"]
    s = RADAR.search(db, a.id, limit=1)["summary"]
    assert s == {"total": 3, "with_evidence": 1, "evidence_counted_over": 3, "scored": 2, "average_score": 70}
    with pytest.raises(ValueError):
        RADAR.search(db, a.id, sort="price")


def test_card_facts_show_rooms_only_when_the_record_holds_them(db_session):
    """The OLP card shows bedrooms / bathrooms only from the record itself."""
    from decimal import Decimal
    db = db_session
    a = org(db, "Rooms")
    p = property_record(db, a, "Rooms 1", "residential", state="GA")
    q = property_record(db, a, "Rooms 2", "residential", state="GA")
    p.bedrooms, p.bathrooms = Decimal("3"), Decimal("2.5")
    db.commit()
    items = {i["id"]: i for i in RADAR.search(db, a.id, market="all")["items"]}
    assert items[p.id]["facts"]["bedrooms"] == 3 and items[p.id]["facts"]["bathrooms"] == 2.5
    assert items[q.id]["facts"]["bedrooms"] is None and items[q.id]["facts"]["bathrooms"] is None
    json.dumps(items[p.id]["facts"])        # plain JSON numbers, never Decimal


def test_farm_market_is_agricultural_only_and_stays_in_its_workspace(db_session):
    """OLP Farm & Ranch: the 'farm' market shows only agricultural records of
    the caller's own workspace - never plain land, homes, or another tenant's farms."""
    db = db_session
    a = org(db, "Farm Workspace")
    b = org(db, "Other Workspace")
    property_record(db, a, "Ranch Road 9", "agricultural", state="KS")
    property_record(db, a, "Lot 4 Mesa Dr", "land", state="KS")
    property_record(db, a, "12 Elm St", "residential", state="KS")
    property_record(db, b, "Other Farm Rd", "agricultural", state="KS")
    db.commit()
    farm = RADAR.search(db, a.id, market="farm")
    assert [v["address"] for v in farm["items"]] == ["Ranch Road 9"]
    assert "farm" in farm["markets"]
    assert sum(s["count"] for s in farm["available"]["states"]) == 1   # coverage counts farms only
    assert RADAR.search(db, a.id, market="land")["total"] == 2      # land still includes farms
    assert RADAR.search(db, b.id, market="farm")["total"] == 1
    with pytest.raises(ValueError):
        RADAR.search(db, a.id, market="ranches")
