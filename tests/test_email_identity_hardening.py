"""WHO THE MAIL SAYS IT IS FROM, WHO GETS A SILENT COPY, AND WHAT WE KEEP.

Three rules, asserted against the shapes production actually produces:

  1. a brand names itself in the From header, from its own identity
  2. no white-label can fall through to another brand's sender
  3. an audit copy is brand-scoped, and never taken of a credential

The fourth block is the one that matters most: a message carrying a setup
link, a reset link or a one-time code gets NO BCC and leaves behind only an
envelope with no token, no URL and no body in it.
"""

import pytest

from app.services import email_identity as ei


# ── duck-types matching what the sender is really handed ────────────────────

class _Identity(object):
    """Shaped like public_identity.SendingIdentity / launch_invitation._BrandSender."""

    def __init__(self, from_email=None, from_name=None, audit_bcc_email=None,
                 resolved=True):
        self.from_email = from_email
        self.from_name = from_name
        self.audit_bcc_email = audit_bcc_email
        self.resolved = resolved
        self.reply_to_email = from_email
        self.cc_email = None
        self.resend_api_key = None


EVOSYS = _Identity("support@evosyspro.live", "EvoSys Pro",
                   audit_bcc_email="support@evosyspro.live")
BOOKABOOST = _Identity("support@bookaboost.live", "BookaBoost",
                       audit_bcc_email="audit@bookaboost.live")
UNCONFIGURED = _Identity(None, "Harmony & Hustle Group")


# ═══════════════════════════════════════════════════════════════════════════
# 1. EvoSys gets a branded display name
# ═══════════════════════════════════════════════════════════════════════════

def test_evosys_sends_under_its_own_name():
    assert ei.format_from(EVOSYS.from_name, EVOSYS.from_email) == \
        '"EvoSys Pro" <support@evosyspro.live>'


def test_a_bare_address_is_still_valid_when_no_name_is_configured():
    # The change must not make an unnamed brand unsendable.
    assert ei.format_from(None, "support@evosyspro.live") == "support@evosyspro.live"
    assert ei.format_from("", "support@evosyspro.live") == "support@evosyspro.live"


def test_no_address_is_no_sender_whatever_the_name_says():
    assert ei.format_from("EvoSys Pro", None) is None
    assert ei.format_from("EvoSys Pro", "   ") is None


def test_a_display_name_cannot_inject_a_header():
    """A brand name is configuration, not user input — but a header injection
    through a brand row is still a header injection. The newline is what
    creates a new header; the quoting is what keeps the rest inert."""
    out = ei.format_from('Evil\r\nBcc: attacker@example.com', "a@b.test")
    assert "\r" not in out and "\n" not in out
    # The whole name stays inside one quoted-string, so a colon in it cannot
    # start a header field, and the address is still the only thing in <>.
    assert out.startswith('"') and out.endswith("<a@b.test>")
    assert out.count('"') == 2


def test_a_display_name_cannot_smuggle_a_second_address():
    out = ei.format_from('Real Brand <evil@attacker.test>', "a@b.test")
    assert out == '"Real Brand evil@attacker.test" <a@b.test>'
    assert out.count("<") == 1 and out.count(">") == 1


# ═══════════════════════════════════════════════════════════════════════════
# 2. another white-label resolves its own identity, independently
# ═══════════════════════════════════════════════════════════════════════════

def test_each_brand_names_itself_and_never_the_other():
    evo = ei.format_from(EVOSYS.from_name, EVOSYS.from_email)
    bb = ei.format_from(BOOKABOOST.from_name, BOOKABOOST.from_email)
    assert "EvoSys Pro" in evo and "evosyspro.live" in evo
    assert "BookaBoost" in bb and "bookaboost.live" in bb
    assert "bookaboost" not in evo.lower()
    assert "evosys" not in bb.lower()


def test_bookaboost_cannot_leak_into_an_evosys_sender():
    """THE LEAK THIS EXISTS TO CLOSE.

    EMAIL_FROM_ADDRESS is `noreply@bookaboost.live` on a deployment serving
    three brands. A resolved identity with no address must refuse, not borrow.
    `format_from` returning None is what the sender's refusal branch keys on.
    """
    assert UNCONFIGURED.resolved is True
    assert ei.format_from(UNCONFIGURED.from_name, UNCONFIGURED.from_email) is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. safe operational email gets the brand's audit BCC
# ═══════════════════════════════════════════════════════════════════════════

def test_operational_mail_is_copied_to_the_brands_audit_mailbox():
    assert ei.audit_bcc_for(EVOSYS, "appointment_confirmation",
                            ei.OPERATIONAL) == "support@evosyspro.live"


def test_the_audit_mailbox_is_brand_scoped_not_global():
    # Each brand's copy goes to its own mailbox, and a brand without one
    # configured gets no copy at all — there is no deployment-wide default.
    assert ei.audit_bcc_for(BOOKABOOST, "receipt", ei.OPERATIONAL) == \
        "audit@bookaboost.live"
    assert ei.audit_bcc_for(UNCONFIGURED, "receipt", ei.OPERATIONAL) is None


