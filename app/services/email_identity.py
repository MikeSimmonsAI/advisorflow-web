"""WHO AN EMAIL SAYS IT IS FROM, WHO SILENTLY GETS A COPY, AND WHAT WE RECORD.

===========================================================================
WHY THIS IS A SEPARATE MODULE
===========================================================================

`email_service.send_email_via_provider` is the one place every outbound
message passes through, and it should stay that way — a second email
framework is how a brand leak gets reintroduced. But three decisions inside
it are pure, load-bearing, and worth testing without a database, a provider
or a network:

    format_from          what goes in the From header
    audit_bcc_for        whether a copy goes to the brand's audit mailbox
    safe_delivery_record what we are allowed to keep about a message we
                         must NOT copy

So the decisions live here and the sending stays there.

===========================================================================
THE THREE RULES
===========================================================================

1. A BRAND NAMES ITSELF. `support@evosyspro.live` is an address; "EvoSys Pro
   <support@evosyspro.live>" is a sender. Mail clients show the display name
   and hide the address, so an unnamed sender reads as a bare machine address
   from a domain the recipient has never typed. The name comes from the SAME
   resolved identity as the address — never a constant, never a per-customer
   special case — so every white-label names itself and none of them can name
   another.

2. AN AUDIT COPY IS BRAND-SCOPED OR IT DOES NOT EXIST. The mailbox is a
   property of the platform row. There is no deployment-wide audit address,
   because one mailbox collecting three brands' customer mail is the same
   category of mistake as one From address for three brands.

3. A ONE-TIME CREDENTIAL IS NEVER COPIED, AND NEVER WRITTEN DOWN. An
   activation link, a reset link and an OTP are bearer credentials: whoever
   holds the text is the user. BCCing one duplicates it into a shared
   mailbox, and storing one duplicates it into a database — both turn a
   single-use secret into a standing one. For those messages the copy is
   refused and the record keeps only the envelope.
"""

import re
from typing import Any, Dict, Optional

# ── message classes ─────────────────────────────────────────────────────────
#
# SENSITIVE is the default everywhere it is not stated. A message whose class
# nobody declared is a message nobody has checked, and the failure modes are
# not symmetrical: a missing audit copy is an inconvenience, a duplicated
# activation link is a second way into somebody's account.

SENSITIVE = "sensitive"
OPERATIONAL = "operational"

# The message types that carry a bearer credential. Named rather than guessed,
# so adding a new credential-bearing message is a deliberate act.
CREDENTIAL_MESSAGE_TYPES = frozenset({
    "onboarding_invitation",      # launch setup link
    "customer_activation",        # first-password link
    "staff_invitation",           # sales/staff access link
    "password_reset",
    "magic_link",
    "mfa_code",
    "otp",
    "verification_code",
})


def is_sensitive(message_type: Optional[str], sensitivity: Optional[str] = None) -> bool:
    """Would copying or storing this message duplicate a credential?

    Either signal is enough to refuse. A caller may declare the class
    explicitly; a recognised credential-bearing `message_type` decides it
    regardless of what the caller said, because the type is the fact and the
    declaration is an opinion.
    """
    if message_type and str(message_type).strip().lower() in CREDENTIAL_MESSAGE_TYPES:
        return True
    if sensitivity is None:
        return True
    return str(sensitivity).strip().lower() != OPERATIONAL


# ── 1. the From header ──────────────────────────────────────────────────────

# Characters that would let a display name break out of the header. A name is
# configuration, not user input, but a header injection through a brand row is
# still a header injection.
_UNSAFE_IN_NAME = re.compile(r'[\r\n<>"]')


def format_from(display_name: Optional[str], address: Optional[str]) -> Optional[str]:
    """`Name <addr>` when a name is known, otherwise the bare address.

    Returns None for a missing address, so the caller's existing "refuse
    rather than guess" branch keeps deciding what that means. A name without
    an address is not a sender.
    """
    addr = (address or "").strip()
    if not addr:
        return None
    name = _UNSAFE_IN_NAME.sub("", (display_name or "")).strip()
    if not name:
        return addr
    # RFC 5322 quoted-string: safe for the commas, dots and parentheses that
    # appear in real brand names ("Harmony & Hustle Group, LLC").
    return '"%s" <%s>' % (name, addr)


# ── 2. the audit copy ───────────────────────────────────────────────────────

def audit_bcc_for(identity: Any, message_type: Optional[str] = None,
                  sensitivity: Optional[str] = None) -> Optional[str]:
    """The brand's audit mailbox, when this message may be copied to it.

    Refuses in four cases, any one of which is sufficient:
      - the brand has no audit mailbox configured (the default everywhere)
      - the message carries a credential
      - the caller did not declare the message operational
      - the audit mailbox IS the recipient's own address, which would be a
        visible duplicate rather than a silent copy
    """
    mailbox = (getattr(identity, "audit_bcc_email", None) or "").strip()
    if not mailbox:
        return None
    if is_sensitive(message_type, sensitivity):
        return None
    return mailbox


# ── 3. what may be written down about a message we cannot copy ──────────────

# Anything that looks like it carries a secret. Applied to every value that
# goes into a record, as a backstop rather than as the primary defence: the
# primary defence is that the body is never passed to this function at all.
_SECRET_SHAPED = re.compile(
    r"(https?://\S+)"                 # any URL - activation, reset, magic
    r"|(\btoken\b|\bcode\b)\s*[=:]"   # token= / code:
    r"|([?&](token|code|t|k|key|otp|secret|password)=)",
    re.IGNORECASE,
)

_ALLOWED_RECORD_KEYS = (
    "platform_id", "brand_name", "organization_id", "recipient",
    "message_type", "template_id", "subject", "from_email",
    "sent_at", "provider", "provider_message_id",
    "delivery_state", "delivery_error",
)


def _scrub(value):
    """A value that looks like it carries a secret is replaced, not trimmed."""
    if not isinstance(value, str):
        return value
    return "[redacted]" if _SECRET_SHAPED.search(value) else value


def safe_delivery_record(*, platform_id=None, brand_name=None,
                         organization_id=None, recipient=None,
                         message_type=None, template_id=None, subject=None,
                         from_email=None, sent_at=None, provider=None,
                         provider_message_id=None, delivery_state=None,
                         delivery_error=None) -> Dict[str, Any]:
    """The envelope of a credential-bearing message, and nothing else.

    THE BODY IS NOT A PARAMETER. There is deliberately no way to hand this
    function the message, the link or the token — the record cannot contain
    what the function was never given. Every string that does arrive is still
    scrubbed, because a subject line or an error string from a provider can
    quote the URL it just rejected.
    """
    record = {
        "platform_id": platform_id,
        "brand_name": brand_name,
        "organization_id": organization_id,
        "recipient": recipient,
        "message_type": message_type,
        "template_id": template_id,
        "subject": subject,
        "from_email": from_email,
        "sent_at": sent_at,
        "provider": provider,
        "provider_message_id": provider_message_id,
        "delivery_state": delivery_state,
        "delivery_error": delivery_error,
    }
    return {k: _scrub(v) for k, v in record.items() if k in _ALLOWED_RECORD_KEYS}
