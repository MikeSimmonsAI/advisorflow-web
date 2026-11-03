# -*- coding: utf-8 -*-
"""#470 Stage B - explicit destinations; EvoSys Sales and customer Acquisition
business imports. Synthetic companies on example.test with 555 numbers only.
The column layout matches the real cleaning-contact package; no real rows."""
import io
import itertools
import uuid

import pytest

from app.models.acquisition_models import AcquisitionEvidence, AcquisitionOpportunity
from app.models.lead_intel_models import LeadIntelProspect
from app.models.models import CadenceState, EmailMessage, Message, Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity, OpportunityEvent
from app.services.auth_service import create_access_token, hash_password
from app.services.intake import business_import as BI
from app.services.intake import sales_import as SI
from tests.test_acquisition_pilot import pilot  # noqa: F401

_SEQ = itertools.count(1)
HDR = ("source_record_id,company,full_name,phone,email,state,country,source,classification,tags,notes,website,"
       "location_status,contact_preference,email_status,phone_line_type,intro_message,source_images")
SRC = "Synthetic test ads"


def _line(sid, company, phone, state="", country="", loc="Eligible", notes="note", email="", pref="not_stated",
          line="unknown", intro=None, name="", website=""):
    intro = intro if intro is not None else "Hi %s - would you take new commercial clients? $250 pilot." % company
    cells = [sid, company, name, phone, email, state, country, SRC, "cold_lead", "pilot_test", notes, website, loc,
             pref, "unverified" if email else "", line, intro, "img_%s.png" % sid]
    return ",".join('"%s"' % c.replace('"', '""') for c in cells)


def _csv(*lines):
    return "\n".join((HDR,) + lines) + "\n"


SALES_CSV = _csv(
    _line("TEST-CLEAN-004", "Sparkle Test Cleaning", "8135550104", "FL", "US"),          # hand-entered already
    _line("TEST-CLEAN-005", "Brightway Test Cleaners", "4805550105", "AZ", "US", pref="text_only", line="mobile"),
    _line("TEST-CLEAN-006", "Nowhere Test Maids", "2025550106", loc="Pending"),           # location unknown
    _line("TEST-CLEAN-007", "Coast Test Cleaning", "3105550107", "CA", "US", loc="Excluded"),
    _line("TEST-CLEAN-008", "Maple Test Janitorial", "4165550108", "", "CA", loc="Excluded"),
    _line("TEST-CLEAN-009", "Brightway Again", "(480) 555-0105", "AZ", "US"),             # repeats 005 by phone
    _line("TEST-CLEAN-010", "Formula Test Co", "6165550110", "MI", "US", notes="=HYPERLINK(\"http://x.test\")",
          email="hello@formula.example.test"),
)


