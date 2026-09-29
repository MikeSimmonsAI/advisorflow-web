"""LEAD INTELLIGENCE CONTROL CENTER — backend gates.

What this file defends:
  1. God only. Every route refuses advisors, org admins and anonymous callers.
  2. Truthful numbers. Summary / pipeline counts equal the rows that exist;
     nothing is estimated, trends are null.
  3. Thresholds are the engine's (HIGH 45+, MEDIUM 22-44, LOW <22) and cannot
     be edited from a screen.
  4. Suppression blocks routing, and EXCLUDED / duplicate / invalid prospects
     are never routable.
  5. Routing writes a STAGED Universal Intake batch in the destination org
     ONLY: no Lead rows, no contacts, no consent, nothing in any other org.
  6. The browser filters and paginates server-side.
  7. The scraper records industry + intended destination without routing, and
     never calls a real provider in tests.
"""
import itertools
import os
import re

import pytest

import app.models.lead_intel_models  # noqa: F401  (registers tables before create_all)
from app.models.lead_intel_models import (LeadIntelProspect, LeadIntelRoutingRule,
                                          LeadIntelScrapeJob)
from app.models.master_contact_models import LeadOccurrence, MasterContact
from app.models.models import Lead, Organization, Platform, SuppressionEntry, User
from app.services import lead_routing as LR
from app.services import qualification as Q
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_router():
    from app.main import app
    from app.routers.lead_intelligence_router import router
    if not any(getattr(r, "path", "") == "/god/lead-intelligence/summary" for r in app.routes):
        app.include_router(router)


_ensure_router()


# ── scaffolding ─────────────────────────────────────────────────────────────

def _org(db, name="Org"):
    p = Platform(name="LI Brand %d" % next(_SEQ), slug="libr%d" % next(_SEQ))
    db.add(p)
    db.commit()
    o = Organization(name=name, slug="liorg%d" % next(_SEQ), platform_id=p.id,
                     is_active=True, plan="trial")
    db.add(o)
    db.commit()
    return o


def _user(db, role, org=None):
    u = User(organization_id=org.id if org else None,
             email="li%d@example.test" % next(_SEQ), password_hash=hash_password("x"),
             full_name="U", role=role, must_change_password=False, is_active=True)
    db.add(u)
    db.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


@pytest.fixture()
def god(db_session):
    return _user(db_session, "god_admin")


@pytest.fixture()
def gh(db_session, god):
    return _h(db_session, god)


BIZ = [
    {"place_id": "p1", "name": "Acme Roofing", "phone": "(214) 555-0101",
     "address": "1 Main St, Dallas, TX 75201, USA", "website": "https://acme.test"},
    {"place_id": "p2", "name": "Beta Roofing", "phone": "214-555-0102",
     "address": "2 Elm St, Plano, TX 75023, USA"},
    {"place_id": "p3", "name": "Acme Roofing Dup", "phone": "+12145550101"},   # same phone as p1
    {"place_id": "p4", "name": "No Contact Co"},                               # invalid
    {"place_id": "p5", "name": "Suppressed LLC", "phone": "2145550105",
     "address": "5 Oak St, Austin, TX 78701, USA"},
]


def _suppress(db, org, phone):
    """Through the REAL write path the Compliance Center uses, so the stored
    format is whatever production stores (11 digits), not what a test guesses."""
    from app.services.compliance_service import add_suppression_entry
    add_suppression_entry(db, org.id, phone, "STOP")
    db.commit()


def _stage(client, gh, results=BIZ, **kw):
    body = {"results": results}
    body.update(kw)
    r = client.post("/scraper/stage", json=body, headers=gh)
    assert r.status_code == 201, r.text
    return r.json()


# ═══ 1. god only ════════════════════════════════════════════════════════════

@pytest.mark.parametrize("method,path", [
    ("get", "/god/lead-intelligence/summary"),
    ("get", "/god/lead-intelligence/pipeline"),
    ("get", "/god/lead-intelligence/prospects"),
    ("get", "/god/lead-intelligence/qualification-config"),
    ("get", "/god/lead-intelligence/routing-rules"),
    ("get", "/god/lead-intelligence/jobs"),
    ("post", "/god/lead-intelligence/route"),
    ("post", "/scraper/stage"),
])
@pytest.mark.parametrize("role", ["advisor", "admin", "org_admin", "super_admin"])
def test_every_route_is_god_only(client, db_session, method, path, role):
    org = _org(db_session)
    u = _user(db_session, role, org)
    body = {"prospect_ids": ["x"], "destination_org_id": org.id} if "route" in path else \
        ({"results": []} if "stage" in path else None)
    r = getattr(client, method)(path, headers=_h(db_session, u),
                                **({"json": body} if method == "post" else {}))
    assert r.status_code == 403, (role, path, r.status_code)


