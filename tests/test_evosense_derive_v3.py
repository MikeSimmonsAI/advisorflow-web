"""EvoSense property truth — Priority 2: derive/v3 and property_opportunity/v3.

Each test pins one correction the pilot audit found, on the smallest input
that reproduces it:

    * normalized address comparison (2427 Lillian: "2427 LILLIAN" is the
      property, not an absentee mailing address)
    * owner / entity classification (church, ISD, life estate, estate, ET AL)
    * truncated tax-roll names are flagged and never enriched
    * closed code cases are HISTORY, shown and never scored
    * CDU POOR / VERY POOR = 4 points, UNDESIRABLE = 0
    * a non-standard delinquency date is TAX TERMS UNVERIFIED, not delinquent
    * a deed transfer inside a year subtracts 10; it is not a "sale"
    * a calendar date is serialised as a date (the May 3 / May 4 bug)
    * unknown is unknown: no evidence is never a penalty or a signal
    * independent record sources earn a bonus; one source counted twice does not
    * institutional owners are excluded by default, and a tenant's override
      changes that tenant's score version only
"""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.evosense import scoring as SC
from app.services.evosense import signals as SIG
from app.services.evosense import views as V
from app.services.evosense.identity import same_address
from app.services.evosense.ingest import owner_flags, owner_type_of
from app.services.evosense.sources import tarrant as TT

NOW = datetime(2026, 9, 26, 12, 0, 0)


# ── addresses ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,za,zb,expect", [
    ("2427 LILLIAN", "2427 Lillian St", None, "76111", True),       # suffix omitted on one side
    ("2427 LILLIAN ST", "2427 LILLIAN STREET", None, None, True),
    ("2427 LILLIAN AVE", "2427 LILLIAN ST", None, None, False),     # both sides state a suffix
    ("2429 LILLIAN ST", "2427 LILLIAN ST", None, None, False),      # different house number
    ("2427 LILLIAN ST", "2427 LILLIAN ST", "76111", "75201", False),  # ZIPs disagree
    ("PO BOX 44", "500 MAIN ST", None, None, None),                 # nothing to compare
    (None, "500 MAIN ST", None, None, None),
])
def test_same_address_is_suffix_tolerant_and_unknown_is_not_different(a, b, za, zb, expect):
    assert same_address(a, b, za, zb) is expect


