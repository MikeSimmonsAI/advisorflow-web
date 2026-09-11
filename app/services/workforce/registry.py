"""THE TWO REGISTRIES: what an AI employee CAN BE, and what it CAN DO.

BOTH LIVE IN CODE, and that is the same decision app/services/capabilities.py
and app/services/entitlements.py already made, for the same reason recorded in
their headers: a registry stored only in a table is a registry a typo can
silently empty, and a grant written against a key nothing recognises is a
grant that appears saved and does nothing.

    TOOLS       every action available to any AI employee, anywhere. A tool
                that is not here cannot be called — `tools.authorize` refuses
                an unknown key before it looks at anything else.

    TEMPLATES   the job library. Eleven jobs today; a twelfth is a dict, not a
                module. `ai_employee_templates` mirrors this into the database
                so God Mode can list and disable one, but THIS is the source.

WHAT IS NOT HERE. No prompts, no model names, no temperature, no provider. A
tool is an ACTION with an authority profile; how a model is persuaded to ask
for it belongs to runtime.py, and which model answers belongs to
model_router.py. Keeping them apart is what lets the authority profile be
reviewed by somebody who never reads a prompt.
"""

from typing import Dict, List, Optional, Tuple

from app.services.workforce import constants as C


# ═══════════════════════════════════════════════════════════════════════════
# THE TOOL REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

class ToolSpec:
    """One registered platform tool.

    THE FLAGS ARE THE AUTHORITY PROFILE, and the gateway reads them rather
    than special-casing tool keys. A new tool that forgets to declare
    `mutating` gets the safe default (True); one that forgets `channel` cannot
    accidentally skip the eligibility gate, because the gate is driven by the
    presence of a channel rather than by a list of tool names somebody has to
    remember to extend.

    `reaches_outside`  This call can touch a real person or a real external
                       provider. It is REFUSED outright in the simulation and
                       shadow stages — see constants.EXECUTING_STAGES — and
                       that refusal lives in the gateway, not in the adapter,
                       so a fake adapter is a convenience and never the thing
                       standing between the engine and a real send.

    `requires_eligibility`  Which channel the Contact Eligibility Engine must
                       clear before this call proceeds. None means the tool
                       does not contact anybody.

    `idempotent_on`    Argument names whose values form the idempotency key,
                       together with the work item. A retry with the same
                       arguments is suppressed rather than repeated: this is
                       what stops a retry storm producing two appointments.

    `schema`           {argument: (type, required)}. Validated BEFORE the tool
                       runs, so a malformed model output becomes a refusal
                       with a reason instead of a partially-applied write.
    """

    __slots__ = ("key", "label", "category", "description", "mutating",
                 "reaches_outside", "requires_eligibility", "channel",
                 "idempotent_on", "schema", "requires_work_item",
                 "terminal_outcome", "required_feature")

    def __init__(self, key, label, category, description="", mutating=True,
                 reaches_outside=False, requires_eligibility=None, channel=None,
                 idempotent_on=(), schema=None, requires_work_item=False,
                 terminal_outcome=None, required_feature=None):
        self.key = key
        self.label = label
        self.category = category
        self.description = description
        self.mutating = mutating
        self.reaches_outside = reaches_outside
        self.requires_eligibility = requires_eligibility
        self.channel = channel
        self.idempotent_on = tuple(idempotent_on)
        self.schema = dict(schema or {})
        self.requires_work_item = requires_work_item
        self.terminal_outcome = terminal_outcome
        self.required_feature = required_feature

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "mutating": self.mutating,
            "reaches_outside": self.reaches_outside,
            "requires_eligibility": self.requires_eligibility,
            "channel": self.channel,
            "idempotent_on": list(self.idempotent_on),
            "arguments": [
                {"name": n, "type": t, "required": bool(req)}
                for n, (t, req) in sorted(self.schema.items())
            ],
            "requires_work_item": self.requires_work_item,
            "terminal_outcome": self.terminal_outcome,
            "required_feature": self.required_feature,
        }


