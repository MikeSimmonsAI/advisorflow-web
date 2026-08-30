"""
What an SMS is allowed to contain, enforced in one place.

The rule, and where it comes from
---------------------------------
The approved A2P campaign these messages send under - campaign `CO3YNIF`,
messaging service MG37d057536564f3228788425b0f83ec92, status VERIFIED, use
case LOW_VOLUME - is registered with:

    has_embedded_links : false
    has_embedded_phone : false

A message carrying a URL therefore contradicts the campaign's own declaration,
and the carrier rejects it as 30007 "Carrier violation". This was established
against five controlled sends on one number inside a 33-minute window: four
messages containing a booking URL were all filtered, and the one message
without a URL was delivered. Same sender, same recipient, same account,
content the only variable.

So the operating rule for the current sample is:

    SMS   = conversation and follow-up. No URL. Ever.
    EMAIL = the scheduling link.

Why a module rather than just better template text
--------------------------------------------------
Editing the five default templates is not sufficient, because a URL can enter
an SMS by four other routes: an org-level custom template from the template
editor, a cadence touch's own `message_template`, an AI-drafted body, and an
advisor typing or pasting a link into the composer by hand. Any one of those
puts the number back in front of a carrier filter. The guarantee has to sit at
the point every one of those paths converges - the moment the body text is
finalised - which is what `enforce_sms_content_policy` is for.

Removal is deliberately visible, not silent. `compose_body` in sms_service is
the SAME function the composer previews with, so an advisor who pastes a link
watches it disappear from the preview before they press Send. They are never
told a link went out when it did not.

Turning links back on
---------------------
Set LINKS_ALLOWED = True, and only after the campaign registration actually
permits them (`has_embedded_links: true` on CO3YNIF, or a new campaign whose
approved samples contain links). This constant is not a feature toggle for
convenience - it is a statement about what the carrier has approved, and
flipping it while the registration says otherwise simply reproduces 30007.
"""

import re

# Tied to campaign CO3YNIF's registered `has_embedded_links: false`.
LINKS_ALLOWED = False
# Tied to the same campaign's registered `has_embedded_phone: false`.
PHONE_NUMBERS_ALLOWED = False

REQUIRED_OPT_OUT = "Reply STOP to opt out."

# Anything a carrier's content filter would read as a link. Deliberately wider
# than "starts with http": a bare `evosyspro.live/book/abc` is still a URL to
# the filter, and `www.` and shortener-shaped hosts are the exact patterns that
# attract scrutiny. The TLD list stays broad rather than exhaustive-clever;
# false positives here cost a stripped word, false negatives cost a violation.
_URL_PATTERNS = [
    re.compile(r"https?://\S+", re.I),
    re.compile(r"\bwww\.\S+", re.I),
    re.compile(
        r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
        r"\.(?:com|net|org|io|co|live|app|link|me|ly|xyz|info|biz|us|site|online)"
        r"(?:/\S*)?",
        re.I,
    ),
]

# A North American number in any of the shapes an advisor would actually type.
_PHONE_PATTERN = re.compile(
    r"(?:\+?1[\s.-]*)?\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}\b"
)


def contains_url(text: str) -> bool:
    return any(p.search(text or "") for p in _URL_PATTERNS)


def contains_phone_number(text: str) -> bool:
    return bool(_PHONE_PATTERN.search(text or ""))


def strip_urls(text: str) -> str:
    out = text or ""
    for p in _URL_PATTERNS:
        out = p.sub("", out)
    return out


def strip_phone_numbers(text: str) -> str:
    return _PHONE_PATTERN.sub("", text or "")


# Removal is done in two steps rather than one substitution, because deleting a
# URL in place leaves the clause that introduced it pointing at nothing:
# "book a time here: https://..." becomes "book a time here:". So the removed
# span is first replaced with a marker, and then the clause containing that
# marker is removed as a unit. The marker is a NUL, which cannot occur in a
# message body an advisor typed.
_MARK = "\x00"

# Where one clause ends and the next begins, for the purpose of deciding how
# much to take out with the marker. Sentence punctuation and newlines are hard
# boundaries; a comma or a leading "or"/"and" starts a new clause within one
# sentence, which is what makes "…, or book a time here: <URL>" collapse to
# nothing rather than to a stray "or".
_HARD_BOUNDARY = ".!?;\n"
_SOFT_LEADINS = (" or ", " and ")