def test_no_bcc_when_the_brand_has_not_configured_one():
    plain = _Identity("support@evosyspro.live", "EvoSys Pro")
    assert ei.audit_bcc_for(plain, "receipt", ei.OPERATIONAL) is None


def test_an_undeclared_message_gets_no_copy():
    """Silence is not consent. A caller that declares nothing is a caller
    nobody has checked, and the failure modes are not symmetrical."""
    assert ei.audit_bcc_for(EVOSYS, "something_new", None) is None
    assert ei.is_sensitive("something_new", None) is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. activation / security mail gets NO BCC
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("message_type", sorted(ei.CREDENTIAL_MESSAGE_TYPES))
def test_no_credential_message_is_ever_copied(message_type):
    assert ei.audit_bcc_for(EVOSYS, message_type, ei.OPERATIONAL) is None
    assert ei.audit_bcc_for(EVOSYS, message_type, ei.SENSITIVE) is None


def test_the_message_type_overrules_an_operational_claim():
    """The type is the fact; the caller's declaration is an opinion. A caller
    that mislabels an activation link as operational still gets no copy."""
    assert ei.is_sensitive("onboarding_invitation", ei.OPERATIONAL) is True
    assert ei.audit_bcc_for(EVOSYS, "onboarding_invitation",
                            ei.OPERATIONAL) is None


def test_the_launch_invitation_sender_carries_no_audit_mailbox_at_all():
    """Defence in depth: two independent things must fail before a setup link
    could be copied anywhere."""
    from app.services.launch_invitation import _BrandSender
    sender = _BrandSender("support@evosyspro.live", None, brand_name="EvoSys Pro")
    assert sender.audit_bcc_email is None
    assert sender.from_name == "EvoSys Pro"
    assert sender.resolved is True
    assert ei.audit_bcc_for(sender, "onboarding_invitation", ei.OPERATIONAL) is None


# ═══════════════════════════════════════════════════════════════════════════
# 5. the security audit record contains no token, link or secret
# ═══════════════════════════════════════════════════════════════════════════

_ACTIVATION_URL = ("https://app.evosyspro.live/activate"
                   "?token=stf_Z1MHevXZ3tEgukSRDpv0TtdF6hLD50m5aM2g6erY7nw")


def test_the_record_keeps_only_the_envelope():
    rec = ei.safe_delivery_record(
        platform_id="plt-evosyspro", brand_name="EvoSys Pro",
        organization_id="org-1", recipient="someone@example.invalid",
        message_type="onboarding_invitation",
        template_id="launch.onboarding_invitation",
        subject="Your EvoSys Pro onboarding",
        from_email="support@evosyspro.live", sent_at="2026-09-14T16:38:45Z",
        provider="resend", provider_message_id="msg_abc",
        delivery_state="sent", delivery_error=None)

    assert rec["brand_name"] == "EvoSys Pro"
    assert rec["recipient"] == "someone@example.invalid"
    assert rec["message_type"] == "onboarding_invitation"
    assert rec["from_email"] == "support@evosyspro.live"
    assert rec["provider"] == "resend"
    assert rec["provider_message_id"] == "msg_abc"
    assert rec["delivery_state"] == "sent"
    # Nothing beyond the agreed envelope may appear.
    assert set(rec) == set(ei._ALLOWED_RECORD_KEYS)


def test_the_record_function_cannot_be_handed_a_body_or_a_link():
    """There is deliberately no parameter for the message, so the record
    cannot contain what the function was never given."""
    with pytest.raises(TypeError):
        ei.safe_delivery_record(body_html="<a href='%s'>set up</a>" % _ACTIVATION_URL)
    with pytest.raises(TypeError):
        ei.safe_delivery_record(activation_url=_ACTIVATION_URL)


def test_a_url_that_arrives_anyway_is_redacted_not_stored():
    """A provider error string can quote the URL it just rejected."""
    rec = ei.safe_delivery_record(
        subject="Your onboarding: %s" % _ACTIVATION_URL,
        delivery_error="Rejected destination for %s" % _ACTIVATION_URL,
        recipient="someone@example.invalid")
    blob = repr(rec)
    assert "stf_" not in blob
    assert "token=" not in blob
    assert "https://" not in blob
    assert rec["subject"] == "[redacted]"
    assert rec["delivery_error"] == "[redacted]"
    # The parts that are not secret survive.
    assert rec["recipient"] == "someone@example.invalid"


@pytest.mark.parametrize("secret", [
    _ACTIVATION_URL,
    "https://app.evosyspro.live/reset?t=abc123",
    "token=abc123",
    "code: 998877",
    "http://x.test/magic?k=zzz",
])
def test_every_secret_shape_is_caught(secret):
    rec = ei.safe_delivery_record(subject=secret)
    assert rec["subject"] == "[redacted]"


def test_ordinary_values_are_not_over_redacted():
    rec = ei.safe_delivery_record(
        subject="Your EvoSys Pro onboarding",
        brand_name="Harmony & Hustle Group, LLC",
        delivery_error="The mail provider refused it.")
    assert rec["subject"] == "Your EvoSys Pro onboarding"
    assert rec["brand_name"] == "Harmony & Hustle Group, LLC"
    assert rec["delivery_error"] == "The mail provider refused it."
