# -*- coding: utf-8 -*-
"""EvoSys Sales one-time owner changes (owner, Nov 23 2026)."""
from tests.test_evosys_sales_texting import _ready, _brand_env  # noqa: F401  (autouse env fixture)
from tests.test_sales_workspace import platform, brand  # noqa: F401  (fixtures)


def test_gov_list_campaigns_switch_to_the_booking_link_once(db_session, brand):
    from app.services.evosys_sales import campaigns as CA, owner_changes as OC
    s = _ready(db_session, brand)
    c = CA.ensure_default(db_session, s)
    c.name = "Gov List Cleaners - Texas (texts)"
    c.steps = [{"day": 0, "channel": "text", "subject": "", "body": "Hi {company}... Reply YES and I'll set it up."}]
    c.sending_on = False
    db_session.commit()
    v, who = c.version, c.updated_by
    assert OC.apply_pending(db_session) == {"gov_lists_booking_link": ["Gov List Cleaners - Texas (texts)"]}
    db_session.refresh(c)
    assert len(c.steps) == 3 and all("{booking_link}" in st["body"] for st in c.steps)
    assert not any("reply yes" in st["body"].lower() for st in c.steps)
    assert c.version == v + 1 and c.sending_on is False and not CA.is_approved(c, s)
    assert c.updated_by == who                         # a real user id, never a label
    assert OC.apply_pending(db_session) == {}          # never again
