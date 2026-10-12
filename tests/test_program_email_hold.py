"""Email to held provider families (Yahoo / AOL / AT&T, Outlook / Hotmail) waits; texts still go."""
import json
from types import SimpleNamespace

from app.services.programs import email_touches as et


def test_provider_families():
    assert et.email_provider("Pat@Gmail.com") == "gmail"
    for a in ("x@yahoo.com", "x@aol.com", "x@sbcglobal.net", "x@att.net", "x@yahoo.co.uk", "x@ymail.com"):
        assert et.email_provider(a) == "yahoo", a
    for a in ("x@hotmail.com", "x@outlook.com", "x@live.com", "x@msn.com", "x@hotmail.co.uk"):
        assert et.email_provider(a) == "outlook", a
    assert et.email_provider("x@icloud.com") == "icloud"
    assert et.email_provider("x@restland.com") == "other"
    assert et.email_provider("") is None and et.email_provider(None) is None


def test_hold_reason_only_for_held_families():
    prog = SimpleNamespace(email_hold_providers=json.dumps(["yahoo", "outlook"]))
    assert "on hold" in et.provider_hold_reason(prog, "x@aol.com")
    assert "on hold" in et.provider_hold_reason(prog, "x@hotmail.com")
    assert et.provider_hold_reason(prog, "x@gmail.com") is None
    assert et.provider_hold_reason(prog, "x@icloud.com") is None
    assert et.provider_hold_reason(SimpleNamespace(email_hold_providers=None), "x@yahoo.com") is None
    assert et.provider_hold_reason(SimpleNamespace(email_hold_providers="not json"), "x@yahoo.com") is None


def test_skip_reason_holds_before_anything_else_is_checked():
    prog = SimpleNamespace(email_hold_providers=json.dumps(["yahoo"]))
    lead = SimpleNamespace(email="pat@yahoo.com", allow_email=True, status="new", id="L1")
    assert "on hold" in et._skip_reason(None, lead, False, prog)