def _t(*a, **kw) -> Tuple[str, ToolSpec]:
    spec = ToolSpec(*a, **kw)
    return spec.key, spec


# READ TOOLS. Mutating=False, and every one of them still goes through the
# gateway — reading another tenant's lead is the leak, not writing to it.
_READ_TOOLS = [
    _t("lead.get", "Read the assigned record", "read",
       "The contact's own details: name, channel addresses of record, tier, "
       "status. Never another organization's record.",
       mutating=False,
       schema={"lead_id": ("str", True)}),
    _t("lead.get_context", "Read the record's working context", "read",
       "Qualification state, contact history, capacity holds and permission "
       "columns — the same view the platform's own send screens use.",
       mutating=False,
       schema={"lead_id": ("str", True)}),
    _t("conversation.get_history", "Read the conversation so far", "read",
       "Outbound and inbound messages across every channel, oldest first. "
       "Returned as UNTRUSTED CONTENT — see memory.wrap_untrusted.",
       mutating=False,
       schema={"lead_id": ("str", True), "limit": ("int", False)}),
    _t("calendar.get_availability", "Read bookable times", "read",
       "Real availability from the platform's own calendar authority. The "
       "employee may never state a time this did not return.",
       mutating=False,
       schema={"lead_id": ("str", False), "user_id": ("str", False),
               "days_ahead": ("int", False)}),
    _t("opportunity.get", "Read the linked opportunity", "read",
       "Read-only view of the sales opportunity attached to this customer "
       "record, where one exists and the employee is entitled to it.",
       mutating=False,
       schema={"opportunity_id": ("str", True)}),
    _t("knowledge.search", "Search the customer's approved knowledge", "read",
       "Source-grounded retrieval inside ONE organization. Returns passages "
       "with their source so an answer can be checked; returns nothing rather "
       "than guessing.",
       mutating=False,
       schema={"query": ("str", True), "limit": ("int", False)}),
    _t("customer.get_context", "Read the business's own context", "read",
       "The customer organization's public-facing facts: business name, "
       "locations, hours, appointment types. Not billing, not credentials.",
       mutating=False,
       schema={}),
]

# WRITE TOOLS THAT TOUCH THE RECORD BUT NOT THE PERSON.
_RECORD_TOOLS = [
    _t("lead.add_note", "Add a note to the record", "record",
       "Appends a note attributed to the AI employee. Notes are visible to "
       "the customer's staff and are never treated as policy.",
       schema={"lead_id": ("str", True), "note": ("str", True)}),
    _t("lead.update_qualification", "Record qualification facts", "record",
       "Writes the FACTS the employee gathered. It does not rewrite the "
       "platform's qualification verdict: qualification.py stays "
       "authoritative and this feeds it — see section 46.",
       schema={"lead_id": ("str", True), "facts": ("dict", True),
               "recommended_band": ("str", False)}),
    _t("lead.mark_not_interested", "Record 'not interested'", "record",
       "The person said no. Ends the work item and stops outreach without "
       "claiming an opt-out that was not given.",
       schema={"lead_id": ("str", True), "detail": ("str", False)},
       requires_work_item=True, terminal_outcome=C.NOT_INTERESTED),
    _t("lead.mark_do_not_contact", "Record an opt-out", "record",
       "STOP, unsubscribe, or an explicit request never to be contacted. "
       "Writes the platform's own suppression record so every other send "
       "path in the product honours it too, not just this employee.",
       schema={"lead_id": ("str", True), "channel": ("str", False),
               "detail": ("str", False)},
       requires_work_item=True, terminal_outcome=C.DO_NOT_CONTACT),
    _t("lead.mark_bad_contact", "Record an unreachable address", "record",
       "A hard bounce, a disconnected number, or a wrong person. Flags the "
       "record so no employee and no human keeps trying it.",
       schema={"lead_id": ("str", True), "channel": ("str", True),
               "detail": ("str", False)},
       requires_work_item=True, terminal_outcome=C.BAD_CONTACT),
    _t("memory.remember", "Remember a fact for next time", "record",
       "Scoped to this organization, this employee and this record. A fact "
       "the CONTACT stated is stored as stated-by-contact and never becomes "
       "customer policy.",
       schema={"key": ("str", True), "value": ("str", True),
               "scope": ("str", False), "source": ("str", False)}),
]


