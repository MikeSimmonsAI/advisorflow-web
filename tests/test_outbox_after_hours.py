"""After the sending window closes, follow-up texts due tonight are listed as
going tomorrow morning, not as "still scheduled today"."""
from datetime import datetime, timedelta

from app.models.models import CadenceState, Lead
from app.services import contact_hours as ch


def test_after_hours_touch_moves_to_next_morning():
    lead = Lead(first_name="A", last_name="B", state="TX", phone="12145550101")
    # 9:00 PM Central = 02:00 UTC next day: outside 8:30 AM - 7:45 PM Eastern.
    night = datetime(2026, 10, 14, 2, 0)
    assert not ch.is_permitted(lead, night)
    go = ch.next_permitted_utc(lead, night)
    assert go is not None and go > night + timedelta(hours=8)
