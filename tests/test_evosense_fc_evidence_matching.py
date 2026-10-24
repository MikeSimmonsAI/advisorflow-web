"""Foreclosure notices that carry no street address.

County clerks often index a notice by city only (Dallas), by legal
description (Tarrant, Johnson), or print only the parcel number and the
borrower's name. These tests pin how the existing clerk check now connects
such a notice to a property - from the property's own records already on
file (appraisal-district legal description, parcel ids, owner, the list's
borrower) - and how it refuses the wrong house:

* VERIFIED needs two independent kinds of evidence (parcel + name/city/legal,
  or lot/block/subdivision + name/city, or a partial legal + the full name);
* a name-only or city-only match is a CANDIDATE, never a verified notice;
* the same subdivision with another lot or block is REJECTED;
* a notice already recorded by hand is never added a second time;
* a November notice and its December re-posting stay two notices.

The notice texts below are written the way the real clerk notices read
(wording and layout taken from Dallas / Tarrant / Johnson notices read on
2026-10-24); the people and parcel numbers are made up.
"""
import json
from datetime import date, timedelta

import pytest

from app.models.evosense_models import EvoSenseEvent, EvoSenseObservation, EvoSenseProperty, EvoSenseSignal
from app.services.evosense import cohort as CO
from app.services.evosense import fc_match as M
from app.services.evosense import hunt as HU
from app.services.evosense.sources import clerk_fc as F

TODAY = date.today()


def first_tuesday_after(d):
    m = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return F.first_tuesday(m.year, m.month)


SALE = first_tuesday_after(TODAY)
LATER = first_tuesday_after(SALE)


def us(d):
    return d.strftime("%m/%d/%Y")


# 1 ── reading legal descriptions ─────────────────────────────────────────────

@pytest.mark.parametrize("text,sub,block,lots", [
    ("CANDLERIDGE ADDITION Block 603 Lot 23", ["CANDLERIDGE"], "603", ["23"]),
    ("LOT 1 BLK 17 INDIAN HILLS #1", ["INDIAN", "HILLS"], "17", ["1"]),
    ("COUNTRY OAKS, LOT 16; & HOUSE", ["COUNTRY", "OAKS"], None, ["16"]),
    ("10 1 LEWIS ADD # 1", ["LEWIS"], "1", ["10"]),
])
def test_appraisal_roll_legal_descriptions_in_every_layout(text, sub, block, lots):
    lg = M.parse_cad_legal_text(text)
    assert lg and lg["core"] == sub and lg["block"] == block and lg["lots"] == lots


def test_notice_legal_reads_words_and_numbers_and_dallas_block_numbers():
    t = ("BEING LOT THIRTY SEVEN (37), IN BLOCK J, OF CRAWFORD FARMS ADDITION, PHASE 2, AN ADDITION TO THE CITY "
         "OF FORT WORTH ... and Lot 12, Block 3/6009, SPRING RIDGE ADDITION")
    got = {(x["block"], tuple(sorted(x["lots"]))) for x in M.notice_legals(t)}
    assert ("J", ("37",)) in got and ("3", ("12",)) in got


# 2 ── the decision: verified / candidate / rejected ─────────────────────────

SPRING = {"parcel_ids": ["00000712345000000"], "legal": M.cad_legal("SPRING RIDGE ADDN PH 2", "3/6009", ["12"]),
          "names": ["KOWALCZYK MAREK"], "city": "Dallas"}


def test_lot_block_subdivision_plus_borrower_is_verified():
    r = M.decide(SPRING, "Being Lot 12, Block 3/6009, of SPRING RIDGE ADDITION, PHASE 2, Dallas County. "
                         "Grantor(s): MAREK KOWALCZYK", "DALLAS")
    assert r["decision"] == M.VERIFIED and "legal description" in r["matched_by"]


def test_neighbouring_lot_in_the_same_subdivision_is_rejected():
    r = M.decide(SPRING, "Being Lot 13, Block 3/6009, of SPRING RIDGE ADDITION. Grantor(s): JANE ROE", "DALLAS")
    assert r["decision"] == M.REJECTED and "different lot/block" in r["reason"]


def test_same_lot_in_another_block_is_rejected():
    r = M.decide(SPRING, "Lot 12, Block 4/6009, SPRING RIDGE ADDITION. Grantor(s): JANE ROE", "DALLAS")
    assert r["decision"] == M.REJECTED