# COMMUNICATION TOOLS.
#
# PREPARE AND SEND ARE TWO TOOLS, NOT ONE WITH A FLAG. `prepare_*` composes and
# returns the exact body; `send_*` is the only thing that can reach a person,
# and it is the only one carrying reaches_outside=True. That split is what
# makes shadow mode honest: in shadow the employee genuinely composes what it
# would send, the recommendation is recorded verbatim, and the send is refused
# by the gateway rather than by an adapter that was swapped out.
_COMMS_TOOLS = [
    _t("conversation.prepare_sms", "Draft an SMS", "comms",
       "Compose the message this employee would send. Reaches nobody.",
       mutating=False, channel=C.CHANNEL_SMS,
       schema={"lead_id": ("str", True), "body": ("str", True)}),
    _t("conversation.prepare_email", "Draft an email", "comms",
       "Compose the subject and body this employee would send. Reaches nobody.",
       mutating=False, channel=C.CHANNEL_EMAIL,
       schema={"lead_id": ("str", True), "subject": ("str", True),
               "body": ("str", True)}),
    _t("conversation.send_sms", "Send an SMS", "comms",
       "Sends through the platform's own SMS path, under the organization's "
       "own number and consent record.",
       reaches_outside=True, requires_eligibility=C.CHANNEL_SMS,
       channel=C.CHANNEL_SMS, required_feature="sms",
       idempotent_on=("lead_id", "body"), requires_work_item=True,
       schema={"lead_id": ("str", True), "body": ("str", True)}),
    _t("conversation.send_email", "Send an email", "comms",
       "Sends through the platform's own email path, from the organization's "
       "configured sender.",
       reaches_outside=True, requires_eligibility=C.CHANNEL_EMAIL,
       channel=C.CHANNEL_EMAIL, required_feature="email",
       idempotent_on=("lead_id", "subject", "body"), requires_work_item=True,
       schema={"lead_id": ("str", True), "subject": ("str", True),
               "body": ("str", True)}),
    # VOICE: THE INTERFACE EXISTS AND THE ACTION DOES NOT HAPPEN.
    #
    # Registered so the architecture is real — an employee can hold voice
    # authority, a template can declare the channel, eligibility answers for
    # it, and the disposition model is wired. `activation.live_voice_enabled()`
    # returns False and `tools.authorize` refuses this key on that basis
    # BEFORE any provider is resolved, so there is no configuration of any
    # customer, brand or employee that places a call in this build.
    _t("conversation.place_call", "Place an AI voice call", "comms",
       "Outbound AI voice. DISABLED PLATFORM-WIDE in this build; refused with "
       "live_voice_disabled regardless of configuration.",
       reaches_outside=True, requires_eligibility=C.CHANNEL_VOICE,
       channel=C.CHANNEL_VOICE, required_feature="voice",
       idempotent_on=("lead_id",), requires_work_item=True,
       schema={"lead_id": ("str", True), "purpose": ("str", False)}),
]

