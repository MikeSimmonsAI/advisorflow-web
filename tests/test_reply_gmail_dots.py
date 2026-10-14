"""Oct 14: we emailed l.laffiette@gmail.com; Larry replied from
llaffiette@gmail.com (Gmail ignores dots) and the reply was logged "no lead".
Gmail dots/+tags now match, and a reply already logged that way is re-read."""
from datetime import datetime, timedelta

from app.models.inbound_mailbox_models import InboundMailboxMessage
from app.models.models import Reply
from app.services import inbound_mailbox_service as S
from tests.test_inbound_mailbox import BOX, _msg, world  # noqa: F401  (fixture)


def _gmail(world, db):
    world.lead.email = "L.Laffiette@gmail.com"
    db.commit()


def test_address_key():
    assert S.address_key("L.Laf.fiette+sci@GoogleMail.com") == "llaffiette@gmail.com"
    assert S.address_key("j.doe@yahoo.com") == "j.doe@yahoo.com"


def test_reply_from_dotless_gmail_lands_on_the_lead(db_session, world):
    _gmail(world, db_session)
    res = S.poll_mailbox(db_session, world.box, fetch=lambda since: [
        _msg("g1", "llaffiette@gmail.com", "Yes I will get back with you I am on a house")])
    assert res["matched"] == 1
    assert db_session.query(Reply).filter(Reply.lead_id == world.lead.id).count() == 1


def test_a_reply_already_logged_no_lead_is_reread(db_session, world):
    _gmail(world, db_session)
    seen = datetime.utcnow() - timedelta(hours=2)
    db_session.add(InboundMailboxMessage(mailbox_id=world.box.id, graph_message_id="g2", outcome="no_lead",
                                         from_address="llaffiette@gmail.com", subject="Re: Explore Your Energy Options",
                                         received_at=seen))
    world.box.cursor_received_at = datetime.utcnow()
    db_session.commit()
    asked = []

    def fetch(since):
        asked.append(since)
        return [_msg("g2", "llaffiette@gmail.com", "Yes I will get back with you", minutes_ago=120)]
    S.poll_mailbox(db_session, world.box, fetch=fetch)
    assert asked[0] <= seen                                   # read back far enough
    assert db_session.query(Reply).filter(Reply.lead_id == world.lead.id).count() == 1
    row = db_session.query(InboundMailboxMessage).filter_by(graph_message_id="g2").one()
    assert row.outcome == "matched"


def test_m365_program_reports_not_tracked_not_zero():
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.routers import program_router as R
    db = MagicMock()
    out = R._reporting(db, [], [], SimpleNamespace(send_via_mailbox="support@evosyspro.live"))
    e = out["email"]
    assert e["delivered"] is None and e["reached"] == 0
    assert "Microsoft" in e["note"]


def test_open_pixel_goes_in_sent_mail_not_saved_body(monkeypatch):
    from app.services import email_service as E
    monkeypatch.setenv("PROGRAM_ASSET_BASE_URL", "https://api.example.test")
    out = E._with_open_pixel("<html><body><p>Hi</p></body></html>", "abc")
    assert 'src="https://api.example.test/email-tracking/open/abc"' in out
    assert out.index("open/abc") < out.lower().index("</body>")