def _prop(**kw):
    base = dict(street_address="2427 LILLIAN ST", city="Fort Worth", state="TX", zip_code="76111",
                occupancy=None, equity_pct=None, equity_basis=None, estimated_value_source=None,
                mortgage_balance=None, mortgage_source=None, last_deed_transfer_date=None,
                ownership_years=None, last_sale_date=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _owner(**kw):
    base = dict(mailing_street="2427 LILLIAN", mailing_city="FORT WORTH", mailing_state="TX",
                mailing_zip=None, owner_type="individual")
    base.update(kw)
    return SimpleNamespace(**base)


def test_lillian_is_not_absentee():
    spec = SIG.derive_specs(_prop(), _owner(), when=NOW)
    assert "ABSENTEE_OWNER" not in spec["make"]


def test_absentee_needs_a_proven_difference_and_po_box_or_homestead_is_unknown():
    assert "ABSENTEE_OWNER" in SIG.derive_specs(_prop(), _owner(mailing_street="9 ELM CT"), when=NOW)["make"]
    po = SIG.derive_specs(_prop(), _owner(mailing_street="PO BOX 12"), when=NOW)
    assert "ABSENTEE_OWNER" not in po["make"] and "PO box" in po["unknown"]["ABSENTEE_OWNER"]
    hs = SIG.derive_specs(_prop(occupancy="owner_occupied"), _owner(mailing_street="9 ELM CT"), when=NOW)
    assert "ABSENTEE_OWNER" not in hs["make"] and "homestead" in hs["unknown"]["ABSENTEE_OWNER"]
    none = SIG.derive_specs(_prop(), None, when=NOW)
    assert not none["make"] and "ABSENTEE_OWNER" in none["unknown"]


def test_out_of_state_requires_a_real_us_state():
    assert "OUT_OF_STATE_OWNER" in SIG.derive_specs(
        _prop(), _owner(mailing_street="1 A ST", mailing_state="OK"), when=NOW)["make"]
    bad = SIG.derive_specs(_prop(), _owner(mailing_street="1 A ST", mailing_state="TEXAS"), when=NOW)
    assert "OUT_OF_STATE_OWNER" not in bad["make"] and "OUT_OF_STATE_OWNER" in bad["unknown"]
    assert "OUT_OF_STATE_OWNER" not in SIG.derive_specs(
        _prop(), _owner(mailing_street="1 A ST", mailing_state="TX"), when=NOW)["make"]


# ── deed transfers ──────────────────────────────────────────────────────────

def test_recent_deed_transfer_subtracts_and_long_ownership_reads_the_deed_date():
    recent = SIG.derive_specs(_prop(last_deed_transfer_date=(NOW - timedelta(days=90)).date()), None, when=NOW)
    assert "RECENT_DEED_TRANSFER" in recent["make"] and "LONG_OWNERSHIP" not in recent["make"]
    assert "price not public" in recent["make"]["RECENT_DEED_TRANSFER"]["value"]
    assert SIG.CATALOG["RECENT_DEED_TRANSFER"]["points"] == -10
    old = SIG.derive_specs(_prop(last_deed_transfer_date=date(2009, 5, 4)), None, when=NOW)
    assert "LONG_OWNERSHIP" in old["make"] and "RECENT_DEED_TRANSFER" not in old["make"]
    assert old["make"]["LONG_OWNERSHIP"]["effective_at"] == datetime(2009, 5, 4)


def test_a_calendar_date_is_serialised_without_a_time_zone():
    assert V._iso(date(2009, 5, 4)) == "2009-05-04"          # never "2009-05-04T00:00:00Z" -> May 3 CT
    assert V._iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"


# ── owners ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,otype,flag", [
    ("GREATER NEW HOPE BAPTIST CHURCH", "religious_org", "INSTITUTIONAL_OWNER"),
    ("FORT WORTH ISD", "government", "INSTITUTIONAL_OWNER"),
    ("CITY OF DALLAS", "government", "INSTITUTIONAL_OWNER"),
    ("GARCIA MARIA LIFE ESTATE", "life_estate", "LIFE_ESTATE"),
    ("JONES ROBERT EST", "estate_indicated", "ESTATE_INDICATED"),
    ("SMITH JOHN ETAL", "multiple_owners", "MULTIPLE_OWNERS_ETAL"),
])
def test_owner_classification(name, otype, flag):
    assert owner_type_of(name) == otype
    assert flag in owner_flags(name)


def test_a_plain_person_carries_no_flags():
    assert owner_type_of("ALVAREZ JUANITA") == "individual"
    assert owner_flags("ALVAREZ JUANITA") == []


def test_truncated_tax_roll_name_is_detected():
    from test_evosense_dfw_sources import master_line
    full = master_line(account="1", street_no="1", street_name="A ST", owner1="ANDRADE ANGEL AND MURILLO GLOR")
    short = master_line(account="2", street_no="1", street_name="A ST", owner1="ALVAREZ JUANITA")
    assert TT.name_truncated(full) is True and TT.name_truncated(short) is False
    assert "NAME_TRUNCATED" in owner_flags("ANDRADE ANGEL AND MURILLO GLOR", truncated=True)


def test_delinquency_date_must_be_the_statutory_one():
    assert TT.standard_delinquency(datetime(2026, 2, 1))
    assert not TT.standard_delinquency(datetime(2026, 8, 1))   # 2224 Skyline
    assert not TT.standard_delinquency(None)


# ── scoring ─────────────────────────────────────────────────────────────────