# APPOINTMENTS. The booking authority is the platform's, never the model's.
_CALENDAR_TOOLS = [
    _t("appointment.book", "Book an appointment", "calendar",
       "Books a slot that calendar.get_availability actually returned, "
       "through the platform's booking authority, and reports back only what "
       "that authority confirmed.",
       reaches_outside=True, required_feature="booking",
       idempotent_on=("lead_id", "start_at"), requires_work_item=True,
       terminal_outcome=C.APPOINTMENT_BOOKED,
       schema={"lead_id": ("str", True), "start_at": ("str", True),
               "user_id": ("str", False), "appt_label": ("str", False)}),
    _t("appointment.reschedule", "Move an existing appointment", "calendar",
       "Moves a booking this employee's record already owns, subject to the "
       "same availability authority.",
       reaches_outside=True, required_feature="booking",
       idempotent_on=("booking_link_id", "start_at"), requires_work_item=True,
       schema={"booking_link_id": ("str", True), "start_at": ("str", True)}),
]

# OPPORTUNITY. Present for the full-lifecycle profile; gated by authority AND
# by the customer's own business rules, never by the template alone.
_OPPORTUNITY_TOOLS = [
    _t("opportunity.create", "Open an opportunity", "opportunity",
       "Creates a sales opportunity from a qualified record, where the "
       "employee holds the authority and the customer's rules permit it.",
       idempotent_on=("lead_id",), required_feature="crm",
       schema={"lead_id": ("str", True), "summary": ("str", False),
               "estimated_value": ("str", False)}),
    _t("opportunity.update", "Update an opportunity", "opportunity",
       "Advances or annotates an opportunity this record already owns. "
       "Pipeline movement is permitted only where existing business rules "
       "already allow it.",
       required_feature="crm",
       schema={"opportunity_id": ("str", True), "stage": ("str", False),
               "note": ("str", False)}),
]

# WORKFLOW. How an employee stops, escalates, or asks for a person.
_WORKFLOW_TOOLS = [
    _t("handoff.create", "Hand this to a person", "workflow",
       "Creates a first-class handoff carrying the summary, known facts, open "
       "questions and a recommended next action.",
       requires_work_item=True, terminal_outcome=C.HUMAN_HANDOFF,
       schema={"reason_code": ("str", True), "summary": ("str", True),
               "recommended_action": ("str", False),
               "known_facts": ("list", False), "open_questions": ("list", False),
               "priority": ("str", False)}),
    _t("employee.request_review", "Send this for review", "workflow",
       "The employee is not confident enough to proceed. Parks the item in "
       "Needs Review rather than guessing.",
       requires_work_item=True, terminal_outcome=C.NEEDS_REVIEW,
       schema={"reason": ("str", True)}),
    _t("employee.pause_work_item", "Pause this record", "workflow",
       "Stops work on one record without ending it.",
       requires_work_item=True,
       schema={"reason": ("str", True), "resume_after_minutes": ("int", False)}),
    _t("employee.wait_for_response", "Wait for a reply", "workflow",
       "The employee has done what it can for now and is waiting. Schedules "
       "the next attempt inside the configured cadence.",
       requires_work_item=True,
       schema={"wait_minutes": ("int", False), "note": ("str", False)}),
    _t("employee.mark_exhausted", "Close as exhausted", "workflow",
       "Every permitted touch has been used with no response.",
       requires_work_item=True, terminal_outcome=C.EXHAUSTED,
       schema={"detail": ("str", False)}),
    _t("employee.mark_qualified", "Close as qualified", "workflow",
       "The record met the customer's stated qualification bar. Terminal for "
       "this employee; the next step belongs to a person or another employee.",
       requires_work_item=True, terminal_outcome=C.QUALIFIED,
       schema={"summary": ("str", True), "facts": ("dict", False)}),
]

TOOLS: Dict[str, ToolSpec] = dict(
    _READ_TOOLS + _RECORD_TOOLS + _COMMS_TOOLS + _CALENDAR_TOOLS
    + _OPPORTUNITY_TOOLS + _WORKFLOW_TOOLS
)

ALL_TOOL_KEYS: Tuple[str, ...] = tuple(sorted(TOOLS))

# Tools that can reach a real person or a real provider. Named as a set rather
# than recomputed at each call site so a test can assert the exact membership —
# a new tool silently joining this set is the change that matters most here.
EXECUTING_TOOL_KEYS: Tuple[str, ...] = tuple(
    sorted(k for k, s in TOOLS.items() if s.reaches_outside))

