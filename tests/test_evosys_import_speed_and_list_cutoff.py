"""Owner request 2026-11-23: "do both".

1. Picking a whole list into a campaign used to see only the 3,000 newest
   leads, so the older leads of a big list were silently left out.
2. The importer's "is this business already a lead?" check made five passes
   over the whole lead table per row. It now reads the table once per request
   and re-checks every hit against the lead's current values.

These tests pin that the answers did not change and that the gaps are closed.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import func

from app.models.sales_models import Opportunity
from app.services.intake import business_import as BI
from app.services.intake import sales_import as SI
from tests.test_evosys_sales_walkthrough import world  # noqa: F401  (fixture)
from tests.test_import_stage_b import sales  # noqa: F401  (fixture)


def _old_find_existing(db, bid, n):
    """The check exactly as it was before the speed change (reference)."""
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bid)
    sid = n.get("source_record_id")
    if sid:
        hit = q.filter(func.lower(Opportunity.source_record_id) == sid.lower()).first()
        if hit:
            return hit.id, "same source record ID (%s)" % sid
        for o in q.filter(Opportunity.source.ilike("%" + sid + "%")).limit(20).all():
            if BI.id_in_text(sid, o.source):
                return o.id, "source text already names %s" % sid
    if n.get("phone10"):
        tail = n["phone10"][-4:]
        for o in q.filter(Opportunity.phone.ilike("%" + tail + "%")).limit(200).all():
            if BI.digits10(o.phone) == n["phone10"]:
                return o.id, "same phone"
    if n.get("domain"):
        for o in q.filter(Opportunity.website.ilike("%" + n["domain"] + "%")).limit(50).all():
            if BI.domain_of(o.website) == n["domain"]:
                return o.id, "same website"
    ck = BI.company_key(n.get("business_name"))
    if ck:
        first = (n.get("business_name") or "").strip()[:3]
        for o in q.filter(Opportunity.company_name.ilike(first + "%")).limit(200).all():
            if BI.company_key(o.company_name) == ck:
                return o.id, "same company name"
    return None


def _target(monkeypatch, bid):
    monkeypatch.setattr(BI, "target_id", lambda b: bid)
    return object()


def _opp(db, bid, **kw):
    kw.setdefault("company_name", "Somebody")
    o = Opportunity(brand_sales_org_id=bid, stage="prospect", status="open", **kw)
    db.add(o)
    db.flush()
    return o


def _n(name=None, sid=None, phone=None, website=None):
    return {"business_name": name, "source_record_id": sid, "phone10": BI.digits10(phone) or None,
            "domain": BI.domain_of(website) or None}


def test_same_answers_as_the_old_check(sales, monkeypatch):
    db, bid = sales["db"], sales["brand"].id
    b = _target(monkeypatch, bid)
    rnd = random.Random(11)
    words = ["sparkle", "lone star", "clean", "pro", "elite", "a+", "d&d", "go-green", "o'neil", "j.r.", "ace"]
    tails = ["LLC", "Inc.", "Cleaning", "Co", "", "Corp", "The"]
    for i in range(250):
        _opp(db, bid, company_name=(rnd.choice(words) + " " + rnd.choice(tails)).strip().title(),
             source_record_id=rnd.choice([None, "GOV-%d" % rnd.randint(1, 60)]),
             source=rnd.choice([None, "Gov list | GOV-%d | TX" % rnd.randint(1, 60), "Facebook ad"]),
             phone=rnd.choice([None, "(214) 555-%04d" % rnd.randint(0, 80), "+1 469 555 %04d" % rnd.randint(0, 80)]),
             website=rnd.choice([None, "https://www.site%d.com/x" % rnd.randint(0, 40), "site%d.com" % rnd.randint(0, 40)]))
    db.commit()
    for i in range(400):
        n = _n(name=(rnd.choice(words) + " " + rnd.choice(tails)).strip().upper() if rnd.random() < .8 else None,
               sid=rnd.choice([None, "GOV-%d" % rnd.randint(1, 90), "gov-%d" % rnd.randint(1, 90)]),
               phone=rnd.choice([None, "214555%04d" % rnd.randint(0, 120), "4695550%03d" % rnd.randint(0, 120)]),
               website=rnd.choice([None, "site%d.com" % rnd.randint(0, 60)]))
        old, new = _old_find_existing(db, bid, n), SI.find_existing(db, b, n)
        if old is None:
            assert new is None, (n, new)
        else:
            assert new is not None and new[1] == old[1], (n, old, new)


def test_finds_a_match_the_old_200_cap_could_hide(sales, monkeypatch):
    db, bid = sales["db"], sales["brand"].id
    b = _target(monkeypatch, bid)
    t0 = datetime(2026, 1, 1)
    for i in range(260):
        _opp(db, bid, company_name="Sparkle Team %d" % i, created_at=t0 + timedelta(minutes=i))
    target = _opp(db, bid, company_name="Sparkle Zeta Janitorial LLC", created_at=t0 + timedelta(days=9))
    db.commit()
    assert SI.find_existing(db, b, _n(name="SPARKLE ZETA JANITORIAL")) == (target.id, "same company name")


def test_sees_leads_written_after_the_list_was_read(sales, monkeypatch):
    db, bid = sales["db"], sales["brand"].id
    b = _target(monkeypatch, bid)
    assert SI.find_existing(db, b, _n(phone="2145550199")) is None       # list read now
    o = _opp(db, bid, company_name="Fresh Co", phone="214-555-0199")
    ix = SI._index(db, bid)
    ix.add(o.id, o.source_record_id, o.source, o.phone, o.website, o.company_name)  # what write() does
    db.commit()
    assert SI.find_existing(db, b, _n(phone="(214) 555 0199")) == (o.id, "same phone")


def test_other_writers_are_picked_up_when_the_list_refreshes(sales, monkeypatch):
    db, bid = sales["db"], sales["brand"].id
    b = _target(monkeypatch, bid)
    assert SI.find_existing(db, b, _n(website="newco.com")) is None
    o = _opp(db, bid, company_name="Elsewhere", website="https://newco.com")  # added by "someone else"
    db.commit()
    SI._index(db, bid).built_at -= SI._INDEX_TTL + 1
    assert SI.find_existing(db, b, _n(website="www.newco.com")) == (o.id, "same website")


def test_a_stale_or_missing_hit_never_blocks_a_new_lead(sales, monkeypatch):
    db, bid = sales["db"], sales["brand"].id
    b = _target(monkeypatch, bid)
    o = _opp(db, bid, company_name="Changing Co", phone="2145550111")
    db.commit()
    assert SI.find_existing(db, b, _n(phone="2145550111"))[0] == o.id
    o.phone = "2145550999"                     # edited after the list was read
    db.commit()
    assert SI.find_existing(db, b, _n(phone="2145550111")) is None
    SI._index(db, bid).add("no-such-lead", None, None, "2145550222", None, None)  # e.g. a row that failed
    assert SI.find_existing(db, b, _n(phone="2145550222")) is None


def test_other_teams_never_match(sales, monkeypatch):
    db = sales["db"]
    _opp(db, sales["other"].id, company_name="Lone Star Custodial", phone="2145550333")
    db.commit()
    b = _target(monkeypatch, sales["brand"].id)
    assert SI.find_existing(db, b, _n(name="Lone Star Custodial", phone="2145550333")) is None


def test_whole_list_is_picked_even_past_3000_newest(world):
    p, db, c, H = world, world["db"], world["c"], world["H"]
    bid = p["brand"].id
    t0 = datetime(2026, 1, 1)
    old_batch = "abcdef12-0000-0000-0000-000000000000"
    db.bulk_save_objects([Opportunity(brand_sales_org_id=bid, company_name="Old List %d" % i, stage="prospect",
                                      status="open", import_batch_id=old_batch, email="o%d@oldlist.test" % i,
                                      created_at=t0 + timedelta(seconds=i)) for i in range(1200)])
    db.bulk_save_objects([Opportunity(brand_sales_org_id=bid, company_name="Newer %d" % i, stage="prospect",
                                      status="open", import_batch_id="99999999-new", email="n%d@newer.test" % i,
                                      created_at=t0 + timedelta(days=30, seconds=i)) for i in range(3000)])
    db.commit()
    r = c.post("/sales/evosys/campaigns/suggest", headers=H, json={"batch": "Import abcdef12"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1200        # was 0: every one of them is older than the 3,000 newest