def _user(db, org=None, role="org_admin"):
    u = User(organization_id=org.id if org else None, email="sb-%s@example.test" % uuid.uuid4().hex[:8],
             full_name="Test %s" % role, password_hash=hash_password("Pass12345!"), role=role, is_active=True,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _h(db, u, **extra):
    return dict({"Authorization": "Bearer %s" % create_access_token(u, db)}, **extra)


@pytest.fixture()
def sales(db_session, client):
    db = db_session
    if db.query(Organization).filter(Organization.id == BI.PLATFORM_ORG_ID).first() is None:
        db.add(Organization(id=BI.PLATFORM_ORG_ID, name="Platform", slug="platform-%d" % next(_SEQ),
                            plan="enterprise", is_active=True))
    plat = Platform(name="EvoSys Pro", slug="evosys-sb-%d" % next(_SEQ))
    db.add(plat)
    db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Sales", slug="evo-sales-sb-%d" % next(_SEQ))
    other = BrandSalesOrg(platform_id=plat.id, name="Other Brand Sales", slug="other-sales-sb-%d" % next(_SEQ))
    db.add_all([brand, other])
    db.commit()
    god = _user(db, None, "god_admin")
    manager, rep, other_mgr = _user(db), _user(db), _user(db)
    db.add_all([Membership(user_id=manager.id, scope_type="brand_sales_org", scope_id=brand.id, role="sales_manager", is_active=True),
                Membership(user_id=rep.id, scope_type="brand_sales_org", scope_id=brand.id, role="sales_rep", is_active=True),
                Membership(user_id=other_mgr.id, scope_type="brand_sales_org", scope_id=other.id, role="sales_manager", is_active=True)])
    # The prospect entered by hand before any import: id only inside the source text.
    hand = Opportunity(brand_sales_org_id=brand.id, owner_user_id=manager.id, company_name="Sparkle Test Cleaning",
                       phone="(813) 555-0104", industry="Cleaning Services", source="Facebook ad | TEST-CLEAN-004 | Anytown",
                       stage="prospect", status="open", next_action="Growth Pilot intro")
    db.add(hand)
    db.commit()
    db.add(OpportunityEvent(opportunity_id=hand.id, event_type="created", summary="Prospect created", actor_user_id=manager.id))
    db.commit()
    return dict(db=db, c=client, brand=brand, other=other, god=god, manager=manager, rep=rep, other_mgr=other_mgr, hand=hand)


def _start(p, u, csv=SALES_CSV, target=None, profile="cleaning_services"):
    target = target or p["brand"].id
    r = p["c"].post("/intake/sales/%s/batches" % target, headers=_h(p["db"], u),
                    files={"file": ("pkg.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"source_system": "facebook_ads", "profile": profile})
    return r


def _analyzed(p, u, csv=SALES_CSV):
    r = _start(p, u, csv)
    assert r.status_code == 200, r.text
    b = r.json()
    m = b["mapping"]
    # The package's own headers map without help.
    assert (m["business_name"], m["source_record_id"], m["contact_name"], m["intro_draft"], m["location_status"]) == \
        ("company", "source_record_id", "full_name", "intro_message", "location_status")
    a = p["c"].post("/intake/sales/%s/batches/%s/analyze" % (p["brand"].id, b["id"]), headers=_h(p["db"], u))
    assert a.status_code == 200, a.text
    return a.json()


def _rows(p, u, b):
    return {r["business"]["source_record_id"]: r for r in
            p["c"].get("/intake/sales/%s/batches/%s/rows" % (p["brand"].id, b["id"]), headers=_h(p["db"], u)).json()["items"]}


def _commit(p, u, b, confirm=True):
    body = {"confirm_target_id": p["brand"].id} if confirm else {}
    return p["c"].post("/intake/sales/%s/batches/%s/commit" % (p["brand"].id, b["id"]), json=body, headers=_h(p["db"], u))


def _sent(db):
    return db.query(EmailMessage).count(), db.query(Message).count(), db.query(CadenceState).count()


# ── staging, geography, matching ────────────────────────────────────────────
def test_sales_staging_holds_pending_excludes_out_of_territory_and_matches_the_hand_entered(sales):
    p = sales
    b = _analyzed(p, p["manager"])
    c = b["counts"]
    assert c["rows"] == 7 and c["ready"] == 2
    assert c["matched_existing"] == 1 and c["held_for_review"] == 1 and c["excluded"] == 2 and c["duplicate_in_file"] == 1
    rows = _rows(p, p["manager"], b)
    assert rows["TEST-CLEAN-004"]["outcome"] == "matched_existing"
    assert rows["TEST-CLEAN-004"]["existing_id"] == p["hand"].id and "TEST-CLEAN-004" in rows["TEST-CLEAN-004"]["match_reason"]
    assert rows["TEST-CLEAN-006"]["outcome"] == "held_for_review"
    assert rows["TEST-CLEAN-007"]["outcome"] == rows["TEST-CLEAN-008"]["outcome"] == "excluded"
    assert rows["TEST-CLEAN-009"]["outcome"] == "duplicate_in_file"
    # A number in an ad is not a mobile line, and nothing is consent.
    biz = rows["TEST-CLEAN-005"]["business"]
    assert biz["phone_line_type"] == "unknown" and biz["sms_consent"] is False
    assert biz["contact_preference_label"].startswith("Text only")


def test_sales_commit_creates_drafts_sends_nothing_and_leaves_the_existing_record_alone(sales):
    p = sales
    before_sent = _sent(p["db"])
    hand_events = p["db"].query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == p["hand"].id).count()
    b = _analyzed(p, p["manager"])
    assert _commit(p, p["manager"], b, confirm=False).status_code == 409         # destination must be confirmed
    r = _commit(p, p["manager"], b)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["created"] == 2 and rep["matched_existing"] == 1 and rep["held_for_review"] == 1 and rep["excluded"] == 2
    assert rep["messages_sent"] == 0 and rep["enrolled"] == 0
    created = p["db"].query(Opportunity).filter(Opportunity.import_batch_id == b["id"]).all()
    assert {o.source_record_id for o in created} == {"TEST-CLEAN-005", "TEST-CLEAN-010"}
    for o in created:
        assert o.stage == "prospect" and o.status == "open" and o.next_action is None and o.next_action_due_at is None
        assert o.brand_sales_org_id == p["brand"].id and o.owner_user_id == p["manager"].id
        assert o.industry == "Cleaning Services" and o.source_system == "facebook_ads"
        evs = p["db"].query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == o.id).all()
        drafts = [e for e in evs if e.summary == "Intro message DRAFT - not sent"]
        assert len(drafts) == 1 and "$250" in drafts[0].detail
        assert any("no texting permission" in (e.detail or "") for e in evs)
    p["db"].refresh(p["hand"])
    assert p["hand"].next_action == "Growth Pilot intro" and p["hand"].source_record_id is None
    assert p["db"].query(OpportunityEvent).filter(OpportunityEvent.opportunity_id == p["hand"].id).count() == hand_events
    assert _sent(p["db"]) == before_sent
    # Committing again changes nothing; a second upload of the same file creates nothing.
    assert _commit(p, p["manager"], b).json()["created"] == 2
    again = _analyzed(p, p["manager"])
    assert again["counts"]["ready"] == 0 and again["counts"]["matched_existing"] == 4   # 009 now matches 005 by phone
    assert _commit(p, p["manager"], again).json()["created"] == 0
    assert p["db"].query(Opportunity).filter(Opportunity.brand_sales_org_id == p["brand"].id).count() == 3


def test_attach_to_matched_adds_notes_once_and_fills_only_a_blank_source_id(sales):
    p = sales
    b = _analyzed(p, p["manager"])
    _commit(p, p["manager"], b)
    url = "/intake/sales/%s/batches/%s/attach-matched" % (p["brand"].id, b["id"])
    out = p["c"].post(url, headers=_h(p["db"], p["manager"])).json()
    assert out == {"notes_added": 1, "source_id_filled": 1, "already_attached": 0}
    assert p["c"].post(url, headers=_h(p["db"], p["manager"])).json()["already_attached"] == 1
    p["db"].refresh(p["hand"])
    assert p["hand"].source_record_id == "TEST-CLEAN-004" and p["hand"].company_name == "Sparkle Test Cleaning"
    assert p["hand"].next_action == "Growth Pilot intro" and p["hand"].source.startswith("Facebook ad")


def test_report_csv_is_spreadsheet_safe_and_has_every_row(sales):
    p = sales
    b = _analyzed(p, p["manager"])
    r = p["c"].get("/intake/sales/%s/batches/%s/report.csv" % (p["brand"].id, b["id"]), headers=_h(p["db"], p["manager"]))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert len(lines) == 8 and lines[0].startswith("row,outcome,next_step,company")
    assert "8135550104" not in r.text                                          # no contact details in the report


# ── backfill of the hand-entered prospects ──────────────────────────────────
def test_backfill_preview_then_apply_fills_only_blank_ids_and_skips_ambiguous(sales):
    p = sales
    db = p["db"]
    twin_a = Opportunity(brand_sales_org_id=p["brand"].id, company_name="Twin A", source="Facebook ad | TEST-CLEAN-099 | X",
                         stage="prospect", status="open")
    twin_b = Opportunity(brand_sales_org_id=p["brand"].id, company_name="Twin B", source="Facebook ad | TEST-CLEAN-099 | Y",
                         stage="prospect", status="open")
    plain = Opportunity(brand_sales_org_id=p["brand"].id, company_name="Plain", source="Referral from Ana",
                        stage="prospect", status="open")
    elsewhere = Opportunity(brand_sales_org_id=p["other"].id, company_name="Other brand",
                            source="Facebook ad | TEST-CLEAN-777 | Z", stage="prospect", status="open")
    db.add_all([twin_a, twin_b, plain, elsewhere])
    db.commit()
    base = "/intake/sales/%s/source-ids" % p["brand"].id
    prev = p["c"].get(base + "/preview", headers=_h(db, p["manager"])).json()
    assert prev["will_fill"] == 1 and prev["items"][0]["source_record_id"] == "TEST-CLEAN-004"
    assert prev["items"][0]["source_system"] == "facebook_ad"
    assert prev["conflicts"][0]["source_record_id"] == "TEST-CLEAN-099"
    assert db.query(Opportunity).filter(Opportunity.source_record_id.isnot(None)).count() == 0   # preview writes nothing
    out = p["c"].post(base + "/apply", json={}, headers=_h(db, p["manager"])).json()
    assert out["filled"] == 1
    db.refresh(p["hand"])
    assert p["hand"].source_record_id == "TEST-CLEAN-004" and p["hand"].next_action == "Growth Pilot intro"
    assert twin_a.source_record_id is None and elsewhere.source_record_id is None
    assert p["c"].post(base + "/apply", json={}, headers=_h(db, p["manager"])).json()["filled"] == 0
    assert p["c"].get(base + "/preview", headers=_h(db, p["rep"])).status_code == 403


def test_id_in_text_does_not_match_a_longer_id():
    assert BI.id_in_text("FB-CLEAN-004", "Facebook ad | FB-CLEAN-004 | Anytown")
    assert not BI.id_in_text("FB-CLEAN-004", "Facebook ad | FB-CLEAN-0041 | Anytown")
    assert not BI.id_in_text("CLEAN-004", "Facebook ad | FB-CLEAN-004 | Anytown")
    assert SI._id_from_source("Facebook ad | FB-CLEAN-004 | Anytown") == "FB-CLEAN-004"
    assert SI._id_from_source("Facebook ad | Anytown") is None


# ── destination isolation ───────────────────────────────────────────────────
def test_only_a_manager_of_that_brand_may_import_and_a_batch_never_crosses_destinations(sales):
    p = sales
    assert _start(p, p["rep"]).status_code == 403
    assert _start(p, p["other_mgr"]).status_code == 403
    b = _start(p, p["manager"]).json()
    for u in (p["rep"], p["other_mgr"]):
        assert p["c"].get("/intake/sales/%s/batches/%s" % (p["brand"].id, b["id"]), headers=_h(p["db"], u)).status_code == 403
    # The other brand's manager can't reach the batch through their own brand either.
    assert p["c"].get("/intake/sales/%s/batches/%s" % (p["other"].id, b["id"]),
                      headers=_h(p["db"], p["other_mgr"])).status_code == 404
    # Nor through the Acquisition routes.
    assert p["c"].get("/intake/acquisition/%s/batches/%s" % (p["brand"].id, b["id"]),
                      headers=_h(p["db"], p["god"])).status_code in (403, 404)


def test_destinations_list_only_what_the_person_may_write(sales):
    p = sales
    keys = lambda u: {d["key"]: [t["id"] for t in d["targets"]] for d in
                      p["c"].get("/intake/destinations", headers=_h(p["db"], u)).json()["items"]}
    assert keys(p["manager"]).get("evosys_sales") == [p["brand"].id]
    assert "evosys_sales" not in keys(p["rep"])
    assert keys(p["other_mgr"]).get("evosys_sales") == [p["other"].id]
    assert p["brand"].id in keys(p["god"])["evosys_sales"] and "platform_acquisition_pool" in keys(p["god"])
    assert "platform_acquisition_pool" not in keys(p["manager"])


def test_purpose_not_industry_a_business_elsewhere_is_never_blocked_and_only_the_owner_is_told(sales):
    p = sales
    pool = LeadIntelProspect(source="maps", name="Pool Office", phone_e164="+14805550105", stage="qualified")
    p["db"].add(pool)
    p["db"].commit()
    # A sales manager isn't told what other areas hold, and isn't blocked.
    b = _analyzed(p, p["manager"])
    row = _rows(p, p["manager"], b)["TEST-CLEAN-005"]
    assert row["outcome"] == "ready" and row["related"] == [] and b["counts"]["also_elsewhere"] == 0
    # The platform owner sees a note - still not blocked.
    b2 = _analyzed(p, p["god"])
    row2 = _rows(p, p["god"], b2)["TEST-CLEAN-005"]
    assert row2["outcome"] == "ready" and "master pool" in row2["related"][0]
    assert _commit(p, p["god"], b2).json()["created"] == 2
    # Nothing was copied or moved between areas.
    p["db"].refresh(pool)
    assert pool.stage == "qualified" and pool.routed_org_id is None
    assert p["db"].query(LeadIntelProspect).count() == 1


# ── retries ─────────────────────────────────────────────────────────────────
def test_a_failed_row_is_reported_and_a_retry_creates_only_what_is_missing(sales, monkeypatch):
    p = sales
    b = _analyzed(p, p["manager"])
    real = SI.ADAPTER.write
    calls = {"n": 0}

    def flaky(db, batch, user, n, row):
        calls["n"] += 1
        if n.get("source_record_id") == "TEST-CLEAN-010" and calls["n"] < 3:
            raise RuntimeError("database hiccup")
        return real(db, batch, user, n, row)

    monkeypatch.setattr(SI.ADAPTER, "write", flaky)
    first = _commit(p, p["manager"], b).json()
    assert first["created"] == 1 and first["failed"] == 1
    st = p["c"].get("/intake/sales/%s/batches/%s" % (p["brand"].id, b["id"]), headers=_h(p["db"], p["manager"])).json()
    assert st["stage"] == "partially_committed"
    second = _commit(p, p["manager"], b).json()
    assert second["created"] == 1 and second["already_created"] == 1 and second["failed"] == 0
    assert p["db"].query(Opportunity).filter(Opportunity.import_batch_id == b["id"]).count() == 2


def test_rollback_removes_untouched_imports_and_keeps_worked_ones(sales):
    p = sales
    b = _analyzed(p, p["manager"])
    _commit(p, p["manager"], b)
    worked = p["db"].query(Opportunity).filter(Opportunity.source_record_id == "TEST-CLEAN-005").first()
    worked.stage = "contacted"
    p["db"].commit()
    out = p["c"].post("/intake/sales/%s/batches/%s/rollback" % (p["brand"].id, b["id"]),
                      headers=_h(p["db"], p["manager"])).json()
    assert out["removed"] == 1 and out["kept_worked_on"] == 1
    assert p["db"].query(Opportunity).filter(Opportunity.source_record_id == "TEST-CLEAN-010").count() == 0
    assert p["db"].query(Opportunity).filter(Opportunity.id == p["hand"].id).count() == 1


# ── customer Acquisition ────────────────────────────────────────────────────
ACQ_CSV = _csv(
    _line("ACQ-1", "Oakview Test Dental", "2145550201", "TX", "US", website="https://oakview.example.test"),
    _line("ACQ-2", "Cedar Test Offices", "9725550202", loc="Pending"),
    _line("ACQ-3", "Pacific Test Clinic", "3105550203", "CA", "US"),
    _line("ACQ-4", "Sales Overlap Test", "8135550299", "FL", "US"),
)


def _acq(p, u, target=None):
    target = target or p["shine"].id
    r = p["c"].post("/intake/acquisition/%s/batches" % target, headers=_h(p["db"], u),
                    files={"file": ("acq.csv", io.BytesIO(ACQ_CSV.encode()), "text/csv")},
                    data={"source_system": "manual_research"})
    return r


def test_acquisition_import_goes_to_one_customer_only_and_sends_nothing(pilot):
    p = pilot
    db = p["db"]
    plat = Platform(name="EvoSys Pro", slug="evosys-acq-%d" % next(_SEQ))
    db.add(plat)
    db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Sales", slug="evo-acq-%d" % next(_SEQ))
    db.add(brand)
    db.commit()
    db.add(Opportunity(brand_sales_org_id=brand.id, company_name="A cleaning company", phone="813-555-0299",
                       stage="prospect", status="open"))
    db.commit()
    before = _sent(db)
    assert _acq(p, p["outsider"]).status_code == 403                 # not an operator for this customer
    assert _acq(p, p["owner"]).status_code == 403                    # the customer's own admin is not the operator
    r = _acq(p, p["god"])
    assert r.status_code == 200, r.text
    b = r.json()
    base = "/intake/acquisition/%s/batches/%s" % (p["shine"].id, b["id"])
    a = p["c"].post(base + "/analyze", headers=_h(db, p["god"])).json()["counts"]
    assert a["ready"] == 2 and a["held_for_review"] == 1 and a["excluded"] == 1 and a["invalid"] == 0
    rows = {r["business"]["source_record_id"]: r for r in p["c"].get(base + "/rows", headers=_h(db, p["god"])).json()["items"]}
    # Also an EvoSys Sales prospect: a note for the owner, not a refusal.
    assert rows["ACQ-4"]["outcome"] == "ready" and "EvoSys Sales" in rows["ACQ-4"]["related"][0]
    rep = p["c"].post(base + "/commit", json={"confirm_target_id": p["shine"].id}, headers=_h(db, p["god"])).json()
    assert rep["created"] == 2 and rep["messages_sent"] == 0
    assert db.query(Opportunity).filter(Opportunity.phone == "813-555-0299").count() == 1     # Sales record untouched
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == rep["record_ids"][0]).first()
    assert opp.organization_id == p["shine"].id and opp.stage == "discovered" and opp.is_practice is False
    assert opp.geo_status == "eligible"
    notes = [e.value for e in db.query(AcquisitionEvidence).filter(AcquisitionEvidence.opportunity_id == opp.id).all()]
    assert any(v.startswith("Intro message DRAFT - not sent") for v in notes)
    assert db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.organization_id == p["other"].id).count() == 0
    assert _sent(db) == before
    # The batch is invisible through another customer's address.
    assert p["c"].get("/intake/acquisition/%s/batches/%s" % (p["other"].id, b["id"]),
                      headers=_h(db, p["god"])).status_code in (403, 404)
    # Re-import: already in this customer's pipeline.
    b2 = _acq(p, p["god"]).json()
    a2 = p["c"].post("/intake/acquisition/%s/batches/%s/analyze" % (p["shine"].id, b2["id"]),
                     headers=_h(db, p["god"])).json()["counts"]
    assert a2["ready"] == 0 and a2["matched_existing"] == 2
    # Undo removes the untouched prospects.
    out = p["c"].post(base + "/rollback", headers=_h(db, p["god"])).json()
    assert out["removed"] == 2
    assert db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.id == opp.id).count() == 0


