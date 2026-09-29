"""Skip-trace economics (stream XE): estimates, dedupe, minimums, ranking,
confirmation-before-queueing, org isolation - and NO NETWORK.

No test here reaches a vendor: httpx / requests / urllib are replaced with
tripwires for every test, and the adapter tests use a local fake transport."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import app.models.skiptrace_cost_models  # noqa: F401  (tables for create_all)
from app.models.models import Organization, User
from app.services.auth_service import create_access_token, hash_password


def _ensure_router():
    from app.main import app
    from app.routers.skiptrace_cost_router import router
    if not any(getattr(r, "path", "").startswith("/wholesale/skip-trace/") for r in app.routes):
        app.include_router(router)


_ensure_router()


class _Tripwire(Exception):
    pass


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any HTTP client use in this module's code paths fails the test."""
    calls = []

    def boom(*a, **k):
        calls.append((a, k))
        raise _Tripwire("network call attempted: %r" % (a[:2],))
    import httpx
    monkeypatch.setattr(httpx, "request", boom)
    # the REAL transports (the in-process TestClient uses its own transport)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    try:
        import requests
        monkeypatch.setattr(requests.sessions.Session, "request", boom)
    except ImportError:                                   # pragma: no cover
        pass
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    for k in ("TRACERFY_API_TOKEN", "TRACERFY_BATCH_ENABLED", "TRACERFY_TRACE_PRODUCT",
              "SKIPTRACE_CUSTOM_PRODUCT", "SKIPTRACE_CUSTOM_ENABLED", "TRACERFY_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    yield calls
    assert calls == [], "an HTTP client was called"


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:500])
    return r.json()


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id, email=email or "%s@%s.test" % (role, org.slug),
             password_hash=hash_password("Pass12345!"), full_name=role.title(),
             role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def admin(db_session, sample_org):
    return _user(db_session, sample_org, "org_admin", "boss@st.test")


@pytest.fixture
def member(db_session, sample_org):
    return _user(db_session, sample_org, "advisor", "rep@st.test")


@pytest.fixture
def other_org(db_session):
    org = Organization(name="Other Wholesaler", slug="other-st", plan="standard", industry="real_estate")
    db_session.add(org)
    db_session.commit()
    return org


@pytest.fixture
def intruder(db_session, other_org):
    return _user(db_session, other_org, "org_admin", "intruder@other-st.test")


def _props(db, org, n):
    from app.models.wholesale_models import WholesaleProperty
    out = []
    for i in range(n):
        p = WholesaleProperty(organization_id=org.id, street_address="%d Cost St" % (i + 1),
                              city="Dallas", state="TX", zip_code="75201")
        db.add(p)
        out.append(p)
    db.commit()
    return out


# ── Pure maths ──────────────────────────────────────────────────────────────

def test_catalogue_every_price_has_a_source_or_is_marked_unverified(db_session):
    from app.services import skiptrace_costing as SC
    items = SC.list_products(db_session)
    assert len(items) >= 10
    for i in items:
        assert i["price_status"] in ("vendor_published", "third_party_reported", "unverified")
        if i["price_status"] != "unverified":
            assert i["source_url"] and i["verified_at"], i["key"]
        else:
            assert "owner to confirm" in (i["notes"] or "").lower() or i["provider"] in ("custom",), i["key"]
    cur = [i for i in items if i["is_current"]]
    assert [c["key"] for c in cur] == ["tracerfy:instant"]
    assert cur[0]["cost_per_hit_cents"] == 10.0 and cur[0]["misses_charged"] is False
    bench = next(i for i in items if i["provider"] == "benchmark_derrick")
    assert bench["is_active"] is False and bench["price_status"] == "unverified"
    # configured is a presence check only; no credential value appears anywhere
    assert all(i["configured"] is False for i in items if i["provider"] == "tracerfy")


