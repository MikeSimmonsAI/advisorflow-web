"""County clerk foreclosure notices (Dallas, Tarrant, Denton) - every category,
matched to the properties a list brought in, without duplicates.

The fixtures copy the clerks' real record shapes (read live 2026-10-23):
Dallas indexes the address and the sale DAY; Tarrant the address and the sale
MONTH; Denton no address at all (only the notice's text). No network is used:
the portal's one network call (`fetch`) is replaced, everything above it -
paging, the full-text fallback, matching, classification, ingest, status and
priority - is the production code.
"""
from datetime import date, timedelta

import pytest

from app.models.evosense_models import EvoSenseEvent, EvoSenseObservation, EvoSenseProperty, EvoSenseSignal
from app.models.models import Organization
from app.services.evosense import cohort as CO
from app.services.evosense import common as C
from app.services.evosense import hunt as HU
from app.services.evosense.sources import base as B
from app.services.evosense.sources import clerk_fc as F

TODAY = date.today()


def first_tuesday_after(d):
    m = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return F.first_tuesday(m.year, m.month)


SALE = first_tuesday_after(TODAY)                 # next month's sale day
LATER = first_tuesday_after(SALE)                 # the month after
PASSED = TODAY - timedelta(days=17)


def us(d):
    return d.strftime("%m/%d/%Y")


def dallas_doc(i, street, city, zc, sale, *, text="NOTICE OF FORECLOSURE SALE AND APPOINTMENT OF SUBSTITUTE TRUSTEE",
               doc_type="NOTICE OF FORECLOSURE", address=True):
    return {"docId": 330000000 + i, "docNumber": "2026000%05d" % i, "docType": doc_type, "docTypeCode": "NOF",
            "recordedDate": us(TODAY - timedelta(days=30)), "instrumentDate": us(sale),
            "propAddress": [{"zip": zc, "city": city, "address1": street, "state": "TEXAS"}] if address
            else [{"city": city}],
            "ocrText": "%s 2026 - 2026000%05d Page 1 of 3 %s, %s, TX %s %s Property: The Property to be sold"
                       % (us(TODAY), i, street, city, zc, text)}


def tarrant_doc(i, street, city, zc, sale, text="Notice of Substitute Trustee Sale PPP# 26-010130"):
    return {"docId": 101000000 + i, "docNumber": "P2260%05d" % i, "docType": "FORECLOSURE",
            "recordedDate": us(TODAY - timedelta(days=20)), "saleDate": sale.strftime("%b %Y"),
            "propAddress": [{"zip": zc, "city": city, "address1": street, "state": "TX"}], "ocrText": text}


def denton_doc(i, text, sale):
    return {"docId": 900000 + i, "docNumber": "2026-11/%03d" % i, "docType": "NOTICE FORECLOSURE",
            "recordedDate": us(TODAY - timedelta(days=25)), "instrumentDate": us(sale), "ocrText": text}


class FakePortal(F.ClerkPortal):
    """The real portal minus the network: `fetch` answers from fixed records
    the way the clerk's server does (date window, paging, full-text search)."""
    DOCS = {}
    FAIL = {}

    def __init__(self, county):
        super().__init__(county)
        self.queries = []

    def fetch(self, query, *, timeout=30.0):
        self.calls += 1
        self.queries.append(dict(query))
        if self.county in self.FAIL:
            raise self.FAIL[self.county]
        docs = list(self.DOCS.get(self.county, []))
        if "searchValue" in query:
            v = query["searchValue"].upper()
            hits = []
            for d in docs:
                t = (d.get("_full") or d.get("ocrText") or "").upper()   # the search reads the WHOLE notice
                if all(w in t for w in v.split()):
                    hits.append(dict(d, highlights={"ocrText": [t]}))
            return hits[:int(query.get("limit") or 250)]
        rng = query.get(self.cfg["window"])
        if rng:
            a, b = rng.split(",")
            lo = date(int(a[:4]), int(a[4:6]), int(a[6:]))
            hi = date(int(b[:4]), int(b[4:6]), int(b[6:]))
            key = "recordedDate" if self.cfg["window_on"] == "recorded" else "instrumentDate"
            docs = [d for d in docs if d.get(key) and lo <= F.recorded_date({"recordedDate": d[key]}) <= hi]
        off = int(query.get("offset") or 0)
        return docs[off:off + F.PAGE]