READ_ONLY_TOOL_KEYS: Tuple[str, ...] = tuple(
    sorted(k for k, s in TOOLS.items() if not s.mutating))


def tool(key: str) -> Optional[ToolSpec]:
    return TOOLS.get((key or "").strip())


def normalize_tool_keys(keys, *, bound=None) -> List[str]:
    """Clean a tool allow-list, dropping anything unregistered.

    `bound` is an OUTER limit — the template's list when normalizing a brand's,
    the brand's when normalizing a customer's. Intersection only: this function
    can never return a key the bound did not contain, which is what makes
    "a brand may narrow and may not widen" a property of the code rather than
    a rule somebody has to follow.
    """
    if keys is None:
        return list(bound) if bound is not None else []
    allowed = set(bound) if bound is not None else set(ALL_TOOL_KEYS)
    out = []
    for k in keys:
        k = (k or "").strip()
        if k in TOOLS and k in allowed and k not in out:
            out.append(k)
    return sorted(out)


def normalize_channels(channels, *, bound=None) -> List[str]:
    """Same intersection rule, for channels."""
    if channels is None:
        return list(bound) if bound is not None else []
    allowed = set(bound) if bound is not None else set(C.ALL_CHANNELS)
    out = []
    for ch in channels:
        ch = (ch or "").strip().lower()
        if ch in C.ALL_CHANNELS and ch in allowed and ch not in out:
            out.append(ch)
    return sorted(out)


# ═══════════════════════════════════════════════════════════════════════════
# THE JOB LIBRARY
# ═══════════════════════════════════════════════════════════════════════════
#
# ELEVEN JOBS, ONE ENGINE. Section 4 of the brief is explicit that these must
# not be ten disconnected agents, and the shape of this file is the proof:
# every entry below is data. There is no per-role code path anywhere in this
# package, and `runtime.execute` never branches on `job_role`.
#
# The tool bundles are composed rather than listed out per job, so "every
# outreach employee can record an opt-out" is one line that cannot be
# forgotten on the eleventh job.

_READ_BUNDLE = [
    "lead.get", "lead.get_context", "conversation.get_history",
    "customer.get_context", "knowledge.search",
]
_WORKFLOW_BUNDLE = [
    "lead.add_note", "memory.remember", "handoff.create",
    "employee.request_review", "employee.pause_work_item",
    "employee.wait_for_response",
]
# THE CLOSING TOOLS TRAVEL WITH THE OUTREACH TOOLS, ALWAYS.
#
# An employee that may text somebody must be able to record that they said
# stop. Bundling them means a job configured to send can never be configured
# without the ability to honour an opt-out — which is the one asymmetry that
# would actually hurt a real person.
_SEND_BUNDLE = [
    "conversation.prepare_sms", "conversation.prepare_email",
    "conversation.send_sms", "conversation.send_email",
]
_CLOSING_BUNDLE = [
    "lead.mark_not_interested", "lead.mark_do_not_contact",
    "lead.mark_bad_contact", "employee.mark_exhausted",
]
_OUTREACH_BUNDLE = _SEND_BUNDLE + _CLOSING_BUNDLE
_BOOKING_BUNDLE = [
    "calendar.get_availability", "appointment.book", "appointment.reschedule",
]
# VOICE TRAVELS WITH THE JOBS THAT ARE VOICE-SHAPED, AND ONLY WITH THEM.
#
# A template declaring the voice CHANNEL without the voice TOOL would be an
# architecture that reads as complete and cannot place a call even once voice
# is enabled. The tool is refused today by `tools.authorize` at gate 7 — see
# activation.live_voice_enabled — so including it here costs nothing and makes
# the eventual switch-on a configuration change rather than a code change.
_VOICE_BUNDLE = ["conversation.place_call"]
_QUALIFY_BUNDLE = ["lead.update_qualification", "employee.mark_qualified"]
_OPPORTUNITY_BUNDLE = ["opportunity.get", "opportunity.create",
                       "opportunity.update"]