def test_configured_is_presence_only_and_never_leaks(db_session, monkeypatch):
    from app.services import skiptrace_costing as SC
    monkeypatch.setenv("TRACERFY_API_TOKEN", "sk_live_SECRET_VALUE")
    items = SC.list_products(db_session)
    t = next(i for i in items if i["key"] == "tracerfy:normal_batch")
    assert t["configured"] is True
    assert "SECRET_VALUE" not in json.dumps(items)


def test_tracerfy_default_is_normal_batch_behind_config(monkeypatch):
    from app.services import skiptrace_costing as SC
    assert SC.tracerfy_default_product() == "normal_batch"
    monkeypatch.setenv("TRACERFY_TRACE_PRODUCT", "advanced")
    assert SC.tracerfy_default_product() == "advanced_batch"
    monkeypatch.setenv("TRACERFY_TRACE_PRODUCT", "instant")
    assert SC.tracerfy_default_product() == "instant"
    monkeypatch.setenv("TRACERFY_TRACE_PRODUCT", "garbage")
    assert SC.tracerfy_default_product() == "normal_batch"


def test_estimate_count_only_misses_free_vs_charged(db_session, sample_org):
    from app.services import skiptrace_costing as SC
    inst = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy:instant", record_count=250,
                             hit_rate=0.7, persist=False)
    # misses free: 175 hits x 10c; maximum = every record a hit
    assert inst["estimated_total_cents"] == 1750.0 and inst["maximum_total_cents"] == 2500.0
    norm = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", record_count=250,
                             hit_rate=0.7, persist=False)
    assert norm["product_key"] == "tracerfy:normal_batch"
    # worst case assumed: every uploaded lead charged at 2c
    assert norm["estimated_total_cents"] == 500.0 and norm["per_record_effective_cents"] == 2.0
    assert any("Every billable record is charged" in a for a in norm["assumptions"])
    ds = SC.estimate_batch(db_session, sample_org.id, product_key="dataskip:bulk", record_count=500,
                           hit_rate=0.5, persist=False)
    assert ds["estimated_total_cents"] == 1000.0 and ds["maximum_total_cents"] == 2000.0


def test_decimal_safe_sub_cent_prices(db_session, sample_org, monkeypatch):
    from app.services import skiptrace_costing as SC
    monkeypatch.setenv("SKIPTRACE_CUSTOM_PRODUCT", json.dumps({
        "label": "Owner-named vendor", "cost_per_hit_cents": "0.7", "misses_charged": True,
        "token_env": "CUSTOM_VENDOR_TOKEN"}))
    e = SC.estimate_batch(db_session, sample_org.id, product_key="custom:default", record_count=333,
                          persist=False)
    assert Decimal(str(e["estimated_total_cents"])) == Decimal("233.1")


def test_monthly_minimum_and_subscription_handling(db_session, sample_org):
    from app.services import skiptrace_costing as SC
    bd = SC.estimate_batch(db_session, sample_org.id, product_key="batchdata:growth", record_count=250,
                           persist=False)
    assert bd["estimated_total_cents"] == 0.0            # within the plan allowance
    assert bd["monthly_total_cents"] == 200000.0         # the $2,000 plan is still owed
    assert "Subscription $2000.00/month" in bd["monthly_minimum_note"]
    assert bd["per_record_effective_cents"] == 800.0     # all-in per record at this volume
    sg = SC.estimate_batch(db_session, sample_org.id, product_key="skipgenie:subscription", record_count=250,
                           persist=False)
    # 100 included, 150 over at 17c, + $58
    assert sg["estimated_total_cents"] == 2550.0 and sg["monthly_total_cents"] == 8350.0
    t = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", record_count=10, persist=False)
    assert "one-time minimum credit purchase $20.00" in t["monthly_minimum_note"]