# ── the tenant importer names its destination ───────────────────────────────
def test_tenant_upload_must_name_the_customer_shown_on_screen(db_session, client):
    db = db_session
    org_a = Organization(name="Tenant A", slug="ta-%d" % next(_SEQ), plan="enterprise", is_active=True)
    org_b = Organization(name="Tenant B", slug="tb-%d" % next(_SEQ), plan="enterprise", is_active=True)
    db.add_all([org_a, org_b])
    db.commit()
    god = _user(db, None, "god_admin")
    f = lambda: {"file": ("c.csv", io.BytesIO(b"First,Last,Phone\nA,B,2145550301\n"), "text/csv")}
    hdr = _h(db, god, **{"X-Org-Override": org_a.id})
    r = client.post("/intake/batches", headers=hdr, files=f(), data={"source": "csv"})
    assert r.status_code == 409 and "destination" in r.json()["detail"]
    r = client.post("/intake/batches", headers=hdr, files=f(), data={"source": "csv", "expected_org_id": org_b.id})
    assert r.status_code == 409
    r = client.post("/intake/batches", headers=hdr, files=f(), data={"source": "csv", "expected_org_id": org_a.id})
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == org_a.id


# ── Stage C: results you can act on ─────────────────────────────────────────
def test_next_actions_and_downloadable_held_and_excluded_lists(sales):
    p = sales
    b = _analyzed(p, p["manager"])
    keys = {a["key"]: a for a in b["next_actions"]}
    assert keys["create"]["text"].startswith("2 ready to create in EvoSys Sales")
    assert keys["held"]["download"] == "held_for_review" and keys["excluded"]["download"] == "excluded"
    assert b["destination"]["target_name"] == "EvoSys Sales"
    url = "/intake/sales/%s/batches/%s/report.csv" % (p["brand"].id, b["id"])
    held = p["c"].get(url + "?outcome=held_for_review", headers=_h(p["db"], p["manager"]))
    lines = held.text.strip().splitlines()
    assert held.status_code == 200 and len(lines) == 2 and "TEST-CLEAN-006" in lines[1] and "Confirm where" in lines[1]
    assert 'held_for_review.csv' in held.headers["content-disposition"]
    exc = p["c"].get(url + "?outcome=excluded", headers=_h(p["db"], p["manager"])).text.strip().splitlines()
    assert len(exc) == 3
    assert p["c"].get(url + "?outcome=nonsense", headers=_h(p["db"], p["manager"])).status_code == 422
    rows = _rows(p, p["manager"], b)
    assert rows["TEST-CLEAN-004"]["next_step"].startswith("Already in this destination")
    done = _commit(p, p["manager"], b).json()
    after = p["c"].get("/intake/sales/%s/batches/%s" % (p["brand"].id, b["id"]), headers=_h(p["db"], p["manager"])).json()
    assert {a["key"] for a in after["next_actions"]} >= {"done", "held", "excluded"} and "Messages sent: 0" in after["next_actions"][0]["text"]
    assert done["created"] == 2