def _sig(stype, source="src", ref=None, days=10, **kw):
    base = dict(id="%s-%s" % (stype, source), signal_type=stype, source=source, connector_kind="public_record",
                source_reference=ref, observation_id=None, observed_at=NOW - timedelta(days=days),
                effective_at=NOW - timedelta(days=days), stale_at=None, confidence=90, strength=None,
                raw_value=None, normalized_value="x", provenance=None, cost_cents=0, active=True,
                evidence_basis=None, case_status=None, rule_version=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _sprop(**kw):
    base = dict(equity_pct=None, equity_basis=None, estimated_value=None, estimated_value_source=None,
                estimated_value_at=None, appraisal_value=None, appraisal_year=None, appraisal_land_value=None,
                appraisal_improvement_value=None, appraisal_source=None, appraisal_at=None,
                ownership_years=None, property_type=None, street_address="1 A ST", state="TX", zip_code=None,
                county="Tarrant", city="Fort Worth", mortgage_balance=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _score(sigs, prop=None, owner=None, options=None, weights=None):
    return SC.property_opportunity(prop or _sprop(), SIG.stack(sigs, NOW), None, weights=weights,
                                   owner=owner, options=options)


def test_cdu_points_and_history_is_shown_not_scored():
    assert SIG.CATALOG["CDU_POOR"]["points"] == 4 and SIG.CATALOG["CDU_UNDESIRABLE"]["points"] == 0
    r = _score([_sig("CDU_POOR"), _sig("CODE_HISTORY", source="fw"), _sig("TAX_TERMS_UNVERIFIED", source="t")])
    by = {f.get("detail", {}).get("signal") if isinstance(f.get("detail"), dict) else None: f for f in r["factors"]}
    labels = " | ".join(f["label"] for f in r["factors"])
    assert "shown, not scored" in labels
    history_pts = [f["points"] for f in r["factors"] if "not scored" in f["label"]]
    assert history_pts and all(p == 0 for p in history_pts)
    # only CDU_POOR's 4 points (plus nothing for history / unverified terms)
    assert r["value"] == 4, (r["value"], labels, by)


def test_unknown_is_never_a_penalty():
    strategy = SimpleNamespace(id="s", version=1, min_value=50000, max_value=300000, min_equity_pct=None,
                               owner_geography="absentee", min_ownership_years=10, property_types=None,
                               excluded_signals=None, preferred_signals=None, states=None, counties=None,
                               cities=None, zip_codes=None)
    from app.services.evosense import strategy as ST
    orig = ST.geography_match
    ST.geography_match = lambda s, p: (True, "")
    try:
        r = SC.property_opportunity(_sprop(), SIG.stack([_sig("TAX_DELINQUENT")], NOW), strategy,
                                    owner=_owner(mailing_street=None))
    finally:
        ST.geography_match = orig
    negatives = [f for f in r["factors"] if f["points"] < 0]
    assert not negatives, negatives
    labels = " | ".join(f["label"] for f in r["factors"])
    assert "not scored" in labels


def test_independent_sources_earn_a_bonus_and_one_source_does_not():
    one = _score([_sig("TAX_DELINQUENT", source="tarrant_tax_roll"), _sig("TAX_SUIT", source="tarrant_tax_roll")])
    three = _score([_sig("TAX_DELINQUENT", source="tarrant_tax_roll"), _sig("CODE_VIOLATION", source="fw_code_violations"),
                    _sig("CDU_POOR", source="dcad")])
    base3 = sum(SIG.CATALOG[t]["points"] for t in ("TAX_DELINQUENT", "CODE_VIOLATION", "CDU_POOR"))
    assert three["value"] >= base3 + 7 or three["value"] == 100
    assert not any("independent" in f["label"].lower() for f in one["factors"])


def test_institutional_owner_is_excluded_by_default_and_the_override_is_a_new_version():
    church = _owner(owner_type="religious_org")
    r = _score([_sig("TAX_DELINQUENT")], owner=church)
    assert r["value"] == 0 and r["label"] == "excluded"
    assert r["version"] == SC.PO_VERSION
    opened = _score([_sig("TAX_DELINQUENT")], owner=church, options={"exclude_institutional": False})
    assert opened["value"] > 0
    assert opened["version"] != SC.PO_VERSION and opened["version"].startswith(SC.PO_VERSION + "+o")
    assert SC.version_for(None, None) == SC.PO_VERSION
    assert SC.version_for(None, {"exclude_institutional": True}) == SC.PO_VERSION   # the default is no override
    assert SC.clean_options({"exclude_institutional": 0, "junk": 1}) == {"exclude_institutional": False}


def test_appraisal_value_is_labelled_in_the_value_range_and_never_a_market_value():
    strategy = SimpleNamespace(id="s", version=1, min_value=100000, max_value=300000, min_equity_pct=None,
                               owner_geography="any", min_ownership_years=None, property_types=None,
                               excluded_signals=None, preferred_signals=None)
    from app.services.evosense import strategy as ST
    orig = ST.geography_match
    ST.geography_match = lambda s, p: (True, "")
    try:
        r = SC.property_opportunity(_sprop(appraisal_value=235070, appraisal_year=2026),
                                    SIG.stack([_sig("TAX_DELINQUENT")], NOW), strategy)
    finally:
        ST.geography_match = orig
    assert any("Appraisal district tax value $235,070" in f["label"] for f in r["factors"])
    assert r["inputs"]["market_value"] is None and r["inputs"]["appraisal_tax_value"] == 235070


# ── the cost governor refuses what a truncated / institutional name would waste ─

def test_enrichment_refuses_institutional_and_truncated_owners(db_session, sample_org):
    from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
    from app.services.evosense import common as C
    from app.services.evosense import enrichment as EN
    db = db_session
    out = {}
    for name, otype, trunc, flags in (("NEW HOPE BAPTIST CHURCH", "religious_org", False, ["INSTITUTIONAL_OWNER"]),
                                      ("ANDRADE ANGEL AND MURILLO GLOR", "joint", True, ["NAME_TRUNCATED"]),
                                      ("JONES ROBERT EST", "estate_indicated", False, ["ESTATE_INDICATED"])):
        p = EvoSenseProperty(organization_id=sample_org.id, street_address="%s ST" % len(out), state="TX",
                             county="Tarrant", status=C.S_NEW)
        o = EvoSenseOwner(organization_id=sample_org.id, display_name=name,
                          owner_type=otype, name_truncated=trunc, review_flags=C.jdump(flags))
        db.add_all([p, o])
        db.flush()
        db.add(EvoSenseOwnership(organization_id=sample_org.id, property_id=p.id, owner_id=o.id,
                                 is_current=True, source="tarrant_tax_roll"))
        db.flush()
        out[otype] = EN.decide(db, p, None)
    assert out["religious_org"].decision == C.D_INSUFFICIENT and "institution" in out["religious_org"].reasons
    assert out["joint"].decision == C.D_INSUFFICIENT and "truncated" in out["joint"].reasons
    assert out["estate_indicated"].decision == C.D_INSUFFICIENT and "estate" in out["estate_indicated"].reasons


def test_long_ownership_freshness_follows_confirmation_not_the_deed_date():
    """A 36-year ownership confirmed by this year's tax roll is CURRENT
    evidence. Measured from the 1990 deed it read as stale and scored 0."""
    sig = _sig("LONG_OWNERSHIP", source=SIG.DERIVED_SOURCE, days=5)
    sig.effective_at = datetime(1990, 6, 1)
    assert SIG.freshness(sig, NOW) == SIG.CURRENT
    r = _score([sig], prop=_sprop(ownership_years=36))
    assert r["value"] == SIG.CATALOG["LONG_OWNERSHIP"]["points"]
    sig.observed_at = NOW - timedelta(days=800)          # nobody has re-confirmed it in two years
    assert SIG.freshness(sig, NOW) == SIG.STALE
    # an EVENT is still dated by the event
    ev = _sig("CODE_VIOLATION", days=5)
    ev.effective_at = NOW - timedelta(days=400)
    assert SIG.freshness(ev, NOW) == SIG.STALE
