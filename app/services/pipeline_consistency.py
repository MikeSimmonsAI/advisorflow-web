"""
WHICH AI CONVERSATIONS ARE CLAIMING SENDS THAT NEVER HAPPENED.

READ-ONLY. This module answers a question and changes nothing. It writes no
row, repairs no counter and has no side effect of any kind.

WHY IT EXISTS

Until the transaction ordering was fixed, `pipeline_service.process_inbound_reply`
advanced the conversation BEFORE attempting the send:

    pipeline.stage = "ai_responding"
    pipeline.ai_responses_sent += 1
    pipeline.last_outbound_at = now
    db.commit()            # <- committed here
    ... then try to send ...

`messages_sent` was the only counter placed after a confirmed send. So the
fingerprint of the defect is arithmetic and needs no guessing:

    ai_responses_sent > messages_sent

Every unit of that difference is one attempt that was recorded as an AI
response and never left. The email branch failed on every call for the whole
period - it imported a symbol that does not exist - so on any conversation
that fell through to email, the difference is the entire count.

THE CLASSIFICATION, AND WHY IT IS THREE BUCKETS AND NOT TWO

DEFINITELY_INCONSISTENT  The conversation's own counters contradict each other
                         (ai_responses_sent > messages_sent), or it claims an
                         outbound timestamp while the lead's authoritative
                         communication history holds nothing at or before it.
                         No interpretation is required to call these wrong.

SUSPICIOUS               The counters agree with each other but disagree with
                         history - fewer real messages than the conversation
                         claims, or a conversation parked in "ai_responding"
                         with nothing outbound since the last inbound. A merge,
                         a manual deletion or a pre-history row can produce
                         this honestly, so it is flagged for a human rather
                         than asserted.

VALID                    Counters and history agree.

Authoritative history means `messages` and `email_messages` rows for the lead -
the tables a provider call actually writes. It deliberately does NOT use
`send_source`, because that column post-dates every affected row and is NULL on
all of them; using it would classify the entire historical population as
missing.

WHAT THIS DOES NOT DO

It proposes no repair and performs none. `cleanup_plan` returns a description
of what a repair WOULD do, in words, for a human to approve. Resetting a
counter is a write to a customer's record on the strength of an inference, and
the inference is exactly what a human should check first.
"""

from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import EmailMessage, Lead, Message, PipelineConversation

DEFINITELY_INCONSISTENT = "definitely_inconsistent"
SUSPICIOUS = "suspicious"
VALID = "valid"

ALL_VERDICTS = (DEFINITELY_INCONSISTENT, SUSPICIOUS, VALID)

# A send and the counter that records it are written in the same request but
# not the same instant. This tolerance is for clock and commit skew only; it is
# not a fudge factor for "close enough".
_SKEW = timedelta(minutes=5)


def _history_for(db: Session, lead_id: str) -> Dict[str, Any]:
    """Everything a provider call would have left behind for this lead."""
    sms_count = db.query(func.count(Message.id)).filter(
        Message.lead_id == lead_id).scalar() or 0
    email_count = db.query(func.count(EmailMessage.id)).filter(
        EmailMessage.lead_id == lead_id).scalar() or 0
    last_sms = db.query(func.max(Message.sent_at)).filter(
        Message.lead_id == lead_id).scalar()
    last_email = db.query(func.max(EmailMessage.sent_at)).filter(
        EmailMessage.lead_id == lead_id).scalar()
    stamps = [s for s in (last_sms, last_email) if s is not None]
    return {
        "sms_rows": sms_count,
        "email_rows": email_count,
        "total_rows": sms_count + email_count,
        "last_outbound_row_at": max(stamps) if stamps else None,
    }


