"""`GET /notifications/` is the hottest endpoint in the product. It had no ceiling.

THE EVIDENCE. Pulled from the production backend logs during the OOM
diagnosis: against roughly 2,700 requests a day, `/notifications/` accounted
for about 1,440 of them - more than half of all HTTP traffic - because the bell
polls on a timer for every signed-in client, in every tab, whether or not
anyone is looking at it.

THE DEFECT. The handler answered every one of those with an unbounded `.all()`
of fully-hydrated ORM rows, each carrying a free-text `message` built from a
lead's name, phone number and the entire body of their reply. Nothing expires a
notification and nothing marks one read except a human clicking it, so the
unread set only grows. An advisor who ignores the bell for a month was asking a
512 MB instance to materialise their whole backlog once a minute forever.

The badge made it worse: the client rendered `notifications.length`, so the
count - the only part most users ever look at - was paid for by loading every
row to measure the list.

THE FIX. `items` is capped at NOTIFICATION_PAGE_SIZE, `unread_count` is a SQL
count that stays exact past the cap, and the rows are named columns rather than
mapped objects so nothing lazy-loads a Lead behind the serializer.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import Notification, NotificationType
from app.services import notification_service
from app.services.notification_service import (NOTIFICATION_PAGE_SIZE,
                                               get_unread_notifications,
                                               unread_notification_count)


def _make(db, user_id, lead_id, n, read=False, minutes_ago=0):
    rows = []
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(n):
        row = Notification(
            user_id=user_id,
            lead_id=lead_id,
            type=NotificationType.HOT_REPLY,
            message="notification %d" % i,
            is_read=read,
            created_at=base - timedelta(minutes=minutes_ago + i),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


# ── the cap ─────────────────────────────────────────────────────────────────

def test_a_large_unread_backlog_is_capped(db_session, sample_advisor, sample_lead):
    _make(db_session, sample_advisor.id, sample_lead.id, NOTIFICATION_PAGE_SIZE + 25)
    items = get_unread_notifications(db_session, sample_advisor.id)
    assert len(items) == NOTIFICATION_PAGE_SIZE


def test_the_count_stays_exact_past_the_cap(db_session, sample_advisor, sample_lead):
    """The badge must not say 50 when the advisor has 75 waiting."""
    _make(db_session, sample_advisor.id, sample_lead.id, NOTIFICATION_PAGE_SIZE + 25)
    assert unread_notification_count(db_session, sample_advisor.id) == NOTIFICATION_PAGE_SIZE + 25


def test_the_cap_keeps_the_newest(db_session, sample_advisor, sample_lead):
    """Truncation has to drop the oldest, not whatever the database felt like."""
    _make(db_session, sample_advisor.id, sample_lead.id, NOTIFICATION_PAGE_SIZE + 5)
    items = get_unread_notifications(db_session, sample_advisor.id)
    times = [i["created_at"] for i in items]
    assert times == sorted(times, reverse=True)
    assert items[0]["message"] == "notification 0"   # minutes_ago = 0, the newest


def test_a_smaller_limit_is_honoured(db_session, sample_advisor, sample_lead):
    _make(db_session, sample_advisor.id, sample_lead.id, 10)
    assert len(get_unread_notifications(db_session, sample_advisor.id, limit=3)) == 3


def test_a_nonsense_limit_does_not_become_unbounded(db_session, sample_advisor, sample_lead):
    """limit=0 must not mean 'no limit'. It is the one value that could."""
    _make(db_session, sample_advisor.id, sample_lead.id, 10)
    assert len(get_unread_notifications(db_session, sample_advisor.id, limit=0)) == 1
    assert len(get_unread_notifications(db_session, sample_advisor.id, limit=-5)) == 1


# ── correctness the rewrite had to preserve ─────────────────────────────────

def test_read_notifications_are_excluded(db_session, sample_advisor, sample_lead):
    _make(db_session, sample_advisor.id, sample_lead.id, 4, read=True)
    _make(db_session, sample_advisor.id, sample_lead.id, 3, read=False)
    assert len(get_unread_notifications(db_session, sample_advisor.id)) == 3
    assert unread_notification_count(db_session, sample_advisor.id) == 3


def test_another_advisors_notifications_are_not_returned(
        db_session, sample_advisor, second_advisor, sample_lead):
    """The scoping filter is the one thing a projection rewrite must not lose."""
    _make(db_session, sample_advisor.id, sample_lead.id, 3)
    _make(db_session, second_advisor.id, sample_lead.id, 7)
    assert len(get_unread_notifications(db_session, sample_advisor.id)) == 3
    assert unread_notification_count(db_session, sample_advisor.id) == 3
    assert unread_notification_count(db_session, second_advisor.id) == 7


def test_an_empty_bell_is_empty_not_an_error(db_session, sample_advisor):
    assert get_unread_notifications(db_session, sample_advisor.id) == []
    assert unread_notification_count(db_session, sample_advisor.id) == 0


# ── the payload shape ───────────────────────────────────────────────────────

def test_rows_are_plain_dicts_not_orm_objects(db_session, sample_advisor, sample_lead):
    """Returning mapped objects is what let the serializer walk relationships."""
    _make(db_session, sample_advisor.id, sample_lead.id, 2)
    for item in get_unread_notifications(db_session, sample_advisor.id):
        assert isinstance(item, dict)
        assert set(item) == {"id", "lead_id", "type", "message", "created_at", "is_read", "link"}


def test_the_type_is_serialised_as_its_value(db_session, sample_advisor, sample_lead):
    """The client compares `n.type === 'hot_reply'`. An Enum repr would break it."""
    _make(db_session, sample_advisor.id, sample_lead.id, 1)
    assert get_unread_notifications(db_session, sample_advisor.id)[0]["type"] == "hot_reply"


def test_lead_id_survives_so_the_bell_can_still_navigate(
        db_session, sample_advisor, sample_lead):
    _make(db_session, sample_advisor.id, sample_lead.id, 1)
    assert get_unread_notifications(db_session, sample_advisor.id)[0]["lead_id"] == sample_lead.id


# ── the endpoint ────────────────────────────────────────────────────────────

def test_endpoint_returns_the_documented_shape(
        client, db_session, sample_advisor, sample_lead, auth_headers):
    _make(db_session, sample_advisor.id, sample_lead.id, 3)
    r = client.get("/notifications/", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "unread_count", "page_size", "has_more"}
    assert body["unread_count"] == 3
    assert body["page_size"] == NOTIFICATION_PAGE_SIZE
    assert body["has_more"] is False
    assert len(body["items"]) == 3


def test_endpoint_reports_has_more_when_truncated(
        client, db_session, sample_advisor, sample_lead, auth_headers):
    _make(db_session, sample_advisor.id, sample_lead.id, NOTIFICATION_PAGE_SIZE + 1)
    body = client.get("/notifications/", headers=auth_headers).json()
    assert body["has_more"] is True
    assert body["unread_count"] == NOTIFICATION_PAGE_SIZE + 1
    assert len(body["items"]) == NOTIFICATION_PAGE_SIZE


def test_endpoint_still_requires_authentication(client):
    assert client.get("/notifications/").status_code in (401, 403)


def test_marking_one_read_removes_it_from_the_payload(
        client, db_session, sample_advisor, sample_lead, auth_headers):
    rows = _make(db_session, sample_advisor.id, sample_lead.id, 2)
    target = rows[0].id
    assert client.post("/notifications/%s/read" % target, headers=auth_headers).status_code == 200
    body = client.get("/notifications/", headers=auth_headers).json()
    assert body["unread_count"] == 1
    assert [i["id"] for i in body["items"]] == [rows[1].id]