@pytest.fixture
def clerks(monkeypatch):
    FakePortal.DOCS = {
        "dallas": [
            dallas_doc(1, "1345 DELL OAK DRIVE", "DALLAS", "75217", SALE),          # list says ZIP 75180
            dallas_doc(2, "2728 RUNNING BROOK LN", "DALLAS", "75228", SALE),
            dallas_doc(3, "2728 RUNNING BROOK LN", "DALLAS", "75228", LATER),        # a re-posting
            dallas_doc(4, "", "DALLAS", "", SALE, address=False,                     # clerk indexed only the city
                       text="Commonly known as: 4405 FALLS DR, DALLAS, TX 75211 NOTICE OF TRUSTEE SALE"),
            dallas_doc(5, "88 VOID ST", "DALLAS", "75201", SALE, doc_type="VOID FC"),
            dallas_doc(6, "7 HOA LN", "DALLAS", "75201", SALE,
                       text="NOTICE OF SALE - OAK HOLLOW HOMEOWNERS ASSOCIATION, INC. assessment lien under the "
                            "Declaration; Substitute Trustee"),
            dallas_doc(7, "1346 DELL OAK DRIVE", "DALLAS", "75217", SALE),           # the neighbour - never ours
            dict(dallas_doc(8, "", "DALLAS", "", SALE, address=False, text=""),      # index: city only, short text
                 ocrText="NOTICE OF SUBSTITUTE TRUSTEE SALE Assert and protect your rights",
                 _full="NOTICE OF SUBSTITUTE TRUSTEE SALE Property Address: 3069 KIESTRIDGE DR, DALLAS, TX 75233"),
        ],
        "tarrant": [
            tarrant_doc(1, "4317 KINGS CIR S", "FORT WORTH", "76111", SALE),
            tarrant_doc(2, "405 COLONIAL PL", "EVERMAN", "76140", LATER,
                        "NOTICE OF ACCELERATION AND NOTICE OF TRUSTEE'S SALE"),     # list claims SALE: conflict
        ],
        "denton": [
            denton_doc(1, "| Os a FILED FOR RECORD 1801 Forsythe Dr %s Savannah, TX 76227 COUNTY CLERK DENTON CO. "
                          "NOTICE OF [SUBSTITUTE] TRUSTEE'S SALE" % TODAY.strftime("%b %d %Y"), SALE),
        ],
    }
    FakePortal.FAIL = {}
    monkeypatch.setattr(F.ClerkForeclosureReader, "__init__",
                        lambda self, portal_factory=FakePortal: (setattr(self, "_factory", FakePortal),
                                                                 setattr(self, "_portals", {}))[-1])
    return FakePortal


LIST = ("street_address,city,state,zip_code,county,record_id\n"
        "1345 DELL OAK DR,Dallas,TX,75180,Dallas,t100:1\n"
        "2728 RUNNING BROOK LN,Dallas,TX,75228,Dallas,t100:2\n"
        "4405 FALLS DR,Dallas,TX,75211,Dallas,t100:3\n"
        "5655 CARY AVE,Dallas,TX,75227,Dallas,t100:4\n"
        "4317 KINGS CIR S,Fort Worth,TX,76111,Tarrant,t100:5\n"
        "405 COLONIAL PL,Everman,TX,76140,Tarrant,t100:6\n"
        "1801 FORSYTHE DR,Savannah,TX,76227,Denton,t100:7\n"
        "5329 SPANISH OAKS,Frisco,TX,75034,Collin,t100:8\n"
        "3069 KIESTRIDGE DR,Dallas,TX,75233,Dallas,t100:9\n")