def _mark_disallowed(text: str) -> tuple[str, bool]:
    out = text or ""
    if not LINKS_ALLOWED:
        for p in _URL_PATTERNS:
            out = p.sub(_MARK, out)
    if not PHONE_NUMBERS_ALLOWED:
        out = _PHONE_PATTERN.sub(_MARK, out)
    return out, (_MARK in out)


def _clause_start(text: str, mark_at: int) -> int:
    """Index where the clause containing `mark_at` begins."""
    start = 0
    for i in range(mark_at - 1, -1, -1):
        if text[i] in _HARD_BOUNDARY or text[i] == ",":
            start = i + 1
            break
    lowered = text.lower()
    for lead in _SOFT_LEADINS:
        found = lowered.rfind(lead, start, mark_at)
        if found != -1:
            start = max(start, found + len(lead))
    return start


def _remove_marked_clauses(text: str) -> str:
    """Delete each marked span together with the clause that introduced it."""
    out = text or ""
    guard = 0
    while _MARK in out and guard < 50:
        guard += 1
        at = out.index(_MARK)
        start = _clause_start(out, at)
        end = at + len(_MARK)
        while end < len(out) and out[end] in " \t":
            end += 1
        out = out[:start] + out[end:]
    return out.replace(_MARK, "")


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _is_wreckage(sentence: str) -> bool:
    """True for a sentence that only existed to introduce what was removed.

    "book a time here: https://..." becomes "book a time here:" - a lead-in
    pointing at nothing. "Reach out anytime: 469-224-1155" becomes "Reach out
    anytime:". Both must go rather than reach a grieving family as a fragment.

    Two signatures catch essentially all of it: a colon (or dash) with nothing
    of substance after it, and a remnant too short to carry meaning on its own.
    """
    s = (sentence or "").strip()
    if not s:
        return True
    if re.search(r"[:\-–—]\s*[.!?]?\s*$", s):
        return True
    words = re.findall(r"[A-Za-z']+", s)
    return len(words) < 3


def _tidy(text: str, salvage: bool) -> str:
    """Close the holes removal leaves, without touching intentional line breaks.

    `salvage` is on only when something was actually stripped. A message that
    needed no redaction is passed through untouched apart from whitespace, so
    this can never quietly delete a short sentence someone meant to send.
    """
    out = text or ""
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\s+([,.!?])", r"\1", out)

    if salvage:
        kept = [p for p in _SENTENCE_SPLIT.split(out) if not _is_wreckage(p)]
        out = " ".join(p.strip() for p in kept)

    out = re.sub(r"\s*\.\s*\.(\s*\.)*", ".", out)
    # Removing a clause can butt two sentences together ("…you need.Reply STOP").
    out = re.sub(r"([.!?])([A-Za-z])", r"\1 \2", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r"[ \t]*\n[ \t]*", "\n", out)
    return out.strip()


def enforce_sms_content_policy(body: str) -> str:
    """The last thing that touches an outbound SMS body.

    Strips whatever the campaign is not registered to send, tidies the result
    so it reads like a person wrote it, and guarantees the opt-out language
    appears exactly once.
    """
    marked, removed_something = _mark_disallowed(body or "")
    text = _remove_marked_clauses(marked) if removed_something else marked
    text = _tidy(text, salvage=removed_something)

    # Exactly once. A template that already ends with it must not get a second
    # copy, and a hand-typed message that omits it must not go out without one.
    lowered = text.lower()
    if "reply stop" not in lowered:
        text = (text + " " + REQUIRED_OPT_OUT).strip() if text else REQUIRED_OPT_OUT

    return text


def policy_report() -> dict:
    """What the send path will do, for diagnostics and the composer."""
    return {
        "links_allowed": LINKS_ALLOWED,
        "phone_numbers_allowed": PHONE_NUMBERS_ALLOWED,
        "required_opt_out": REQUIRED_OPT_OUT,
        "campaign": "CO3YNIF",
        "reason": (
            "Campaign CO3YNIF is registered has_embedded_links=false and "
            "has_embedded_phone=false. A URL or phone number in an SMS "
            "contradicts the registration and is filtered as 30007. Booking "
            "links are sent by email only."
        ),
    }