def test_anonymous_is_refused(client):
    assert client.get("/god/lead-intelligence/summary").status_code in (401, 403)


# ═══ 2. summary truthfulness ════════════════════════════════════════════════

def test_empty_summary_is_zero_not_invented(client, db_session, gh):
    r = client.get("/god/lead-intelligence/summary", headers=gh)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["prospects"]["total"] == 0
    assert s["prospects"]["routed"] == 0
    assert s["master_pool"]["total_contacts"] == 0
    assert s["master_pool"]["qualification"] is None       # not derivable -> null
    assert s["trends"] is None                               # never fabricated
    assert s["recent_activity"] == []
    assert all(v == 0 for v in s["prospects"]["by_bucket"].values())


def test_summary_counts_equal_real_rows(client, db_session, gh):
    org = _org(db_session, "Dest")
    _suppress(db_session, org, "+12145550105")
    # A master contact (synthetic ones must be excluded from the headline).
    mc = MasterContact(normalized_email="a@b.test")
    syn = MasterContact(normalized_email="qa@b.test", is_synthetic=True)
    db_session.add_all([mc, syn]); db_session.commit()
    db_session.add(LeadOccurrence(master_contact_id=mc.id, organization_id=org.id,
                                  lead_id="L1", source="import"))
    db_session.commit()

    out = _stage(client, gh, destination_org_id=org.id, industry="roofing")
    c = out["counts"]
    assert c["discovered"] == 5 and c["invalid"] == 1 and c["duplicate"] == 1
    assert c["suppressed"] == 1 and c["qualified"] == 2

    s = client.get("/god/lead-intelligence/summary", headers=gh).json()
    p = s["prospects"]
    assert p["total"] == db_session.query(LeadIntelProspect).count() == 5
    assert p["by_stage"] == {"qualified": 2, "duplicate": 1, "invalid": 1, "suppressed": 1}
    assert p["suppressed"] == 1 and p["duplicates"] == 1
    assert sum(p["by_bucket"].values()) == 3        # 2 qualified + 1 suppressed (EXCLUDED)
    assert p["by_bucket"][Q.EXCLUDED] == 1
    assert p["routed"] == 0
    assert s["master_pool"]["total_contacts"] == 1
    assert s["master_pool"]["total_occurrences"] == 1
    assert s["master_pool"]["sources"] == [{"source": "import", "count": 1}]

    pl = client.get("/god/lead-intelligence/pipeline", headers=gh).json()
    counts = {st["key"]: st["count"] for st in pl["stages"]}
    assert counts == {"discovered": 5, "normalized": 4, "deduped": 3,
                      "suppressed": 1, "qualified": 2, "routed": 0}


# ═══ 3. thresholds ══════════════════════════════════════════════════════════

def test_thresholds_are_the_engines_and_read_only(client, gh):
    assert Q.HIGH_THRESHOLD == 45 and Q.MEDIUM_THRESHOLD == 22
    cfg = client.get("/god/lead-intelligence/qualification-config", headers=gh).json()
    assert cfg["editable"] is False
    assert cfg["thresholds"]["high_min"] == 45
    assert cfg["thresholds"]["medium_min"] == 22 and cfg["thresholds"]["medium_max"] == 44
    assert cfg["thresholds"]["low_max"] == 21
    assert [b["bucket"] for b in cfg["buckets"]] == [Q.READY, Q.REVIEW, Q.EXCLUDED]
    r = client.put("/god/lead-intelligence/qualification-config",
                   json={"high_min": 10}, headers=gh)
    assert r.status_code == 409
    assert Q.HIGH_THRESHOLD == 45


@pytest.mark.parametrize("score,band", [(45, "HIGH"), (80, "HIGH"), (44, "MEDIUM"),
                                        (22, "MEDIUM"), (21, "LOW"), (0, "LOW")])
def test_threshold_buckets(score, band):
    assert LR.band_for(score) == band


def test_scraped_business_is_never_sms_ready():
    n = LR.normalize_business({"name": "X", "phone": "2145550199"})
    d = LR.qualify_normalized(n)
    assert d["channels"]["sms"]["bucket"] == Q.EXCLUDED
    assert d["channels"]["sms"]["reasons"][0]["code"] == "no_sms_consent"
    assert d["best_channel"] == "voice" and d["bucket"] == Q.READY