# Found on production 2026-10-24: true notices that the first version rejected
# (scan slips and multi-lot wording). People and numbers changed.

def test_scan_slips_with_the_borrowers_full_name_are_held_not_rejected():
    ident = {"parcel_ids": [], "legal": M.cad_legal("IRON HORSE PHASE 2", "I", ["11"]), "names": ["ROE JANE ANN"],
             "city": "Mesquite"}
    r = M.decide(ident, "DALLAS LOT 1], BLOCK I, IRON HORSE PHASE 2, AN ADDITION TO THE CITY OF MESQUITE "
                        "Grantor(s): JANE ANN ROE", "MESQUITE")
    assert r["decision"] == M.CANDIDATE and "confirm on the notice" in r["reason"]


def test_block_zero_and_letter_o_are_the_same_block():
    ident = {"parcel_ids": [], "legal": M.cad_legal("ST AUGUSTINE HIGHLANDS", "O/6757", ["2"]), "names": ["ROE JANE"],
             "city": "Dallas"}
    r = M.decide(ident, "Lot 2, Block 0/6757, ST. AUGUSTINE HIGHLANDS ADDITION. Grantor: JANE ROE", "DALLAS")
    assert r["decision"] == M.VERIFIED


def test_every_lot_of_a_multi_lot_notice_counts():
    ident = {"parcel_ids": [], "legal": M.cad_legal("MORGAN HEIGHTS", "B", ["17"]), "names": [], "city": "Glenn Heights"}
    r = M.decide(ident, "Property To Be Sold. LOTS 16 AND 17, BLOCK B OF MORGAN HEIGHTS ADDITION", "GLENN HEIGHTS")
    assert r["decision"] == M.VERIFIED


def test_a_replatted_lot_matches_its_original_lot_only_with_the_name():
    ident = {"parcel_ids": [], "legal": M.cad_legal("BELMEAD", "L/4869", ["20A"]), "names": ["ROE JANE ANN"],
             "city": "Dallas"}
    text = "LOTS 19 AND 20, BLOCK L/4869, BELMEAD ADDITION"
    assert M.decide(ident, text, "DALLAS")["decision"] == M.CANDIDATE                 # partial + city only
    assert M.decide(ident, text + " Grantor: JANE ANN ROE", "DALLAS")["decision"] == M.VERIFIED


def test_owner_name_only_is_a_candidate_not_verified():
    r = M.decide(SPRING, "Grantor(s): MAREK KOWALCZYK. The property is in Dallas County.", "GARLAND")
    assert r["decision"] == M.CANDIDATE and "needs" in r["reason"]


def test_city_only_or_one_surname_is_never_verified_or_even_a_candidate():
    assert M.decide(SPRING, "NOTICE OF SUBSTITUTE TRUSTEE SALE", "DALLAS")["decision"] == M.NONE
    assert M.decide(SPRING, "executed by ANNA KOWALCZYK", "DALLAS")["decision"] == M.NONE
    # an LLC owner's business word is not a name ('Housing and Urban Development')
    llc = {"parcel_ids": [], "legal": None, "names": ["DYNAMIC DEVELOPMENT INTERESTS LLC"], "city": "Dallas"}
    assert M.decide(llc, "Secretary of Housing and Urban Development", "DALLAS")["decision"] == M.NONE


def test_a_common_name_in_the_same_city_is_never_verified():
    ident = {"parcel_ids": [], "legal": None, "names": ["GARCIA MARIA"], "city": "Dallas"}
    r = M.decide(ident, "Grantor(s): MARIA GARCIA", "DALLAS")
    assert r["decision"] == M.CANDIDATE


def test_parcel_number_plus_city_is_verified_and_short_ids_never_count():
    r = M.decide(SPRING, "APN: 00000712345000000 Property situated in Dallas", "DALLAS")
    assert r["decision"] == M.VERIFIED and "parcel number" in r["matched_by"]
    short = {"parcel_ids": ["165830"], "legal": None, "names": [], "city": "Ennis"}
    assert M.decide(short, "APN 165830", "ENNIS")["signals"].get("parcel") is None   # 6 digits collide


def test_a_different_phase_is_held_for_review():
    r = M.decide(SPRING, "Lot 12, Block 3/6009, SPRING RIDGE ADDITION PHASE 3. Grantor: MAREK KOWALCZYK", "DALLAS")
    assert r["decision"] == M.CANDIDATE and "phase" in r["reason"]


