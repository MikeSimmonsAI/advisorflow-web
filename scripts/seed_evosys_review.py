"""THE canonical local review environment for Wholesale + EvoSense (Phase 7.2).

    cd C:\\Dev\\advisorflow-web
    .venv\\Scripts\\python.exe scripts\\seed_evosys_review.py

(START_EVOSYS_REVIEW.bat runs this for you on every start. It is safe to run
any number of times.)

ONE organization  : EvoSense Review (TEST)          slug evosense-review-test
ONE login         : evosense.review@example.test / EvoSense-Review-2026!
ONE database      : the local SQLite file named by DATABASE_URL (advisorflow.db)

What it guarantees, and how:

  * SAFE. Refuses anything that is not SQLite. Touches no other organization.
    Every row it causes is is_test / SANDBOX.
  * IDEMPOTENT. Every step looks for what it would create first (by name /
    street address) and skips it if present. Re-running never duplicates a
    row, never resets a row, never re-hunts an organization that already has
    its EvoSense data.
  * NON-DESTRUCTIVE. It deletes nothing and never overwrites a value a person
    changed, with two deliberate exceptions that are configuration, not data:
    the organization is linked to the EvoSys Pro platform (so the shell is
    branded EvoSys Pro), and the review login is kept active with its
    documented password.
  * REAL. The EvoSense data comes from the real engine against the sandbox
    adapters (Phase 7 seed). The Wholesale Operations data is created through
    the real Wholesale API with the review login, exactly as a person would,
    so nothing here can be a state the application would refuse.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()
# The seed never calls a paid AI model: seller messages are read by the
# deterministic rules the AI gateway falls back to when no key is configured.
os.environ.pop("OPENAI_API_KEY", None)
os.environ["AI_MANUAL_ACTIONS_ENABLED"] = "false"
if not os.environ.get("DATABASE_URL", "sqlite").startswith("sqlite"):
    raise SystemExit("REFUSING: the review seed only writes to a local SQLite database.")

from app.main import app  # noqa: E402

SLUG = "evosense-review-test"
NAME = "EvoSense Review (TEST)"
EMAIL = "evosense.review@example.test"
PASSWORD = "EvoSense-Review-2026!"
SEED_TAG = "EvoSys review seed"
AVATAR = ("data:image/svg+xml;base64," + __import__("base64").b64encode(
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' "
    b"rx='32' fill='#123a66'/><text x='32' y='41' font-family='Arial' font-size='24' "
    b"font-weight='700' fill='#9fd8ff' text-anchor='middle'>ER</text></svg>").decode())
FEATURES = ["leads", "wholesale_real_estate", "master_dashboard", "users", "reports",
            "availability", "branding_settings", "compliance", "audit_log"]

# Wholesale Operations scenarios. Every address is a sandbox address; every
# person and company is invented; every contact ends in .example.
BUYERS = [
    dict(company_name="Trinity Cash Homes (TEST)", contact_name="Avery Stone",
         email="avery@trinitycash.example", phone="2145550141", cash_verified=True,
         proof_of_funds_on_file=True, typical_close_days=10, past_deals_count=14,
         reliability_rating=5, source="reia",
         box=dict(states=["TX"], counties=["Dallas", "Tarrant"], property_types=["single_family"],
                  strategies=["flip"], min_price=80000, max_price=260000, min_beds=2,
                  rehab_tolerance="heavy")),
    dict(company_name="Oak Cliff Rentals LLC (TEST)", contact_name="Jordan Reyes",
         email="jordan@oakcliffrentals.example", phone="2145550142", cash_verified=True,
         proof_of_funds_on_file=False, typical_close_days=21, past_deals_count=6,
         reliability_rating=4, source="referral",
         box=dict(states=["TX"], counties=["Dallas"], property_types=["single_family", "duplex"],
                  strategies=["rental"], min_price=60000, max_price=200000,
                  rehab_tolerance="moderate")),
    dict(company_name="Lone Star Flip Partners (TEST)", contact_name="Casey Morgan",
         email="casey@lonestarflip.example", phone="8175550143", cash_verified=False,
         proof_of_funds_on_file=False, typical_close_days=30, past_deals_count=2,
         reliability_rating=3, source="reia",
         box=dict(states=["TX"], counties=["Tarrant"], property_types=["single_family"],
                  strategies=["flip"], min_price=100000, max_price=300000,
                  rehab_tolerance="light")),
    dict(company_name="Metroplex Holdings (TEST)", contact_name="Riley Chen",
         email="riley@metroplexholdings.example", phone="9725550144", cash_verified=True,
         proof_of_funds_on_file=True, typical_close_days=14, past_deals_count=22,
         reliability_rating=5, source="referral",
         box=dict(states=["TX"], min_price=50000, max_price=400000, rehab_tolerance="heavy")),
    dict(company_name="Red River Capital (TEST)", contact_name="Morgan Blake",
         email="morgan@redriver.example", phone="4055550145", cash_verified=True,
         proof_of_funds_on_file=True, typical_close_days=12, past_deals_count=9,
         reliability_rating=4, source="import",
         box=dict(states=["OK"], min_price=50000, max_price=250000)),
]

# (street, zip, county, beds, baths, sqft, year, seller first/last, target stage)
DEALS = [
    ("2847 Kilburn Ave", "75216", "Dallas", 3, 2, 1380, 1958, "Dana", "Whitfield", "closed"),
    ("4119 Bonnie View Rd", "75216", "Dallas", 3, 1, 1210, 1952, "Lee", "Harmon", "assignment"),
    ("6613 Lovett Ave", "75227", "Dallas", 4, 2, 1760, 1964, "Sam", "Ortega", "under_contract"),
    ("1522 E Ohio Ave", "75216", "Dallas", 3, 2, 1450, 1961, "Terry", "Vance", "offer_review"),
    ("3308 Hamilton Ave", "76110", "Tarrant", 2, 1, 980, 1949, "Kim", "Ashby", "qualifying"),
]


def say(*a):
    print(*a, flush=True)


def ensure_org_and_login(db):
    from app.models.models import Organization, Platform, User
    from app.services.auth_service import hash_password
    evosys = db.query(Platform).filter(Platform.slug == "evosyspro").first()
    org = db.query(Organization).filter(Organization.slug == SLUG).first()
    created = False
    if org is None:
        org = Organization(name=NAME, slug=SLUG, plan="standard", industry="real_estate")
        import json
        org.enabled_features = json.dumps(FEATURES)
        db.add(org)
        db.commit()
        created = True
    # Configuration, not data: the review organization is an EvoSys Pro
    # customer, so the shell shows EvoSys Pro. See the branding note in
    # app/routers/branding_router.py (GET /branding/org -> platform).
    if evosys is not None and org.platform_id != evosys.id:
        org.platform_id = evosys.id
        say("branding      linked to platform EvoSys Pro")
    user = db.query(User).filter(User.email == EMAIL).first()
    if user is None:
        user = User(organization_id=org.id, email=EMAIL, password_hash=hash_password(PASSWORD),
                    full_name="EvoSense Reviewer (TEST)", role="org_admin",
                    must_change_password=False)
        db.add(user)
    else:
        from app.services.auth_service import verify_password
        if not verify_password(PASSWORD, user.password_hash):
            user.password_hash = hash_password(PASSWORD)
        user.is_active = True
        user.must_change_password = False
    # A complete review profile, so the platform's profile checklist does not
    # sit over the screens being reviewed. Fictional number; monogram image.
    if not getattr(user, "phone", None):
        user.phone = "+12145550100"
    if not getattr(user, "profile_photo_url", None):
        user.profile_photo_url = AVATAR
    if evosys is not None and getattr(user, "platform_id", None) is None:
        user.platform_id = evosys.id
    db.commit()
    return org, user, created


def ensure_evosense(db, org, user, created):
    """The EvoSense data. Built by the real engine once; never re-hunted."""
    from app.models.evosense_models import EvoSenseProperty, EvoSenseStrategy
    from app.services.evosense import sandbox_seed as SS
    from app.services.evosense import scheduler as SCH
    have = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org.id).count()
    if have == 0:
        say("evosense      building sandbox data with the real engine (first run only)")
        SS.seed_review(db, org.id, user, replies=True)
        db.commit()
    # Automatic hunting is registered for every active strategy (Phase 7.1).
    # schedule_for only creates what is missing; a strategy that hunted
    # before becomes due a day after its last hunt.
    for s in db.query(EvoSenseStrategy).filter(EvoSenseStrategy.organization_id == org.id,
                                               EvoSenseStrategy.status == "active").all():
        SCH.schedule_for(db, s)
    db.commit()
    return db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org.id).count()


class Api:
    def __init__(self, client, headers):
        self.c, self.h = client, headers

    def _ok(self, r):
        if r.status_code not in (200, 201):
            raise RuntimeError("%s %s -> %s %s" % (r.request.method, r.request.url,
                                                   r.status_code, r.text[:300]))
        return r.json()

    def get(self, path, **params):
        return self._ok(self.c.get("/wholesale" + path, headers=self.h, params=params))

    def post(self, path, body=None):
        return self._ok(self.c.post("/wholesale" + path, headers=self.h, json=body or {}))

    def patch(self, path, body):
        return self._ok(self.c.patch("/wholesale" + path, headers=self.h, json=body))

    def try_post(self, path, body=None):
        r = self.c.post("/wholesale" + path, headers=self.h, json=body or {})
        return r.status_code, (r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else r.text)


def ensure_buyers(api):
    existing = {b["company_name"]: b for b in
                api.get("/buyers", include_test=True, active_only=False, limit=1000)["buyers"]}
    out = {}
    for spec in BUYERS:
        spec = dict(spec)
        box = spec.pop("box")
        if spec["company_name"] in existing:
            b = existing[spec["company_name"]]
            if not b.get("buy_boxes"):            # finish a half-built buyer
                api.post("/buyers/%s/buy-boxes" % b["id"], box)
            out[spec["company_name"]] = b
            continue
        b = api.post("/buyers", dict(spec, is_test=True, source_detail=SEED_TAG,
                                     notes="SANDBOX buyer for the EvoSys review."))
        api.post("/buyers/%s/buy-boxes" % b["id"], box)
        out[spec["company_name"]] = b
        say("buyer         %s" % spec["company_name"])
    return out


def _advance(api, deal_id, stage_goal, buyers, street):
    """Walk a deal forward through the real gates to its review state."""
    stages = ["qualifying", "offer_review", "under_contract", "assignment", "closed"]
    goal = stages.index(stage_goal)
    # Comps, repairs and the MAO the formula allows (always).
    base = {"2847 Kilburn Ave": 215000, "4119 Bonnie View Rd": 185000,
            "6613 Lovett Ave": 248000, "1522 E Ohio Ave": 205000,
            "3308 Hamilton Ave": 165000}[street]
    for i, (d_price, d_sqft) in enumerate(((0, 0), (-8000, -40), (9000, 30))):
        api.post("/deals/%s/comps" % deal_id, {
            "street_address": "%d Sandbox Comp St" % (1000 + i), "sale_price": base + d_price,
            "square_feet": 1400 + d_sqft, "sale_date": "2026-07-1%d" % i})
    analysis = api.patch("/deals/%s/analysis" % deal_id, {
        "repair_estimate": 38000, "desired_wholesale_fee": 12000})
    mao = analysis.get("max_allowable_offer")
    if goal < 1 or not mao:
        return
    api.patch("/deals/%s/analysis" % deal_id, {"proposed_offer": mao})
    offer = api.post("/deals/%s/approvals" % deal_id, {
        "kind": "offer", "amount": mao, "recommendation": "Offer the maximum allowable amount",
        "reasoning": "Motivated seller; the spread holds at the estimated ARV."})
    if goal < 2:
        return                                   # stays AWAITING APPROVAL
    api.post("/approvals/%s/decide" % offer["id"], {"approve": True, "comments": "Approved"})
    api.post("/deals/%s/stage" % deal_id, {"stage": "offer_sent"})
    contract = api.post("/deals/%s/approvals" % deal_id, {"kind": "contract", "amount": mao})
    api.post("/approvals/%s/decide" % contract["id"], {"approve": True})
    api.patch("/deals/%s/contract" % deal_id, {
        "contract_price": mao, "contract_status": "signed",
        "close_of_escrow_target": {"closed": "2026-09-18", "assignment": "2026-10-02",
                                   "under_contract": "2026-10-09"}[stage_goal]})
    api.post("/deals/%s/stage" % deal_id, {"stage": "under_contract"})
    matches = api.post("/deals/%s/match-buyers" % deal_id)["matches"]
    fit = [m for m in matches if not m.get("disqualified")][:3]
    if fit:
        api.post("/deals/%s/disposition" % deal_id, {
            "buyer_ids": [m["buyer_id"] for m in fit], "asking_price": mao + 12000})
    if goal < 3 or not fit:
        return
    room = api.get("/deals/%s" % deal_id)
    chosen = fit[0]["buyer_id"]
    for o in room["buyer_outreach"]:
        if o["buyer_id"] == chosen:
            api.patch("/outreach/%s" % o["id"], {
                "status": "offer_submitted", "offer_amount": mao + 12000,
                "response_note": "We can close in ten days."})
    api.post("/deals/%s/assign" % deal_id, {"buyer_id": chosen, "buyer_price": mao + 12000})
    appr = api.post("/deals/%s/approvals" % deal_id, {"kind": "assignment"})
    api.post("/approvals/%s/decide" % appr["id"], {"approve": True})
    api.post("/deals/%s/stage" % deal_id, {"stage": "assignment_pending"})
    doc = api.post("/deals/%s/documents" % deal_id, {
        "doc_type": "assignment_agreement", "title": "Assignment - %s (SANDBOX)" % street,
        "parties": [{"name": "Seller (TEST)", "role": "seller"},
                    {"name": "Selected buyer (TEST)", "role": "assignee"}]})
    api.patch("/deals/%s/title" % deal_id, {"title_company": "Sandbox Title Co. (TEST)",
                                            "title_status": "opened"})
    if goal < 4:
        return
    api.patch("/documents/%s" % doc["id"], {"doc_type": "assignment_agreement",
                                             "file_name": "assignment-signed-SANDBOX.pdf",
                                             "signature_status": "signed"})
    api.post("/deals/%s/close" % deal_id, {"wholesale_fee_collected": 12000,
                                           "closing_date": "2026-09-18",
                                           "deal_result": "closed_won"})


def ensure_deals(api, buyers):
    listing = api.get("/properties", include_test=True)["properties"]
    have = {p["street_address"]: p for p in listing}
    made = {}
    for (street, zip_code, county, beds, baths, sqft, year, first, last, goal) in DEALS:
        if street in have:
            made[street] = have[street]
            continue
        prop = api.post("/properties", {
            "street_address": street, "city": "Dallas" if county == "Dallas" else "Fort Worth",
            "state": "TX", "zip_code": zip_code, "county": county, "market": "DFW",
            "property_type": "single_family", "bedrooms": beds, "bathrooms": baths,
            "square_feet": sqft, "year_built": year, "is_test": True, "test_note": SEED_TAG})
        api.post("/properties/%s/seller" % prop["id"], {
            "first_name": first, "last_name": last + " (TEST)",
            "owner_status": "owner_of_record"})
        api.post("/properties/%s/manual-contact" % prop["id"], {
            "phone": "21455501%02d" % (50 + len(made)),
            "email": "%s.%s@seller.example" % (first.lower(), last.lower())})
        deal_id = prop["deal"]["id"]
        api.post("/deals/%s/seller-reply" % deal_id, {
            "message": "Yes, I'd sell. It's vacant and needs work - roof and kitchen. "
                       "Looking for around the low hundreds and I'd like it done soon.",
            "mode": "manual"})
        _advance(api, deal_id, goal, buyers, street)
        made[street] = prop
        say("deal          %-24s -> %s" % (street, goal))
    return made


CLOSING_DATES = {"4119 Bonnie View Rd": "2026-10-02", "6613 Lovett Ave": "2026-10-09"}


def ensure_closing_dates(api, made):
    """Scheduled closings, so Contracts & Closing has a calendar to show.
    Set through the title endpoint, and only where no date exists yet."""
    for street, iso in CLOSING_DATES.items():
        deal_id = (made.get(street) or {}).get("deal", {}).get("id")
        if not deal_id:
            continue
        room = api.get("/deals/%s" % deal_id)
        if not (room.get("deal") or {}).get("closing_date"):
            api.patch("/deals/%s/title" % deal_id, {"closing_date": iso,
                                                    "title_company": "Sandbox Title Co. (TEST)"})
            say("closing       %-24s -> %s" % (street, iso))


def ensure_share_links(api, made, buyers):
    """Seller portal + investor deal room for the under-contract deal."""
    deal_id = (made.get("6613 Lovett Ave") or {}).get("deal", {}).get("id")
    if not deal_id:
        return {}
    pub = api.get("/deals/%s/publication" % deal_id)
    links = {l["audience"]: l for l in pub.get("links", []) if not l.get("revoked_at")}
    if not pub["buyer"].get("summary"):
        api.patch("/deals/%s/publication" % deal_id, {
            "buyer_room_summary": "Vacant 4/2 single-family in Southeast Dallas. Needs a roof, "
                                  "kitchen and flooring; solid foundation. SANDBOX listing.",
            "buyer_room_condition": "Heavy cosmetic, roof at end of life",
            "buyer_room_show_arv": True, "buyer_room_show_repairs": True,
            "buyer_room_show_comps": True,
            "seller_room_message": "Thanks for working with us. Your sale is under contract "
                                   "and we are lining up the buyer now. (SANDBOX)",
            "seller_room_contact_name": "EvoSense Review Team (TEST)",
            "seller_room_contact_email": "review@evosys.example"})
    for audience in ("seller", "buyer"):
        if not pub[audience].get("published"):
            api.post("/deals/%s/publication/state" % deal_id,
                     {"audience": audience, "published": True})
    if "seller" not in links:
        links["seller"] = api.post("/deals/%s/share-links" % deal_id, {
            "audience": "seller", "recipient_name": "Sam Ortega (TEST)",
            "expires_in_days": None})
    if "buyer" not in links:
        room = api.get("/deals/%s" % deal_id)
        buyer_id = next((o["buyer_id"] for o in room.get("buyer_outreach", [])), None)
        if buyer_id:
            links["buyer"] = api.post("/deals/%s/share-links" % deal_id, {
                "audience": "buyer", "buyer_id": buyer_id, "expires_in_days": None})
    return links


def ensure_promoted(db, org, user):
    """One EvoSense opportunity already handed to Wholesale Operations, so the
    'promoted' state and the EvoSense -> Deal hand-off are reviewable."""
    from app.models.evosense_models import EvoSenseProperty
    from app.services.evosense import promotion as PR
    if db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org.id,
                                         EvoSenseProperty.promoted_deal_id.isnot(None)).count():
        return None
    prop = (db.query(EvoSenseProperty)
            .filter(EvoSenseProperty.organization_id == org.id,
                    EvoSenseProperty.street_address == "6120 Wedgwood Dr").first())
    if prop is None:
        return None
    try:
        out = PR.promote(db, org.id, prop, user, note="Promoted for the EvoSys review (SANDBOX).")
        db.commit()
        say("promoted      6120 Wedgwood Dr -> Wholesale deal %s" % out.get("deal_id"))
        return out
    except Exception as exc:  # noqa: BLE001 - the review never fails on this
        db.rollback()
        say("promoted      skipped (%s)" % getattr(exc, "detail", exc))
        return None


def main() -> int:
    from fastapi.testclient import TestClient
    from app.deps import SessionLocal
    from app.services.auth_service import create_access_token
    from app.services.evosense import sandbox_seed

    with TestClient(app) as c:            # startup: create_all + migrations
        c.get("/ping")
        db = SessionLocal()
        try:
            org, user, created = ensure_org_and_login(db)
            n = ensure_evosense(db, org, user, created)
            say("evosense      %s properties" % n)
            ensure_promoted(db, org, user)
            api = Api(c, {"Authorization": "Bearer %s" % create_access_token(user, db)})
            buyers = ensure_buyers(api)
            made = ensure_deals(api, buyers)
            ensure_closing_dates(api, made)
            links = ensure_share_links(api, made, buyers)
            flag = sandbox_seed.prop_at(db, org.id, "1418 Cedar Springs Rd")
            say("")
            say("REVIEW READY")
            say("  organization  %s  (%s)" % (org.name, org.id))
            say("  login         %s / %s" % (EMAIL, PASSWORD))
            say("  database      %s" % os.environ.get("DATABASE_URL", "sqlite:///./advisorflow.db"))
            say("  flagship      http://localhost:5173/wholesale/evosense/property/%s"
                % (flag.id if flag else "-"))
            if links.get("seller"):
                say("  seller portal http://localhost:5173/my-property/%s" % links["seller"]["token"])
            if links.get("buyer"):
                say("  deal room     http://localhost:5173/investor/%s" % links["buyer"]["token"])
            return 0
        finally:
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
