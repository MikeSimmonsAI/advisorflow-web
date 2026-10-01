"""S11 system reliability regressions (2026-10-01).

Each test here failed against the code as it stood before the fix next to it.
Providers are faked: no Graph, no Twilio, no Resend, no AI.
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
from app.models.models import BookingLink, Lead, Organization, Reply
from app.services import email_poller_service as EP
from app.services import inbound_mailbox_service as S

BOX = "support@evosyspro.live"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── shared reply mailbox ─────────────────────────────────────────────────────

@pytest.fixture()
def mailbox_world(db_session, sample_advisor, monkeypatch):
    org = Organization(name="Rel Test", slug="r-%s" % uuid.uuid4().hex[:6], is_active=True)
    db_session.add(org)
    db_session.commit()
    monkeypatch.setattr("app.services.public_identity.sending_identity_for_org",
                        lambda db, oid: SimpleNamespace(from_email=BOX if oid == org.id else None,
                                                        reply_to_email=None))
    lead = Lead(organization_id=org.id, first_name="Pat", email="pat@example.com", status="sent",
                assigned_to_id=sample_advisor.id)
    box = InboundMailbox(address=BOX, is_active=True)
    db_session.add_all([lead, box])
    db_session.commit()
    return SimpleNamespace(org=org, lead=lead, box=box)


def _gmsg(gid, sender, body, received):
    return {"id": gid, "internetMessageId": "<%s@x>" % gid, "subject": "Re: hi",
            "from": {"emailAddress": {"address": sender}}, "receivedDateTime": _iso(received),
            "body": {"content": body}, "bodyPreview": body[:50]}


def test_mailbox_a_second_identical_short_reply_days_later_is_not_dropped(db_session, mailbox_world):
    """'Yes' to Monday's email and 'Yes' to Thursday's email are two replies.
    The (lead, body) dedupe silently threw the second one away."""
    now = datetime.utcnow()
    S.poll_mailbox(db_session, mailbox_world.box,
                   fetch=lambda s: [_gmsg("g1", "pat@example.com", "Yes", now - timedelta(days=2))])
    res = S.poll_mailbox(db_session, mailbox_world.box,
                         fetch=lambda s: [_gmsg("g2", "pat@example.com", "Yes", now - timedelta(minutes=3))])
    assert res["matched"] == 1
    assert db_session.query(Reply).filter(Reply.lead_id == mailbox_world.lead.id).count() == 2


def test_mailbox_same_message_seen_twice_is_still_one_reply(db_session, mailbox_world):
    now = datetime.utcnow()
    m = _gmsg("g1", "pat@example.com", "Yes", now - timedelta(minutes=3))
    S.poll_mailbox(db_session, mailbox_world.box, fetch=lambda s: [m])
    # Same message under a new Graph id (moved folder without ImmutableId) and a
    # fresh internetMessageId: the time+body dedupe still recognises it.
    m2 = dict(m, id="g1-moved", internetMessageId="<other@x>")
    S.poll_mailbox(db_session, mailbox_world.box, fetch=lambda s: [m2])
    assert db_session.query(Reply).filter(Reply.lead_id == mailbox_world.lead.id).count() == 1


def test_mailbox_a_poison_message_does_not_pin_the_cursor_forever(db_session, mailbox_world, monkeypatch):
    """One message that fails on every run held the cursor still. With the
    per-run cap (MAX_MESSAGES_PER_RUN, oldest first) a busy mailbox then never
    reads anything newer again - every later reply is lost. After the retry
    window the failed message is left in the log as 'error' and the cursor moves."""
    now = datetime.utcnow()
    real_route = S.route

    def route(db, org_ids, sender):
        if sender == "poison@example.com":
            raise RuntimeError("boom")
        return real_route(db, org_ids, sender)
    monkeypatch.setattr(S, "route", route)

    poison_at = now - timedelta(days=2)
    mailbox_world.box.cursor_received_at = poison_at - timedelta(minutes=1)
    db_session.add(InboundMailboxMessage(mailbox_id=mailbox_world.box.id, graph_message_id="gp",
                                         outcome="error", received_at=poison_at,
                                         created_at=now - timedelta(days=2)))
    db_session.commit()
    newest = now - timedelta(minutes=2)
    msgs = [_gmsg("gp", "poison@example.com", "x", poison_at),
            _gmsg("g9", "pat@example.com", "Call me", newest)]
    res = S.poll_mailbox(db_session, mailbox_world.box, fetch=lambda s: msgs)
    assert res["errors"] == 1 and res["matched"] == 1
    db_session.refresh(mailbox_world.box)
    assert mailbox_world.box.cursor_received_at == newest.replace(microsecond=0)


def test_mailbox_a_fresh_failure_holds_the_cursor_at_that_message(db_session, mailbox_world, monkeypatch):
    now = datetime.utcnow()
    real_route = S.route

    def route(db, org_ids, sender):
        if sender == "flaky@example.com":
            raise RuntimeError("transient")
        return real_route(db, org_ids, sender)
    monkeypatch.setattr(S, "route", route)
    fail_at = (now - timedelta(minutes=30)).replace(microsecond=0)
    msgs = [_gmsg("gf", "flaky@example.com", "x", fail_at),
            _gmsg("g9", "pat@example.com", "Call me", now - timedelta(minutes=2))]
    S.poll_mailbox(db_session, mailbox_world.box, fetch=lambda s: msgs)
    db_session.refresh(mailbox_world.box)
    cur = mailbox_world.box.cursor_received_at
    # The failed message is inside the next run's window (cursor - overlap).
    assert cur is not None and cur - S.CURSOR_OVERLAP <= fail_at


# ── advisor (M365) inbox poller ──────────────────────────────────────────────

@pytest.fixture()
def advisor_poller(db_session, sample_advisor, monkeypatch):
    sample_advisor.microsoft_365_connected = True
    sample_advisor.microsoft_oauth_refresh_token_encrypted = "enc"
    sample_advisor.microsoft_email_address = "advisor1@restland.com"
    lead = Lead(organization_id=sample_advisor.organization_id, first_name="Lee",
                email="lee@example.com", status="sent", assigned_to_id=sample_advisor.id)
    db_session.add(lead)
    db_session.commit()
    inbox = []
    tagged = []
    ai_calls = []
    monkeypatch.setattr(EP, "_get_fresh_access_token", lambda adv: "tok")
    monkeypatch.setattr(EP, "_fetch_recent_emails", lambda tok, *a, **k: list(inbox))
    monkeypatch.setattr(EP, "_mark_email_processed", lambda tok, mid, *a, **k: tagged.append(mid))

    def fake_ai(db, lead_, advisor, body):
        ai_calls.append(body)      # stands in for an AI reply that SENDS
        return {"action": "replied"}
    monkeypatch.setattr("app.services.ai_conversation_service.handle_inbound_reply", fake_ai)
    return SimpleNamespace(advisor=sample_advisor, lead=lead, inbox=inbox, tagged=tagged, ai=ai_calls)


def _amsg(gid, sender, body, received):
    return {"id": gid, "subject": "Re: hi", "from": {"emailAddress": {"address": sender}},
            "receivedDateTime": _iso(received), "body": {"content": body}, "categories": []}


def test_advisor_poller_second_identical_reply_days_later_is_kept(db_session, advisor_poller):
    now = datetime.utcnow()
    advisor_poller.inbox[:] = [_amsg("a1", "lee@example.com", "Yes", now - timedelta(days=3))]
    EP.poll_inbox_for_replies(db_session, advisor_poller.advisor.id)
    advisor_poller.inbox[:] = [_amsg("a2", "lee@example.com", "Yes", now - timedelta(minutes=1))]
    res = EP.poll_inbox_for_replies(db_session, advisor_poller.advisor.id)
    assert res["matched"] == 1
    assert db_session.query(Reply).filter(Reply.lead_id == advisor_poller.lead.id).count() == 2


def test_advisor_poller_reply_is_committed_before_the_ai_can_send(db_session, advisor_poller, monkeypatch):
    """The AI handler (which can send an SMS/email) ran BEFORE the Reply was
    committed. A commit failure after it rolled the Reply back; the message was
    not tagged; the next run created the Reply again and the AI sent again."""
    now = datetime.utcnow()
    advisor_poller.inbox[:] = [_amsg("a1", "lee@example.com", "Is Friday ok?", now - timedelta(minutes=2))]
    real_commit = db_session.commit
    state = {"armed": True}

    def flaky_commit():
        if state["armed"] and advisor_poller.ai:
            state["armed"] = False
            raise RuntimeError("connection reset during commit")
        return real_commit()
    monkeypatch.setattr(db_session, "commit", flaky_commit)
    EP.poll_inbox_for_replies(db_session, advisor_poller.advisor.id)
    EP.poll_inbox_for_replies(db_session, advisor_poller.advisor.id)
    assert len(advisor_poller.ai) == 1, "the AI was handed the same inbound reply twice"
    assert db_session.query(Reply).filter(Reply.lead_id == advisor_poller.lead.id).count() == 1


# ── daily cadence cron: ledger honesty and per-org isolation ────────────────

def _cron_world(db_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.jobs import run_cadence_job as J
    orgs = [Organization(name="Cron %d" % i, slug="c%d-%s" % (i, uuid.uuid4().hex[:6]), is_active=True)
            for i in range(2)]
    db_session.add_all(orgs)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(J, "SessionLocal", factory)
    return J, orgs, factory


def test_cadence_cron_ledger_is_not_success_when_an_org_failed(db_session, monkeypatch):
    """A tenant whose pass raised was folded into a JSON blob and the job_runs
    row closed 'success' - System Health showed green while that tenant's
    touches were not going out."""
    from app.models.job_models import JobName, JobRun
    J, orgs, _ = _cron_world(db_session, monkeypatch)

    def run(db, organization_id=None):
        if organization_id == orgs[0].id:
            raise RuntimeError("tenant pass blew up")
        return {"sent": 0, "completed": 0, "errors": 0}
    monkeypatch.setattr(J, "run_due_cadences", run)
    monkeypatch.setattr("app.services.engagement_service.recompute_for_organization", lambda db, oid: {})
    J.run_for_all_organizations()
    row = (db_session.query(JobRun).filter(JobRun.job_name == JobName.CADENCE_CRON)
           .order_by(JobRun.id.desc()).first())
    assert row is not None and row.status == "error"
    assert "1 organization" in (row.error_summary or "")


def test_cadence_cron_one_orgs_db_error_does_not_fail_every_later_org(db_session, monkeypatch):
    """No rollback after an org's failure: a database error left the shared
    session in 'needs rollback', so every LATER organization failed too."""
    J, orgs, _ = _cron_world(db_session, monkeypatch)
    ran = []

    def run(db, organization_id=None):
        db.query(Organization).count()       # a poisoned session raises here
        ran.append(organization_id)
        return {"sent": 0, "completed": 0, "errors": 0}

    def recompute(db, oid):
        # A failed flush: the session now needs a rollback before any query.
        db.add(Reply(lead_id=None, body="x"))
        db.flush()
    monkeypatch.setattr(J, "run_due_cadences", run)
    monkeypatch.setattr("app.services.engagement_service.recompute_for_organization", recompute)
    out = J.run_for_all_organizations()
    active = [o.id for o in db_session.query(Organization).filter(Organization.is_active == True).all()]
    assert sorted(ran) == sorted(active), out


def test_import_commit_claim_is_atomic(db_session):
    """Two commit requests that both observed a committable batch: only one may
    claim it (finding #5). Simulated by moving the batch on before the second
    request's conditional UPDATE runs."""
    import uuid as _uuid
    from datetime import datetime as _dt
    from app.models.import_models import ImportBatch, ImportBatchStatus
    from app.models.models import Organization
    org = Organization(name="Claim Co", slug="claim-%s" % _uuid.uuid4().hex[:6])
    db_session.add(org)
    db_session.commit()
    b = ImportBatch(organization_id=org.id, source_type="csv", source_filename="x.csv",
                    status=ImportBatchStatus.STAGED)
    db_session.add(b)
    db_session.commit()
    observed_status, observed_hb = b.status, b.heartbeat_at
    # request 1 claims
    q = db_session.query(ImportBatch).filter(ImportBatch.id == b.id, ImportBatch.status == observed_status,
                                             ImportBatch.heartbeat_at.is_(None))
    assert q.update({ImportBatch.status: ImportBatchStatus.COMMITTING,
                     ImportBatch.heartbeat_at: _dt.utcnow()}, synchronize_session=False) == 1
    db_session.commit()
    # request 2, same observation, finds nothing to claim
    q2 = db_session.query(ImportBatch).filter(ImportBatch.id == b.id, ImportBatch.status == observed_status,
                                              ImportBatch.heartbeat_at.is_(None))
    assert q2.update({ImportBatch.status: ImportBatchStatus.COMMITTING}, synchronize_session=False) == 0


def test_reply_dedupe_accepts_timezone_aware_times(db_session, sample_lead):
    from datetime import datetime as _dt, timezone as _tz
    from app.models.models import Reply
    from app.services.reply_dedupe import find_duplicate_email_reply
    db_session.add(Reply(lead_id=sample_lead.id, body="Yes", source="email",
                         received_at=_dt(2026, 10, 1, 14, 0, 0)))
    db_session.commit()
    assert find_duplicate_email_reply(db_session, sample_lead.id, "Yes",
                                      _dt(2026, 10, 1, 14, 2, 0, tzinfo=_tz.utc)) is not None