def test_misspelled_subdivision_and_run_together_names_still_match():
    kiest = {"parcel_ids": [], "legal": M.cad_legal("KIEST VALLEY ADDN", "B", ["4"]), "names": [], "city": "Dallas"}
    assert M.decide(kiest, "LOT 4, BLOCK B, KEIST VALLEY ADDITION", "DALLAS")["decision"] == M.VERIFIED
    glen = {"parcel_ids": [], "legal": M.cad_legal("GLEN CREST", "2", ["7"]), "names": [], "city": "Cleburne"}
    assert M.decide(glen, "Lot 7, Block 2, GLENCREST ADDITION", "CLEBURNE")["decision"] == M.VERIFIED


def test_trusts_llcs_and_shared_surnames_become_people():
    assert M.name_groups("SMITH JOHN A REVOCABLE TRUST") == [["SMITH", "JOHN"]]
    assert M.name_groups("JOSE A/MARIA SANCHEZ") == [["JOSE", "SANCHEZ"], ["MARIA", "SANCHEZ"]]
    assert M.name_match(["DYNAMIC DEVELOPMENT INTERESTS LLC"], "Grantor: CHRISTOPHER CLAASSEN") == ""
    assert M.name_match(["CLAASSEN CHRISTOPHER & TANZEEN"], "Grantor: CHRISTOPHER CLAASSEN") == "strong"


def test_identity_keeps_owner_and_list_borrower_apart():
    class P:
        id, parcel_apn, city = "p1", None, "Dallas"
    dcad = json.dumps({"ACCOUNT_INFO": {"ACCOUNT_NUM": "00000712345000000", "LEGAL1": "SPRING RIDGE ADDN PH 2",
                                        "LEGAL2": "BLK 3/6009 LOT 12", "OWNER_NAME1": "VILLANUEVA JOSE LUIS"}})
    csv = json.dumps({"evidence": {"list_claims": {"list_owner_name": "YASMIN VASQUEZ"}}})
    ident = M.identity_from(P, [("dcad", dcad), ("csv_import", csv)])
    assert ident["owner_names"] == ["VILLANUEVA JOSE LUIS"] and ident["list_names"] == ["YASMIN VASQUEZ"]
    assert ident["legal"]["block"] == "3" and ident["legal"]["lots"] == ["12"]
    assert "00000712345000000" in ident["parcel_ids"]


# 3 ── the clerk check, end to end, against a portal that answers like the clerk ─

class PhrasePortal(F.ClerkPortal):
    """The clerk portal minus the network. Full-text search reads the whole
    notice; a quoted value is a phrase, unquoted words must all appear."""
    DOCS = {}

    def __init__(self, county):
        super().__init__(county)
        self.queries = []

    def fetch(self, query, *, timeout=30.0):
        self.calls += 1
        self.queries.append(dict(query))
        docs = list(self.DOCS.get(self.county, []))
        if "searchValue" in query:
            v = query["searchValue"].upper()
            hits = []
            for d in docs:
                t = (d.get("_full") or d.get("ocrText") or "").upper()
                ok = v.strip('"') in t if v.startswith('"') else all(w in t for w in v.split())
                if ok:
                    hits.append(dict(d, highlights={"ocrText": [t]}))
            return hits[:int(query.get("limit") or 250)]
        rng = query.get(self.cfg["window"])
        if rng:
            a, b = rng.split(",")
            lo, hi = date(int(a[:4]), int(a[4:6]), int(a[6:])), date(int(b[:4]), int(b[4:6]), int(b[6:]))
            key = "recordedDate" if self.cfg["window_on"] == "recorded" else "instrumentDate"
            docs = [d for d in docs if d.get(key) and lo <= F.recorded_date({"recordedDate": d[key]}) <= hi]
        off = int(query.get("offset") or 0)
        return docs[off:off + F.PAGE]


def notice(i, text, sale=SALE, city="DALLAS", full=None, legal_index=None):
    """A Dallas notice the clerk indexed by CITY only (no street)."""
    d = {"docId": 440000000 + i, "docNumber": "2026009%05d" % i, "docType": "NOTICE OF FORECLOSURE",
         "recordedDate": us(TODAY - timedelta(days=30)), "instrumentDate": us(sale),
         "propAddress": [{"city": city}], "ocrText": text}
    if full:
        d["_full"] = full
    if legal_index:
        d["propertyAddress"] = [legal_index]
    return d