def test_compare_ranks_by_total_cost_not_unit_price(db_session):
    from app.services import skiptrace_costing as SC
    for n in (250, 500):
        r = SC.compare_providers(db_session, n)
        keys = [i["key"] for i in r["items"]]
        assert r["recommendation"]["key"] == "tracerfy:normal_batch"
        # BatchData is ~2c/record at full use but its $2,000/mo plan loses at this volume
        assert keys.index("batchdata:growth") > keys.index("dataskip:bulk")
        assert keys.index("reapi:skiptrace") > keys.index("tracerfy:instant")
        # the benchmark and non-API tools never outrank a runnable product
        runnable = [i["runnable"] for i in r["items"]]
        assert runnable == sorted(runnable, reverse=True)
        assert r["current"]["key"] == "tracerfy:instant"
        assert any("saves" in x for x in r["reasoning"])
        assert any("Derrick" in x and "not recommended" in x for x in r["reasoning"])
        ranked = [i["monthly_incl_minimum_cents"] for i in r["items"] if i["runnable"]]
        assert ranked == sorted(ranked)


def test_benchmark_can_never_be_confirmed(db_session, sample_org, admin):
    from app.services import skiptrace_costing as SC
    e = SC.estimate_batch(db_session, sample_org.id, product_key="benchmark_derrick:reported",
                          record_count=10, user=admin)
    with pytest.raises(SC.ConfirmationRequired):
        SC.confirm_estimate(db_session, sample_org.id, e["estimate_id"], e["confirmation_phrase"], admin)


# ── Dedupe ──────────────────────────────────────────────────────────────────

def test_dedupe_excludes_recently_traced_properties(db_session, sample_org, admin):
    from app.models.wholesale_models import WholesaleEnrichmentRequest
    from app.services import skiptrace_costing as SC
    props = _props(db_session, sample_org, 5)
    db_session.add_all([
        WholesaleEnrichmentRequest(organization_id=sample_org.id, property_id=props[0].id, provider="x",
                                   status="succeeded"),
        WholesaleEnrichmentRequest(organization_id=sample_org.id, property_id=props[1].id, provider="x",
                                   status="no_match"),
        WholesaleEnrichmentRequest(organization_id=sample_org.id, property_id=props[2].id, provider="x",
                                   status="failed"),                         # a failure is retried
        WholesaleEnrichmentRequest(organization_id=sample_org.id, property_id=props[3].id, provider="x",
                                   status="succeeded", created_at=datetime.utcnow() - timedelta(days=200)),
    ])
    db_session.commit()
    ids = [p.id for p in props] + [props[4].id]                               # a duplicate in the request
    e = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy:instant", property_ids=ids,
                          hit_rate=1, user=admin)
    assert (e["records"], e["already_traced"], e["billable_requests"]) == (5, 2, 3)
    assert e["estimated_total_cents"] == 30.0          # 3 billable x 10c (hit_rate 1)
    raw = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy:instant", property_ids=ids,
                            hit_rate=1, dedupe=False, persist=False)
    assert raw["billable_requests"] == 5


def test_dedupe_evosense_ledger(db_session, sample_org):
    from app.models.evosense_models import EvoSenseCostEntry, EvoSenseProperty
    from app.services import skiptrace_costing as SC
    a = EvoSenseProperty(organization_id=sample_org.id)
    b = EvoSenseProperty(organization_id=sample_org.id)
    db_session.add_all([a, b])
    db_session.commit()
    db_session.add(EvoSenseCostEntry(organization_id=sample_org.id, provider_key="tracerfy",
                                     capability="CONTACT_ENRICHMENT", operation="owner_contact_lookup",
                                     property_id=a.id, total_cents=10, status="charged", success=True,
                                     period_day="2026-09-28", period_month="2026-09"))
    db_session.commit()
    e = SC.estimate_batch(db_session, sample_org.id, product_key="dataskip:bulk", property_ids=[a.id, b.id],
                          property_kind="evosense", persist=False)
    assert e["already_traced"] == 1 and e["billable_requests"] == 1


# ── API, confirmation gate, isolation ───────────────────────────────────────

