"""A "couldn't be delivered" notice in the sending mailbox marks our email
bounced and flags the address so it is not emailed again (Oct 13: James Hill's
Gmail address did not exist; the notice sat in the support mailbox and the app
still said "sent")."""
from types import SimpleNamespace

from app.models.inbound_mailbox_models import InboundMailboxMessage
from app.models.models import EmailMessage, Lead, Reply
from app.services import inbound_mailbox_service as S
from tests.test_inbound_mailbox import BOX, _msg, world  # noqa: F401  (fixture)

NDR = ("Your message to josh@example.com couldn't be delivered. josh wasn't found at example.com. "
       "support Action Required Unknown To address. Status code: 550 5.1.1 "
       "Original Message Details Sender Address: support@evosyspro.live Recipient Address: josh@example.com "
       "Subject: Following up, Joshua Error: 550-5.1.1 The email account that you tried to reach does not exist.")


def _ndr(gid="n1", sender="MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@evosyspro.live", body=NDR,
         subject="Undeliverable: Following up, Joshua"):
    m = _msg(gid, sender, body, subject=subject)
    m["from"]["emailAddress"]["name"] = "Microsoft Outlook"
    m["toRecipients"] = [{"emailAddress": {"address": BOX}}]
    return m


def test_bounce_notice_marks_email_bounced_and_flags_address(db_session, world):
    res = S.poll_mailbox(db_session, world.box, fetch=lambda since: [_ndr()])
    assert res["errors"] == 0
    em = db_session.query(EmailMessage).filter(EmailMessage.lead_id == world.lead.id).one()
    assert em.status == "bounced"
    db_session.refresh(world.lead)
    assert world.lead.manual_flag == "bad_email"
    row = db_session.query(InboundMailboxMessage).one()
    assert row.outcome == "bounce" and "bounced" in row.detail
    # It is not treated as the person replying.
    assert db_session.query(Reply).filter(Reply.lead_id == world.lead.id).count() == 0


def test_mailbox_full_bounce_does_not_flag_the_address(db_session, world):
    body = "Delivery has failed to these recipients: josh@example.com. The mailbox is full; try again later."
    S.poll_mailbox(db_session, world.box, fetch=lambda since: [_ndr(body=body)])
    em = db_session.query(EmailMessage).filter(EmailMessage.lead_id == world.lead.id).one()
    assert em.status == "bounced"
    db_session.refresh(world.lead)
    assert world.lead.manual_flag in (None, "")


def test_a_real_reply_is_not_mistaken_for_a_bounce(db_session, world):
    m = _msg("r1", "josh@example.com", "Thanks, that address does not exist anymore at my old job, use this one.")
    assert S.bounce_addresses(m, own={BOX}) is None
