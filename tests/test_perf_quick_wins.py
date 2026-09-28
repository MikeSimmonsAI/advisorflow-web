"""Performance quick wins: list endpoints that used to issue one or two SQL
statements PER ROW now batch-load what each row needs.

Every test here does two things:
  * pins the OUTPUT on a small fixture (so the batching changed nothing a
    caller can see), and
  * counts the SQL statements the request issues (an engine-level
    `before_cursor_execute` listener) and asserts the count does not grow
    with the number of rows - seeding more rows must not add queries.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.models.models import (BookingLink, CRMContact, EmailMessage, Lead, LeadOutcome, Message,
                               Organization, PipelineConversation, User)
from app.models.wholesale_models import (WholesaleBuyer, WholesaleDeal, WholesaleProperty,
                                         WholesaleSellerProfile, WholesaleWorkException)
from app.routers.auto_send_router import AutoSendItem
from app.services import wholesale_exceptions as EX
from app.services.auth_service import create_access_token, hash_password


# ── helpers ──────────────────────────────────────────────────────────────────

@contextmanager
def count_queries(db_session):
    engine = db_session.get_bind()
    seen = []

    def _count(conn, cursor, statement, params, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def ok(r):
    assert r.status_code in (200, 201), "%s %s" % (r.status_code, r.text[:400])
    return r.json()


def _h(db, u):
    return {"Authorization": "Bearer %s" % create_access_token(u, db)}


def _user(db, org, role, email, full_name=None):
    u = User(organization_id=org.id, email=email, password_hash=hash_password("Pass12345!"),
             full_name=full_name or email.split("@")[0], role=role, must_change_password=False)
    db.add(u)
    db.commit()
    return u


_n = [0]


def _lead(db, org, advisor, first="Pat", last=None):
    _n[0] += 1
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id if advisor else None,
                first_name=first, last_name=last or "L%d" % _n[0],
                phone="1214555%04d" % _n[0], status="new")
    db.add(lead)
    db.commit()
    return lead


def _counted_get(client, db_session, url, headers):
    with count_queries(db_session) as seen:
        body = ok(client.get(url, headers=headers))
    return body, len(seen)


# ── 1. wholesale exception queue ─────────────────────────────────────────────

def _seed_exceptions(db, org, n_each):
    """n_each exceptions of each subject type (property, buyer, deal, lead)."""
    created = []
    for i in range(n_each):
        p = WholesaleProperty(organization_id=org.id, street_address="%d Elm St" % (100 + len(created)),
                              city="Dallas", state="TX", zip_code="75201", county="Dallas")
        b = WholesaleBuyer(organization_id=org.id, company_name="Buyer %d" % len(created),
                           contact_name="Contact", email="b%d@x.test" % len(created))
        dp = WholesaleProperty(organization_id=org.id, street_address="%d Oak St" % (100 + len(created)),
                               city="Plano", state="TX")
        db.add_all([p, b, dp])
        db.flush()
        d = WholesaleDeal(organization_id=org.id, property_id=dp.id, stage="new_property")
        lead = Lead(organization_id=org.id, first_name="Sam", last_name="Seller%d" % len(created),
                    phone="1972555%04d" % len(created), status="new")
        db.add_all([d, lead])
        db.flush()
        for st, sid in (("property", p.id), ("buyer", b.id), ("deal", d.id), ("lead", lead.id)):
            ex = WholesaleWorkException(organization_id=org.id, kind="verify_owner", subject_type=st,
                                        subject_id=sid, title="Check %s" % st, priority=50,
                                        status="open")
            db.add(ex)
            created.append(ex)
    db.commit()
    return created


def test_exception_queue_output_matches_per_row_subjects_and_query_count_is_flat(
        client, db_session, sample_org):
    admin = _user(db_session, sample_org, "org_admin", "owner@perf.test")
    h = _h(db_session, admin)
    _seed_exceptions(db_session, sample_org, 1)
    # one exception whose subject no longer exists
    db_session.add(WholesaleWorkException(organization_id=sample_org.id, kind="verify_owner",
                                          subject_type="property", subject_id="gone",
                                          title="Orphan", status="open"))
    db_session.commit()

    body, small = _counted_get(client, db_session, "/wholesale/exceptions?scope=all", h)
    assert body["manager"] is True and body["scope"] == "all"
    assert body["limit"] == 200 and body["offset"] == 0
    assert len(body["items"]) == 5
    # Identical to the per-row path every other caller still uses.
    by_id = {e.id: e for e in db_session.query(WholesaleWorkException).all()}
    for item in body["items"]:
        expected = EX.exception_json(db_session, sample_org.id, by_id[item["id"]], {})
        assert item == expected
    subjects = {i["subject"]["type"]: i["subject"] for i in body["items"] if i["title"] != "Orphan"}
    assert subjects["property"]["label"] == "100 Elm St, Dallas, TX, 75201"
    assert subjects["buyer"]["label"].startswith("Buyer")
    assert subjects["deal"]["label"] == "100 Oak St, Plano, TX" and subjects["deal"]["stage"] == "new_property"
    assert subjects["lead"]["label"].startswith("Sam Seller")
    orphan = next(i for i in body["items"] if i["title"] == "Orphan")
    assert orphan["subject"] == {"type": "property", "label": None}

    _seed_exceptions(db_session, sample_org, 5)            # 25 total now
    body, big = _counted_get(client, db_session, "/wholesale/exceptions?scope=all", h)
    assert len(body["items"]) == 25
    assert big == small, (small, big)
    assert big < 25


def test_exception_queue_limit_and_offset(client, db_session, sample_org):
    admin = _user(db_session, sample_org, "org_admin", "owner2@perf.test")
    h = _h(db_session, admin)
    _seed_exceptions(db_session, sample_org, 3)            # 12 rows
    full = ok(client.get("/wholesale/exceptions?scope=all", headers=h))["items"]
    assert len(full) == 12
    page1 = ok(client.get("/wholesale/exceptions?scope=all&limit=5", headers=h))
    page3 = ok(client.get("/wholesale/exceptions?scope=all&limit=5&offset=10", headers=h))
    assert [i["id"] for i in page1["items"]] == [i["id"] for i in full[:5]]
    assert [i["id"] for i in page3["items"]] == [i["id"] for i in full[10:]]
    assert client.get("/wholesale/exceptions?limit=501", headers=h).status_code == 422
    assert client.get("/wholesale/exceptions?offset=-1", headers=h).status_code == 422


def test_exception_queue_service_default_is_unbounded(db_session, sample_org):
    admin = _user(db_session, sample_org, "org_admin", "owner3@perf.test")
    _seed_exceptions(db_session, sample_org, 2)
    assert len(EX.queue(db_session, sample_org.id, admin, scope="all")) == 8
    assert len(EX.queue(db_session, sample_org.id, admin, scope="all", limit=3, offset=6)) == 2


# ── 2. calendar /events ──────────────────────────────────────────────────────

def _booking(db, advisor, lead, when, status="booked"):
    b = BookingLink(lead_id=lead.id, user_id=advisor.id, status=status, booked_time=when)
    db.add(b)
    db.commit()
    return b


def test_calendar_events_window_in_sql_and_leads_batched(client, db_session, sample_org, sample_advisor,
                                                         auth_headers):
    now = datetime.utcnow()
    la, lb = _lead(db_session, sample_org, sample_advisor, "Ann", "Able"), \
        _lead(db_session, sample_org, sample_advisor, "Bob", "Baker")
    inside1 = _booking(db_session, sample_advisor, la, now + timedelta(days=2))
    inside2 = _booking(db_session, sample_advisor, lb, now + timedelta(days=1), status="confirmed")
    _booking(db_session, sample_advisor, la, now - timedelta(days=1))                 # past
    _booking(db_session, sample_advisor, la, now + timedelta(days=90))                # beyond cutoff
    _booking(db_session, sample_advisor, la, now + timedelta(days=3), status="pending")  # wrong status

    events, small = _counted_get(client, db_session, "/calendar/events?days_ahead=60", auth_headers)
    assert [e["id"] for e in events] == [inside2.id, inside1.id]
    assert events[0]["lead_name"] == "Bob Baker" and events[1]["lead_name"] == "Ann Able"
    assert events[0]["status"] == "confirmed" and events[0]["advisor_id"] == sample_advisor.id
    assert "advisor_name" not in events[0]

    for i in range(10):
        _booking(db_session, sample_advisor, _lead(db_session, sample_org, sample_advisor),
                 now + timedelta(days=4, hours=i))
    events, big = _counted_get(client, db_session, "/calendar/events?days_ahead=60", auth_headers)
    assert len(events) == 12
    assert big == small, (small, big)


def test_calendar_events_org_wide_advisor_names(client, db_session, sample_org, sample_advisor,
                                                second_advisor):
    admin = _user(db_session, sample_org, "org_admin", "cal-admin@perf.test")
    now = datetime.utcnow()
    _booking(db_session, sample_advisor, _lead(db_session, sample_org, sample_advisor),
             now + timedelta(days=1))
    _booking(db_session, second_advisor, _lead(db_session, sample_org, second_advisor),
             now + timedelta(days=2))
    events = ok(client.get("/calendar/events?org_wide=true", headers=_h(db_session, admin)))
    assert [e["advisor_name"] for e in events] == ["Advisor One", "Advisor Two"]


# ── 3. pipeline /flagged and /conversations ──────────────────────────────────

def _pipeline(db, org, advisor, lead, flagged=True, lead_id=None):
    p = PipelineConversation(organization_id=org.id, lead_id=lead_id or lead.id, advisor_id=advisor.id,
                             stage="replied", flagged=flagged, flag_reason="low confidence",
                             flagged_at=datetime.utcnow())
    db.add(p)
    db.commit()
    return p


def test_pipeline_flagged_and_conversations_batch_scoped_leads(client, db_session, sample_org,
                                                               sample_advisor, auth_headers):
    other_org = Organization(name="Other", slug="other-perf", plan="standard")
    db_session.add(other_org)
    db_session.commit()
    other_adv = _user(db_session, other_org, "advisor", "other@perf.test")
    foreign = _lead(db_session, other_org, other_adv, "Foreign", "Lead")

    mine = _lead(db_session, sample_org, sample_advisor, "Jo", "Mine")
    _pipeline(db_session, sample_org, sample_advisor, mine)
    # A pipeline row pointing at a lead outside the caller's scope: "Unknown".
    _pipeline(db_session, sample_org, sample_advisor, None, lead_id=foreign.id)

    flagged, small = _counted_get(client, db_session, "/pipeline/flagged", auth_headers)
    names = sorted(f["lead_name"] for f in flagged)
    assert names == ["Jo Mine", "Unknown"]
    known = next(f for f in flagged if f["lead_name"] == "Jo Mine")
    assert known["lead_phone"] == mine.phone and known["flag_reason"] == "low confidence"

    convs, small_c = _counted_get(client, db_session, "/pipeline/conversations", auth_headers)
    assert sorted(c["lead_name"] for c in convs) == ["Jo Mine", "Unknown"]

    for _ in range(10):
        _pipeline(db_session, sample_org, sample_advisor, _lead(db_session, sample_org, sample_advisor))
    flagged, big = _counted_get(client, db_session, "/pipeline/flagged", auth_headers)
    convs, big_c = _counted_get(client, db_session, "/pipeline/conversations", auth_headers)
    assert len(flagged) == 12 and len(convs) == 12
    assert big == small, (small, big)
    assert big_c == small_c, (small_c, big_c)


# ── 4. auto-send /queue and /history ─────────────────────────────────────────

def _item(db, org, advisor, lead, status="pending"):
    it = AutoSendItem(organization_id=org.id, lead_id=lead.id, advisor_id=advisor.id,
                      message="See you then", status=status,
                      actioned_at=datetime.utcnow() if status != "pending" else None)
    db.add(it)
    db.commit()
    return it


def test_auto_send_queue_and_history_batch_leads(client, db_session, sample_org, sample_advisor,
                                                 auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, "Queue", "Person")
    pending = _item(db_session, sample_org, sample_advisor, lead)
    sent = _item(db_session, sample_org, sample_advisor, lead, status="sent")

    q, small_q = _counted_get(client, db_session, "/auto-send/queue", auth_headers)
    hist, small_h = _counted_get(client, db_session, "/auto-send/history", auth_headers)
    assert [i["id"] for i in q] == [pending.id]
    assert q[0]["lead_name"] == "Queue Person" and q[0]["phone"] == lead.phone
    assert [i["id"] for i in hist] == [sent.id] and hist[0]["lead_name"] == "Queue Person"

    for _ in range(8):
        l2 = _lead(db_session, sample_org, sample_advisor)
        _item(db_session, sample_org, sample_advisor, l2)
        _item(db_session, sample_org, sample_advisor, l2, status="skipped")
    q, big_q = _counted_get(client, db_session, "/auto-send/queue", auth_headers)
    hist, big_h = _counted_get(client, db_session, "/auto-send/history", auth_headers)
    assert len(q) == 9 and len(hist) == 9
    assert big_q == small_q and big_h == small_h, (small_q, big_q, small_h, big_h)


def test_auto_send_queue_is_capped(client, db_session, sample_org, sample_advisor, auth_headers,
                                   monkeypatch):
    from app.routers import auto_send_router
    monkeypatch.setattr(auto_send_router, "QUEUE_LIMIT", 3)
    lead = _lead(db_session, sample_org, sample_advisor)
    items = [_item(db_session, sample_org, sample_advisor, lead) for _ in range(5)]
    q = ok(client.get("/auto-send/queue", headers=auth_headers))
    assert len(q) == 3
    assert {i["id"] for i in q} <= {i.id for i in items}


# ── 5. lead activity timeline ────────────────────────────────────────────────

def test_timeline_batches_sender_and_recorder_lookups(client, db_session, sample_org, sample_advisor,
                                                      second_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor, "Tim", "Line")
    db_session.add_all([
        Message(lead_id=lead.id, sender_id=sample_advisor.id, body="hello", sent_at=datetime.utcnow()),
        Message(lead_id=lead.id, sender_id=second_advisor.id, body="hi again", sent_at=datetime.utcnow()),
        EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="Info", body_html="<p/>"),
        LeadOutcome(lead_id=lead.id, recorded_by_id=second_advisor.id),
    ])
    db_session.commit()
    url = "/leads/%s/activity" % lead.id
    body, small = _counted_get(client, db_session, url, auth_headers)
    events = body if isinstance(body, list) else body.get("events", body)
    labels = sorted(e["label"] for e in events)
    assert "SMS sent by Advisor One" in labels and "SMS sent by Advisor Two" in labels
    email = next(e for e in events if e["type"] == "email_sent")
    assert email["label"] == "Email sent by Advisor One"
    assert any(e["type"] == "outcome_recorded" for e in events)

    for i in range(10):
        db_session.add(Message(lead_id=lead.id, sender_id=second_advisor.id, body="m%d" % i,
                               sent_at=datetime.utcnow()))
        db_session.add(LeadOutcome(lead_id=lead.id, recorded_by_id=sample_advisor.id))
    db_session.commit()
    _, big = _counted_get(client, db_session, url, auth_headers)
    assert big == small, (small, big)


# ── 6. CRM contacts ──────────────────────────────────────────────────────────

def test_contacts_limit_and_offset(client, db_session, sample_org, auth_headers):
    base = datetime(2026, 1, 1)
    for i in range(7):
        db_session.add(CRMContact(organization_id=sample_org.id, first_name="C%d" % i,
                                  created_at=base + timedelta(days=i)))
    db_session.commit()
    full = ok(client.get("/crm/contacts", headers=auth_headers))
    assert [c["first_name"] for c in full] == ["C6", "C5", "C4", "C3", "C2", "C1", "C0"]
    page = ok(client.get("/crm/contacts?limit=3&offset=2", headers=auth_headers))
    assert [c["first_name"] for c in page] == ["C4", "C3", "C2"]
    assert client.get("/crm/contacts?limit=2001", headers=auth_headers).status_code == 422
    assert client.get("/crm/contacts?limit=0", headers=auth_headers).status_code == 422


# ── 7. availability /upcoming ────────────────────────────────────────────────

def test_availability_upcoming_batches_leads(client, db_session, sample_org, sample_advisor,
                                            second_advisor, auth_headers):
    now = datetime.now()
    first = _booking(db_session, sample_advisor, _lead(db_session, sample_org, sample_advisor, "Up", "One"),
                     now + timedelta(days=1))
    _booking(db_session, sample_advisor, _lead(db_session, sample_org, sample_advisor), now - timedelta(days=1))
    rows, small = _counted_get(client, db_session, "/availability/upcoming", auth_headers)
    assert [r["id"] for r in rows] == [first.id] and rows[0]["lead_name"] == "Up One"
    assert rows[0]["lead_phone"] is not None

    for i in range(8):
        _booking(db_session, sample_advisor, _lead(db_session, sample_org, sample_advisor),
                 now + timedelta(days=2, hours=i))
    rows, big = _counted_get(client, db_session, "/availability/upcoming", auth_headers)
    assert len(rows) == 9 and big == small, (small, big)

    # org-wide admin view: advisor names + leads, still flat
    admin = _user(db_session, sample_org, "org_admin", "avail-admin@perf.test")
    ah = _h(db_session, admin)
    _booking(db_session, second_advisor, _lead(db_session, sample_org, second_advisor, "Two", "Lead"),
             now + timedelta(hours=1))
    rows, small_o = _counted_get(client, db_session, "/availability/upcoming?org_wide=true", ah)
    assert rows[0]["advisor_name"] == "Advisor Two" and rows[0]["lead_name"] == "Two Lead"
    assert rows[1]["advisor_name"] == "Advisor One" and rows[1]["lead_name"] == "Up One"
    for i in range(5):
        _booking(db_session, second_advisor, _lead(db_session, sample_org, second_advisor),
                 now + timedelta(days=5, hours=i))
    rows, big_o = _counted_get(client, db_session, "/availability/upcoming?org_wide=true", ah)
    assert len(rows) == 15 and big_o == small_o, (small_o, big_o)


def test_availability_upcoming_single_path_keeps_org_filter(client, db_session, sample_org,
                                                           sample_advisor, auth_headers):
    other_org = Organization(name="Other2", slug="other-perf-2", plan="standard")
    db_session.add(other_org)
    db_session.commit()
    other_adv = _user(db_session, other_org, "advisor", "other2@perf.test")
    foreign = _lead(db_session, other_org, other_adv, "Not", "Mine")
    _booking(db_session, sample_advisor, foreign, datetime.now() + timedelta(days=1))
    rows = ok(client.get("/availability/upcoming", headers=auth_headers))
    assert rows[0]["lead_name"] == "Unknown" and rows[0]["lead_phone"] is None


# ── 8. wholesale deals list: band filter in SQL ──────────────────────────────

def test_wholesale_deals_band_filter_counts_and_pages_correctly(client, db_session, sample_org,
                                                                sample_advisor, auth_headers):
    bands = ["high", "low", "high", "medium", "high", None]
    ids = {}
    for i, band in enumerate(bands):
        p = WholesaleProperty(organization_id=sample_org.id, street_address="%d Band Rd" % i,
                              city="Dallas", state="TX")
        lead = _lead(db_session, sample_org, sample_advisor)
        db_session.add(p)
        db_session.flush()
        prof = None
        if band is not None:
            prof = WholesaleSellerProfile(organization_id=sample_org.id, lead_id=lead.id,
                                          property_id=p.id, qualification_band=band)
            db_session.add(prof)
            db_session.flush()
        d = WholesaleDeal(organization_id=sample_org.id, property_id=p.id, seller_lead_id=lead.id,
                          seller_profile_id=prof.id if prof else None,
                          updated_at=datetime(2026, 1, 1) + timedelta(hours=i))
        db_session.add(d)
        db_session.flush()
        ids[i] = d.id
    db_session.commit()

    everything = ok(client.get("/wholesale/deals", headers=auth_headers))
    assert everything["total"] == 6

    high = ok(client.get("/wholesale/deals?band=high", headers=auth_headers))
    assert high["total"] == 3
    assert [d["id"] for d in high["deals"]] == [ids[4], ids[2], ids[0]]

    # Paging used to fetch the page first and THEN drop non-matching rows.
    p1 = ok(client.get("/wholesale/deals?band=high&limit=2", headers=auth_headers))
    p2 = ok(client.get("/wholesale/deals?band=high&limit=2&offset=2", headers=auth_headers))
    assert p1["total"] == 3 and p2["total"] == 3
    assert [d["id"] for d in p1["deals"]] == [ids[4], ids[2]]
    assert [d["id"] for d in p2["deals"]] == [ids[0]]
    assert ok(client.get("/wholesale/deals?band=review", headers=auth_headers))["total"] == 0