def test_api_providers_compare_estimate(client, db_session, admin):
    h = _h(db_session, admin)
    p = ok(client.get("/wholesale/skip-trace/providers", headers=h))
    assert p["integration"]["current"]["product"] == "instant"
    assert p["integration"]["default_product"] == "tracerfy:normal_batch"
    assert p["integration"]["paid_execution_in_this_build"] is False
    assert all(a["enabled"] is False for a in p["adapters"])
    c = ok(client.get("/wholesale/skip-trace/compare?records=500", headers=h))
    assert c["recommendation"]["key"] == "tracerfy:normal_batch"
    e = ok(client.post("/wholesale/skip-trace/estimate", headers=h, json={"record_count": 250}))
    assert e["estimate_id"] and e["estimated_total"] == "$5.00" and e["status"] == "estimated"
    assert e["confirmation_phrase"] == "APPROVE SKIP TRACE %s" % e["estimate_id"]
    # count-only estimates are informational: never approvable, never runnable
    assert e["approvable"] is False
    r = client.post("/wholesale/skip-trace/estimates/%s/confirm" % e["estimate_id"], headers=h,
                    json={"confirm": e["confirmation_phrase"]})
    assert r.status_code == 409 and "count-only" in r.text
    bad = client.post("/wholesale/skip-trace/estimate", headers=h, json={"product_key": "nope", "record_count": 1})
    assert bad.status_code == 422


def test_confirmation_required_before_a_paid_run_can_be_queued(client, db_session, sample_org, admin, member):
    from app.services import skiptrace_costing as SC
    h = _h(db_session, admin)
    props = _props(db_session, sample_org, 3)
    ids = [p.id for p in props]
    e = ok(client.post("/wholesale/skip-trace/estimate", headers=h,
                       json={"property_ids": ids[:2], "product_key": "tracerfy"}))
    eid = e["estimate_id"]
    # no estimate / unconfirmed estimate -> refused
    with pytest.raises(SC.ConfirmationRequired):
        SC.gate_paid_run(db_session, sample_org.id, None, provider="tracerfy", record_count=2)
    with pytest.raises(SC.ConfirmationRequired):
        SC.gate_paid_run(db_session, sample_org.id, eid, provider="tracerfy", record_count=2)
    # a non-admin cannot approve spend; a wrong phrase is refused
    assert client.post("/wholesale/skip-trace/estimates/%s/confirm" % eid, headers=_h(db_session, member),
                       json={"confirm": e["confirmation_phrase"]}).status_code == 403
    assert client.post("/wholesale/skip-trace/estimates/%s/confirm" % eid, headers=h,
                       json={"confirm": "yes"}).status_code == 409
    c = ok(client.post("/wholesale/skip-trace/estimates/%s/confirm" % eid, headers=h,
                       json={"confirm": e["confirmation_phrase"]}))
    assert c["status"] == "confirmed" and c["executes_anything"] is False
    # confirmed, but the run must match: provider, size, ids
    for kw in ({"provider": "dataskip", "record_count": 2}, {"provider": "tracerfy", "record_count": 3},
               {"provider": "tracerfy", "property_ids": ids}):
        with pytest.raises(SC.ConfirmationRequired):
            SC.gate_paid_run(db_session, sample_org.id, eid, consume=False, **kw)
    ok_e = SC.gate_paid_run(db_session, sample_org.id, eid, provider="tracerfy", property_ids=ids[:2],
                            record_count=2, consumer="test")
    assert ok_e.status == "consumed"
    # consumed once: a second run with the same approval is refused (no double billing)
    with pytest.raises(SC.ConfirmationRequired):
        SC.gate_paid_run(db_session, sample_org.id, eid, provider="tracerfy", record_count=2)


def test_expired_estimate_cannot_be_confirmed(client, db_session, admin):
    from app.models.skiptrace_cost_models import SkipTraceCostEstimate
    h = _h(db_session, admin)
    e = ok(client.post("/wholesale/skip-trace/estimate", headers=h, json={"record_count": 5}))
    row = db_session.get(SkipTraceCostEstimate, e["estimate_id"])
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    r = client.post("/wholesale/skip-trace/estimates/%s/confirm" % e["estimate_id"], headers=h,
                    json={"confirm": e["confirmation_phrase"]})
    assert r.status_code == 409 and "expired" in r.text


