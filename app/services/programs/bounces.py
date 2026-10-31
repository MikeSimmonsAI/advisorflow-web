"""#449: what a "couldn't be delivered" notice actually says.

Program email goes out through Microsoft 365. Microsoft does not report
delivery; it only reports FAILURE, as a notice (NDR) back to the sending
mailbox. So the honest outcomes for one email are:

    accepted     Microsoft took it (EmailMessage.status "sent")
    permanent    the receiving server says the address does not exist
                 (5.1.x, "recipient not found", "Not our Customer", ...)
    rejected     the receiving server refused it for its own policy - e.g. a
                 mistyped domain whose server answers "Relay access denied"
    expired      Microsoft tried for a day or more and the receiving server
                 never accepted ("Despite repeated attempts", 4.4.7 / 5.4.7)
    temporary    a delay notice; Microsoft is still trying
    sender_config  OUR side: authentication / tenant sending limits. Never
                 blamed on the family's address.

"Delivered" is never claimed: no receipt exists. An email with no failure
notice is "accepted - no failure reported", not "delivered".

Only the code and the receiving server's own sentence are used. A notice that
merely EXPLAINS relaying ("support is not authorized to relay ...") is not a
sender problem when the code says the REMOTE server refused (5.7.367): that is
the Oct 31 atot.com case - a mistyped domain - and the address is what's wrong.
"""
import re
from typing import Dict, Optional

PERMANENT, REJECTED, EXPIRED, TEMPORARY, SENDER_CONFIG, BLOCKED = (
    "permanent", "rejected", "expired", "temporary", "sender_config", "blocked")
KINDS = (PERMANENT, REJECTED, EXPIRED, TEMPORARY, SENDER_CONFIG, BLOCKED)

# Address-level outcomes: the address itself is the problem -> flag it so it
# is never emailed again until a person corrects or confirms it.
FLAG_ADDRESS = (PERMANENT, REJECTED)
# Repeated expiry (the server never answers) is treated the same on the
# SECOND notice for the same address: Wilbert's mail expired four times
# (Oct 25 - Oct 31) and kept being sent.
EXPIRED_FLAG_AFTER = 2

LABELS = {
    PERMANENT: "Address does not exist",
    REJECTED: "Refused by the receiving server",
    EXPIRED: "Receiving server never accepted it",
    TEMPORARY: "Delayed - still being tried",
    SENDER_CONFIG: "Our sending setup was refused",
    BLOCKED: "Blocked by their spam filter",
}

_CODE = re.compile(r"\b([245])\.(\d{1,3})\.(\d{1,3})\b")
# The receiving server's own words, in the shapes Microsoft writes them.
_REMOTE = [
    re.compile(r"(?is)Remote server returned\s*'([^']{3,400})'"),
    re.compile(r"(?is)Error:\s*(?:</?\w[^>]*>\s*)*([0-9]{3}\s+[245]\.\d{1,3}\.\d{1,3}[^<\r\n]{0,300})"),
    re.compile(r"(?is)gave this error:\s*(?:<br\s*/?>|\s)*([^<\r\n]{3,300})"),
]
_STATUS = re.compile(r"(?is)Status code:\s*([0-9]{3}\s+[245]\.\d{1,3}\.\d{1,3})")

_SENDER_CODES = {"5.7.23", "5.7.25", "5.7.26", "5.7.57", "5.7.509", "5.7.510", "5.7.511", "5.7.700",
                 "5.7.703", "5.7.705", "5.7.708", "5.7.750", "5.7.501", "5.7.502", "5.7.503", "5.7.505"}
_PERMANENT_WORDS = re.compile(
    r"(?i)(recipient not found|not our customer|user unknown|no such user|does not exist|doesn'?t exist|"
    r"address not found|wasn'?t found at|mailbox unavailable|unknown user|invalid recipient|"
    r"account (has been )?disabled|no mailbox here|recipient address rejected: user)")
_EXPIRED_WORDS = re.compile(
    r"(?i)(despite repeated attempts|message expired|couldn'?t be delivered in time|"
    r"retry time(out)? (has been )?exceeded|delivery time expired)")