# ═══ 4 + 5. routing ═════════════════════════════════════════════════════════

def _ids_by_name(db):
    return {p.name: p for p in db.query(LeadIntelProspect).all()}


def test_routing_stages_intake_in_destination_only(client, db_session, gh):
    from app.models.import_models import ImportBatch, ImportStagedRow
    from app.models.intake_models import OrgContact
    dest = _org(db_session, "Destination Co")
    other = _org(db_session, "Other Co")
    _stage(client, gh)
    by = _ids_by_name(db_session)
    ids = [by["Acme Roofing"].id, by["Beta Roofing"].id]
    leads_before = db_session.query(Lead).count()

    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": ids, "destination_org_id": dest.id}, headers=gh)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["routed"] == 2 and out["status"] == "staged_for_review"

    batches = db_session.query(ImportBatch).all()
    assert len(batches) == 1 and batches[0].organization_id == dest.id
    rows = db_session.query(ImportStagedRow).filter(ImportStagedRow.batch_id == batches[0].id).all()
    assert len(rows) == 2 and all(r.organization_id == dest.id for r in rows)
    # NO Lead rows, NO contacts, nothing in the other org.
    assert db_session.query(Lead).count() == leads_before
    assert db_session.query(OrgContact).count() == 0
    assert db_session.query(ImportBatch).filter(ImportBatch.organization_id == other.id).count() == 0
    assert db_session.query(Lead).filter(Lead.sms_consent.is_(True)).count() == 0

    db_session.expire_all()
    for pid in ids:
        p = db_session.query(LeadIntelProspect).get(pid)
        assert p.routed_org_id == dest.id and p.routed_batch_id == batches[0].id
    s = client.get("/god/lead-intelligence/summary", headers=gh).json()
    assert s["prospects"]["routed"] == 2 and s["prospects"]["routed_organizations"] == 1
    assert any(a["type"] == "route" for a in s["recent_activity"])

    # Already routed -> refused, and nothing new is written.
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": ids, "destination_org_id": other.id}, headers=gh)
    assert r.status_code == 400
    assert db_session.query(ImportBatch).count() == 1


def test_suppression_at_destination_blocks_routing(client, db_session, gh):
    from app.models.import_models import ImportBatch
    dest = _org(db_session, "Dest")
    _stage(client, gh)                                  # no destination -> clean at stage time
    p = _ids_by_name(db_session)["Acme Roofing"]
    assert p.stage == "qualified"
    _suppress(db_session, dest, "+12145550101")         # suppressed since
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": [p.id], "destination_org_id": dest.id}, headers=gh)
    assert r.status_code == 400
    assert "suppressed" in r.text
    assert db_session.query(ImportBatch).count() == 0


def test_dnc_lead_at_destination_blocks_routing(client, db_session, gh):
    dest = _org(db_session, "Dest")
    db_session.add(Lead(organization_id=dest.id, first_name="Old", phone="12145550102",
                        status="dnc"))
    db_session.commit()
    _stage(client, gh)
    p = _ids_by_name(db_session)["Beta Roofing"]
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": [p.id], "destination_org_id": dest.id}, headers=gh)
    assert r.status_code == 400 and "dnc" in r.text


def test_excluded_duplicate_invalid_are_not_routable(client, db_session, gh):
    dest = _org(db_session, "Dest")
    _suppress(db_session, dest, "+12145550105")
    _stage(client, gh, destination_org_id=dest.id)
    by = _ids_by_name(db_session)
    bad = [by["Acme Roofing Dup"].id, by["No Contact Co"].id, by["Suppressed LLC"].id]
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": bad, "destination_org_id": dest.id}, headers=gh)
    assert r.status_code == 400
    # Mixed: the good one routes, the bad ones are reported.
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": bad + [by["Beta Roofing"].id],
                          "destination_org_id": dest.id}, headers=gh)
    assert r.status_code == 200, r.text
    reasons = {x["reason"] for x in r.json()["refused"]}
    assert reasons == {"duplicate", "invalid", "suppressed"}
    assert r.json()["routed"] == 1


def test_route_refuses_platform_org_and_unknown_org(client, db_session, gh):
    _stage(client, gh)
    pid = _ids_by_name(db_session)["Acme Roofing"].id
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": [pid], "destination_org_id": "org-god-platform"},
                    headers=gh)
    assert r.status_code == 400
    r = client.post("/god/lead-intelligence/route",
                    json={"prospect_ids": [pid], "destination_org_id": "nope"}, headers=gh)
    assert r.status_code == 404