def test_org_isolation(client, db_session, sample_org, admin, intruder):
    from app.services import skiptrace_costing as SC
    h, hi = _h(db_session, admin), _h(db_session, intruder)
    props = _props(db_session, sample_org, 2)
    e = ok(client.post("/wholesale/skip-trace/estimate", headers=h, json={"record_count": 10}))
    eid = e["estimate_id"]
    assert client.get("/wholesale/skip-trace/estimates/%s" % eid, headers=hi).status_code == 404
    assert client.post("/wholesale/skip-trace/estimates/%s/confirm" % eid, headers=hi,
                       json={"confirm": e["confirmation_phrase"]}).status_code == 404
    with pytest.raises(SC.ConfirmationRequired):
        SC.gate_paid_run(db_session, intruder.organization_id, eid, provider="tracerfy")
    # another org's property ids are ignored, never priced or revealed
    x = ok(client.post("/wholesale/skip-trace/estimate", headers=hi,
                       json={"property_ids": [p.id for p in props]}))
    assert (x["records"], x["not_found"], x["billable_requests"]) == (0, 2, 0)
    assert ok(client.get("/wholesale/skip-trace/estimates/%s" % eid, headers=h))["estimate_id"] == eid


def test_routes_require_the_wholesale_feature(client, db_session, other_org, intruder):
    other_org.enabled_features = json.dumps(["leads"])
    db_session.commit()
    for path in ("/wholesale/skip-trace/providers", "/wholesale/skip-trace/compare?records=10"):
        assert client.get(path, headers=_h(db_session, intruder)).status_code in (402, 403)


# ── Adapters: request construction with fakes, never a send by default ─────

class _FakeTransport:
    def __init__(self):
        self.sent = []

    def send(self, request, secret_headers):
        self.sent.append((request, secret_headers))
        return {"queued": True, "fake": True}


def _records(props):
    from app.services.skiptrace_adapters import SkipTraceRecord
    return [SkipTraceRecord(property_id=p.id, street_address=p.street_address, city=p.city,
                            state=p.state, zip_code=p.zip_code) for p in props]


def test_tracerfy_batch_request_defaults_to_normal(db_session, sample_org, monkeypatch):
    from app.services.skiptrace_adapters import TracerfyBatchAdapter, default_adapter
    props = _props(db_session, sample_org, 2)
    a = default_adapter()
    assert isinstance(a, TracerfyBatchAdapter) and a.product_key == "tracerfy:normal_batch"
    req = a.build_request(_records(props))
    assert req.method == "POST" and req.data["trace_type"] == "normal"
    assert req.url == "https://tracerfy.com/v1/api/trace/" and req.record_count == 2
    assert "lookup" not in req.url                       # not the 5-credit instant endpoint
    name, body, mime = req.files["csv_file"]
    lines = body.strip().split("\n")
    assert lines[0].startswith("address,city,state,zip") and len(lines) == 3
    assert "1 Cost St,Dallas,TX,75201" in lines[1]
    assert req.headers["Authorization"] == "Bearer ***redacted***"
    assert "webhook_url" not in req.data
    monkeypatch.setenv("TRACERFY_TRACE_PRODUCT", "advanced")
    monkeypatch.setenv("TRACERFY_WEBHOOK_URL", "https://example.test/hook")
    req2 = TracerfyBatchAdapter().build_request(_records(props))
    assert req2.data["trace_type"] == "advanced" and req2.data["webhook_url"] == "https://example.test/hook"
    monkeypatch.setenv("TRACERFY_TRACE_PRODUCT", "instant")
    assert TracerfyBatchAdapter().product_key == "tracerfy:normal_batch"   # batch never silently premium