_DELAY_WORDS = re.compile(r"(?i)(delivery (has been )?delayed|delay(ed)? notification|still trying|"
                          r"will (continue|keep) trying|mailbox (is )?full|over quota|try again later)")
_RELAY_REMOTE = re.compile(r"(?i)(relay access denied|not permitted to relay|relaying denied|5\.7\.367|"
                           r"recipient address rejected|recipient rejected)")
# The receiving side refused OUR message (spam / reputation / blocklist), not
# the address: never a reason to stop writing to the family.
_FILTER_WORDS = re.compile(r"(?i)(spam|blocklist|blacklist|block ?list|spamhaus|reputation|content filter|"
                           r"client host .{0,40}blocked|poor reputation|message rejected due to|policy)")


def _text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw or "")
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = (t.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&#39;", "'")
          .replace("&quot;", '"').replace("&nbsp;", " "))
    # The original message's headers ride along; they say nothing about the
    # failure and carry SPF/DKIM words that must not be read as one.
    for marker in ("Original message headers", "Original Message Headers"):
        i = t.find(marker)
        if i > 0:
            t = t[:i]
    return re.sub(r"[ \t]+", " ", t)


def remote_reason(raw: str) -> Optional[str]:
    """The receiving server's own sentence, e.g. "550 5.1.0 Not our Customer"."""
    for rx in _REMOTE:
        m = rx.search(raw or "")
        if m:
            s = re.sub(r"\s+", " ", _text(m.group(1))).strip(" .'")
            if s:
                return s[:300]
    m = _STATUS.search(_text(raw))
    return m.group(1).strip() if m else None


def classify(raw_body: str, subject: str = "") -> Dict[str, Optional[str]]:
    """{kind, code, reason, label}. `raw_body` is the notice's HTML or text."""
    text = _text(raw_body or "")
    reason = remote_reason(raw_body or "")
    hay = "%s\n%s\n%s" % (subject or "", reason or "", text)
    codes = ["%s.%s.%s" % c for c in _CODE.findall((reason or "") + "\n" + text)]
    # The remote server's code wins over the explanatory text's.
    code = None
    if reason:
        m = _CODE.search(reason)
        code = "%s.%s.%s" % m.groups() if m else None
    code = code or next((c for c in codes if not c.startswith("2.")), None)
    kind = None
    if _EXPIRED_WORDS.search(hay) or re.search(r"(?i)queue\.expired", hay) or code in ("5.4.7", "5.4.316"):
        kind = EXPIRED                                   # final: Microsoft gave up
    elif _RELAY_REMOTE.search(hay):
        kind = REJECTED                                  # the RECEIVING server refused
    elif code in _SENDER_CODES:
        kind = SENDER_CONFIG
    elif code and (code.startswith("5.1.") or code in ("5.4.1", "5.4.4", "5.4.310", "5.2.1")) \
            or _PERMANENT_WORDS.search(hay):
        kind = PERMANENT
    elif code and code.startswith("5.7.") and _FILTER_WORDS.search(hay):
        kind = BLOCKED
    elif code and code.startswith("5.7."):
        kind = REJECTED
    elif (code and code.startswith("4.")) or _DELAY_WORDS.search(hay):
        kind = TEMPORARY
    elif code and code.startswith("5.") and _FILTER_WORDS.search(hay):
        kind = BLOCKED
    elif code and code.startswith("5."):
        kind = PERMANENT if code.startswith("5.1.") else REJECTED
    else:
        kind = PERMANENT if re.search(r"(?i)couldn'?t be delivered|could not be delivered", hay) else TEMPORARY
    if not reason:
        m = re.search(r"(?i)(Your message to \S+ couldn'?t be delivered\.?[^\n]{0,160})", text)
        reason = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    return {"kind": kind, "code": code, "reason": reason, "label": LABELS[kind]}


def message_status(kind: str) -> str:
    """EmailMessage.status for a notice of this kind."""
    return "soft_bounced" if kind == TEMPORARY else "bounced"


def describe(kind: Optional[str], reason: Optional[str]) -> Optional[str]:
    if not kind:
        return None
    return "%s%s" % (LABELS.get(kind, kind), (": %s" % reason) if reason else "")
