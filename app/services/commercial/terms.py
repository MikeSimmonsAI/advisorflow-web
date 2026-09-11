"""TERM STATE, AND WHAT AN UNANSWERED TERM ACTUALLY BLOCKS.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
An incomplete term sheet blocks the things that need the missing term. It does
not block onboarding.

That sounds obvious and is the single easiest thing to get wrong, because the
cheap implementation is one boolean — `commercial_complete` — consulted by
everything. Under that design a customer whose settlement frequency has not
been agreed cannot create a user account, connect a calendar, or configure
anything, and the operator's only escape is to type a frequency nobody agreed
to. The fake answer then becomes the record.

So blocking is per ACTION, and the actions are named:

  agreement_activation      needs every activation-required term, a valid
                            allocation, an effective date, and an approval.
  settlement_calculation    needs an ACTIVE agreement, a collections source,
                            authoritative records covering the period, and a
                            resolved attribution.
  settlement_statement      needs a calculation.
  settlement_distribution   is refused outright in this phase. There is no
                            approved payout path behind it.

Everything else in onboarding — the workspace, the users, the calendars, the
business profile, the AI configuration, the data import, the interview — asks
this module nothing, and is therefore never blocked by it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.commercial_models import (
    AG_ACTIVE, AG_APPROVED, AG_SUSPENDED, CommercialAgreement, CommercialTerm,
    IMPLEMENTED_COLLECTION_SOURCES, SHARE_BEARING_TYPES, TERM_ANSWERED,
    TERM_NOT_APPLICABLE, TERM_REQUIRED, TERM_UNKNOWN,
)
from app.models.models import User
from app.services.commercial import audit as caudit
from app.services.commercial import questions as q
from app.services.commercial import revenue_share as rs

# The actions whose availability depends on the terms being complete.
ACTION_ACTIVATE      = "agreement_activation"
ACTION_CALCULATE     = "settlement_calculation"
ACTION_STATEMENT     = "settlement_statement"
ACTION_DISTRIBUTE    = "settlement_distribution"

BLOCKABLE_ACTIONS = (ACTION_ACTIVATE, ACTION_CALCULATE, ACTION_STATEMENT,
                     ACTION_DISTRIBUTE)

ACTION_LABELS = {
    ACTION_ACTIVATE:   "Activate the agreement",
    ACTION_CALCULATE:  "Calculate a settlement",
    ACTION_STATEMENT:  "Generate a settlement statement",
    ACTION_DISTRIBUTE: "Distribute funds",
}

# Onboarding steps that are NEVER gated by commercial completeness. Written out
# so the intent survives somebody later "tidying up" the blocking logic.
NEVER_BLOCKED_BY_TERMS = (
    "customer_organization", "primary_contact", "workspace_setup",
    "users_roles", "business_profile", "integrations", "calendar_booking",
    "lead_data_intake", "ai_workforce_setup", "demo_discovery",
)

# Distribution is not implemented. This is a constant rather than a comment so
# a future change has to be deliberate and shows up in a diff.
PAYOUT_EXECUTION_SUPPORTED = False


def _stored(db: Session, agreement: CommercialAgreement) -> Dict[str, CommercialTerm]:
    rows = (db.query(CommercialTerm)
            .filter(CommercialTerm.agreement_id == agreement.id).all())
    return {r.key: r for r in rows}


def _value_of(row: Optional[CommercialTerm]) -> Any:
    if row is None or row.value_json is None:
        return None
    if isinstance(row.value_json, dict) and "value" in row.value_json:
        return row.value_json["value"]
    return row.value_json


def value(db: Session, agreement: CommercialAgreement, key: str) -> Any:
    """The answer to one term, or None for UNANSWERED.

    Callers that need to know the difference between "answered zero" and
    "nobody answered" ask `state_map` instead — this returns None for both and
    is only safe where the caller has already established the term is answered.
    """
    return _value_of(_stored(db, agreement).get(key))


def state_map(db: Session, agreement: CommercialAgreement) -> Dict[str, Dict[str, Any]]:
    """Every term this agreement's structure has, answered or not.

    The effective state is computed from the CURRENT definition rather than
    read off the row, because `required_for_activation` is configuration and a
    brand may change it. What is read off the row is the ANSWER and the label
    that was shown when it was given.
    """
    defs = {d["key"]: d for d in q.for_agreement(
        db, agreement.platform_id, agreement.agreement_type)}
    stored = _stored(db, agreement)

    out: Dict[str, Dict[str, Any]] = {}
    for key, defn in defs.items():
        row = stored.get(key)
        val = _value_of(row)
        if row is not None and row.state == TERM_NOT_APPLICABLE:
            state = TERM_NOT_APPLICABLE
        elif val is not None:
            state = TERM_ANSWERED
        elif defn["required_for_activation"]:
            state = TERM_REQUIRED
        else:
            state = TERM_UNKNOWN

        out[key] = {
            "key": key,
            "label": row.label if (row is not None and row.label) else defn["label"],
            "description": defn.get("description"),
            "help_text": defn.get("help_text"),
            "kind": defn["kind"],
            "audience": defn["audience"],
            "allowed_values": defn.get("allowed_values"),
            "validation": defn.get("validation"),
            "default_value": defn.get("default_value"),
            "display_order": defn["display_order"],
            "required_for_activation": defn["required_for_activation"],
            "state": state,
            "value": val,
            "value_label": q.answer_label(defn, val),
            "note": getattr(row, "note", None),
            "source": getattr(row, "source", None),
            "answered_by_user_id": getattr(row, "answered_by_user_id", None),
            "answered_at": getattr(row, "answered_at", None),
            "revision": int(getattr(row, "revision", 0) or 0),
        }

    # A stored term whose definition has since been retired still has to be
    # readable. It is reported, marked, and never counted as required.
    for key, row in stored.items():
        if key in out:
            continue
        out[key] = {
            "key": key,
            "label": row.label or key,
            "description": None, "help_text": None,
            "kind": "text", "audience": q.AUDIENCE_INTERNAL,
            "allowed_values": None, "validation": None, "default_value": None,
            "display_order": 9000,
            "required_for_activation": False,
            "state": TERM_ANSWERED if _value_of(row) is not None else TERM_UNKNOWN,
            "value": _value_of(row),
            "value_label": None,
            "note": row.note, "source": row.source,
            "answered_by_user_id": row.answered_by_user_id,
            "answered_at": row.answered_at,
            "revision": int(row.revision or 0),
            "retired_definition": True,
        }

    return out


def ordered_terms(db: Session, agreement: CommercialAgreement,
                  audience: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list(state_map(db, agreement).values())
    if audience:
        rows = [r for r in rows
                if r["audience"] in (audience, q.AUDIENCE_BOTH)]
    rows.sort(key=lambda r: (r["display_order"], r["key"]))
    return rows


def missing_required(db: Session,
                     agreement: CommercialAgreement) -> List[Dict[str, Any]]:
    """The terms that stand between this agreement and activation."""
    return [r for r in ordered_terms(db, agreement)
            if r["state"] == TERM_REQUIRED]


def completeness(db: Session, agreement: CommercialAgreement) -> Dict[str, Any]:
    rows = ordered_terms(db, agreement)
    required = [r for r in rows if r["required_for_activation"]]
    answered = [r for r in required
                if r["state"] in (TERM_ANSWERED, TERM_NOT_APPLICABLE)]
    return {
        "required_total": len(required),
        "required_answered": len(answered),
        "required_missing": len(required) - len(answered),
        "terms_complete": len(required) == len(answered),
        "pct": (100 if not required
                else int(round(100 * len(answered) / len(required)))),
    }


# ════════════════════════════════════════════════════════════════════════════
# WRITING AN ANSWER
# ════════════════════════════════════════════════════════════════════════════


def set_term(db: Session, agreement: CommercialAgreement, actor: User,
             key: str, raw_value: Any, *,
             source: str = "internal",
             note: Optional[str] = None,
             not_applicable: bool = False,
             expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """Answer, re-answer, clear or disapply one term.

    Concurrency: `expected_revision` is the revision the caller last read. If
    somebody else has answered the same term since, this raises 409 and writes
    NOTHING. Two people negotiating one term sheet at once is the normal case
    here, and a silent last-write-wins would lose a commercial term without
    anybody knowing it had been given.

    Clearing (`raw_value=None`, `not_applicable=False`) is legitimate and
    audited: somebody recorded the wrong thing and is taking it back, which is
    not the same as never having answered — the audit trail keeps both.
    """
    from fastapi import HTTPException

    defn = q.definition(db, agreement.platform_id, key)
    if defn is None:
        raise HTTPException(status_code=404,
                            detail="No commercial question named '%s'." % key)
    if not q.applies_to(defn, agreement.agreement_type):
        raise HTTPException(
            status_code=400,
            detail="\"%s\" does not apply to a %s agreement."
                   % (defn["label"], agreement.agreement_type))

    row = (db.query(CommercialTerm)
           .filter(CommercialTerm.agreement_id == agreement.id,
                   CommercialTerm.key == key)
           .first())

    if expected_revision is not None:
        current = int(getattr(row, "revision", 0) or 0)
        if current != int(expected_revision):
            raise HTTPException(
                status_code=409,
                detail="\"%s\" was changed by somebody else while you were "
                       "editing it. Reload the agreement and try again."
                       % defn["label"])

    value_norm = None if not_applicable else q.validate_answer(defn, raw_value)

    before = {"state": getattr(row, "state", None),
              "value": _value_of(row),
              "note": getattr(row, "note", None)}

    if row is None:
        row = CommercialTerm(agreement_id=agreement.id, key=key, revision=0)
        db.add(row)

    if not_applicable:
        row.state = TERM_NOT_APPLICABLE
        row.value_json = None
    elif value_norm is None:
        row.state = (TERM_REQUIRED if defn["required_for_activation"]
                     else TERM_UNKNOWN)
        row.value_json = None
    else:
        row.state = TERM_ANSWERED
        row.value_json = {"value": value_norm}

    row.label = defn["label"]
    row.definition_version = int(defn.get("version") or 1)
    row.required_for_activation = bool(defn["required_for_activation"])
    row.source = source
    row.note = note
    row.answered_by_user_id = getattr(actor, "id", None)
    row.answered_at = datetime.utcnow()
    row.revision = int(row.revision or 0) + 1

    action = (caudit.A_TERM_CLEARED
              if (value_norm is None and not not_applicable)
              else (caudit.A_TERM_CHANGED if before["value"] is not None
                    else caudit.A_TERM_ANSWERED))

    caudit.record(db, agreement, actor, action,
                  target_type="commercial_term", target_id=row.id,
                  before=before,
                  after={"state": row.state, "value": _value_of(row),
                         "note": row.note},
                  details={"key": key, "label": defn["label"],
                           "source": source,
                           "required_for_activation": row.required_for_activation})

    agreement.version = int(agreement.version or 1) + 1
    db.flush()

    # THE STATUS FOLLOWS THE TERMS, ALWAYS. Imported here rather than at module
    # scope because `agreements` imports this module; a status that lags behind
    # the terms underneath it is the exact disagreement the agreement module's
    # docstring refuses to allow, so it is refreshed on every answer rather
    # than left to whichever caller remembers.
    from app.services.commercial import agreements as _agreements
    _agreements.refresh_status(db, agreement, actor)

    return state_map(db, agreement)[key]


# ════════════════════════════════════════════════════════════════════════════
# BLOCKING — PER ACTION, NEVER GLOBAL
# ════════════════════════════════════════════════════════════════════════════


def _activation_reasons(db: Session,
                        agreement: CommercialAgreement) -> List[str]:
    reasons: List[str] = []

    for row in missing_required(db, agreement):
        reasons.append("\"%s\" has not been answered." % row["label"])

    if agreement.effective_date is None:
        reasons.append("The agreement has no effective date.")

    if agreement.agreement_type in SHARE_BEARING_TYPES:
        rule = value(db, agreement, "allocation_rule")
        verdict = rs.validate(db, agreement, rule)
        reasons.extend(verdict["reasons"])
        if not any(p["is_payee"] for p in verdict["parties"]):
            reasons.append("No party on this agreement is marked as receiving "
                           "a settlement.")

    if agreement.status not in (AG_APPROVED, AG_ACTIVE):
        reasons.append("The agreement has not been approved.")

    return reasons


def _settlement_term_reasons(db: Session,
                             agreement: CommercialAgreement) -> List[str]:
    """What stops a settlement BEFORE anybody looks at a period's records."""
    reasons: List[str] = []

    if agreement.status == AG_SUSPENDED:
        reasons.append("The agreement is suspended.")
    elif agreement.status != AG_ACTIVE:
        reasons.append("The agreement is not active.")

    if agreement.agreement_type not in SHARE_BEARING_TYPES:
        reasons.append("This agreement has no revenue share to settle.")
        return reasons

    for row in missing_required(db, agreement):
        reasons.append("\"%s\" has not been answered." % row["label"])

    source = value(db, agreement, "collections_source")
    if source is None:
        reasons.append("No authoritative source of collection figures has been "
                       "named for this agreement.")
    elif source not in IMPLEMENTED_COLLECTION_SOURCES:
        reasons.append(
            "The named collections source (%s) is recorded but has no feed "
            "this platform can settle from yet. Approved manual records are "
            "the supported path until it does." % source)

    if value(db, agreement, "attribution_rule") is None:
        reasons.append("The attribution rule has not been answered.")

    rule = value(db, agreement, "allocation_rule")
    verdict = rs.validate(db, agreement, rule)
    reasons.extend(verdict["reasons"])

    return reasons


def blocking(db: Session, agreement: CommercialAgreement) -> Dict[str, Dict[str, Any]]:
    """Every gated action, whether it is available, and why not.

    The screens render this directly. "Incomplete" on its own has never helped
    anybody decide what to do next; "Settlement frequency has not been
    answered" has.
    """
    activation = _activation_reasons(db, agreement)
    settlement = _settlement_term_reasons(db, agreement)

    out = {
        ACTION_ACTIVATE: {
            "label": ACTION_LABELS[ACTION_ACTIVATE],
            "allowed": not activation,
            "reasons": activation,
        },
        ACTION_CALCULATE: {
            "label": ACTION_LABELS[ACTION_CALCULATE],
            "allowed": not settlement,
            "reasons": settlement,
        },
        ACTION_STATEMENT: {
            "label": ACTION_LABELS[ACTION_STATEMENT],
            "allowed": not settlement,
            "reasons": settlement,
        },
        ACTION_DISTRIBUTE: {
            "label": ACTION_LABELS[ACTION_DISTRIBUTE],
            "allowed": False,
            "reasons": (settlement or []) + [
                "This platform does not move money on a custom commercial "
                "agreement. Distribution is recorded outside it."],
        },
    }
    return out