def _cohort(db, org, user, declared=SALE):
    HU.import_csv(db, org.id, LIST, user=user, filename="Boo Boo Shushu.csv", declared_sale_date=declared.isoformat())
    db.commit()
    props = {p.street_address.upper(): p for p in db.query(EvoSenseProperty).filter_by(organization_id=org.id)}
    for p in props.values():                       # the import has no county column mapping in every layout
        if not p.county:
            p.county = {"FORT WORTH": "Tarrant", "EVERMAN": "Tarrant", "SAVANNAH": "Denton",
                        "FRISCO": "Collin"}.get((p.city or "").upper(), "Dallas")
    db.commit()
    return props


def _rows(db, org):
    return {r["street_address"].upper(): r for r in CO.rows(db, org.id, "t100")}


# 1 ── every foreclosure category, read from the notice itself ─────────────────

@pytest.mark.parametrize("doc_type,text,cat,status", [
    ("NOTICE OF FORECLOSURE", "NOTICE OF FORECLOSURE SALE AND APPOINTMENT OF SUBSTITUTE TRUSTEE", F.SUBSTITUTE_TRUSTEE, F.SCHEDULED),
    ("FORECLOSURE", "Notice of [Substitute] Trustee Sale Assert and protect your rights", F.SUBSTITUTE_TRUSTEE, F.SCHEDULED),
    ("FORECLOSURE", "NOTICE OF ACCELERATION AND NOTICE OF TRUSTEE'S SALE", F.TRUSTEE, F.SCHEDULED),
    ("NOTICE FORECLOSURE", "OAK HOLLOW HOMEOWNERS ASSOCIATION assessment lien - Declaration - Trustee", F.HOA, F.SCHEDULED),
    ("FORECLOSURE", "CONSTABLE'S SALE under ORDER OF SALE, cause DC-24-1101 (judgment of foreclosure)", F.JUDICIAL, F.SCHEDULED),
    ("FORECLOSURE", "TAX SALE - Constable Precinct 1 - delinquent property taxes, order of sale", F.TAX, F.SCHEDULED),
    ("VOID FC", "NOTICE OF SUBSTITUTE TRUSTEE SALE", F.SUBSTITUTE_TRUSTEE, F.VOID),
    ("RESCISSION OF NOTICE", "NOTICE OF TRUSTEE SALE", F.TRUSTEE, F.CANCELED),
    ("FORECLOSURE", "Grantor(s)/Mortgagor(s): ...", F.NOTICE, F.SCHEDULED),
])
def test_every_foreclosure_category_is_classified_from_the_notice(doc_type, text, cat, status):
    assert F.classify(doc_type, text) == (cat, status)


# 2 ── dates: the clerk's day, the clerk's month, holidays ────────────────────

def test_sale_dates_from_each_clerk_format():
    assert F.sale_date({"instrumentDate": "11/03/2026"})[0] == date(2026, 11, 3)
    d, basis = F.sale_date({"saleDate": "Nov 2026"})
    assert d == date(2026, 11, 3) and "first Tuesday" in basis
    assert F.first_tuesday(2030, 1) == date(2030, 1, 2)          # Jan 1 is a Tuesday -> Wednesday
    assert F.sale_date({"saleDate": "Dec 2026"})[0] == date(2026, 12, 1)
    assert F.sale_date({"saleDate": "soon"}) == (None, None)       # never a guess
    assert F.sale_date({}) == (None, None)


# 3 ── addresses from the notice text; the neighbour is never a match ─────────

def test_addresses_and_matching_rules():
    assert F.ocr_address("26-01888 2905 MARE DRIVE BLDG 28, MESQUITE, TX 75150 NOTICE")["address1"] == "2905 MARE DRIVE"
    assert F.ocr_address("Property Address: 1833 FORSYTHE DR SAVANNAH, TEXAS 76227 . Trustor(s)")["zip"] == "76227"
    assert F.ocr_address("Grantor(s)/Mortgagor(s): LATRICE SALVADOR AND TENIOLA") is None
    doc = {"propAddress": [{"zip": "75217", "city": "DALLAS", "address1": "1345 DELL OAK DRIVE"}]}
    assert F.same_place(doc, "1345 DELL OAK DR", "75180", "Dallas")            # ZIP differs, same city
    assert not F.same_place(doc, "1345 DELL OAK DR", "75180", "Balch Springs")  # neither agrees
    assert not F.same_place(doc, "1346 DELL OAK DR", "75217", "Dallas")         # the neighbour
    assert F.street_key("899 S FM 1138") == F.street_key("899 FM 1138 RD") == "899 FM 1138"