@pytest.fixture
def portal(monkeypatch):
    PhrasePortal.DOCS = {"dallas": [
        # 1 the Spring Ridge house: lot/block + borrower, city-only index
        notice(1, "NOTICE OF SUBSTITUTE TRUSTEE SALE Being Lot 12, Block 3/6009, of SPRING RIDGE ADDITION, an "
                  "addition to the City of Dallas. Grantor(s): MAREK KOWALCZYK"),
        # 2 its neighbour, lot 13 - never the Spring Ridge house
        notice(2, "NOTICE OF SUBSTITUTE TRUSTEE SALE Being Lot 13, Block 3/6009, of SPRING RIDGE ADDITION, an "
                  "addition to the City of Dallas. Grantor(s): JANE ROE"),
        # 3 parcel number only (short index snippet; the number is in the full notice)
        notice(3, "NOTICE OF TRUSTEE SALE Assert and protect your rights",
               full="NOTICE OF TRUSTEE SALE APN 26098500040120000 Grantor: ROSA QUINTANILLA"),
        # 4 a borrower's name only, filed for another city
        notice(4, "NOTICE OF FORECLOSURE SALE Grantor(s): ZBIGNIEW BRZEZINSKI", city="GARLAND"),
        # 5 December re-posting of notice 1 (same lot/block, same borrower)
        notice(5, "NOTICE OF SUBSTITUTE TRUSTEE SALE Being Lot 12, Block 3/6009, of SPRING RIDGE ADDITION. "
                  "Grantor(s): MAREK KOWALCZYK", sale=LATER),
    ]}
    monkeypatch.setattr(F.ClerkForeclosureReader, "__init__",
                        lambda self, portal_factory=PhrasePortal: (setattr(self, "_factory", PhrasePortal),
                                                                   setattr(self, "_portals", {}))[-1])
    return PhrasePortal


LIST = ("street_address,city,state,zip_code,county,owner_name\n"
        "1234 SPRING RIDGE DR,Dallas,TX,,Dallas,MAREK KOWALCZYK\n"          # no ZIP on the list
        "77 QUINTANA WAY,Dallas,TX,75217,Dallas,ROSA QUINTANILLA\n"
        "9 PINE GLADE,Dallas,TX,75228,Dallas,ZBIGNIEW BRZEZINSKI\n"
        "1236 SPRING RIDGE DR,Dallas,TX,75232,Dallas,JOHN DOE\n")           # lot 14: in the subdivision, no notice


def _houses(db, org, user):
    HU.import_csv(db, org.id, LIST, user=user, filename="evidence.csv")
    db.commit()
    props = {p.street_address.upper(): p for p in db.query(EvoSenseProperty).filter_by(organization_id=org.id)}
    for p in props.values():
        p.county = p.county or "Dallas"
    roll = {
        "1234 SPRING RIDGE DR": ("00000712345000000", "SPRING RIDGE ADDN", "BLK 3/6009 LOT 12", "KOWALCZYK MAREK"),
        "77 QUINTANA WAY": ("26098500040120000", "QUINTANA PLACE", "", "QUINTANILLA ROSA"),
        "9 PINE GLADE": ("00000855555000000", "", "", "BRZEZINSKI ZBIGNIEW"),
        "1236 SPRING RIDGE DR": ("00000712346000000", "SPRING RIDGE ADDN", "BLK 3/6009 LOT 14", "DOE JOHN"),
    }
    for street, (acct, l1, l2, owner) in roll.items():
        p = props[street]
        raw = json.dumps({"ACCOUNT_INFO": {"ACCOUNT_NUM": acct, "LEGAL1": l1, "LEGAL2": l2, "OWNER_NAME1": owner}})
        db.add(EvoSenseObservation(organization_id=org.id, property_id=p.id, provider_key="dcad",
                                   connector_kind="real", capability="property_record",
                                   source_reference="DCAD:%s" % acct, raw_payload=raw, payload=raw))
    db.commit()
    return props


def _check(db, org, props):
    from app.services.evosense import fc_match as FM
    reader = F.ClerkForeclosureReader()
    targets = [{"id": p.id, "street_address": p.street_address, "zip_code": p.zip_code, "city": p.city,
                "identity": FM.identity(db, p)} for p in props.values()]
    return reader.check("dallas", targets)


