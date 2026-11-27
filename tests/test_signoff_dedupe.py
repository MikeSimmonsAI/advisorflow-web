"""A sign-off the person already typed is not added again, even when they
wrote "&" for "and" (Restland reply to H. Faris, Nov 26: the text ended
"Restland Cemetery & Funeral Home - Mike Simmons, Restland Cemetery and
Funeral Home Reply STOP to opt out.")."""
from types import SimpleNamespace

from app.services.programs import identity
from app.services.programs.email_reply import _norm_sig


def _stubs(monkeypatch):
    prog = SimpleNamespace(primary_contact_name="Mike Simmons")
    prof = SimpleNamespace(official_name="Restland Cemetery and Funeral Home", sms_identity_name=None)
    monkeypatch.setattr(identity, "sms_signoff", lambda p, f: "- Mike Simmons, Restland Cemetery and Funeral Home")
    return prog, prof


def test_norm_sig_reads_ampersand_as_and():
    assert _norm_sig("Restland Cemetery & Funeral  Home") == _norm_sig("restland cemetery and funeral home")


def test_typed_signature_with_ampersand_is_not_repeated(monkeypatch):
    prog, prof = _stubs(monkeypatch)
    body = "What day works?\n\nBest,\nMike Simmons\nRestland Cemetery & Funeral Home"
    assert identity.with_signoff(prog, prof, body) == body


def test_signoff_still_added_when_missing_and_stays_before_stop(monkeypatch):
    prog, prof = _stubs(monkeypatch)
    assert identity.with_signoff(prog, prof, "What day works?") == \
        "What day works? - Mike Simmons, Restland Cemetery and Funeral Home"
    assert identity.with_signoff(prog, prof, "What day works? Reply STOP to opt out.") == \
        "What day works? - Mike Simmons, Restland Cemetery and Funeral Home Reply STOP to opt out."


def test_location_alone_without_person_still_gets_signoff(monkeypatch):
    prog, prof = _stubs(monkeypatch)
    out = identity.with_signoff(prog, prof, "Visit Restland Cemetery & Funeral Home soon.")
    assert out.endswith("- Mike Simmons, Restland Cemetery and Funeral Home")