def test_adapter_submit_refused_unless_enabled_configured_transport_and_confirmed(db_session, sample_org, admin,
                                                                                 monkeypatch):
    from app.services import skiptrace_costing as SC
    from app.services.skiptrace_adapters import AdapterRefused, TracerfyBatchAdapter
    props = _props(db_session, sample_org, 2)
    recs = _records(props)
    fake = _FakeTransport()
    a = TracerfyBatchAdapter()
    with pytest.raises(AdapterRefused):                                     # disabled by default
        a.submit(db_session, sample_org.id, recs, estimate_id=None, transport=fake)
    monkeypatch.setenv("TRACERFY_BATCH_ENABLED", "true")
    with pytest.raises(AdapterRefused):                                     # no credential
        a.submit(db_session, sample_org.id, recs, estimate_id=None, transport=fake)
    monkeypatch.setenv("TRACERFY_API_TOKEN", "tok_fake")
    with pytest.raises(AdapterRefused):                                     # no transport
        a.submit(db_session, sample_org.id, recs, estimate_id=None, transport=None)
    with pytest.raises(SC.ConfirmationRequired):                            # no confirmed estimate
        a.submit(db_session, sample_org.id, recs, estimate_id=None, transport=fake)
    e = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", property_ids=[p.id for p in props],
                          user=admin)
    SC.confirm_estimate(db_session, sample_org.id, e["estimate_id"], e["confirmation_phrase"], admin)
    out = a.submit(db_session, sample_org.id, recs, estimate_id=e["estimate_id"], transport=fake)
    assert out["fake"] is True and len(fake.sent) == 1
    req, secret = fake.sent[0]
    assert secret["Authorization"] == "Bearer tok_fake" and "tok_fake" not in json.dumps(req.headers)
    with pytest.raises(SC.ConfirmationRequired):                            # approval consumed
        a.submit(db_session, sample_org.id, recs, estimate_id=e["estimate_id"], transport=fake)


def test_custom_adapter_slot_is_config_driven_and_disabled_by_default(db_session, sample_org, monkeypatch):
    from app.services import skiptrace_costing as SC
    from app.services.skiptrace_adapters import AdapterRefused, CustomHttpAdapter
    monkeypatch.setenv("SKIPTRACE_CUSTOM_PRODUCT", json.dumps({
        "label": "Owner vendor", "url": "https://vendor.example.test/trace", "token_env": "OWNER_VENDOR_TOKEN",
        "cost_per_hit_cents": 1, "misses_charged": False}))
    items = {i["key"]: i for i in SC.list_products(db_session)}
    assert items["custom:default"]["is_active"] is False                    # not recommended until enabled
    a = CustomHttpAdapter()
    props = _props(db_session, sample_org, 1)
    req = a.build_request(_records(props))
    assert req.json_body["records"][0]["street_address"] == "1 Cost St"
    with pytest.raises(AdapterRefused):
        a.submit(db_session, sample_org.id, _records(props), estimate_id=None, transport=_FakeTransport())
    monkeypatch.setenv("SKIPTRACE_CUSTOM_ENABLED", "true")
    items = {i["key"]: i for i in SC.list_products(db_session)}
    assert items["custom:default"]["is_active"] is True


def test_costing_modules_import_no_http_client():
    import inspect
    from app.services import skiptrace_adapters, skiptrace_costing
    for mod in (skiptrace_costing, skiptrace_adapters):
        src = inspect.getsource(mod)
        for bad in ("import httpx", "import requests", "urllib.request", "import aiohttp"):
            assert bad not in src, "%s imports %s" % (mod.__name__, bad)


def test_catalogue_seeding_is_idempotent_and_refreshes_stale_rows(db_session):
    from app.models.skiptrace_cost_models import SkipTraceProviderProduct as P
    from app.services import skiptrace_costing as SC
    SC.ensure_catalogue(db_session)
    n = db_session.query(P).count()
    row = db_session.query(P).filter(P.key == "tracerfy:instant").first()
    row.cost_per_hit_cents = Decimal("99")
    row.catalogue_version = "stale"
    db_session.commit()
    SC.ensure_catalogue(db_session)
    SC.ensure_catalogue(db_session)
    assert db_session.query(P).count() == n
    assert db_session.query(P).filter(P.key == "tracerfy:instant").first().cost_per_hit_cents == Decimal("10")