def test_guided_import_report_lists_rows_needing_attention_safely(db_session, client):
    db = db_session
    org = Organization(name="Tenant R", slug="tr-%d" % next(_SEQ), plan="enterprise", is_active=True)
    other = Organization(name="Tenant S", slug="ts-%d" % next(_SEQ), plan="enterprise", is_active=True)
    db.add_all([org, other])
    db.commit()
    god = _user(db, None, "god_admin")
    h = _h(db, god, **{"X-Org-Override": org.id})
    csv_body = b"First,Last,Company,Phone,Email\nAna,Row,=SUM(A1),2145550401,ana@example.test\nBo,Row,Plain Co,,\n"
    r = client.post("/intake/batches", headers=h, data={"source": "csv", "expected_org_id": org.id},
                    files={"file": ("c.csv", io.BytesIO(csv_body), "text/csv")})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert client.post("/intake/batches/%s/analyze" % bid, headers=h).status_code in (200, 202)
    rep = client.get("/intake/batches/%s/report.csv" % bid, headers=h)
    assert rep.status_code == 200 and rep.text.splitlines()[0].startswith("row,status,next_step")
    assert "'=SUM(A1)" in rep.text and "2145550401" not in rep.text and "ana@example.test" not in rep.text
    prob = client.get("/intake/batches/%s/report.csv?status=problems" % bid, headers=h)
    assert prob.status_code == 200
    assert client.get("/intake/batches/%s/report.csv?status=bogus" % bid, headers=h).status_code == 422
    # Another customer can't read it.
    h2 = _h(db, god, **{"X-Org-Override": other.id})
    assert client.get("/intake/batches/%s/report.csv" % bid, headers=h2).status_code == 404