# 4 ── the Top 100 proof: verified / claimed / conflict / not found ───────────

def test_recheck_splits_verified_claimed_conflict_and_not_found(db_session, sample_org, sample_advisor, clerks):
    props = _cohort(db_session, sample_org, sample_advisor)
    n_before = db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).count()
    res = CO.fc_check(db_session, sample_org.id, list(props.values()), user=sample_advisor)
    assert db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).count() == n_before  # no new houses
    assert res["dallas"]["status"] == "COMPLETED" and res["dallas"]["matched_index"] == 3   # 4405 FALLS: address read from the notice text
    assert res["dallas"]["matched_full_text"] == 1                         # 3069 KIESTRIDGE: only the clerk's full-text search finds it
    assert res["collin"]["status"] == "NOT CONNECTED" and "Record a notice" in res["collin"]["reason"]
    rows = _rows(db_session, sample_org)
    dell = rows["1345 DELL OAK DR"]["foreclosure"]
    assert dell["status"] == "VERIFIED NOTICE" and dell["category"] == "A" and dell["verified"] is True
    assert dell["document"] == "202600000001" and dell["document_url"].endswith("/doc/330000001")
    assert dell["sale_date"] == SALE.isoformat() and dell["foreclosure_type"].startswith("Substitute trustee")
    rb = rows["2728 RUNNING BROOK LN"]["foreclosure"]
    assert rb["status"] == "VERIFIED NOTICE" and "re-posting" in rb["detail"] and len(rb["notices"]) == 2
    assert rows["4405 FALLS DR"]["foreclosure"]["status"] == "VERIFIED NOTICE"
    kies = rows["3069 KIESTRIDGE DR"]["foreclosure"]
    assert kies["status"] == "VERIFIED NOTICE" and kies["confidence"] == 85      # matched on the notice text
    assert rows["4317 KINGS CIR S"]["foreclosure"]["status"] == "VERIFIED NOTICE"   # Tarrant month -> first Tuesday
    assert rows["1801 FORSYTHE DR"]["foreclosure"]["status"] == "VERIFIED NOTICE"   # Denton: notice text
    col = rows["405 COLONIAL PL"]["foreclosure"]
    assert col["status"] == "CONFLICT" and col["category"] == "C" and LATER.isoformat() in col["detail"]
    cary = rows["5655 CARY AVE"]["foreclosure"]
    assert cary["status"] == "NO NOTICE FOUND" and cary["category"] == "E" and "Not proof" in cary["detail"]
    frisco = rows["5329 SPANISH OAKS"]["foreclosure"]
    assert frisco["status"] == "CLAIMED" and frisco["category"] == "B" and not frisco["clerk_checked"]
    assert any("not read automatically" in m for m in rows["5329 SPANISH OAKS"]["missing"])
    s = CO.summary(list(rows.values()))
    assert s["groups"]["A"]["count"] == 6 and s["groups"]["B"]["count"] == 1
    assert s["groups"]["C"]["count"] == 1 and s["groups"]["E"]["count"] == 1
    assert s["sale_dates"]["verified"] == 7


# 5 ── re-running changes nothing: no duplicate evidence ──────────────────────

def test_rechecking_twice_adds_no_duplicates(db_session, sample_org, sample_advisor, clerks):
    props = _cohort(db_session, sample_org, sample_advisor)
    CO.fc_check(db_session, sample_org.id, list(props.values()), user=sample_advisor)
    def counts():
        obs = db_session.query(EvoSenseObservation).filter_by(organization_id=sample_org.id,
                                                              provider_key=CO.CLERK_SOURCE).count()
        sig = db_session.query(EvoSenseSignal).filter_by(organization_id=sample_org.id, source=CO.CLERK_SOURCE,
                                                         active=True).count()
        return obs, sig, db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).count()
    first = counts()
    CO.fc_check(db_session, sample_org.id, list(props.values()), user=sample_advisor)
    assert counts() == first and first[0] == 8            # 8 notices on 7 houses; the void one never lands