def classify(db: Session, pipeline: PipelineConversation) -> Dict[str, Any]:
    """One conversation, with the reasoning shown.

    `reasons` is a list because a row can be wrong in more than one way, and an
    operator deciding whether to touch a customer's record deserves all of it
    rather than the first thing that matched.
    """
    ai_sent = pipeline.ai_responses_sent or 0
    messages_sent = pipeline.messages_sent or 0
    history = _history_for(db, pipeline.lead_id)

    reasons: List[str] = []
    verdict = VALID

    # 1. The conversation contradicts itself. This is the defect's signature.
    if ai_sent > messages_sent:
        verdict = DEFINITELY_INCONSISTENT
        reasons.append(
            "ai_responses_sent (%d) exceeds messages_sent (%d): %d attempt(s) "
            "were counted as AI responses before the send was attempted, and "
            "the send did not succeed."
            % (ai_sent, messages_sent, ai_sent - messages_sent))

    # 2. It claims an outbound moment that history has no record of at all.
    if pipeline.last_outbound_at and history["total_rows"] == 0:
        verdict = DEFINITELY_INCONSISTENT
        reasons.append(
            "last_outbound_at is set (%s) but the lead has no message or email "
            "row of any kind. Nothing was ever handed to a provider."
            % pipeline.last_outbound_at)

    if verdict == DEFINITELY_INCONSISTENT:
        return _result(pipeline, verdict, reasons, history)

    # 3. Counters agree with each other but overshoot the history.
    if messages_sent > history["total_rows"]:
        verdict = SUSPICIOUS
        reasons.append(
            "messages_sent (%d) exceeds the %d communication row(s) on record. "
            "A lead merge or a deleted row can explain this honestly, so it is "
            "flagged rather than asserted."
            % (messages_sent, history["total_rows"]))

    # 4. Parked mid-response with nothing outbound since the inbound that
    #    triggered it. The stage was set before the send for the whole period.
    if (pipeline.stage == "ai_responding" and pipeline.last_inbound_at
            and (history["last_outbound_row_at"] is None
                 or history["last_outbound_row_at"] < pipeline.last_inbound_at - _SKEW)):
        verdict = SUSPICIOUS
        reasons.append(
            "stage is 'ai_responding' but no outbound row exists after the last "
            "inbound message. The conversation is presented as mid-reply and "
            "nothing has gone out.")

    return _result(pipeline, verdict, reasons, history)


def _result(pipeline, verdict, reasons, history) -> Dict[str, Any]:
    return {
        "pipeline_id": pipeline.id,
        "lead_id": pipeline.lead_id,
        "organization_id": pipeline.organization_id,
        "advisor_id": pipeline.advisor_id,
        "stage": pipeline.stage,
        "verdict": verdict,
        "reasons": reasons,
        "counters": {
            "ai_responses_sent": pipeline.ai_responses_sent or 0,
            "messages_sent": pipeline.messages_sent or 0,
            "replies_received": pipeline.replies_received or 0,
            "last_outbound_at": pipeline.last_outbound_at,
            "last_inbound_at": pipeline.last_inbound_at,
        },
        "history": history,
    }


def scan(db: Session, organization_id: Optional[str] = None,
         limit: int = 500, verdict: Optional[str] = None) -> Dict[str, Any]:
    """Classify conversations, newest first. Read-only.

    `organization_id` narrows to one customer. Omitting it scans the platform,
    which is a God-level question; the caller is responsible for having asked
    it with God-level authority.
    """
    query = db.query(PipelineConversation)
    if organization_id:
        query = query.filter(PipelineConversation.organization_id == organization_id)
    rows = (query.order_by(PipelineConversation.created_at.desc())
            .limit(max(1, min(limit, 2000))).all())

    results = [classify(db, row) for row in rows]
    if verdict:
        results = [r for r in results if r["verdict"] == verdict]

    counts = {v: 0 for v in ALL_VERDICTS}
    for r in results:
        counts[r["verdict"]] += 1

    overstated = sum(
        max(0, (r["counters"]["ai_responses_sent"] - r["counters"]["messages_sent"]))
        for r in results)

    return {
        "scanned": len(rows),
        "returned": len(results),
        "counts": counts,
        "overstated_ai_responses": overstated,
        "results": results,
        "cleanup_plan": cleanup_plan(),
    }


def cleanup_plan() -> Dict[str, Any]:
    """WHAT A REPAIR WOULD DO. Nothing here executes it.

    Deliberately returned alongside every scan so the proposal and the evidence
    for it are read together.
    """
    return {
        "executed": False,
        "why_not": (
            "Resetting a counter is a write to a customer's record on the "
            "strength of an inference. The inference is what a human should "
            "check first, and no repair runs without an explicit decision."
        ),
        "proposed": [
            {
                "bucket": DEFINITELY_INCONSISTENT,
                "action": (
                    "Set ai_responses_sent = messages_sent. The difference is "
                    "attempts that were counted before the send and never left, "
                    "and messages_sent is the counter that was already gated on "
                    "a confirmed send."
                ),
                "also": (
                    "Where last_outbound_at is set and the lead has no "
                    "communication row at all, clear last_outbound_at. It "
                    "describes a moment that did not happen."
                ),
                "reversible": (
                    "Capture the before/after per row in one audit entry per "
                    "organization so the original counters remain recoverable."
                ),
            },
            {
                "bucket": SUSPICIOUS,
                "action": (
                    "Do not touch. Review a sample by hand first - a lead merge "
                    "or a deleted row explains most of these honestly, and the "
                    "rest are worth understanding before a bulk write."
                ),
            },
            {
                "bucket": "stage",
                "action": (
                    "Leave stage alone in both buckets. It is the conversation's "
                    "position, not a send receipt, and the next inbound message "
                    "moves it correctly now that the ordering is fixed."
                ),
            },
        ],
        "safety": [
            "Run against a restored snapshot before production.",
            "Scope every repair to one organization at a time.",
            "No row outside DEFINITELY_INCONSISTENT is written under any option.",
        ],
    }