def _member(db, user, org, role):
    from app.models.sales_models import Membership
    from app.services.workspace_access import SCOPE_CUSTOMER_ORG
    m = Membership(user_id=user.id, scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id, role=role, is_active=True)
    db.add(m)
    db.commit()
    return m


def test_home_org_super_admin_cannot_approve_in_a_workspace_where_they_are_an_advisor(
        client, db_session, sample_org, admin, other_org):
    from app.services.workspace_access import WORKSPACE_HEADER
    boss_elsewhere = _user(db_session, other_org, "super_admin", "super@other-st.test")
    _member(db_session, boss_elsewhere, sample_org, "advisor")
    props = _props(db_session, sample_org, 2)
    e = ok(client.post("/wholesale/skip-trace/estimate", headers=_h(db_session, admin),
                       json={"property_ids": [p.id for p in props]}))
    assert e["approvable"] is True
    hx = dict(_h(db_session, boss_elsewhere), **{WORKSPACE_HEADER: sample_org.id})
    r = client.post("/wholesale/skip-trace/estimates/%s/confirm" % e["estimate_id"], headers=hx,
                    json={"confirm": e["confirmation_phrase"]})
    assert r.status_code == 403, r.text
    # an org_admin MEMBERSHIP in this workspace may approve
    promoted = _user(db_session, other_org, "advisor", "promoted@other-st.test")
    _member(db_session, promoted, sample_org, "org_admin")
    hp = dict(_h(db_session, promoted), **{WORKSPACE_HEADER: sample_org.id})
    ok(client.post("/wholesale/skip-trace/estimates/%s/confirm" % e["estimate_id"], headers=hp,
                   json={"confirm": e["confirmation_phrase"]}))


def test_count_only_estimate_can_never_authorize_a_run(db_session, sample_org, admin):
    from app.models.skiptrace_cost_models import SkipTraceCostEstimate
    from app.services import skiptrace_costing as SC
    e = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", record_count=5, user=admin)
    row = db_session.get(SkipTraceCostEstimate, e["estimate_id"])
    row.status = "confirmed"                       # even if forced to confirmed
    db_session.commit()
    with pytest.raises(SC.ConfirmationRequired, match="count-only"):
        SC.gate_paid_run(db_session, sample_org.id, e["estimate_id"], provider="tracerfy", property_ids=[])


def test_consume_is_atomic_a_stale_confirmed_row_cannot_be_used_twice(db_session, sample_org, admin):
    from app.models.skiptrace_cost_models import SkipTraceCostEstimate as E
    from app.services import skiptrace_costing as SC
    props = _props(db_session, sample_org, 2)
    ids = [p.id for p in props]
    e = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", property_ids=ids, user=admin)
    SC.confirm_estimate(db_session, sample_org.id, e["estimate_id"], e["confirmation_phrase"], admin)
    row = db_session.get(E, e["estimate_id"])
    assert row.status == "confirmed"
    # a concurrent request consumes it behind this session's back (identity map stays stale)
    db_session.query(E).filter(E.id == row.id).update({"status": "consumed"}, synchronize_session=False)
    assert row.status == "confirmed"               # stale in-memory view
    with pytest.raises(SC.ConfirmationRequired, match="no longer confirmed"):
        SC.gate_paid_run(db_session, sample_org.id, row.id, provider="tracerfy", property_ids=ids)
    # and a double-listed id is refused
    e2 = SC.estimate_batch(db_session, sample_org.id, product_key="tracerfy", property_ids=ids,
                           dedupe=False, user=admin)
    SC.confirm_estimate(db_session, sample_org.id, e2["estimate_id"], e2["confirmation_phrase"], admin)
    with pytest.raises(SC.ConfirmationRequired, match="more than once"):
        SC.gate_paid_run(db_session, sample_org.id, e2["estimate_id"], provider="tracerfy",
                         property_ids=[ids[0], ids[0]])