# 6 ── a verified sale is "act now" even with taxes current ───────────────────

def test_tax_current_mortgage_foreclosure_stays_act_now(db_session, sample_org, sample_advisor, clerks):
    props = _cohort(db_session, sample_org, sample_advisor)
    CO.fc_check(db_session, sample_org.id, list(props.values()), user=sample_advisor)
    rows = _rows(db_session, sample_org)
    fc = rows["1345 DELL OAK DR"]["foreclosure"]
    owner = {"status": "VERIFIED", "name": "OWNER", "flags": []}
    if fc["days"] <= CO.IMMINENT_DAYS:
        assert CO._priority(10, owner, {"status": "CURRENT"}, fc, []) == "A"
    else:
        assert CO._priority(10, owner, {"status": "CURRENT"}, fc, []) == "B"
    claimed = rows["5329 SPANISH OAKS"]["foreclosure"]
    assert CO._priority(10, owner, {"status": "CURRENT"}, claimed, []) == "B"       # a claim alone: never A
    assert "Sale confirmed on clerk notice" in CO._next_action("A", owner, {"status": "CURRENT"},
                                                                {"phones": 0, "emails": 0}, dict(fc, days=5))


# 7 ── a failing source is FAILED, never "checked - nothing found" ─────────────

def test_a_failed_county_is_reported_and_its_houses_are_not_marked_checked(db_session, sample_org, sample_advisor,
                                                                           clerks):
    props = _cohort(db_session, sample_org, sample_advisor)
    clerks.FAIL = {"dallas": B.SourceError(B.AUTH_FAILED, "dallas.tx.publicsearch.us refused the search connection (403)")}
    res = CO.fc_check(db_session, sample_org.id, list(props.values()), user=sample_advisor)
    assert res["dallas"]["status"] == "FAILED" and "403" in res["dallas"]["error"]
    assert res["tarrant"]["status"] == "COMPLETED"                     # one county never stops the others
    assert CO.recheck_outcome({}, res) == "PARTIAL"
    rows = _rows(db_session, sample_org)
    assert rows["5655 CARY AVE"]["foreclosure"]["status"] == "CLAIMED"   # not "no notice found"
    assert not rows["5655 CARY AVE"]["foreclosure"]["clerk_checked"]
    clerks.FAIL = {c: B.SourceError(B.TIMEOUT_ERR, "down") for c in ("dallas", "tarrant", "denton")}
    assert CO.recheck_outcome({}, CO.fc_check(db_session, sample_org.id, list(props.values()))) == "FAILED"


# 8 ── passed and cancelled sales; a passed notice does not confirm a new date ─

def test_passed_and_cancelled_sales(db_session, sample_org, sample_advisor, clerks):
    clerks.DOCS = {"dallas": [dallas_doc(9, "5655 CARY AVE", "DALLAS", "75227", PASSED)]}
    props = _cohort(db_session, sample_org, sample_advisor)
    CO.fc_check(db_session, sample_org.id, [props["5655 CARY AVE"]], user=sample_advisor)
    fc = _rows(db_session, sample_org)["5655 CARY AVE"]["foreclosure"]
    assert fc["status"] == "CONFLICT" and fc["verified"] is False and "passed" in fc["detail"]
    cancel = dallas_doc(10, "5655 CARY AVE", "DALLAS", "75227", SALE, doc_type="RESCISSION OF NOTICE OF FORECLOSURE",
                        text="NOTICE OF TRUSTEE SALE withdrawn")
    clerks.DOCS = {"dallas": [cancel]}
    CO.fc_check(db_session, sample_org.id, [props["5655 CARY AVE"]], user=sample_advisor)
    fc = _rows(db_session, sample_org)["5655 CARY AVE"]["foreclosure"]
    assert fc["status"] == "CANCELED" and fc["category"] == "D"


# 9 ── discovery: the Pre-Foreclosure strategy finds real notices ─────────────

