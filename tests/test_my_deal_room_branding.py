# -*- coding: utf-8 -*-
"""My Deal Room rebrand (owner directive, Nov 2026): BRANDING ONLY.

EvoSys Pro's Wholesale product is now called "My Deal Room". Pinned here:
  - EvoSys workspaces: team invitation email and the product name say My Deal Room;
  - BookaBoost keeps exactly the words it had (no My Deal Room anywhere);
  - text messages keep the carrier-registered program name "EvoSys Wholesale"
    (renaming SMS copy needs the registration updated first);
  - an EvoSys workspace with no brand of its own shows My Deal Room on its
    investor portal; a workspace with its own brand (BookaBoost Wholesale)
    keeps it; nothing about routes or links changes.
Synthetic data; no email leaves the test."""
from app.services import brand_config as BC
from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_wholesale_access_builder import _ok, _profile, sent, ws  # noqa: F401


def _identity(monkeypatch):
    from app.services import launch_invitation as LI
    monkeypatch.setattr(LI, "_sending_identity",
                        lambda db, org, brand: type("I", (), {"from_email": "support@brand.example.com"})())


def test_product_name_is_my_deal_room_for_evosys_only():
    assert BC.product_name("evosyspro", "wholesale") == "My Deal Room"
    assert BC.product_name("bookaboost", "wholesale") is None          # BookaBoost names its own


def test_evosys_team_invitation_says_my_deal_room(ws, monkeypatch):
    p = ws
    _identity(monkeypatch)
    prof = _profile(p, "Field", "field_representative")
    out = _ok(p["c"].post("/wholesale/team/invites", headers=p["owner_h"],
                          json={"email": "mdr.invite@example.com", "profile_id": prof["id"], "send": True}))
    assert out["delivery"]["state"] == "sent"
    mail = p["sent"][-1]
    assert mail["subject"] == "You're invited to My Deal Room"
    assert "the My Deal Room workspace for" in mail["body"]
    assert "/activate?token=" in out["link"]                            # same link as before


def test_bookaboost_invitation_keeps_its_own_words(ws, monkeypatch):
    p = ws
    _identity(monkeypatch)
    prof = _profile(p, "VA", "research_assistant", h=p["bab_h"])
    _ok(p["c"].post("/wholesale/team/invites", headers=p["bab_h"],
                    json={"email": "bab.invite@example.com", "profile_id": prof["id"], "send": True,
                          "base_url": "https://app.bookaboost.live"}))
    mail = p["sent"][-1]
    assert "BookaBoost" in mail["subject"] and "My Deal Room" not in mail["subject"] + mail["body"]
    assert "EvoSys" not in mail["body"]


def test_text_messages_keep_the_registered_program_name(ws):
    p, db = ws, ws["db"]
    from app.services import wholesale_sms as S
    assert S.SMS_REGISTERED_NAMES == {"evosyspro": "EvoSys Wholesale"}
    assert "My Deal Room" not in S.OPT_IN_CONFIRMATION + S.HELP_REPLY


def test_investor_portal_default_brand(ws):
    p, db = ws, ws["db"]
    from app.services import wholesale_deal_room_setup as SET
    evo = SET.view(db, p["evo"].id)["settings"]
    bab = SET.view(db, p["bab"].id)["settings"]
    if not getattr(p["evo"], "brand_name", None):
        assert evo["brand_name"] == "My Deal Room" and evo["name"] == "My Deal Room"
    assert "My Deal Room" not in str(bab.get("brand_name")) + str(bab.get("name"))
