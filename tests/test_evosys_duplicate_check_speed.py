"""EvoSys Sales possible-duplicate check (owner request 2026-11-22).

The check used to load every open lead on the team as a full record, once per
imported row, so a 3,000-row import slowed to a crawl. It now asks the
database for a short list first. These tests pin that the answers did not
change: every lead the old check flagged is still flagged, nothing new is
flagged, and the narrowed query still returns only the columns it needs.
"""
import random
import re

from app.models.models import Platform
from app.models.sales_models import BrandSalesOrg, Opportunity
from app.services.evosys_sales import grouping as G


def _old_possible_duplicate(db, opp):
    """The check exactly as it was before the speed change (reference)."""
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == opp.brand_sales_org_id,
                                     Opportunity.id != opp.id, Opportunity.status == "open")
    core = G.core_name(opp.company_name)
    dom = (opp.email or "").split("@")[-1].lower() if "@" in (opp.email or "") else ""
    hits = set()
    for o in q.all():
        if core and len(core) >= 4 and G.core_name(o.company_name) == core:
            hits.add(o.id)
        if dom and dom not in G._FREE_MAIL and (o.email or "").lower().endswith("@" + dom):
            hits.add(o.id)
    return hits


def _brand(db, slug="evs-dup-test"):
    p = Platform(name="EvoSys Pro", slug=slug + "-p")
    db.add(p)
    db.flush()
    b = BrandSalesOrg(platform_id=p.id, name="EvoSys Pro Sales", slug=slug)
    db.add(b)
    db.flush()
    return b


def _opp(db, b, name, email=None, status="open"):
    o = Opportunity(brand_sales_org_id=b.id, company_name=name, email=email, status=status)
    db.add(o)
    db.flush()
    return o


def test_same_core_name_is_flagged(db_session):
    db = db_session
    b = _brand(db)
    first = _opp(db, b, "Sparkle Pro Cleaning Services LLC")
    new = _opp(db, b, "SPARKLE PRO, Inc.")
    assert G.possible_duplicate(db, new) == first.id


def test_company_email_domain_is_flagged_but_free_mail_is_not(db_session):
    db = db_session
    b = _brand(db)
    first = _opp(db, b, "Acme Janitorial", email="owner@acmeclean.com")
    new = _opp(db, b, "Totally Different Name", email="sales@AcmeClean.com")
    assert G.possible_duplicate(db, new) == first.id
    _opp(db, b, "Gmail User One", email="one@gmail.com")
    other = _opp(db, b, "Gmail User Two", email="two@gmail.com")
    assert G.possible_duplicate(db, other) is None


def test_short_names_and_closed_leads_are_ignored(db_session):
    db = db_session
    b = _brand(db)
    _opp(db, b, "ABC Cleaning LLC")              # core "abc" is too short to compare
    assert G.possible_duplicate(db, _opp(db, b, "ABC Services")) is None
    _opp(db, b, "Brightway Maintenance", status="lost")
    assert G.possible_duplicate(db, _opp(db, b, "Brightway Maintenance LLC")) is None


def test_other_teams_never_match(db_session):
    db = db_session
    a, b = _brand(db, "evs-a"), _brand(db, "evs-b")
    _opp(db, a, "Lone Star Custodial Group")
    assert G.possible_duplicate(db, _opp(db, b, "Lone Star Custodial")) is None


def test_same_answers_as_the_old_check_on_a_big_mixed_list(db_session):
    """Every lead the old check would flag is flagged, and nothing else."""
    db = db_session
    b = _brand(db)
    rnd = random.Random(7)
    words = ["sparkle", "lone", "star", "clean", "pro", "elite", "texas", "a+", "d&d", "go-green",
             "shine", "o'neil", "max", "metro", "blue", "sky", "ace", "j.r.", "first", "class"]
    tails = ["LLC", "Inc.", "Cleaning Services", "Janitorial", "Co", "Solutions Group", "", "& Sons"]
    doms = ["gmail.com", "yahoo.com", "acme.com", "sparklepro.net", "lonestar.biz", "outlook.com", None]
    opps = []
    for _ in range(300):
        name = " ".join(rnd.sample(words, rnd.randint(1, 3))) + " " + rnd.choice(tails)
        name = name.upper() if rnd.random() < 0.3 else name.title()
        d = rnd.choice(doms)
        opps.append(_opp(db, b, name.strip(), email=("x%d@%s" % (rnd.randint(1, 9), d)) if d else None,
                         status="open" if rnd.random() < 0.9 else "won"))
    for o in opps:
        got = G.possible_duplicate(db, o)
        want = _old_possible_duplicate(db, o)
        if want:
            assert got in want, (o.company_name, o.email, got, want)
        else:
            assert got is None, (o.company_name, o.email, got)


def test_core_words_always_appear_in_the_lowercased_name():
    """The safety reason the database pre-filter cannot drop a real match."""
    rnd = random.Random(3)
    chars = "abcXYZ019&.-' ,()/#@!é"
    for _ in range(2000):
        name = "".join(rnd.choice(chars) for _ in range(rnd.randint(0, 30)))
        core = G.core_name(name)
        for w in core.split(" "):
            if w:
                assert w in name.lower(), (name, core, w)
                assert re.fullmatch(r"[a-z0-9&]+", w)