def test_discovery_returns_upcoming_scheduled_sales_only(clerks):
    res = F.ClerkForeclosureReader().discover(["dallas", "collin"], limit=50)
    refs = {r["source_reference"] for r in res["records"]}
    assert "TXCLERK:DALLAS:FC:202600000005" not in refs                   # void filing
    assert "TXCLERK:DALLAS:FC:202600000001" in refs
    hoa = next(r for r in res["records"] if r["street_address"] == "7 HOA LN")
    assert hoa["signals"][0]["foreclosure_type"] == F.HOA and hoa["signals"][0]["type"] == "PRE_FORECLOSURE"
    assert res["stats"]["collin"]["status"] == "NOT CONNECTED"
    assert all(r["signals"][0]["sale_date"] >= TODAY.isoformat() for r in res["records"])


def test_a_tax_sale_notice_is_a_tax_sale_not_a_mortgage_foreclosure(clerks):
    clerks.DOCS = {"dallas": [dallas_doc(11, "9 TAX ST", "DALLAS", "75201", SALE,
                                         text="TAX SALE - Constable Precinct 1 - order of sale, delinquent taxes")]}
    (rec,) = F.ClerkForeclosureReader().discover(["dallas"])["records"]
    assert rec["signals"][0]["type"] == "TAX_SALE" and rec["signals"][0]["foreclosure_type"] == F.TAX


# 10 ── a tax list's sale date is a tax sale; a row's own status is kept ───────

def test_tax_list_sale_dates_and_row_status(db_session, sample_org, sample_advisor):
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code,sale_date,case_status\n"
                  "3 Tax Rd,Dallas,TX,75201,%s,\n4 Pp Ln,Dallas,TX,75201,%s,postponed\n" % (SALE, SALE),
                  user=sample_advisor, list_kind="tax_sale")
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code,sale_date,case_status\n"
                  "5 Mort Rd,Dallas,TX,75201,%s,postponed\n" % SALE, user=sample_advisor, list_kind="pre_foreclosure")
    db_session.commit()
    sig = {s.property_id: s for s in db_session.query(EvoSenseSignal).filter_by(organization_id=sample_org.id)
           if s.signal_type in ("TAX_SALE", "PRE_FORECLOSURE")}
    by = {p.street_address.upper(): p.id for p in db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id)}
    assert sig[by["3 TAX RD"]].signal_type == "TAX_SALE"
    assert sig[by["4 PP LN"]].case_status == "postponed"
    assert sig[by["5 MORT RD"]].signal_type == "PRE_FORECLOSURE" and sig[by["5 MORT RD"]].case_status == "postponed"
    assert CO.foreclosure_status([sig[by["5 MORT RD"]]])["status"] == "POSTPONED"


# 11 ── manual entry: verified only with the record's document number ─────────

def test_manual_notice_needs_a_document_number_to_count_as_verified(client, db_session, sample_org, admin_auth_headers):
    from app.models.models import User
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code\n8 Collin Way,Plano,TX,75023\n",
                  user=db_session.query(User).first())
    db_session.commit()
    p = db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).first()
    url = "/wholesale/evosense/properties/%s/signals" % p.id
    r = client.post(url, headers=admin_auth_headers, json={"signal_type": "PRE_FORECLOSURE", "sale_date": SALE.isoformat()})
    assert r.status_code == 200, r.text
    s = db_session.query(EvoSenseSignal).filter_by(property_id=p.id, signal_type="PRE_FORECLOSURE").one()
    assert C.jload(s.provenance, {})["claim"] == "operator"
    assert CO.foreclosure_status([s])["status"] == "CLAIMED"
    r = client.post(url, headers=admin_auth_headers, json={"signal_type": "PRE_FORECLOSURE", "sale_date": SALE.isoformat(),
                                                           "document_number": "2026-0099123",
                                                           "record_url": "https://www.collincountytx.gov/x"})
    assert r.status_code == 200, r.text
    sigs = db_session.query(EvoSenseSignal).filter_by(property_id=p.id, signal_type="PRE_FORECLOSURE").all()
    st = CO.foreclosure_status(sigs)
    assert st["status"] == "VERIFIED NOTICE" and st["document"] == "2026-0099123"
    bad = client.post(url, headers=admin_auth_headers, json={"signal_type": "PRE_FORECLOSURE", "sale_date": "soon"})
    assert bad.status_code == 422


