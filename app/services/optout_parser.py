"""Opt-out parser. Stdlib only, shared by the SMS pipeline, the workforce
router and the readiness harness. Decides ONLY "is this an opt-out"; it never
sends or stores anything."""
import re

# Kept as the hard-override safety net for legal opt-out language - see
# module docstring above for why this is never fully replaced by the AI
# classification alone. Deliberately narrow: "stop"/"unsubscribe"/"remove
# me" are unambiguous legal opt-out phrasing. A plain "not interested" is
# NOT in this list on purpose - that's its own not_interested category,
# not an automatic DNC trigger (see module docstring).
HARD_STOP_KEYWORDS = ["stop", "unsubscribe", "remove me"]
# Whole-message opt-outs (see contains_hard_stop_language).
STANDARD_OPT_OUT_WORDS = {"cancel", "end", "quit", "optout", "opt out", "opt-out", "revoke", "stopall"}


def contains_hard_stop_language(body: str) -> bool:
    """
    The non-negotiable legal opt-out check - always runs regardless of
    what the AI classifier returns. If someone says STOP, UNSUBSCRIBE, or
    explicitly asks to be removed, that lead goes to DNC, full stop, no
    exceptions, no AI judgment call. A plain "not interested" does NOT
    trigger this - see module docstring for why that's now its own
    not_interested category instead of an automatic DNC trigger.
    """
    text = _normalize_reply(body)
    if not text:
        return False
    # Twilio's standard opt-out keywords, which the carrier layer already
    # honours on its own. Matched only as the WHOLE message (optionally with
    # "please"), because as substrings "end", "quit", "cancel" and "stop"
    # appear in ordinary replies ("weekend", "cancel my appointment",
    # "can I stop by Friday?") that are not opt-outs.
    if _WHOLE_MESSAGE_OPT_OUT.match(text):
        return True
    return bool(_OPT_OUT_INTENT.search(text))


def _normalize_reply(body) -> str:
    """Lowercase, drop quotes/punctuation at the edges, collapse whitespace."""
    text = " ".join(str(body or "").lower().split())
    return text.strip(" \t.!?,;:'\"`“”‘’*_-")


_OPT_OUT_WORD = "|".join(sorted(
    (re.escape(w) for w in set(STANDARD_OPT_OUT_WORDS) | {"stop", "unsubscribe", "stop all"}),
    key=len, reverse=True))
_WHOLE_MESSAGE_OPT_OUT = re.compile(rf"^(?:please )?(?:{_OPT_OUT_WORD})(?: please)?$")
# Unambiguous requests inside a longer message. "stop" alone is NOT here: it
# only counts when it is directed at us contacting them.
_OPT_OUT_INTENT = re.compile(
    r"\bunsubscribe\b"
    r"|\bremove me\b"
    r"|\btake me off\b"
    r"|\bstop (?:all )?(?:texting|texts|messaging|messages|calling|calls|emailing|emails|contacting|sending)\b"
    r"|\bstop (?:harassing|bothering|spamming|pestering) (?:me|us)\b"
)
