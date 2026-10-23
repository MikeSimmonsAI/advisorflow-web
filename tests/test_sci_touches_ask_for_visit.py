"""Mike, Oct 22: James got 'Would you like me to keep your request open, or
close it out?' - the mission is an appointment. Every touch asks for a visit
and carries the family's pick-a-time link; none invites a close-out."""
import re

import pytest

from app.services.programs import message_brain as brain
from tests.test_sci_platinum import _packet

URL = "https://parkhill.evosyspro.live/abc"
TOUCHES = (1, 2, 3, 4, 5, brain.REACTIVATION)
_CLOSE = r"(keep (this|your request) open|close (it|this|your request) out|filling your inbox)"
_VISIT = r"(visit|sit down|calendar|openings|a time|set it up|hold (it|a time))"


@pytest.mark.parametrize("touch", TOUCHES)
@pytest.mark.parametrize("channel", ["email", "sms"])
def test_every_touch_asks_for_a_visit_and_passes(touch, channel):
    m = brain.compose(_packet(), touch, channel, booking_url=URL)
    text = "%s %s" % (m.get("subject") or "", m["body"])
    assert not re.search(_CLOSE, text, re.I), text
    assert re.search(_VISIT, text, re.I), text
    assert brain.quality(_packet(), m)["ok"]


@pytest.mark.parametrize("touch", TOUCHES)
def test_every_email_touch_carries_the_booking_link(touch):
    assert URL in brain.compose(_packet(), touch, "email", booking_url=URL)["body"]


def test_without_a_link_the_email_still_reads_cleanly():
    body = brain.compose(_packet(), 4, "email")["body"]
    assert "http" not in body and "Pick a time" not in body and "{" not in body and "reply" in body.lower()
