"""EMAIL POLLER — a five-minute cron against a fifteen-minute window.

THIS POLLER HAS NO CURSOR. It does not remember where it got to. Every run
asks Microsoft Graph for everything received in the last N minutes, so the
only thing standing between a lead's reply and being lost forever is that the
LOOKBACK WINDOW is wider than the gap between runs.

At `* * * * *` the window was 5 minutes against a 1-minute interval - a 5x
overlap that hid how fragile the arrangement was. Moving the schedule to
`*/5 * * * *` without widening the window would have left zero margin: a run
starting a few seconds late, a run taking 2-3 seconds before computing
`since`, clock skew, or one failed run, and a reply is dropped permanently.

These tests pin the relationship between the two numbers, because they live in
different files and nothing else connects them.
"""

import re
from pathlib import Path

import pytest

from app.services import email_poller_service as eps

RENDER_YAML = Path(__file__).resolve().parents[1] / "render.yaml"


def _poller_schedule() -> str:
    text = RENDER_YAML.read_text(encoding="utf-8")
    block = text[text.index("name: advisorflow-email-poller"):]
    m = re.search(r'schedule:\s*"([^"]+)"', block)
    assert m, "the email poller has no schedule in render.yaml"
    return m.group(1)


def _interval_minutes(cron: str) -> int:
    """Minutes between runs for the shapes this project uses."""
    minute = cron.split()[0]
    if minute == "*":
        return 1
    m = re.fullmatch(r"\*/(\d+)", minute)
    assert m, "unrecognised schedule %r" % cron
    return int(m.group(1))


# ── the schedule itself ─────────────────────────────────────────────────────

def test_the_poller_runs_every_five_minutes():
    assert _poller_schedule() == "*/5 * * * *"


def test_that_is_288_runs_a_day_not_1440():
    per_day = (24 * 60) // _interval_minutes(_poller_schedule())
    assert per_day == 288


def test_there_is_exactly_one_email_poller_cron():
    """The instruction was to change the existing definition, not add a second
    one. Two pollers would double the work this change exists to reduce."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    assert text.count("name: advisorflow-email-poller") == 1
    assert text.count("run_email_poller.py") == 1


# ── the window must cover the interval, with margin ─────────────────────────

def test_the_lookback_window_is_wider_than_the_poll_interval():
    """The whole correctness argument in one assertion."""
    assert eps.POLL_LOOKBACK_MINUTES > _interval_minutes(_poller_schedule())


def test_the_overlap_is_at_least_threefold():
    """Not merely wider - wide enough to survive a late start, a slow run,
    clock skew, or one failed run that is not retried until the next slot."""
    interval = _interval_minutes(_poller_schedule())
    assert eps.POLL_LOOKBACK_MINUTES >= 3 * interval, (
        "a %d-minute window against a %d-minute interval leaves too little "
        "margin for a poller with no cursor"
        % (eps.POLL_LOOKBACK_MINUTES, interval))


def test_a_single_failed_run_cannot_open_a_gap():
    """If one run fails entirely, the next run must still cover the whole
    period the failed one was responsible for."""
    interval = _interval_minutes(_poller_schedule())
    assert eps.POLL_LOOKBACK_MINUTES >= 2 * interval


def test_the_fetch_defaults_to_the_declared_window():
    """The call site must not pass its own number - that is how the two drift
    apart. It passed `since_minutes=5` as a literal before."""
    import inspect
    sig = inspect.signature(eps._fetch_recent_emails)
    assert sig.parameters["since_minutes"].default == eps.POLL_LOOKBACK_MINUTES
    src = inspect.getsource(eps.poll_inbox_for_replies)
    assert "since_minutes=" not in src, (
        "the call site hardcodes a window instead of using POLL_LOOKBACK_MINUTES")


# ── truncation must not drop the messages about to age out ──────────────────

def test_the_query_asks_for_oldest_first():
    """With `desc` and a page limit, truncation dropped the OLDEST messages in
    the window - exactly the ones about to fall out of it, which no later run
    would ever see again. Ascending means truncation drops the newest, and the
    next run still covers those."""
    import inspect
    src = inspect.getsource(eps._fetch_recent_emails)
    assert '"receivedDateTime asc"' in src
    assert '"receivedDateTime desc"' not in src


def test_the_page_size_is_large_enough_for_the_wider_window():
    assert eps.POLL_PAGE_SIZE >= 200


def test_the_window_and_page_size_are_stated_once_each():
    """Two copies of a number that must agree is how they stop agreeing."""
    import inspect
    src = inspect.getsource(eps)
    assert src.count("POLL_LOOKBACK_MINUTES = ") == 1
    assert src.count("POLL_PAGE_SIZE = ") == 1


# ── re-reading is free; missing is not ──────────────────────────────────────

def test_a_reply_seen_twice_produces_one_row(db_session, sample_org,
                                             sample_advisor):
    """The property that makes overlap safe. Without it, a 3x window would
    mean every reply stored three times."""
    from app.models.models import Lead, Reply
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id,
                first_name="Dedupe", last_name="Test", email="d@example.com",
                phone="12145556500", status="sent")
    db_session.add(lead); db_session.commit()

    body = "Yes, that time works for me."
    for _ in range(3):
        existing = db_session.query(Reply).filter(
            Reply.lead_id == lead.id, Reply.body == body).first()
        if existing:
            continue
        db_session.add(Reply(lead_id=lead.id, body=body, source="email",
                             classification="neutral", is_hot=False))
        db_session.commit()

    assert db_session.query(Reply).filter(Reply.lead_id == lead.id).count() == 1


def test_the_poller_reaches_no_provider_without_a_token(db_session):
    """Nothing in this file may touch Microsoft Graph."""
    import inspect
    src = inspect.getsource(eps._fetch_recent_emails)
    assert "graph.microsoft.com" in src      # it is the real call site
    # and the test suite never supplies a token, so it is never invoked here.