# 12 ── tenant isolation: one org's check never touches another's houses ──────

def test_one_tenants_check_never_touches_another(db_session, sample_org, sample_advisor, clerks):
    other = Organization(name="Other Org", slug="other-org-fc", plan="standard", platform_id=sample_org.platform_id)
    db_session.add(other)
    db_session.commit()
    a = _cohort(db_session, sample_org, sample_advisor)
    b = _cohort(db_session, other, sample_advisor)
    CO.fc_check(db_session, sample_org.id, list(a.values()), user=sample_advisor)
    assert db_session.query(EvoSenseSignal).filter_by(organization_id=other.id, source=CO.CLERK_SOURCE).count() == 0
    assert db_session.query(EvoSenseEvent).filter_by(organization_id=other.id, action=CO.FC_CHECK_EVENT).count() == 0
    assert _rows(db_session, other)["1345 DELL OAK DR"]["foreclosure"]["status"] == "CLAIMED"
    assert len(b) == len(a)


# 13 ── a hunt with nothing to search is BLOCKED, not "succeeded" ─────────────

def test_a_strategy_with_no_working_source_is_blocked_not_green(db_session, sample_org, sample_advisor):
    from app.services.evosense import strategy as ST
    v = ST.starter_values("pre_foreclosure")
    clean, problems = ST.validate(v)
    assert not problems
    assert "Dallas, Tarrant, Denton and Johnson" in ST.STARTERS["pre_foreclosure"]["data"]
    from app.models.evosense_models import EvoSenseStrategy
    s = EvoSenseStrategy(organization_id=sample_org.id, name="PF", status="active",
                         provider_preferences=C.jdump({"discovery": ["FORECLOSURE"]}))
    db_session.add(s)
    db_session.commit()
    run = HU.run_strategy(db_session, sample_org.id, s, trigger="test", user=sample_advisor, enrich=False)
    counts = C.jload(run.counts, {})
    assert "No connected source" in counts.get("blocked", "") and run.status == "partial"


# 14 ── the "running" flag comes from the database, not one server's memory ──

def test_recheck_running_state_survives_other_workers(db_session, sample_org, sample_advisor):
    assert CO.running(sample_org.id, "t100", db_session) is False
    CO.mark_started(db_session, sample_org.id, "t100", user=sample_advisor)
    assert CO.running(sample_org.id, "t100", db_session) is True
    C.log_event(db_session, sample_org.id, "cohort.rechecked", summary="List t100 re-checked against public records (0 properties)",
                details={"kind": "t100"})
    db_session.commit()
    assert CO.running(sample_org.id, "t100", db_session) is False


# 15 ── enabling the clerk source never changes an all-sources strategy ───────

def test_clerk_source_feeds_only_strategies_that_ask_for_foreclosure(db_session, sample_org, clerks):
    from app.services.evosense import providers as PV
    cfg = PV.config(db_session, sample_org.id, CO.CLERK_SOURCE)
    cfg.enabled = True
    db_session.commit()
    keys = lambda focus: {p.key for _c, p, _cfg in HU._discovery_routes(db_session, sample_org.id, focus)}
    assert CO.CLERK_SOURCE not in keys([])                       # e.g. "New Check Out": no focus
    assert CO.CLERK_SOURCE not in keys(["TAX"])
    assert CO.CLERK_SOURCE in keys(["FORECLOSURE"])               # the Pre-Foreclosure starter
    assert CO.CLERK_SOURCE in keys([CO.CLERK_SOURCE])


# 16 ── small daily runs reach every county and new notices each day ─────────

def test_discovery_alternates_counties_and_rotates_daily(clerks):
    r = F.ClerkForeclosureReader()
    one = r.discover(["dallas", "tarrant", "denton"], limit=3, rotate=0)["records"]
    assert {x["county"] for x in one} == {"Dallas", "Tarrant", "Denton"}      # every county, not 3 Dallas notices
    day1 = {x["source_reference"] for x in r.discover(["dallas", "tarrant", "denton"], limit=3, rotate=1)["records"]}
    day0 = {x["source_reference"] for x in one}
    assert day1 != day0                                                      # the next day reaches other notices
    everything = r.discover(["dallas", "tarrant", "denton"], limit=500, rotate=7)["records"]
    assert len(everything) == len({x["source_reference"] for x in everything})   # no repeats when all fit