def test_routing_rules_crud_and_preview_never_route(client, db_session, gh):
    dest = _org(db_session, "Dest")
    r = client.post("/god/lead-intelligence/routing-rules", headers=gh,
                    json={"name": "TX roofers", "destination_org_id": dest.id,
                          "match_state": "tx", "allowed_buckets": ["EXCLUDED"]})
    assert r.status_code == 400                        # excluded is never routable
    r = client.post("/god/lead-intelligence/routing-rules", headers=gh,
                    json={"name": "TX roofers", "destination_org_id": dest.id,
                          "match_state": "tx", "match_city": "Dallas"})
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["match_state"] == "TX"
    _stage(client, gh)
    by = _ids_by_name(db_session)
    pv = client.post("/god/lead-intelligence/route/preview", headers=gh,
                     json={"prospect_ids": [by["Acme Roofing"].id, by["Beta Roofing"].id]}).json()
    sug = {s["prospect_id"]: s for s in pv["suggestions"]}
    assert sug[by["Acme Roofing"].id]["rule_id"] == rule["id"]
    assert sug[by["Beta Roofing"].id]["rule_id"] is None      # Plano, not Dallas
    assert db_session.query(LeadIntelProspect).filter(
        LeadIntelProspect.routed_org_id.isnot(None)).count() == 0
    # Route via the rule's destination, explicitly.
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [by["Acme Roofing"].id], "rule_id": rule["id"]})
    assert r.status_code == 200 and r.json()["destination_org_id"] == dest.id
    r = client.put("/god/lead-intelligence/routing-rules/" + rule["id"], headers=gh,
                   json={"name": "Renamed", "destination_org_id": dest.id, "is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.delete("/god/lead-intelligence/routing-rules/" + rule["id"],
                         headers=gh).status_code == 200
    assert db_session.query(LeadIntelRoutingRule).count() == 0


# ═══ 6. browser filters ═════════════════════════════════════════════════════

def test_prospect_browser_filters_and_paginates(client, db_session, gh):
    dest = _org(db_session, "Dest")
    _suppress(db_session, dest, "+12145550105")
    _stage(client, gh, industry="roofing", destination_org_id=dest.id)
    g = lambda **p: client.get("/god/lead-intelligence/prospects", params=p, headers=gh).json()
    assert g()["total"] == 5
    assert g(bucket=Q.READY)["total"] == 2
    assert g(stage="duplicate")["total"] == 1
    assert g(state="tx")["total"] == 3
    assert g(city="Plano")["rows"][0]["name"] == "Beta Roofing"
    assert g(search="acme")["total"] == 2
    assert g(search="0102")["total"] == 1
    assert g(industry="roofing")["total"] == 5
    assert g(routed=True)["total"] == 0 and g(routed=False)["total"] == 5
    assert g(destination_org_id=dest.id)["total"] == 5
    ready = g(bucket=Q.READY)["rows"]
    lo = min(r["score"] for r in ready)
    assert g(score_min=lo + 1000)["total"] == 0
    assert g(score_min=lo, score_max=lo)["total"] >= 1
    page = g(limit=2, skip=0)
    assert len(page["rows"]) == 2 and page["total"] == 5
    assert len(g(limit=2, skip=4)["rows"]) == 1


def test_master_browser_status_and_date_filters(client, db_session, gh):
    from datetime import datetime
    org = _org(db_session)
    a = MasterContact(normalized_email="a@x.test"); b = MasterContact(normalized_email="b@x.test")
    db_session.add_all([a, b]); db_session.commit()
    db_session.add_all([
        LeadOccurrence(master_contact_id=a.id, organization_id=org.id, lead_id="1",
                       tenant_lead_status="new", first_seen_at=datetime(2026, 1, 5)),
        LeadOccurrence(master_contact_id=b.id, organization_id=org.id, lead_id="2",
                       tenant_lead_status="dnc", first_seen_at=datetime(2026, 6, 5)),
    ]); db_session.commit()
    g = lambda **p: client.get("/god/master/contacts", params=p, headers=gh)
    assert g().json()["total"] == 2
    assert g(status="dnc").json()["total"] == 1
    assert g(date_from="2026-06-01").json()["total"] == 1
    assert g(date_to="2026-01-05").json()["total"] == 1
    assert g(date_from="bad").status_code == 400


# ═══ 7. scraper ═════════════════════════════════════════════════════════════

def test_scraper_search_maps_industry_records_destination_and_never_calls_out(
        client, db_session, gh, monkeypatch):
    from app.routers import lead_scraper_router as S
    dest = _org(db_session, "Dest")
    seen = {}

    async def fake_search(query, location, radius, max_results, key):
        seen["query"] = query
        return [{"place_id": "g1", "name": "Acme", "formatted_address": "1 Main St, Dallas, TX 75201, USA"}]

    async def fake_details(pid, key):
        return {"formatted_phone_number": "(214) 555-0101"}

    monkeypatch.setattr(S, "_google_places_search", fake_search)
    monkeypatch.setattr(S, "_google_place_details", fake_details)
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    r = client.post("/scraper/search", json={"industry": "roofing"}, headers=gh)
    assert r.status_code == 503                         # provider config still required

    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key-not-real")
    r = client.post("/scraper/search", headers=gh, json={
        "industry": "roofing", "query": "Dallas", "location": "Dallas, TX",
        "destination_org_id": dest.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["query"] == "Dallas roofing contractor"
    job = db_session.query(LeadIntelScrapeJob).get(body["job_id"])
    assert job.destination_org_id == dest.id and job.industry == "roofing"
    assert job.result_count == 1
    # Not auto-routed: nothing in the pool is routed, nothing in the org.
    out = _stage(client, gh, results=body["results"], job_id=job.id)
    assert out["prospects"][0]["destination_org_id"] == dest.id
    assert out["prospects"][0]["routed_org_id"] is None
    assert db_session.query(Lead).filter(Lead.organization_id == dest.id).count() == 0

    assert client.post("/scraper/search", json={}, headers=gh).status_code == 422


def test_scraper_import_normalizes_dedupes_suppresses_and_qualifies(client, db_session, gh):
    org = _org(db_session, "Import Co")
    _suppress(db_session, org, "+12145550105")
    db_session.add(Lead(organization_id=org.id, first_name="Existing", phone="12145550102"))
    db_session.commit()
    r = client.post("/scraper/import", headers=gh, json={"target_org_id": org.id, "leads": [
        {"place_id": "a", "name": "Acme Roofing", "phone": "(214) 555-0101"},
        {"place_id": "b", "name": "Acme Again", "phone": "214.555.0101"},     # in-request dup
        {"place_id": "c", "name": "Beta", "phone": "214-555-0102"},          # existing (E.164)
        {"place_id": "d", "name": "Suppressed", "phone": "2145550105"},      # suppression list
    ]})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["imported"] == 1 and out["skipped"] == 2 and out["suppressed"] == 1
    lead = db_session.query(Lead).filter(Lead.first_name == "Acme").one()
    assert lead.phone == "(214) 555-0101"      # stored as before: as given
    assert lead.sms_consent in (False, None)
    assert out["qualification"]["channel"] == "voice"
    assert sum(out["qualification"]["buckets"].values()) == 1


# ═══ nav contract (static) ══════════════════════════════════════════════════

# Routes the coordinator adds in App.jsx alongside this workstream.
PLANNED_ROUTES = {"/god/lead-intelligence", "/god/entitlements"}


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as fh:
        return fh.read()


def test_every_god_nav_target_is_a_registered_route():
    shell = _read("frontend", "src", "pages", "GodShell.jsx")
    block = shell[shell.index("const NAV = ["):]
    block = block[:block.index("\n]")]
    paths = re.findall(r"path:\s*'([^']+)'", block)
    assert paths
    app_src = _read("frontend", "src", "App.jsx")
    routes = set(re.findall(r'<Route\s+path="([^"]+)"', app_src)) | PLANNED_ROUTES
    exact = {r for r in routes if "*" not in r and ":" not in r}
    dead = [p for p in paths if re.split(r"[?#]", p)[0] not in exact]
    assert not dead, "NAV targets without an exact route (catch-all does not count): %s" % dead


def test_moved_lead_tools_stay_reachable():
    shell = _read("frontend", "src", "pages", "GodShell.jsx")
    for group in ("PLATFORM", "SALES & REVENUE", "LEAD INTELLIGENCE", "AI WORKFORCE",
                  "SECURITY & PLATFORM"):
        assert "group: '%s'" % group in shell
    for p in ("/god/lead-intelligence", "/god/entitlements",
              "/god/diagnostics/qualification"):
        assert "'%s'" % p in shell
    app_src = _read("frontend", "src", "App.jsx")
    for p in ("/god/lead-scraper", "/god/lead-browser", "/god/diagnostics/qualification"):
        assert 'path="%s"' % p in app_src, "an existing route was removed: %s" % p
    li = _read("frontend", "src", "pages", "god", "LeadIntelligence.jsx")
    for p in ("/god/lead-scraper", "/god/lead-browser", "/god/diagnostics/qualification"):
        assert p in li, "Lead Intelligence no longer links to %s" % p


# ═══ review follow-ups ═══════════════════════════════════════════════════════

def test_real_suppression_format_is_refused_by_ingest_and_route(client, db_session, gh):
    from app.models.import_models import ImportBatch
    dest = _org(db_session, "Dest")
    _suppress(db_session, dest, "(214) 555-0101")
    assert db_session.query(SuppressionEntry).one().phone == "12145550101"   # real stored format
    # ingest(): destination known at staging time
    out = _stage(client, gh, results=[BIZ[0]], destination_org_id=dest.id)
    assert out["counts"]["suppressed"] == 1
    assert out["prospects"][0]["suppressed_reason"] == "suppression_list"
    # route(): staged clean (no destination), suppressed at the destination
    _suppress(db_session, dest, "2145550177")
    fresh = _stage(client, gh, results=[{"place_id": "y", "name": "Fresh Co",
                                         "phone": "+1 214 555 0177"}])
    assert fresh["prospects"][0]["stage"] == "qualified"
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [fresh["prospects"][0]["id"]],
                          "destination_org_id": dest.id})
    assert r.status_code == 400 and "suppression_list" in r.text
    assert db_session.query(ImportBatch).count() == 0


def test_email_suppression_blocks_routing(client, db_session, gh):
    from app.models.intake_models import OrgContact
    dest = _org(db_session, "Dest")
    db_session.add(OrgContact(organization_id=dest.id, email="owner@mail.test",
                              email_status="unsubscribed"))
    db_session.commit()
    # Scraper results carry no email, so stage through the service directly.
    out = LR.ingest(db_session, [{"place_id": "e1", "name": "Mail Only",
                                  "email": "Owner@Mail.test"}])
    db_session.commit()
    assert out["prospects"][0].stage == "qualified"
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [out["prospects"][0].id],
                          "destination_org_id": dest.id})
    assert r.status_code == 400 and "email_suppressed" in r.text
    assert LR.suppression_reason(db_session, dest.id, None, "owner@mail.test") == "email_suppressed"


