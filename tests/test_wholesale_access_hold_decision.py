"""Deciding an investor's exclusive review is an approval, not a reply (#415/#439)."""
from app.services import wholesale_access as A

HOLD = "/wholesale/deal-room/holds/3f2a9c1e-5b7d-4e8a-9f10-2c3d4e5f6a7b"
THREAD = "/wholesale/deal-room/threads/3f2a9c1e-5b7d-4e8a-9f10-2c3d4e5f6a7b"


def test_hold_decision_needs_approve():
    assert A.module_for(HOLD) == "deal_room"
    assert A.action_for(HOLD, "POST") == "approve"
    assert A.action_for(HOLD, "GET") == "view"


def test_reply_is_create_note_is_edit():
    assert A.action_for(THREAD + "/reply", "POST") == "create"
    assert A.action_for(THREAD + "/note", "POST") == "edit"   # staff-only notes are an edit


def test_answer_only_login_cannot_decide_holds():
    eff = {"mode": "custom", "permissions": {"deal_room": ["view", "create"]}}
    assert A.allows(eff, "deal_room", A.action_for(THREAD + "/reply", "POST"))
    assert not A.allows(eff, "deal_room", A.action_for(HOLD, "POST"))


def test_hold_list_still_view():
    assert A.action_for("/wholesale/deal-room/holds", "GET") == "view"