def _tools(*bundles) -> List[str]:
    """Compose a template's tool list.

    THE CLOSING TOOLS ARE ADDED AUTOMATICALLY WHEREVER A SEND TOOL APPEARS,
    and that is a structural invariant rather than a convention.

    The convention version was `_OUTREACH_BUNDLE`, which bundled them — and
    the AI Support Specialist, whose tool list was written out by hand because
    it only needed email, could send and could not record an opt-out. A test
    caught it, which is the good outcome; the better outcome is that it cannot
    be written that way again. An employee that may message somebody can
    always honour a STOP, because composing the list adds the ability.
    """
    out = []
    for b in bundles:
        for k in b:
            if k not in out:
                out.append(k)
    can_send = any(
        (TOOLS.get(k) is not None and TOOLS[k].reaches_outside
         and TOOLS[k].channel in (C.CHANNEL_SMS, C.CHANNEL_EMAIL,
                                  C.CHANNEL_VOICE))
        for k in out)
    if can_send:
        for k in _CLOSING_BUNDLE:
            if k not in out:
                out.append(k)
    return sorted(out)


# The business questions a customer answers when hiring an employee. These are
# the ONLY thing a normal customer configures — section 24. No system prompt,
# no model name, no temperature, no tool JSON.
_COMMON_QUESTIONS = [
    {"key": "goal", "label": "What should this employee accomplish?",
     "type": "text", "required": True},
    {"key": "audience", "label": "Which group of records may it work?",
     "type": "audience", "required": True},
    {"key": "channels", "label": "Which channels may it use?",
     "type": "channels", "required": True},
    {"key": "hours", "label": "When may it work?",
     "type": "hours", "required": True},
    {"key": "handoff_to", "label": "Who receives human handoffs?",
     "type": "user", "required": True},
    {"key": "always_escalate", "label": "What should always go to a person?",
     "type": "text_list", "required": False},
    {"key": "knowledge", "label": "Which knowledge should it use?",
     "type": "knowledge", "required": False},
]
_BOOKING_QUESTIONS = [
    {"key": "booking_owner", "label": "Whose calendar should it book into?",
     "type": "user", "required": True},
    {"key": "appointment_type", "label": "What kind of appointment is it booking?",
     "type": "appointment_type", "required": False},
]
_QUALIFY_QUESTIONS = [
    {"key": "good_lead", "label": "What makes a good lead for you?",
     "type": "text_list", "required": True},
]


class TemplateSpec:
    __slots__ = ("key", "name", "job_role", "summary", "description",
                 "objective", "tool_keys", "channels", "policy", "questions",
                 "entitlement_key", "required_feature", "depth")

    def __init__(self, key, name, job_role, summary, objective, tool_keys,
                 channels, questions, description="", policy=None,
                 entitlement_key=None, required_feature=None, depth="architected"):
        self.key = key
        self.name = name
        self.job_role = job_role
        self.summary = summary
        self.description = description or summary
        self.objective = objective
        self.tool_keys = sorted(tool_keys)
        self.channels = sorted(channels)
        self.questions = list(questions)
        self.policy = dict(policy or {})
        self.entitlement_key = entitlement_key or ("ai_employee_%s" % job_role)
        self.required_feature = required_feature
        # `depth` is honesty, not configuration. "implemented" means this job
        # has been driven end to end through the simulator and the evaluation
        # harness; "architected" means the engine supports it and it has not
        # been proven to that standard yet. God Mode shows it so nobody
        # demonstrates a job that has never been exercised.
        self.depth = depth

    def as_dict(self) -> dict:
        return {
            "key": self.key, "name": self.name, "job_role": self.job_role,
            "summary": self.summary, "description": self.description,
            "objective": self.objective, "tool_keys": list(self.tool_keys),
            "channels": list(self.channels), "questions": list(self.questions),
            "policy": dict(self.policy),
            "entitlement_key": self.entitlement_key,
            "required_feature": self.required_feature,
            "depth": self.depth,
        }


