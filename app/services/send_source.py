"""
WHERE DID THIS MESSAGE COME FROM — the one vocabulary for that answer.

`messages` and `email_messages` are per-provider delivery logs. Between them
they carry a lead, a sender, a body, a provider id and a status, and nothing
whatsoever about WHY the send happened. A text queued by the cadence engine,
one an advisor typed by hand, and one an AI drafted and a human approved are
byte-identical rows. That is why nobody could answer "who was contacted today,
and was it us or the robot" without guessing from timestamps.

This module is modelled on app/services/message_state.py deliberately: same
shape, same reason. Presentation and vocabulary are defined once so a router
cannot invent a fourteenth spelling of "cadence" in passing.

NAMES ARE REUSED, NOT INVENTED. Four of these values are lifted verbatim from
AutoSendItem.source (app/routers/auto_send_router.py) so the queue row and the
message it produces read as one vocabulary, and AI_EMPLOYEE matches
ACTOR_AI_EMPLOYEE in app/services/ai_operations/constants.py and
app/services/workforce/constants.py for the same reason.

WHAT THIS IS NOT:

  * Not a channel. That is "sms" / "email" / "voice", spelled the same way in
    compliance_service, qualification and ai_operations/constants, and a fifth
    spelling here would answer eligibility against the wrong channel.
  * Not Lead.source. That is lead ACQUISITION provenance - where the family
    came from - which is a different axis entirely and already taken.
  * Not an actor. Who pressed the button is sent_by_user_id, a separate column
    on both tables, and it is frequently NULL because most of these paths have
    no authenticated human in them at all.
  * Not the drafting reason. AutoSendItem.source records why a queue item was
    DRAFTED (ai / proactive / cadence / campaign); AUTO_SEND below records how
    it LEFT. Both are true at once and both are kept.

NULL IS A REAL VALUE AND MEANS SOMETHING SPECIFIC: written before this column
existed, or by a path not yet migrated to stamp it. It does not mean "manual".
Nothing backfills it, because no authoritative evidence of origin exists for
historical rows - inferring one would be manufacturing data.
"""

# ── Human-initiated ─────────────────────────────────────────────────────────
MANUAL = "manual"
BULK = "bulk"
BULK_AI = "bulk_ai"
AUTO_SEND = "auto_send"
CAMPAIGN = "campaign"

# ── Engine-initiated ────────────────────────────────────────────────────────
CADENCE = "cadence"
AI_CONVERSATION = "ai_conversation"
PIPELINE_AUTO_REPLY = "pipeline_auto_reply"
APPOINTMENT_FOLLOWUP = "appointment_followup"
VOICE_BOOKING_LINK = "voice_booking_link"
AI_EMPLOYEE = "ai_employee"

# ── Not a real send ─────────────────────────────────────────────────────────
DEMO = "demo"

ALL_SOURCES = (
    MANUAL,
    BULK,
    BULK_AI,
    AUTO_SEND,
    CAMPAIGN,
    CADENCE,
    AI_CONVERSATION,
    PIPELINE_AUTO_REPLY,
    APPOINTMENT_FOLLOWUP,
    VOICE_BOOKING_LINK,
    AI_EMPLOYEE,
    DEMO,
)

# A human authenticated actor exists at the send point for these, so a row
# carrying one of them and a NULL sent_by_user_id is a path that has not been
# migrated yet rather than a genuinely system-initiated send.
HUMAN_INITIATED = (MANUAL, BULK, BULK_AI, AUTO_SEND, CAMPAIGN)

_LABELS = {
    MANUAL: ("Manual", "An advisor composed and sent this to one lead."),
    BULK: ("Bulk", "An advisor sent this to several leads at once."),
    BULK_AI: ("Bulk AI", "AI drafted it, an advisor triggered the batch."),
    AUTO_SEND: ("Approved", "Drafted into the auto-send queue and approved by a human."),
    CAMPAIGN: ("Campaign", "Sent as part of a campaign."),
    CADENCE: ("Cadence", "The multi-touch cadence engine sent this on schedule."),
    AI_CONVERSATION: ("AI conversation", "The AI conversation engine sent this without a human in the loop."),
    PIPELINE_AUTO_REPLY: ("AI auto-reply", "The pipeline answered an inbound message automatically."),
    APPOINTMENT_FOLLOWUP: ("Appointment", "Confirmation, reminder, thank-you or review request tied to an appointment."),
    VOICE_BOOKING_LINK: ("Voice booking", "Sent during or after an AI voice call."),
    AI_EMPLOYEE: ("AI employee", "Sent by an AI workforce employee."),
    DEMO: ("Demo", "Simulated. No provider was ever called."),
}


def is_valid(source) -> bool:
    """True for a known source. NULL/empty is valid: it means unrecorded."""
    return source is None or source == "" or source in ALL_SOURCES


def source_label(source) -> str:
    """Short label for a timeline chip or a table cell."""
    if source is None or source == "":
        return "Unrecorded"
    return _LABELS.get(source, ("Unknown", ""))[0]


def source_description(source) -> str:
    """One sentence, for a tooltip or a report legend."""
    if source is None or source == "":
        return "Sent before the platform recorded where sends came from."
    return _LABELS.get(source, ("Unknown", "Source not recognised."))[1]
