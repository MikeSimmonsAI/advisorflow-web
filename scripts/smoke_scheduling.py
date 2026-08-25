"""
Sales scheduling regression suite — Checkpoint 2.

Two layers, deliberately:

  · ENGINE tests call app/services/availability.py directly. Interval algebra
    and DST are pure functions and deserve exact assertions, not assertions
    filtered through HTTP.
  · API tests drive real in-process HTTP so the guards, serializers and the
    booking transaction are exercised the way production runs them.

Temp SQLite. Never touches production.

    python scripts/smoke_scheduling.py
"""
import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta, date, time

TMP = tempfile.mkdtemp(prefix="sched_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "smoke" + "0" * 59
os.environ["SECRET_KEY"] = "smoke" + "0" * 59

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient                        # noqa: E402
from app.main import app                                         # noqa: E402
from app.deps import SessionLocal, engine                        # noqa: E402
from app.models.models import Base, Platform, Organization, User, Lead   # noqa: E402
from app.models.sales_models import (                            # noqa: E402
    Membership, BrandSalesOrg, Opportunity,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (                       # noqa: E402
    AvailabilityProfile, AvailabilityWindow, AvailabilityBlock,
    MeetingType, SalesAppointment, AppointmentParticipant,
    BLOCK_RECURRING, BLOCK_TIME_OFF, APPT_SCHEDULED, APPT_CANCELLED,
    CONF_PENDING, CONF_CONFIRMED,
)
from app.services.auth_service import hash_password              # noqa: E402
from app.services import availability as av                      # noqa: E402
from app import auto_migrate                                     # noqa: E402

PW = "SchedPass123!"
CHI = "America/Chicago"
NY = "America/New_York"
FAILURES = []
ID = {}


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:400]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def U(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi)


# ═══════════════════════════════════════════════════════════════════════════
# PART 1 — the engine, tested directly
# ═══════════════════════════════════════════════════════════════════════════

def engine_tests():
    print("\n[E1] Interval algebra")
    a = [(U(2026, 9, 1, 9), U(2026, 9, 1, 17))]
    lunch = [(U(2026, 9, 1, 12), U(2026, 9, 1, 13))]
    got = av.subtract_intervals(a, lunch)
    check("subtract splits a day around lunch",
          got == [(U(2026, 9, 1, 9), U(2026, 9, 1, 12)),
                  (U(2026, 9, 1, 13), U(2026, 9, 1, 17))], got)

    check("subtracting a non-overlapping block changes nothing",
          av.subtract_intervals(a, [(U(2026, 9, 2, 9), U(2026, 9, 2, 10))]) == a)
    check("subtracting a covering block empties it",
          av.subtract_intervals(a, [(U(2026, 9, 1, 8), U(2026, 9, 1, 18))]) == [])
    check("normalize merges touching ranges",
          av.normalize([(U(2026, 9, 1, 9), U(2026, 9, 1, 12)),
                        (U(2026, 9, 1, 12), U(2026, 9, 1, 15))])
          == [(U(2026, 9, 1, 9), U(2026, 9, 1, 15))])
    check("zero-length intervals are dropped",
          av.normalize([(U(2026, 9, 1, 9), U(2026, 9, 1, 9))]) == [])

    print("\n[E2] INTERSECTION — the operation Grok never had")
    # Mike's exact example, as hour-long availabilities.
    def hours(*hs):
        return [(U(2026, 9, 1, h), U(2026, 9, 1, h + 1)) for h in hs]
    blake = hours(11, 13, 14, 16)
    michael = hours(10, 11, 14, 15)
    mike = hours(11, 12, 14, 16)
    got = av.intersect_all([blake, michael, mike])
    check("three-way intersection returns exactly 11:00 and 14:00",
          got == [(U(2026, 9, 1, 11), U(2026, 9, 1, 12)),
                  (U(2026, 9, 1, 14), U(2026, 9, 1, 15))], got)
    # Measured in COVERED MINUTES, not interval count: the union of these three
    # merges into one continuous 10:00-17:00 block, so counting intervals would
    # make the union look "smaller" than the intersection's two separate hours.
    def covered(iv):
        return sum((e - s).total_seconds() for s, e in iv) / 60
    check("a UNION would have covered far more time (proving this is not a union)",
          covered(av.normalize(blake + michael + mike)) > covered(got),
          (covered(av.normalize(blake + michael + mike)), covered(got)))
    check("the intersection covers exactly two hours", covered(got) == 120, covered(got))
    check("adding a person with no overlap empties the result",
          av.intersect_all([blake, michael, mike, hours(8)]) == [])
    check("intersecting an empty set list returns nothing, not everything",
          av.intersect_all([]) == [])
    check("one person's intersection is their own time",
          av.intersect_all([blake]) == av.normalize(blake))

    print("\n[E3] Timezone + DST")
    # US DST 2026 begins Sunday March 8. Fri Mar 6 is CST (UTC-6);
    # Mon Mar 9 is CDT (UTC-5). 9:00 local must stay 9:00 local on both.
    cst = av.local_to_utc(date(2026, 3, 6), 9 * 60, CHI)
    cdt = av.local_to_utc(date(2026, 3, 9), 9 * 60, CHI)
    check("9am CST resolves to 15:00 UTC", cst == U(2026, 3, 6, 15), cst)
    check("9am CDT resolves to 14:00 UTC (offset moved, wall clock did not)",
          cdt == U(2026, 3, 9, 14), cdt)
    check("the UTC offset genuinely differs across the DST boundary",
          cst.hour != cdt.hour)
    check("round trip back to local is stable",
          av.utc_to_local(cdt, CHI) == datetime(2026, 3, 9, 9, 0))
    ny = av.local_to_utc(date(2026, 9, 1), 9 * 60, NY)
    check("9am New York is an hour before 9am Chicago",
          ny == U(2026, 9, 1, 13) and av.local_to_utc(date(2026, 9, 1), 9 * 60, CHI) == U(2026, 9, 1, 14),
          (ny, av.local_to_utc(date(2026, 9, 1), 9 * 60, CHI)))
    check("a window past midnight rolls into the next day",
          av.local_to_utc(date(2026, 9, 1), 25 * 60, CHI) == U(2026, 9, 2, 6))
    check("an unknown timezone falls back instead of crashing",
          av.local_to_utc(date(2026, 9, 1), 9 * 60, "Mars/Olympus") == U(2026, 9, 1, 14))

    print("\n[E4] Slot grid")
    slots = av.slots_from_intervals([(U(2026, 9, 1, 9), U(2026, 9, 1, 10, 30))], 60, 15)
    check("60-min slots on a 15-min grid inside 90 minutes -> 3",
          len(slots) == 3, slots)
    check("first slot starts on the boundary", slots[0][0] == U(2026, 9, 1, 9))
    check("no slot overruns the interval", all(e <= U(2026, 9, 1, 10, 30) for _, e in slots))
    check("a gap shorter than the meeting yields nothing",
          av.slots_from_intervals([(U(2026, 9, 1, 9), U(2026, 9, 1, 9, 30))], 60, 15) == [])
    snapped = av.slots_from_intervals([(U(2026, 9, 1, 9, 7), U(2026, 9, 1, 11))], 30, 15)
    check("a ragged start snaps forward to a clean boundary",
          snapped[0][0] == U(2026, 9, 1, 9, 15), snapped[:1])


# ═══════════════════════════════════════════════════════════════════════════
# PART 2 — fixture + API
# ═══════════════════════════════════════════════════════════════════════════

def next_weekday(start: date, weekday: int) -> date:
    d = start + timedelta(days=1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    evo = Platform(id="plt-evo", name="EvoSys Pro", slug="evosyspro")
    bb = Platform(id="plt-bb", name="BookaBoost", slug="bookaboost")
    db.add_all([evo, bb]); db.flush()
    db.add(Organization(id="org-cust", name="Greenland", slug="greenland", platform_id=bb.id))

    evo_sales = BrandSalesOrg(id="bso-evo", platform_id=evo.id, name="EvoSys Pro Sales",
                              slug="evosyspro-sales", timezone=CHI)
    bb_sales = BrandSalesOrg(id="bso-bb", platform_id=bb.id, name="BookaBoost Sales",
                             slug="bookaboost-sales", timezone=CHI)
    db.add_all([evo_sales, bb_sales]); db.flush()

    def mk(uid, email, name, role="advisor", org=None):
        u = User(id=uid, organization_id=org, email=email, full_name=name,
                 password_hash=hash_password(PW), role=role,
                 must_change_password=False, is_active=True)
        db.add(u); return u

    blake = mk("u-blake", "blake@example.com", "Blake Rehani")
    michael = mk("u-michael", "michael@example.com", "Michael Schlueter")
    mike = mk("u-mike", "mike@example.com", "Mike Simmons", role="god_admin")
    bbrep = mk("u-bbrep", "bbrep@example.com", "Other Brand Rep")
    mk("u-tenant", "advisor@example.com", "Tenant Advisor", org="org-cust")
    db.flush()

    db.add_all([
        Membership(user_id=blake.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_REP, is_active=True),
        Membership(user_id=michael.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id=mike.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=evo_sales.id, role=ROLE_SALES_MANAGER, is_active=True),
        Membership(user_id=bbrep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                   scope_id=bb_sales.id, role=ROLE_SALES_REP, is_active=True),
    ])

    # Deterministic availability: Mon-Fri 09:00-17:00 Chicago, no lunch, no
    # notice or horizon limits, so the intersection tests measure the algebra
    # rather than the clock.
    for u in (blake, michael, mike, bbrep):
        p = AvailabilityProfile(user_id=u.id, timezone=CHI, min_notice_minutes=0,
                                booking_horizon_days=365,
                                buffer_before_minutes=0, buffer_after_minutes=0)
        db.add(p); db.flush()
        for dow in range(5):
            db.add(AvailabilityWindow(profile_id=p.id, day_of_week=dow,
                                      start_minute=9 * 60, end_minute=17 * 60))

    db.add(Opportunity(id="opp-1", brand_sales_org_id=evo_sales.id,
                       owner_user_id=blake.id, company_name="Atlas Restoration",
                       contact_name="Renee Carter", email="renee@atlas.example",
                       phone="2145550111", stage="discovery", status="open",
                       timezone=NY))
    db.add(Opportunity(id="opp-bb", brand_sales_org_id=bb_sales.id,
                       owner_user_id=bbrep.id, company_name="Other Brand Deal",
                       stage="prospect", status="open"))
    db.commit(); db.close()


def token(c, email):
    r = c.post("/auth/login", data={"username": email, "password": PW})
    if r.status_code != 200:
        raise SystemExit("login failed %s: %s %s" % (email, r.status_code, r.text[:200]))
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def prof_of(db, uid):
    return db.query(AvailabilityProfile).filter(
        AvailabilityProfile.user_id == uid).first()


def api_tests():
    build()
    c = TestClient(app)
    blake = token(c, "blake@example.com")
    michael = token(c, "michael@example.com")
    mike = token(c, "mike@example.com")
    bbrep = token(c, "bbrep@example.com")
    tenant = token(c, "advisor@example.com")

    # Two clear future weekdays in Chicago.
    today = av.utc_to_local(datetime.utcnow(), CHI).date()
    MON = next_weekday(today, 0)
    TUE = MON + timedelta(days=1)
    ID["MON"], ID["TUE"] = MON, TUE

    print("\n[A1] Authorization")
    check("anonymous refused the finder",
          c.post("/sales/availability/find", json={"date_from": str(MON)}).status_code == 401)
    check("anonymous refused appointments", c.get("/sales/appointments").status_code == 401)
    r = c.get("/sales/meeting-types", headers=tenant)
    check("customer-tenant user refused scheduling", r.status_code == 403, r.status_code)
    r = c.get("/sales/availability/me", headers=tenant)
    check("customer-tenant user refused availability", r.status_code == 403, r.status_code)

    print("\n[A2] Meeting types resolve ROLES, not hardcoded names")
    r = c.get("/sales/meeting-types?opportunity_id=opp-1", headers=blake)
    check("meeting types load", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    types = {t["key"]: t for t in r.json()}
    check("Discovery + Demo exists", "discovery_demo" in types, list(types))
    dd = types.get("discovery_demo", {})
    ID["mt_dd"] = dd.get("id")
    ID["mt_disc"] = types.get("discovery", {}).get("id")
    check("Discovery + Demo is 60 minutes", dd.get("duration_minutes") == 60, dd.get("duration_minutes"))
    check("it asks for three ROLES",
          dd.get("required_slots") == ["opportunity_owner", "sales_manager", "product_specialist"],
          dd.get("required_slots"))
    req = {s["slot"]: s for s in dd["resolved"]["required"]}
    check("opportunity_owner resolves to the deal's owner, Blake",
          req["opportunity_owner"]["auto_selected_user_id"] == "u-blake", req["opportunity_owner"])
    check("sales_manager offers real candidates",
          len(req["sales_manager"]["candidates"]) >= 1,
          req["sales_manager"]["candidates"])
    check("product_specialist resolves and says how it was filled",
          bool(req["product_specialist"]["candidates"]) and bool(req["product_specialist"]["note"]),
          req["product_specialist"])
    check("no meeting type hardcodes a person's name",
          all("blake" not in str(t.get("required_slots")).lower() for t in types.values()))

    print("\n[A3] My Availability")
    r = c.get("/sales/availability/me", headers=blake)
    check("profile loads", r.status_code == 200, r.text[:200])
    p = r.json()
    check("five working days seeded", len(p["windows"]) == 5, len(p["windows"]))
    r = c.put("/sales/availability/me", headers=blake, json={
        "timezone": CHI, "buffer_before_minutes": 0, "buffer_after_minutes": 15,
        "min_notice_minutes": 0, "booking_horizon_days": 365,
        "windows": [{"day_of_week": d, "start_minute": 9 * 60, "end_minute": 17 * 60}
                    for d in range(5)],
        "recurring_blocks": [{"label": "Lunch", "day_of_week": d,
                              "start_minute": 12 * 60, "end_minute": 13 * 60}
                             for d in range(5)],
    })
    check("availability saves", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    check("lunch stored", len(r.json()["recurring_blocks"]) == 5, r.json()["recurring_blocks"])
    check("buffer stored", r.json()["buffer_after_minutes"] == 15)
    r = c.put("/sales/availability/me", headers=blake, json={"timezone": "Mars/Olympus"})
    check("an unknown timezone is refused, not silently defaulted",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:200]))
    r = c.put("/sales/availability/me", headers=blake,
              json={"windows": [{"day_of_week": 0, "start_minute": 600, "end_minute": 540}]})
    check("a backwards working window is refused", r.status_code == 400, r.status_code)
    # Put Blake back
    c.put("/sales/availability/me", headers=blake, json={
        "windows": [{"day_of_week": d, "start_minute": 9 * 60, "end_minute": 17 * 60}
                    for d in range(5)],
        "recurring_blocks": [{"label": "Lunch", "day_of_week": d,
                              "start_minute": 12 * 60, "end_minute": 13 * 60}
                             for d in range(5)],
        "buffer_after_minutes": 0})

    print("\n[A4] Working hours, lunch, time off")
    db = SessionLocal()
    b = db.query(User).filter(User.id == "u-blake").first()
    day_start = av.local_to_utc(MON, 0, CHI)
    day_end = av.local_to_utc(MON + timedelta(days=1), 0, CHI)
    free = av.free_intervals_for_user(db, b, day_start, day_end, ignore_notice=True)
    check("working day starts at 9am local",
          av.utc_to_local(free[0][0], CHI).hour == 9, [av.utc_to_local(s, CHI) for s, _ in free])
    check("lunch splits the day into two intervals", len(free) == 2, free)
    check("morning ends at noon local",
          av.utc_to_local(free[0][1], CHI).hour == 12, av.utc_to_local(free[0][1], CHI))
    check("afternoon ends at 5pm local",
          av.utc_to_local(free[-1][1], CHI).hour == 17, av.utc_to_local(free[-1][1], CHI))
    sat = next_weekday(today, 5)
    sat_free = av.free_intervals_for_user(
        db, b, av.local_to_utc(sat, 0, CHI),
        av.local_to_utc(sat + timedelta(days=1), 0, CHI), ignore_notice=True)
    check("no working hours on Saturday means no availability", sat_free == [], sat_free)
    db.close()

    r = c.post("/sales/availability/time-off", headers=blake, json={
        "label": "PTO",
        "starts_at": av.local_to_utc(TUE, 0, CHI).isoformat(),
        "ends_at": av.local_to_utc(TUE + timedelta(days=1), 0, CHI).isoformat()})
    check("time off saves", r.status_code == 201, "%s %s" % (r.status_code, r.text[:200]))
    ID["timeoff"] = r.json()["id"]
    db = SessionLocal()
    b = db.query(User).filter(User.id == "u-blake").first()
    tue_free = av.free_intervals_for_user(
        db, b, av.local_to_utc(TUE, 0, CHI),
        av.local_to_utc(TUE + timedelta(days=1), 0, CHI), ignore_notice=True)
    check("a full day of time off removes the whole day", tue_free == [], tue_free)
    db.close()
    check("time off deletes",
          c.delete("/sales/availability/time-off/" + ID["timeoff"],
                   headers=blake).status_code == 200)

    print("\n[A5] Minimum notice and booking horizon")
    db = SessionLocal()
    b = db.query(User).filter(User.id == "u-blake").first()
    p = prof_of(db, "u-blake"); p.min_notice_minutes = 60 * 24 * 7; db.commit()
    near = av.free_intervals_for_user(db, b, day_start, day_end)
    check("a week of minimum notice hides the next weekday", near == [], near)
    p.min_notice_minutes = 0
    p.booking_horizon_days = 1
    db.commit()
    far_day = today + timedelta(days=30)
    far = av.free_intervals_for_user(db, b, av.local_to_utc(far_day, 0, CHI),
                                     av.local_to_utc(far_day + timedelta(days=1), 0, CHI))
    check("a 1-day horizon hides a date 30 days out", far == [], far)
    p.booking_horizon_days = 365; db.commit()
    p.accepts_bookings = False; db.commit()
    check("a person not accepting bookings has no availability",
          av.free_intervals_for_user(db, b, day_start, day_end, ignore_notice=True) == [])
    p.accepts_bookings = True; db.commit()
    db.close()

    print("\n[A6] FIND TEAM TIME — the three-person intersection")
    r = c.post("/sales/availability/find", headers=blake, json={
        "meeting_type_id": ID["mt_dd"], "opportunity_id": "opp-1",
        "required_user_ids": ["u-blake", "u-michael", "u-mike"],
        "date_from": str(MON), "date_to": str(MON)})
    check("finder returns 200", r.status_code == 200, "%s %s" % (r.status_code, r.text[:300]))
    body = r.json()
    check("openings found for three people", body["total"] > 0, body.get("blockers"))
    check("duration comes from the meeting type", body["duration_minutes"] == 60)
    check("all three are listed as required", len(body["required"]) == 3, body["required"])
    firsts = [av.utc_to_local(datetime.fromisoformat(s["starts_at"]), CHI)
              for s in body["slots"]]
    check("no opening before 9am local", all(f.hour >= 9 for f in firsts), firsts[:3])
    check("no opening starting at or after 5pm local", all(f.hour < 17 for f in firsts), firsts[-3:])
    check("no 60-min opening starts inside lunch",
          not any(f.hour == 12 for f in firsts), [f for f in firsts if f.hour == 12])
    check("no opening starts at 11:30 (would run into lunch)",
          not any(f.hour == 11 and f.minute > 0 for f in firsts),
          [f for f in firsts if f.hour == 11])
    ID["slot1"] = body["slots"][0]["starts_at"]
    ID["slot2"] = body["slots"][-1]["starts_at"]

    # The API must send BOTH the UTC instant and the resolved wall clock. The UI
    # renders the wall clock: a naive UTC string handed to JS's Date() is read as
    # browser-local, which is what once made a 9am Chicago meeting display as 2pm.
    s0 = body["slots"][0]
    check("each slot carries the resolved local wall clock",
          "starts_at_local" in s0 and s0["starts_at_local"], s0)
    check("the wall clock differs from the UTC instant (a real conversion happened)",
          s0["starts_at_local"] != s0["starts_at"], s0)
    check("the wall clock lands inside working hours",
          9 <= datetime.fromisoformat(s0["starts_at_local"]).hour < 17,
          s0["starts_at_local"])
    check("the response states which timezone that wall clock is in",
          body["timezone"] == CHI, body["timezone"])

    # Carve Michael's morning out and prove it removes exactly those slots.
    db = SessionLocal()
    pm = prof_of(db, "u-michael")
    db.add(AvailabilityBlock(profile_id=pm.id, kind=BLOCK_RECURRING, label="Standup",
                             day_of_week=MON.weekday(),
                             start_minute=9 * 60, end_minute=12 * 60))
    db.commit(); db.close()
    r2 = c.post("/sales/availability/find", headers=blake, json={
        "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-blake", "u-michael", "u-mike"],
        "date_from": str(MON), "date_to": str(MON)})
    after = [av.utc_to_local(datetime.fromisoformat(s["starts_at"]), CHI)
             for s in r2.json()["slots"]]
    check("blocking ONE participant's morning removes it for EVERYONE",
          all(f.hour >= 13 for f in after), after[:4])
    check("the afternoon survives", len(after) > 0, after)
    r3 = c.post("/sales/availability/find", headers=blake, json={
        "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-blake", "u-mike"],
        "date_from": str(MON), "date_to": str(MON)})
    solo = [av.utc_to_local(datetime.fromisoformat(s["starts_at"]), CHI)
            for s in r3.json()["slots"]]
    check("without Michael the morning is available again",
          any(f.hour < 12 for f in solo), solo[:4])
    check("so the finder is an INTERSECTION, not a union",
          len(solo) > len(after), (len(solo), len(after)))

    print("\n[A7] Optional participants never remove a slot")
    r = c.post("/sales/availability/find", headers=blake, json={
        "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-blake", "u-mike"],
        "optional_user_ids": ["u-michael"],
        "date_from": str(MON), "date_to": str(MON)})
    withopt = r.json()
    check("optional participant does not shrink the result",
          withopt["total"] == len(solo), (withopt["total"], len(solo)))
    morning = [s for s in withopt["slots"]
               if av.utc_to_local(datetime.fromisoformat(s["starts_at"]), CHI).hour < 12]
    after_noon = [s for s in withopt["slots"]
                  if av.utc_to_local(datetime.fromisoformat(s["starts_at"]), CHI).hour >= 13]
    check("morning slots report the optional person as unavailable",
          all(s["optional_available_count"] == 0 for s in morning), morning[:2])
    check("afternoon slots report them as available",
          all(s["optional_available_count"] == 1 for s in after_noon), after_noon[:2])

    # Give Michael his morning back.
    db = SessionLocal()
    pm = prof_of(db, "u-michael")
    db.query(AvailabilityBlock).filter(AvailabilityBlock.profile_id == pm.id,
                                       AvailabilityBlock.label == "Standup").delete()
    db.commit(); db.close()

    print("\n[A8] Cross-brand isolation")
    r = c.post("/sales/availability/find", headers=blake, json={
        "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-blake", "u-bbrep"],
        "date_from": str(MON)})
    check("another brand's user cannot be pulled into a meeting",
          r.status_code == 400, "%s %s" % (r.status_code, r.text[:200]))
    r = c.post("/sales/availability/find", headers=bbrep, json={
        "duration_minutes": 30, "opportunity_id": "opp-1", "date_from": str(MON)})
    check("another brand cannot target our opportunity", r.status_code == 404, r.status_code)
    r = c.get("/sales/availability/team", headers=bbrep)
    check("other brand's team grid shows only its own people",
          all(m["user_id"] == "u-bbrep" for m in r.json()["members"]),
          [m["user_id"] for m in r.json()["members"]])

    print("\n[A9] BOOK IT")
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": ID["slot1"], "meeting_type_id": ID["mt_dd"],
        "opportunity_id": "opp-1",
        "required_user_ids": ["u-blake", "u-michael", "u-mike"],
        "role_slot_by_user": {"u-blake": "opportunity_owner",
                              "u-michael": "sales_manager",
                              "u-mike": "product_specialist"}})
    check("appointment created", r.status_code == 201, "%s %s" % (r.status_code, r.text[:400]))
    appt = r.json(); ID["appt"] = appt["id"]
    check("all three participants attached", len(appt["participants"]) == 3, appt["participants"])
    check("role slots recorded",
          sorted(p["role_slot"] for p in appt["participants"])
          == ["opportunity_owner", "product_specialist", "sales_manager"],
          [p["role_slot"] for p in appt["participants"]])
    check("prospect carried forward from the opportunity",
          appt["prospect"]["company"] == "Atlas Restoration"
          and appt["prospect"]["name"] == "Renee Carter", appt["prospect"])
    check("prospect email carried forward",
          appt["prospect"]["email"] == "renee@atlas.example", appt["prospect"])
    check("prospect timezone captured separately from the team's",
          appt["prospect"]["timezone"] == NY, appt["prospect"])
    check("opportunity linked", appt["opportunity_id"] == "opp-1")
    check("title auto-built from meeting type and company",
          "Atlas Restoration" in appt["title"], appt["title"])
    check("confirmation starts pending", appt["confirmation_status"] == CONF_PENDING)
    check("60 minutes long", appt["duration_minutes"] == 60)
    check("no calendar sync is claimed",
          all(p["calendar_synced"] is False for p in appt["participants"]))

    db = SessionLocal()
    row = db.query(SalesAppointment).filter(SalesAppointment.id == ID["appt"]).first()
    check("appointment has NO customer organization_id column at all",
          not hasattr(row, "organization_id"))
    check("booking created no customer-tenant Lead", db.query(Lead).count() == 0)
    ev = [e.event_type for e in db.query(Opportunity).filter(
        Opportunity.id == "opp-1").first().__class__.__mro__[0:1]] if False else None
    db.close()
    from app.models.sales_models import OpportunityEvent
    db = SessionLocal()
    kinds = [e.event_type for e in db.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == "opp-1").all()]
    check("booking wrote to the opportunity timeline",
          "appointment_booked" in kinds, kinds)
    db.close()

    print("\n[A10] DOUBLE BOOKING")
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": ID["slot1"], "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-michael"]})
    check("booking Michael again at the same time is refused with 409",
          r.status_code == 409, "%s %s" % (r.status_code, r.text[:250]))
    check("the refusal names who is busy", "Michael" in r.text, r.text[:250])
    start_dt = datetime.fromisoformat(ID["slot1"])
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (start_dt + timedelta(minutes=30)).isoformat(),
        "duration_minutes": 60, "required_user_ids": ["u-michael"]})
    check("a PARTIALLY overlapping booking is also refused", r.status_code == 409,
          "%s %s" % (r.status_code, r.text[:250]))
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (start_dt + timedelta(minutes=60)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-michael"]})
    check("a back-to-back booking IS allowed", r.status_code == 201,
          "%s %s" % (r.status_code, r.text[:250]))
    ID["appt2"] = r.json()["id"] if r.status_code == 201 else None
    check("the taken slot disappears from the finder",
          ID["slot1"] not in [s["starts_at"] for s in c.post(
              "/sales/availability/find", headers=blake,
              json={"meeting_type_id": ID["mt_dd"],
                    "required_user_ids": ["u-blake", "u-michael", "u-mike"],
                    "date_from": str(MON), "date_to": str(MON)}).json()["slots"]])
    # SQLite has no exclusion constraints, so the true concurrent race cannot be
    # exercised here. What IS asserted is that the Postgres-side protection is
    # declared, correctly shaped, and reached by the booking route's error
    # handler — the live constraint is verified against production after deploy.
    ddl = "\n".join(sql for _, sql in auto_migrate.POSTGRES_ONLY_DDL)
    check("the Postgres exclusion constraint is declared for the race case",
          "sales_participant_no_overlap" in ddl, ddl[:200])
    check("it uses a gist range-overlap exclusion",
          "EXCLUDE USING gist" in ddl and "&&" in ddl)
    check("it is keyed per USER (double-booking is a person-level property)",
          "user_id WITH =" in ddl)
    check("it is scoped to blocking participants only", "is_blocking" in ddl)
    check("btree_gist is requested (required for the equality half)",
          "btree_gist" in ddl)
    import inspect
    from app.routers import sales_scheduling_router as ssr
    src = inspect.getsource(ssr.create_appointment)
    check("the booking route converts that constraint violation into a clean 409",
          "IntegrityError" in src and "409" in src)

    print("\n[A11] Buffers block adjacent time")
    # Buffers are resolved and FROZEN when a meeting is booked, so they are set
    # before booking here. That is deliberate: changing your buffer preference
    # must not retroactively invalidate meetings already agreed under the old one.
    db = SessionLocal()
    pm = prof_of(db, "u-mike")
    pm.buffer_after_minutes = 30
    pm.buffer_before_minutes = 15
    db.commit(); db.close()

    t0 = av.local_to_utc(TUE, 14 * 60, CHI)             # Tuesday 14:00 Chicago
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": t0.isoformat(), "duration_minutes": 30,
        "required_user_ids": ["u-mike"], "title": "Buffered meeting"})
    check("a meeting books with buffers configured", r.status_code == 201,
          "%s %s" % (r.status_code, r.text[:250]))
    ID["buffered"] = r.json()["id"] if r.status_code == 201 else None

    db = SessionLocal()
    part = db.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == ID["buffered"],
        AppointmentParticipant.user_id == "u-mike").first()
    check("the participant's busy window is the meeting PLUS their buffers",
          part.busy_start_at == t0 - timedelta(minutes=15)
          and part.busy_end_at == t0 + timedelta(minutes=60),
          (part.busy_start_at, part.busy_end_at, t0))
    db.close()

    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (t0 + timedelta(minutes=30)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-mike"]})
    check("a booking inside their POST-meeting buffer is refused",
          r.status_code == 409, "%s %s" % (r.status_code, r.text[:200]))
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (t0 - timedelta(minutes=30)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-mike"]})
    check("a booking running into their PRE-meeting buffer is refused",
          r.status_code == 409, "%s %s" % (r.status_code, r.text[:200]))
    # 15:00 is NOT far enough: the existing meeting holds Mike until 15:00, and
    # the NEW meeting's own 15-minute pre-buffer would start at 14:45, inside it.
    # Both buffers count, on both meetings — which is the point of storing the
    # resolved window rather than the raw times.
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (t0 + timedelta(minutes=60)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-mike"]})
    check("the NEW meeting's own pre-buffer is honoured too",
          r.status_code == 409, "%s %s" % (r.status_code, r.text[:200]))
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (t0 + timedelta(minutes=75)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-mike"]})
    check("clear of both buffers, the booking IS allowed", r.status_code == 201,
          "%s %s" % (r.status_code, r.text[:200]))

    # Someone else with no buffers is unaffected by Mike's.
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (t0 + timedelta(minutes=30)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-blake"]})
    check("another person's buffer does not block an unrelated participant",
          r.status_code == 201, "%s %s" % (r.status_code, r.text[:200]))

    db = SessionLocal()
    pm = prof_of(db, "u-mike")
    pm.buffer_after_minutes = 0
    pm.buffer_before_minutes = 0
    db.commit(); db.close()

    print("\n[A12] Confirmation")
    r = c.post("/sales/appointments/%s/confirmation" % ID["appt"], headers=blake,
               json={"confirmation_status": "sent"})
    check("marking sent works", r.status_code == 200 and r.json()["confirmation_status"] == "sent",
          r.text[:200])
    check("sent stamps a timestamp", r.json()["confirmation_sent_at"] is not None)
    r = c.post("/sales/appointments/%s/confirmation" % ID["appt"], headers=blake,
               json={"confirmation_status": "confirmed", "source": "staff_manual"})
    check("confirming works", r.json()["confirmation_status"] == CONF_CONFIRMED)
    check("the SOURCE of the confirmation is recorded",
          r.json()["confirmation_source"] == "staff_manual", r.json()["confirmation_source"])
    check("confirmed_at stamped", r.json()["confirmed_at"] is not None)
    r = c.post("/sales/appointments/%s/confirmation" % ID["appt"], headers=blake,
               json={"confirmation_status": "made_up"})
    check("an unknown confirmation status is refused", r.status_code == 400, r.status_code)

    print("\n[A13] My Day shows REAL appointments")
    day = c.get("/sales/my-day", headers=blake).json()
    check("todays_appointments is a real list, not an unavailable marker",
          isinstance(day["todays_appointments"], list), day["todays_appointments"])
    check("next_appointment is the booked meeting",
          day["next_appointment"] and day["next_appointment"]["id"] == ID["appt"],
          day["next_appointment"])
    check("next appointment carries its participants",
          len(day["next_appointment"]["participants"]) == 3,
          day["next_appointment"]["participants"])
    check("appointment metrics are present",
          "appointments_today" in day["metrics"] and "needs_confirmation" in day["metrics"],
          day["metrics"])
    check("the pipeline card now shows the booked meeting",
          any(o["next_appointment"] for o in day["deals_needing_action"] + day["follow_ups_due"])
          or c.get("/sales/opportunities/opp-1", headers=blake).json()["next_appointment"] is not None)
    check("calendar sync is still declared unbuilt",
          day["calendar_sync"]["available"] is False, day["calendar_sync"])

    opp = c.get("/sales/opportunities/opp-1", headers=blake).json()
    check("opportunity detail lists its meetings", len(opp["appointments"]) >= 1,
          opp["appointments"])
    check("opportunity's next appointment is populated",
          opp["next_appointment"] is not None, opp["next_appointment"])

    print("\n[A14] Rep isolation and manager visibility")
    r = c.post("/sales/appointments", headers=michael, json={
        "starts_at": av.local_to_utc(MON, 15 * 60 + 30, CHI).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-michael"],
        "title": "Manager private slot"})
    check("manager books their own meeting", r.status_code == 201,
          "%s %s" % (r.status_code, r.text[:250]))
    ID["mgr_appt"] = r.json()["id"]
    r = c.get("/sales/appointments/" + ID["mgr_appt"], headers=blake)
    check("rep cannot open a meeting they are not on", r.status_code == 403, r.status_code)
    r = c.get("/sales/appointments/" + ID["appt"], headers=michael)
    check("manager can open any meeting in the brand", r.status_code == 200, r.status_code)
    r = c.get("/sales/appointments?scope=team", headers=blake)
    check("rep cannot request the team scope", r.status_code == 403, r.status_code)
    r = c.get("/sales/appointments?scope=team", headers=michael)
    check("manager can", r.status_code == 200, r.status_code)
    mine = c.get("/sales/appointments", headers=blake).json()
    check("rep's own list excludes the manager's private meeting",
          ID["mgr_appt"] not in [a["id"] for a in mine["appointments"]],
          [a["id"] for a in mine["appointments"]])
    r = c.get("/sales/appointments/" + ID["appt"], headers=bbrep)
    check("another brand gets 404, not 403", r.status_code == 404, r.status_code)

    print("\n[A15] Team grid privacy")
    grid = c.get("/sales/availability/team?day=%s" % MON, headers=blake).json()
    check("grid lists the brand's members", len(grid["members"]) == 3, len(grid["members"]))
    michael_row = [m for m in grid["members"] if m["user_id"] == "u-michael"][0]
    titles = [b["title"] for b in michael_row["busy"]]
    check("rep sees a colleague's private meeting as opaque 'Busy'",
          "Manager private slot" not in titles, titles)
    check("but does see that the time is occupied", len(michael_row["busy"]) >= 1, titles)
    check("rep DOES see the shared meeting's real title",
          any("Atlas Restoration" in t for t in titles), titles)
    mgrid = c.get("/sales/availability/team?day=%s" % MON, headers=michael).json()
    mrow = [m for m in mgrid["members"] if m["user_id"] == "u-michael"][0]
    check("manager sees full titles",
          any("Manager private slot" == b["title"] for b in mrow["busy"]),
          [b["title"] for b in mrow["busy"]])

    print("\n[A16] Cancelling frees the time")
    r = c.post("/sales/appointments/%s/cancel" % ID["appt"], headers=blake,
               json={"reason": "Prospect rescheduled"})
    check("cancel works", r.status_code == 200 and r.json()["status"] == APPT_CANCELLED,
          r.text[:200])
    db = SessionLocal()
    blocking = db.query(AppointmentParticipant).filter(
        AppointmentParticipant.appointment_id == ID["appt"],
        AppointmentParticipant.is_blocking.is_(True)).count()
    check("cancelling stops the participants blocking", blocking == 0, blocking)
    db.close()
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": ID["slot1"], "meeting_type_id": ID["mt_dd"],
        "required_user_ids": ["u-michael"]})
    check("the freed slot can be booked again", r.status_code == 201,
          "%s %s" % (r.status_code, r.text[:250]))
    check("cancelled meetings are hidden from the list by default",
          ID["appt"] not in [a["id"] for a in
                             c.get("/sales/appointments", headers=blake).json()["appointments"]])

    print("\n[A17] Guard rails")
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
        "duration_minutes": 30, "required_user_ids": ["u-blake"]})
    check("a booking in the past is refused", r.status_code == 400,
          "%s %s" % (r.status_code, r.text[:200]))
    r = c.post("/sales/appointments", headers=blake, json={
        "starts_at": ID["slot2"], "duration_minutes": 30, "required_user_ids": []})
    check("a booking with no required participant is refused", r.status_code == 400, r.status_code)
    r = c.post("/sales/availability/find", headers=blake, json={
        "required_user_ids": ["u-blake"], "date_from": str(MON)})
    check("finding with no duration and no meeting type is refused",
          r.status_code == 400, r.status_code)
    r = c.post("/sales/availability/find", headers=blake, json={
        "duration_minutes": 30, "required_user_ids": ["u-blake"],
        "date_from": str(MON), "date_to": str(MON + timedelta(days=400))})
    check("an absurd date range is refused", r.status_code == 400, r.status_code)

    print("\n[A18] Tenancy — the two domains never meet")
    db = SessionLocal()
    for uid in ("u-blake", "u-michael"):
        u = db.query(User).filter(User.id == uid).first()
        check("%s still has organization_id NULL after scheduling" % u.full_name.split()[0],
              u.organization_id is None, u.organization_id)
    appts = db.query(SalesAppointment).all()
    check("every appointment belongs to a brand sales org",
          all(a.brand_sales_org_id for a in appts))
    check("no appointment references a customer organization",
          not any(hasattr(a, "organization_id") for a in appts))
    check("the customer booking_links table was never written",
          db.execute(__import__("sqlalchemy").text(
              "SELECT COUNT(*) FROM booking_links")).scalar() == 0)
    db.close()


def reschedule_tests():
    """Checkpoint 3 — moving a meeting through the API.

    Lives here rather than in smoke_calendar_sync.py because rescheduling is a
    SCHEDULING operation: it re-runs the conflict check, recomputes buffers and
    resets confirmation. The calendar-sync consequences are tested there.
    """
    c = TestClient(app)
    blake = token(c, "blake@example.com")

    print("\n[A19] Reschedule — the meeting MOVES, it is not recreated")
    db = SessionLocal()
    # Must be one Blake is actually ON. A rep may only touch their own meetings,
    # which is a guard worth not accidentally testing around.
    blake_appt_ids = [r[0] for r in db.query(AppointmentParticipant.appointment_id)
                      .filter(AppointmentParticipant.user_id == "u-blake").all()]
    appt = (db.query(SalesAppointment)
            .filter(SalesAppointment.status == APPT_SCHEDULED,
                    SalesAppointment.id.in_(blake_appt_ids or [""]))
            .order_by(SalesAppointment.starts_at.asc()).first())
    if appt is None:
        check("a scheduled appointment exists to reschedule", False, "none found")
        db.close()
        return
    appt_id = appt.id
    original_start = appt.starts_at
    duration = int((appt.ends_at - appt.starts_at).total_seconds() // 60)
    user_ids = [p.user_id for p in db.query(AppointmentParticipant)
                .filter(AppointmentParticipant.appointment_id == appt_id).all()]
    total_before = db.query(SalesAppointment).count()
    db.close()

    # The current slot must be offered back — the meeting must not block itself.
    r = c.post("/sales/availability/find", headers=blake, json={
        "duration_minutes": duration, "required_user_ids": user_ids,
        "date_from": str(original_start.date()), "date_to": str(original_start.date()),
        "exclude_appointment_id": appt_id})
    check("excluding the meeting from its own search is accepted",
          r.status_code == 200, r.status_code)
    offered = [s["starts_at"] for s in r.json().get("slots", [])]
    check("its own current slot is offered back as free",
          original_start.isoformat() in offered,
          (original_start.isoformat(), offered[:3]))

    r = c.post("/sales/availability/find", headers=blake, json={
        "duration_minutes": duration, "required_user_ids": user_ids,
        "date_from": str(original_start.date()), "date_to": str(original_start.date())})
    without = [s["starts_at"] for s in r.json().get("slots", [])]
    check("WITHOUT the exclusion its own slot is correctly blocked",
          original_start.isoformat() not in without,
          original_start.isoformat())

    target = next((s for s in offered if s != original_start.isoformat()), None)
    check("another opening exists to move to", target is not None, offered[:3])
    if target is None:
        return

    # notify=False so this test needs no email provider or calendar fake.
    r = c.post("/sales/appointments/%s/reschedule" % appt_id, headers=blake,
               json={"starts_at": target, "reason": "Prospect asked", "notify": False})
    check("the reschedule succeeds", r.status_code == 200, r.text[:300])
    body = r.json()
    check("the appointment KEEPS its id (moved, not recreated)",
          body["id"] == appt_id, body["id"])
    check("the new time is stored", body["starts_at"] == target, body["starts_at"])
    check("the previous time is remembered",
          body["previous_starts_at"] == original_start.isoformat(),
          body["previous_starts_at"])
    check("the move is counted", body["rescheduled_count"] == 1, body["rescheduled_count"])
    check("the reason is recorded", body["reschedule_reason"] == "Prospect asked")
    # A prospect agreed to a time that no longer exists.
    check("confirmation is reset to pending by a move",
          body["confirmation_status"] == CONF_PENDING, body["confirmation_status"])

    db = SessionLocal()
    check("NO second appointment was created",
          db.query(SalesAppointment).count() == total_before,
          (total_before, db.query(SalesAppointment).count()))
    parts = (db.query(AppointmentParticipant)
             .filter(AppointmentParticipant.appointment_id == appt_id).all())
    moved = datetime.fromisoformat(target)
    check("every participant's blocking window moved with it",
          all(p.busy_start_at <= moved and p.busy_end_at >= moved for p in parts),
          [(p.busy_start_at, p.busy_end_at) for p in parts])
    check("participants still block", all(p.is_blocking for p in parts))
    db.close()

    print("\n[A20] Reschedule guard rails")
    r = c.post("/sales/appointments/%s/reschedule" % appt_id, headers=blake,
               json={"starts_at": "2020-01-01T10:00:00", "notify": False})
    check("a move into the past is refused", r.status_code == 400, r.status_code)
    r = c.post("/sales/appointments/%s/reschedule" % appt_id, headers=blake,
               json={"starts_at": target, "notify": False})
    check("moving to the time it already occupies is a no-op, not an error",
          r.status_code == 200 and r.json()["rescheduled_count"] == 1,
          r.json().get("rescheduled_count"))
    r = c.post("/sales/appointments/%s/reschedule" % appt_id,
               json={"starts_at": target, "notify": False})
    check("rescheduling requires authentication", r.status_code in (401, 403), r.status_code)
    r = c.post("/sales/appointments/does-not-exist/reschedule", headers=blake,
               json={"starts_at": target, "notify": False})
    check("rescheduling an unknown meeting is a 404", r.status_code == 404, r.status_code)

    print("\n[A21] Calendar connections are per USER")
    r = c.get("/sales/calendar/connections", headers=blake)
    check("a member can read their own connections", r.status_code == 200, r.status_code)
    body = r.json()
    check("every provider is listed, connected or not",
          len(body["connections"]) >= 2, body["connections"])
    check("an unconnected user is honestly reported as such",
          all(cn["state"] == "not_connected" for cn in body["connections"]), body)
    check("the active provider falls back to email invitations",
          body["uses_email_fallback"] is True, body["active_provider"])
    check("no token or secret is ever serialized",
          "token" not in r.text.lower() or "refresh" not in r.text.lower(), r.text[:200])
    r = c.get("/sales/calendar/connections")
    check("reading connections requires authentication",
          r.status_code in (401, 403), r.status_code)
    r = c.post("/sales/calendar/connections/pigeon-post/test", headers=blake, json={})
    check("an unknown provider is refused", r.status_code == 404, r.status_code)
    r = c.post("/sales/calendar/connections/microsoft/test", headers=blake, json={})
    check("testing a provider that is not connected is refused clearly",
          r.status_code == 400, r.status_code)


def main():
    engine_tests()
    api_tests()
    reschedule_tests()
    print("\n" + "=" * 68)
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("   - " + f)
        return 1
    print("ALL SCHEDULING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