# 17 ── which county clerks are read, and which are honestly not ─────────────

def test_county_coverage_is_stated_county_by_county():
    assert set(F.COUNTIES) == {"dallas", "tarrant", "denton", "johnson"}
    for c in ("collin", "ellis", "kaufman", "rockwall"):
        assert "checked 2026-10-23" in F.NO_PORTAL[c]
        assert "https://" in F.NO_PORTAL[c] and "Record a notice" in F.NO_PORTAL[c]   # where to look, what to do
    with pytest.raises(B.SourceError):
        F.ClerkPortal("ellis")                      # never a pretend read


# 18 ── the re-check time is sent as UTC, so the screen shows the true time ───

def test_last_recheck_time_is_marked_utc(db_session, sample_org):
    C.log_event(db_session, sample_org.id, "cohort.rechecked", summary="List t100 re-checked against public records (1 properties)",
                details={"kind": "t100"})
    db_session.commit()
    assert CO.last_recheck(db_session, sample_org.id, "t100")["at"].endswith("Z")


# 19 ── the clerk and the appraisal district space street names differently ──

def test_running_brook_and_runningbrook_are_the_same_street():
    doc = {"propAddress": [{"address1": "2728 RUNNING BROOK LN", "zip": "75228", "city": "DALLAS"}]}
    assert F.same_place(doc, "2728 RUNNINGBROOK LN", "75228", "Dallas")
    assert F.same_place({"ocrText": "Property: 2233 PARKHURST DRIVE, ARLINGTON, TX 76001"},
                        "2233 PARK HURST DR", "76001", "Arlington")
    assert not F.same_place(doc, "2729 RUNNINGBROOK LN", "75228", "Dallas")          # never the neighbour
    assert not F.same_place(doc, "2728 RUNNING BROOK LN", "76001", "Arlington")      # nor another town


# 20 ── a large window is read in chunks: nothing skipped, nothing doubled ────

def test_window_reads_in_chunks_without_losing_or_doubling_notices(monkeypatch, clerks):
    many = [dallas_doc(100 + i, "%d BULK ST" % (100 + i), "DALLAS", "75201", SALE) for i in range(600)]
    clerks.DOCS = {"dallas": many}
    monkeypatch.setattr(F, "PAGE", 250)
    p = clerks("dallas")
    got = p.window(SALE - timedelta(days=40), SALE + timedelta(days=40))
    assert len(got) == 600 and len({d["docId"] for d in got}) == 600
    assert any("offset" in q and q["offset"] != "0" for q in p.queries)   # one day held 600: paged, de-duplicated


# 21 ── a notice spelled with a space is the same house, not a new one ──────

def test_running_brook_notice_attaches_to_runningbrook_house(db_session, sample_org):
    from app.services.evosense import identity as ID
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="2728 RUNNINGBROOK LN", zip_code="75228",
                         city="Dallas", state="TX")
    ID.apply_keys(p, {"street_address": p.street_address, "zip_code": "75228", "city": "Dallas", "state": "TX"})
    db_session.add(p)
    db_session.commit()
    m, prop, _c, keys = ID.resolve(db_session, sample_org.id, {"street_address": "2728 RUNNING BROOK LANE",
                                                               "zip_code": "75228", "city": "Dallas", "state": "TX"})
    assert m == ID.PROBABLE and prop.id == p.id
    m2, prop2, _c2, _k2 = ID.resolve(db_session, sample_org.id, {"street_address": "2728 RUNNING BROOK LANE",
                                                                 "zip_code": "75229", "city": "Dallas", "state": "TX"})
    assert m2 == ID.NEW and prop2 is None                       # another ZIP: another house
    m3, _p3, _c3, _k3 = ID.resolve(db_session, sample_org.id, {"street_address": "2730 RUNNING BROOK LANE",
                                                               "zip_code": "75228", "city": "Dallas", "state": "TX"})
    assert m3 == ID.NEW                                         # the neighbour stays separate