def _tpl(*a, **kw) -> Tuple[str, TemplateSpec]:
    t = TemplateSpec(*a, **kw)
    return t.key, t


# Default channels for a job that talks to people. VOICE IS ABSENT FROM EVERY
# DEFAULT. A template may declare it (see the receptionist and the care agent,
# which are voice-shaped jobs) but no template turns it on by default, and the
# gateway refuses it regardless while live voice is disabled.
_TEXT_CHANNELS = [C.CHANNEL_SMS, C.CHANNEL_EMAIL]
_ALL_CHANNELS = [C.CHANNEL_SMS, C.CHANNEL_EMAIL, C.CHANNEL_VOICE]


TEMPLATES: Dict[str, TemplateSpec] = dict([
    # ── THE FIRST DEEPLY IMPLEMENTED EMPLOYEE ───────────────────────────────
    _tpl("reactivation_specialist", "AI Reactivation Specialist",
         "reactivation_specialist",
         "Works an aged or dormant contact database and moves eligible "
         "records toward a real outcome.",
         "Re-engage this dormant record. Establish whether they are still "
         "interested, qualify them against the customer's stated criteria, "
         "and either book an appointment or hand them to a person. Stop at "
         "the first sign they do not want to hear from you.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE, _QUALIFY_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS + _QUALIFY_QUESTIONS,
         description=(
             "The reactivation job assumes the records are OLD and that being "
             "old is not consent. Every record is re-checked against the "
             "platform's contact eligibility engine before the first touch "
             "and again before every subsequent one, because a suppression "
             "added last night outranks an assignment made last week."),
         policy={"max_touches": 9, "escalate_on_complaint": True,
                 "stop_on_any_negative": True},
         depth="implemented"),

    # ── THE SECOND PROOF: a full-lifecycle team ─────────────────────────────
    _tpl("lead_qualifier", "AI Lead Qualifier", "lead_qualifier",
         "Qualifies new and inbound enquiries against the customer's own "
         "definition of a good lead.",
         "Find out whether this enquiry matches what the customer said a good "
         "lead looks like. Ask only what you need, record what you learn, and "
         "route the answer — qualified to the next step, unqualified closed "
         "with a reason, uncertain to a person.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _QUALIFY_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _QUALIFY_QUESTIONS,
         depth="implemented"),

    _tpl("appointment_setter", "AI Appointment Setter", "appointment_setter",
         "Turns an interested contact into a confirmed appointment on a real "
         "calendar.",
         "Get this interested contact onto the calendar. Offer only times the "
         "calendar actually returned, confirm the details you were told to "
         "confirm, and book through the platform. Never state a time you have "
         "not been given.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS,
         depth="implemented"),

    _tpl("follow_up_specialist", "AI Follow-Up Specialist",
         "follow_up_specialist",
         "Follows up after a conversation, a missed appointment or a "
         "discovery call, on the cadence the customer configured.",
         "Follow up on what already happened with this contact. Reference it "
         "accurately, collect whatever is still missing, and either move them "
         "forward or close the loop honestly.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE, _QUALIFY_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS,
         depth="implemented"),

    _tpl("sales_assistant", "AI Sales Assistant", "sales_assistant",
         "Supports an active sale: answers questions from approved knowledge, "
         "keeps the record current, and brings a person in at the right time.",
         "Help this contact make progress on an active sale. Answer only from "
         "the customer's approved knowledge, say plainly when you do not know, "
         "keep the record accurate, and hand over the moment the conversation "
         "needs a person.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE, _QUALIFY_BUNDLE, _OPPORTUNITY_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS,
         depth="implemented"),

    _tpl("scheduling_coordinator", "AI Scheduling Coordinator",
         "scheduling_coordinator",
         "Confirms, reminds about and reschedules appointments.",
         "Keep this appointment real: confirm it, remind about it, and "
         "reschedule it through the calendar if they need a different time.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS),

    _tpl("customer_care_agent", "AI Customer Care Agent",
         "customer_care_agent",
         "Looks after existing customers: questions, changes and check-ins.",
         "Look after this existing customer. Answer from approved knowledge, "
         "record what they asked for, and escalate anything about money, "
         "contracts or complaints to a person immediately.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE, _VOICE_BUNDLE),
         _ALL_CHANNELS,
         _COMMON_QUESTIONS,
         policy={"always_escalate": ["billing", "contract", "complaint"]}),

    _tpl("receptionist", "AI Receptionist", "receptionist",
         "Answers first contact, works out what the person needs, and routes "
         "them.",
         "Find out what this person needs and get them to the right place — "
         "an appointment, a person, or an answer from approved knowledge.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _BOOKING_BUNDLE, _VOICE_BUNDLE),
         _ALL_CHANNELS,
         _COMMON_QUESTIONS + _BOOKING_QUESTIONS),

    _tpl("support_specialist", "AI Support Specialist", "support_specialist",
         "Works support requests using the platform's existing support engine "
         "as the authoritative ticket system.",
         "Work this support request. The support system owns the ticket and "
         "its state; you gather, clarify and escalate.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, ["conversation.prepare_email",
                                                 "conversation.send_email"]),
         [C.CHANNEL_EMAIL],
         _COMMON_QUESTIONS,
         description=(
             "DELIBERATELY THIN. Support Intelligence (T5) is the "
             "authoritative ticketing, diagnostics and remediation engine and "
             "this template does not duplicate any of it — section 30. The "
             "job exists so a future support employee consumes the SAME "
             "workforce framework rather than growing a second one.")),

    _tpl("prospecting_specialist", "AI Prospecting Specialist",
         "prospecting_specialist",
         "Works legitimately imported prospects that are not yet contacts.",
         "Work this imported prospect toward a first legitimate conversation, "
         "within whatever permission the import actually carried.",
         _tools(_READ_BUNDLE, _WORKFLOW_BUNDLE, _OUTREACH_BUNDLE,
                _QUALIFY_BUNDLE),
         _TEXT_CHANNELS,
         _COMMON_QUESTIONS + _QUALIFY_QUESTIONS,
         description=(
             "SITS DOWNSTREAM OF LEAD SCRAPER, which is a separate product "
             "and is not built here — section 29. This job consumes prospects "
             "that already reached AdvisorFlow through the normal import "
             "path, which means they have already been through dedupe, "
             "contactability and the permission columns. Scraped data is "
             "never treated as permission by this or any other job.")),

    _tpl("manager_supervisor", "AI Manager", "manager_supervisor",
         "Watches the workforce and tells an operator what needs attention.",
         "Report what is happening across this customer's AI team and what a "
         "person should look at. Recommend; do not act.",
         _tools(_READ_BUNDLE, ["memory.remember", "handoff.create",
                               "employee.request_review"]),
         [],
         [{"key": "report_to", "label": "Who should receive its reports?",
           "type": "user", "required": True}],
         description=(
             "THE SUPERVISOR HAS NO OUTREACH TOOLS AND NO ROOT AUTHORITY. It "
             "observes and recommends — section 21. Anything it wants done "
             "goes through the same registered tools and the same authority "
             "checks as every other actor, which is why its tool list is "
             "short rather than special."),
         depth="implemented"),
])

ALL_TEMPLATE_KEYS: Tuple[str, ...] = tuple(sorted(TEMPLATES))


def template(key: str) -> Optional[TemplateSpec]:
    return TEMPLATES.get((key or "").strip())


def templates_for_role(job_role: str) -> List[TemplateSpec]:
    return [t for t in TEMPLATES.values() if t.job_role == job_role]