def test_scraper_import_honours_real_suppression_format(client, db_session, gh):
    org = _org(db_session, "Import Co")
    _suppress(db_session, org, "214-555-0188")
    r = client.post("/scraper/import", headers=gh, json={"target_org_id": org.id, "leads": [
        {"place_id": "s", "name": "Stopped", "phone": "(214) 555-0188"}]})
    assert r.status_code == 201, r.text
    assert r.json()["suppressed"] == 1 and r.json()["imported"] == 0
    assert db_session.query(Lead).filter(Lead.organization_id == org.id).count() == 0


def test_search_escapes_like_wildcards(client, db_session, gh):
    _stage(client, gh)
    g = lambda s: client.get("/god/lead-intelligence/prospects", params={"search": s},
                             headers=gh).json()["total"]
    assert g("%") == 0 and g("_") == 0
    assert g("acme") == 2


def test_route_analysis_failure_rolls_back_and_retry_makes_one_batch(
        client, db_session, gh, monkeypatch):
    from app.models.import_models import ImportBatch
    from app.services.intake import engine as ENG
    dest = _org(db_session, "Dest")
    _stage(client, gh)
    pid = _ids_by_name(db_session)["Acme Roofing"].id
    real = ENG.run_analysis

    def boom(*a, **k):
        raise RuntimeError("analysis down")
    monkeypatch.setattr(ENG, "run_analysis", boom)
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [pid], "destination_org_id": dest.id})
    assert r.status_code == 400
    assert db_session.query(ImportBatch).count() == 0
    db_session.expire_all()
    assert db_session.query(LeadIntelProspect).get(pid).routed_org_id is None

    monkeypatch.setattr(ENG, "run_analysis", real)
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [pid], "destination_org_id": dest.id})
    assert r.status_code == 200, r.text
    r = client.post("/god/lead-intelligence/route", headers=gh,
                    json={"prospect_ids": [pid], "destination_org_id": dest.id})
    assert r.status_code == 400                           # already routed, no second batch
    assert db_session.query(ImportBatch).count() == 1
