"""What a Twilio delivery error does to a family's texts (#482).

Each code maps to ONE action. The defaults keep every decision the owner has
already made and change only the one #482 asks for:

  30006  hold_for_review     was: texts end for good. Now: texts wait for a
                             person; it is not DNC and not proof of a landline.
  21614  hold_for_review     Twilio says "not a mobile number" (same as above).
  30003  continue_schedule   phone off / no signal - the next text goes on its
                             normal day (owner decision, Oct 16).
  30005  continue_schedule   unknown / inactive right now - same (Oct 16).
  30007  investigate         the carrier filtered the CONTENT or sender: an
                             investigation of our message (Twilio ticket
                             #29953372), not a fault in the family's phone.
                             Nothing is resent; the family is not held.
  other  record              shown in the queue, nothing else changes.

No retry rule is invented here. `continue_schedule` only means the family's
normal next text is not cancelled; a failed text is never resent.

A workspace may override the defaults with a reviewed, versioned policy row
(SmsRecoveryPolicy). Rows are never edited in place, so history - and which
version opened each hold - is kept.
"""
import json
from typing import Dict, Optional, Tuple

HOLD_FOR_REVIEW = "hold_for_review"
TEMPORARY_HOLD = "temporary_hold"
CONTINUE_SCHEDULE = "continue_schedule"
INVESTIGATE = "investigate"
RECORD = "record"
ACTIONS = (HOLD_FOR_REVIEW, TEMPORARY_HOLD, CONTINUE_SCHEDULE, INVESTIGATE, RECORD)
HOLD_ACTIONS = (HOLD_FOR_REVIEW, TEMPORARY_HOLD)

DEFAULT_VERSION = "default-2026-11-03"
DEFAULT_RULES: Dict[str, Dict] = {
    "30006": {"action": HOLD_FOR_REVIEW,
              "label": "Carrier could not deliver to this number (landline or unreachable carrier)",
              "note": "Not DNC and not proof of a landline - a person reviews the number."},
    "21614": {"action": HOLD_FOR_REVIEW,
              "label": "Twilio says this is not a mobile number",
              "note": "A person reviews the number."},
    "30003": {"action": CONTINUE_SCHEDULE,
              "label": "Phone off, no signal or out of area",
              "note": "The next text goes on its normal day. The failed text is not resent."},
    "30005": {"action": CONTINUE_SCHEDULE,
              "label": "Number unknown or not active right now",
              "note": "The next text goes on its normal day. The failed text is not resent."},
    "30007": {"action": INVESTIGATE,
              "label": "Carrier filtered the message",
              "note": "A content or sender problem to investigate (Twilio ticket #29953372), "
                      "not the family's phone. Nothing is resent."},
}
TRACKED_CODES = ("30003", "30005", "30006", "30007")


def validate(rules) -> Dict[str, Dict]:
    """A clean copy of `rules`, or ValueError saying what is wrong."""
    if not isinstance(rules, dict) or not rules:
        raise ValueError("rules must be a non-empty object of error code -> rule")
    out = {}
    for code, rule in rules.items():
        c = str(code).strip()
        if not c.isdigit() or len(c) > 6:
            raise ValueError("'%s' is not a Twilio error code" % code)
        if not isinstance(rule, dict) or rule.get("action") not in ACTIONS:
            raise ValueError("code %s: action must be one of %s" % (c, ", ".join(ACTIONS)))
        r = {"action": rule["action"], "label": str(rule.get("label") or "")[:200],
             "note": str(rule.get("note") or "")[:500]}
        if rule["action"] == TEMPORARY_HOLD:
            hours = rule.get("hours")
            if not isinstance(hours, (int, float)) or not 1 <= hours <= 24 * 30:
                raise ValueError("code %s: a temporary hold needs hours between 1 and 720" % c)
            r["hours"] = hours
        if rule.get("retry") is not None:
            r["retry"] = _validate_retry(c, rule["retry"])
        if rule.get("evidence"):
            r["evidence"] = str(rule["evidence"])[:500]      # e.g. "Twilio ticket #29953372 reply, <date>"
        out[c] = r
    return out


# FUTURE CARRIER GUIDANCE (#482 Phase 2). When Twilio answers ticket
# #29953372 a code's rule may carry the guidance it gives as a `retry` block.
# It is RECORDED and SHOWN, never acted on by itself: no code path here
# resends a failed text, and a policy change never touches an existing hold
# or a past failure. Any future retry feature must ALSO pass every send gate
# (consent, STOP/DNC, suppression, contact hours, campaign switch, spacing,
# duplicate claim) - these fields can only narrow what is allowed, never
# widen it.
RETRY_FIELDS = {"max_attempts": (1, 10), "cooldown_hours": (1, 24 * 30)}


def _validate_retry(code: str, retry) -> Dict:
    if not isinstance(retry, dict):
        raise ValueError("code %s: retry must be an object" % code)
    out = {}
    for k, (lo, hi) in RETRY_FIELDS.items():
        v = retry.get(k)
        if v is None:
            raise ValueError("code %s: retry.%s is required (%d-%d)" % (code, k, lo, hi))
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError("code %s: retry.%s must be between %d and %d" % (code, k, lo, hi))
        out[k] = v
    out["requires_verified_capability"] = bool(retry.get("requires_verified_capability", True))
    out["enabled"] = False            # recorded guidance only - see above
    unknown = set(retry) - set(RETRY_FIELDS) - {"requires_verified_capability", "enabled"}
    if unknown:
        raise ValueError("code %s: unknown retry fields %s" % (code, ", ".join(sorted(unknown))))
    return out


def effective(db, organization_id: Optional[str]) -> Tuple[str, Dict[str, Dict]]:
    """(version, rules) in force for this workspace: its own latest active
    row, else a platform-wide row, else the built-in default. The default's
    rules are the base; a row only overrides the codes it names."""
    rules = {k: dict(v) for k, v in DEFAULT_RULES.items()}
    version = DEFAULT_VERSION
    try:
        from app.models.sms_recovery_models import SmsRecoveryPolicy as P
        row = None
        if organization_id:
            row = (db.query(P).filter(P.organization_id == organization_id, P.active.is_(True))
                   .order_by(P.created_at.desc()).first())
        if row is None:
            row = (db.query(P).filter(P.organization_id.is_(None), P.active.is_(True))
                   .order_by(P.created_at.desc()).first())
        if row is not None:
            rules.update(validate(json.loads(row.rules)))
            version = row.version
    except Exception:                                    # noqa: BLE001 - a bad row never breaks sending
        import logging
        logging.getLogger(__name__).exception("sms recovery policy unreadable - using the default")
    return version, rules


def rule_for(rules: Dict[str, Dict], code: Optional[str]) -> Dict:
    c = str(code or "").strip()
    return rules.get(c) or {"action": RECORD, "label": "Carrier error %s" % (c or "unknown"), "note": ""}