def test_address_less_notices_are_found_from_the_records_on_file(db_session, sample_org, sample_advisor, portal):
    props = _houses(db_session, sample_org, sample_advisor)
    res = _check(db_session, sample_org, props)
    spring, quint = props["1234 SPRING RIDGE DR"], props["77 QUINTANA WAY"]
    docs = lambda pid: {d["docNumber"] for d, _ in res["matches"].get(pid, [])}
    # lot/block/subdivision + borrower; the December re-posting is a second notice, not merged
    assert docs(spring.id) == {"202600900001", "202600900005"}
    assert all(h.startswith("evidence:") and "legal description" in h for _, h in res["matches"][spring.id])
    # parcel number + borrower, found by the clerk's full-text search
    assert docs(quint.id) == {"202600900003"}
    assert "parcel number" in res["matches"][quint.id][0][1]
    assert res["stats"]["matched_evidence"] == 2


def test_false_matches_are_rejected_or_held_never_verified(db_session, sample_org, sample_advisor, portal):
    props = _houses(db_session, sample_org, sample_advisor)
    res = _check(db_session, sample_org, props)
    spring, pine, lot14 = props["1234 SPRING RIDGE DR"], props["9 PINE GLADE"], props["1236 SPRING RIDGE DR"]
    # the neighbour's notice (lot 13) never lands on lot 12, and is recorded as rejected
    assert "202600900002" not in {d["docNumber"] for d, _ in res["matches"][spring.id]}
    assert "202600900002" in {r["document"] for r in res["rejected"][spring.id]}
    # lot 14: same subdivision, no notice of its own -> nothing verified, the others rejected
    assert lot14.id not in res["matches"]
    assert {r["document"] for r in res["rejected"][lot14.id]} >= {"202600900001", "202600900002"}
    # a name alone (filed for another city) is only a candidate
    assert pine.id not in res["matches"]
    assert [c["document"] for c in res["candidates"][pine.id]] == ["202600900004"]
    assert res["candidates"][pine.id][0]["decision"] == M.CANDIDATE


def test_recheck_ingests_evidence_matches_and_never_duplicates_a_manual_notice(
        client, db_session, sample_org, sample_advisor, admin_auth_headers, portal):
    props = _houses(db_session, sample_org, sample_advisor)
    quint = props["77 QUINTANA WAY"]
    # a person already recorded notice 3 by hand, with the county prefix
    r = client.post("/wholesale/evosense/properties/%s/signals" % quint.id, headers=admin_auth_headers,
                    json={"signal_type": "PRE_FORECLOSURE", "document_number": "DALLAS-202600900003",
                          "sale_date": SALE.isoformat(), "note": "read on the notice: APN and borrower agree"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    ps = list(props.values())
    out = CO.fc_check(db_session, sample_org.id, ps, user=sample_advisor)
    assert out["dallas"]["already_recorded"] == 1 and out["dallas"]["ingested"] == 2

    def state():
        sig = db_session.query(EvoSenseSignal).filter_by(organization_id=sample_org.id, active=True,
                                                         signal_type="PRE_FORECLOSURE").count()
        return sig, db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).count()
    first = state()
    CO.fc_check(db_session, sample_org.id, ps, user=sample_advisor)          # again: nothing new
    assert state() == first and first == (3, 4)
    db_session.expire_all()
    # the hand-recorded notice keeps its note; one notice, not two
    st = CO.foreclosure_status(db_session.query(EvoSenseSignal).filter_by(property_id=quint.id, active=True).all())
    assert st["status"] == "VERIFIED NOTICE" and len(st["notices"]) == 1
    assert st["notices"][0]["evidence"].startswith("read on the notice")
    # the Spring Ridge house: November notice and December re-posting both kept
    sp = props["1234 SPRING RIDGE DR"]
    st = CO.foreclosure_status(db_session.query(EvoSenseSignal).filter_by(property_id=sp.id, active=True).all())
    assert st["status"] == "VERIFIED NOTICE" and st["sale_date"] == SALE.isoformat()
    assert st["reposted_for"] == [LATER.isoformat()] and len(st["notices"]) == 2
    assert all((n["matched_by"] or "").startswith("evidence:") for n in st["notices"])
    # the candidate and the rejection are on the dated check, for a person to review
    ev = (db_session.query(EvoSenseEvent).filter_by(property_id=props["9 PINE GLADE"].id,
                                                    action=CO.FC_CHECK_EVENT).first())
    det = json.loads(ev.details) if isinstance(ev.details, str) else ev.details
    assert det["matched"] == 0 and det["candidates"][0]["document"] == "202600900004"


def test_match_preview_is_read_only(db_session, sample_org, sample_advisor, portal):
    props = _houses(db_session, sample_org, sample_advisor)
    before = db_session.query(EvoSenseSignal).filter_by(organization_id=sample_org.id).count()
    r = CO.match_preview(db_session, sample_org.id, property_ids=[p.id for p in props.values()], evidence_only=True)
    out = {x["address"]: x for x in r["results"]}
    assert out["1234 SPRING RIDGE DR"]["outcome"] == "verified"
    assert out["77 QUINTANA WAY"]["outcome"] == "verified"
    assert out["9 PINE GLADE"]["outcome"] == "candidate"
    assert out["1236 SPRING RIDGE DR"]["outcome"] == "no match" and out["1236 SPRING RIDGE DR"]["rejected"]
    assert db_session.query(EvoSenseSignal).filter_by(organization_id=sample_org.id).count() == before


def test_match_preview_endpoint_is_admin_only(client, db_session, sample_org, admin_auth_headers, auth_headers,
                                              monkeypatch):
    jobs = {}
    monkeypatch.setattr(CO, "start_preview", lambda org_id, **kw: jobs.setdefault("j", "abc123"))
    monkeypatch.setattr(CO, "preview_result", lambda org_id, job: {"status": "done", "result": {"results": []}}
                        if job == "abc123" else None)
    r = client.post("/wholesale/evosense/foreclosure/match-preview", headers=admin_auth_headers, json={})
    assert r.status_code == 422
    r = client.post("/wholesale/evosense/foreclosure/match-preview", headers=admin_auth_headers,
                    json={"property_ids": ["x"]})
    assert r.status_code == 200 and r.json()["job"] == "abc123"
    assert client.get("/wholesale/evosense/foreclosure/match-preview/abc123", headers=admin_auth_headers).json()["status"] == "done"
    assert client.get("/wholesale/evosense/foreclosure/match-preview/nope", headers=admin_auth_headers).status_code == 404
    assert client.post("/wholesale/evosense/foreclosure/match-preview", headers=auth_headers,
                       json={"property_ids": ["x"]}).status_code in (401, 403)


# 4 ── sale status: re-postings, pending lawsuits, the same notice twice ──────

def test_doc_key_joins_hand_recorded_and_clerk_numbers():
    assert CO.doc_key("JOHNSON-2026-392") == CO.doc_key("2026-392") == "2026-392"
    assert CO.doc_key("COLLIN-FC-2026-11-03-0042") == "2026-11-03-0042"
    assert CO.doc_key("P226014403") == "P226014403"


def test_pending_hoa_suit_is_shown_and_is_not_a_sale(client, db_session, sample_org, admin_auth_headers):
    """2848 Caminata: earlier notices for passed sales, an HOA lien suit with no
    sale date -> a conflict for review with the suit visible, never 'verified'
    or 'cancelled'."""
    from app.models.models import User
    HU.import_csv(db_session, sample_org.id, "street_address,city,state,zip_code\n2848 Caminata,Fort Worth,TX,76179\n",
                  user=db_session.query(User).first(), declared_sale_date=SALE.isoformat())
    db_session.commit()
    p = db_session.query(EvoSenseProperty).filter_by(organization_id=sample_org.id).first()
    url = "/wholesale/evosense/properties/%s/signals" % p.id
    r = client.post(url, headers=admin_auth_headers,
                    json={"signal_type": "PRE_FORECLOSURE", "document_number": "D226170973",
                          "foreclosure_type": "hoa_assessment_lien_sale",
                          "note": "Lis pendens: HOA suit to foreclose its assessment lien"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    st = CO.foreclosure_status(db_session.query(EvoSenseSignal).filter_by(property_id=p.id, active=True).all())
    assert st["status"] != "VERIFIED NOTICE" and st["status"] != "CANCELED"
    assert st["pending_actions"] and st["pending_actions"][0]["document"] == "D226170973"
    assert st["pending_actions"][0]["type"] == "hoa_assessment_lien_sale"
