"""THE MODEL ROUTER — capability classes in, a provider out.

BUSINESS LOGIC ASKS FOR A CAPABILITY, NOT FOR A VENDOR. Nothing in this package
names a model. `runtime.py` asks for `PLAN` and gets whatever the platform has
configured; the day that changes, it changes here and in no other file.
Section 18: do not scatter provider model names across the application.

THE CAPABILITY CLASSES, and why each is separate rather than "the good model"
and "the cheap model": they have genuinely different requirements and are
served by different things.

    CLASSIFY        short, cheap, high volume. Is this reply a yes?
    PLAN            the working loop: pick the next tool and its arguments.
    CONVERSE        compose something a person will read.
    REASON          the hard case — escalation, an unusual objection.
    STRUCTURED      emit an object that must validate against a schema.
    REALTIME_VOICE  a live spoken conversation.
    STT / TTS       speech in, speech out.
    EMBED           retrieval, if and when retrieval needs it.

THE DEFAULT PROVIDER NEEDS NO CREDENTIALS, AND THAT IS DELIBERATE.

Section 57: a missing AI credential is not a blocker to this architecture. The
`deterministic` provider is a real, reviewable planner that drives the whole
engine — eligibility, tools, state machine, handoffs, appointments — from the
record's own state. It is what the simulator and the evaluation harness run
against, which means every gate in this system is exercised by tests that
cannot be flaky because a vendor was slow, and it is what a customer's employee
falls back to if a provider is down rather than the work simply stopping.

It is also honest about what it is: a deterministic planner is not a language
model, and `AIEmployeeRun.provider` records which one answered so nobody reads
a simulation result as proof that a model behaved.

LIVE LLM PROVIDERS ARE PRESENT AND SWITCHED OFF IN THIS BUILD. `OpenAIProvider`
resolves only when a key exists AND `AI_WORKFORCE_LLM_ENABLED` is set. Neither
is true here, so no external model is called by this deployment.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

# ── capability classes ──────────────────────────────────────────────────────
CLASSIFY = "classify"
PLAN = "plan"
CONVERSE = "converse"
REASON = "reason"
STRUCTURED = "structured"
REALTIME_VOICE = "realtime_voice"
STT = "stt"
TTS = "tts"
EMBED = "embed"

CAPABILITIES = {
    CLASSIFY: "Fast classification and extraction",
    PLAN: "Choose the next action for a work item",
    CONVERSE: "Compose a message a person will read",
    REASON: "Harder reasoning and escalation judgement",
    STRUCTURED: "Emit a validated structured object",
    REALTIME_VOICE: "Live spoken conversation",
    STT: "Speech to text",
    TTS: "Text to speech",
    EMBED: "Embeddings for retrieval",
}


class ProviderUnavailable(RuntimeError):
    """This provider cannot serve the request. The router falls back."""


class ModelRequest:
    """What the engine asks for. Carries no vendor concepts at all."""

    __slots__ = ("capability", "context", "tools", "objective", "max_output")

    def __init__(self, capability: str, context: Dict,
                 tools: Optional[List[Dict]] = None, objective: str = "",
                 max_output: int = 1200):
        self.capability = capability
        self.context = context or {}
        self.tools = list(tools or [])
        self.objective = objective
        self.max_output = max_output


class ModelResponse:
    """What comes back. ALWAYS a proposal, never an instruction.

    `tool` and `arguments` are a REQUEST that `tools.authorize` will answer.
    `confidence` is the model's own word and is used only to decide whether to
    ask for a human review — never to widen authority.
    """

    __slots__ = ("tool", "arguments", "rationale", "confidence", "provider",
                 "model", "prompt_tokens", "completion_tokens", "raw",
                 "parse_error")

    def __init__(self, tool=None, arguments=None, rationale="", confidence=None,
                 provider=None, model=None, prompt_tokens=None,
                 completion_tokens=None, raw=None, parse_error=None):
        self.tool = tool
        self.arguments = dict(arguments or {})
        self.rationale = rationale
        self.confidence = confidence
        self.provider = provider
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.raw = raw
        self.parse_error = parse_error

    def as_dict(self) -> Dict:
        return {"tool": self.tool, "arguments": self.arguments,
                "rationale": self.rationale, "confidence": self.confidence,
                "provider": self.provider, "model": self.model,
                "parse_error": self.parse_error}


# ── providers ───────────────────────────────────────────────────────────────

class BaseProvider:
    key = "base"
    label = "Base"
    capabilities: tuple = ()
    model_name: Optional[str] = None

    def available(self) -> bool:
        return True

    def plan(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError


class DeterministicProvider(BaseProvider):
    """A real planner, written down, that needs no credentials.

    IT READS THE SAME CONTEXT A MODEL WOULD and returns the same shape. The
    rules below are the reactivation/qualification/booking playbook stated
    explicitly, in the order a person would apply them:

        1. somebody said stop                 -> record the opt-out
        2. somebody said no                   -> record not interested
        3. somebody asked for a person, or is
           upset, or raised money or law      -> hand off
        4. somebody asked to book             -> read availability, then book
        5. they replied and it is unclear     -> ask for review
        6. they replied positively            -> qualify, then book
        7. nothing sent yet                   -> compose and send the opener
        8. sent, no reply, cadence due        -> compose and send the follow-up
        9. touches used up                    -> exhausted

    IT IS NOT A LANGUAGE MODEL AND DOES NOT PRETEND TO BE. Its classification
    of a reply is keyword-based and it says so in `rationale`, so a simulation
    result is never mistaken for evidence about model behaviour.
    """

    key = "deterministic"
    label = "Deterministic planner (no external model)"
    capabilities = (CLASSIFY, PLAN, CONVERSE, STRUCTURED)
    model_name = "advisorflow.deterministic.v1"

    STOP_WORDS = ("stop", "unsubscribe", "opt out", "opt-out", "remove me",
                  "do not contact", "don't contact", "take me off")
    NEGATIVE = ("not interested", "no thanks", "no thank you", "not at this",
                "leave me alone", "wrong person", "wrong number")
    HUMAN = ("speak to someone", "talk to a person", "call me", "real person",
             "speak to a human", "manager", "speak with someone")
    ESCALATE = ("lawyer", "attorney", "sue", "complaint", "refund", "billing",
                "charge", "invoice", "fraud", "dispute")
    HOSTILE = ("harass", "furious", "disgusting", "shut up", "never again")
    BOOKING = ("book", "appointment", "schedule", "come in", "meet", "visit",
               "available", "what time")
    POSITIVE = ("yes", "sure", "interested", "please", "sounds good", "ok",
                "okay", "tell me more", "how much")

    def _last_inbound(self, context: Dict) -> str:
        for block in reversed(context.get("untrusted_blocks") or []):
            if "UNTRUSTED:contact_message" in block:
                return block.lower()
        return ""

    def _has(self, text: str, words) -> bool:
        return any(w in text for w in words)

    # ── READING WHAT ALREADY HAPPENED THIS RUN ──────────────────────────────
    #
    # A planner that cannot see the result of its own last call is a planner
    # that asks the same question forever. The run loop puts the structured
    # result of every tool call on `observations`; these two helpers are how
    # the planner uses it.

    def _observations(self, context: Dict) -> List[Dict]:
        obs = context.get("observations")
        return list(obs) if isinstance(obs, list) else []

    def _offered_slots(self, context: Dict) -> List[Dict]:
        """Slots the CALENDAR returned this run. The only bookable times.

        Returns them in the order the calendar gave them. The planner never
        constructs a time of its own — the one hard rule in section 45 — so if
        this is empty there is nothing to book, whatever the contact asked for.
        """
        for obs in reversed(self._observations(context)):
            if obs.get("tool") != "calendar.get_availability":
                continue
            data = obs.get("data") or {}
            if data.get("availability_status") == "ok":
                return [s for s in (data.get("slots") or [])
                        if isinstance(s, dict) and s.get("starts_at")]
            return []
        return []

    def _read_calendar_yet(self, context: Dict) -> bool:
        return any(o.get("tool") == "calendar.get_availability"
                   for o in self._observations(context))

    def _book_attempts(self, context: Dict) -> int:
        return sum(1 for o in self._observations(context)
                   if o.get("tool") == "appointment.book")

    # CHANNELS THAT HAVE ALREADY BEEN REFUSED THIS RUN.
    #
    # A record can have a phone number and no SMS consent. The planner looks at
    # `has_phone`, asks to text, and the gateway refuses on eligibility — which
    # is correct. What was NOT correct was the next turn: the planner asked to
    # text again, the repetition guard stopped it, and a record that could
    # perfectly well have been emailed ended up in Needs Review. Remembering
    # which channel was refused is what makes the fallback happen.
    _SEND_TOOL_CHANNEL = {"conversation.send_sms": "sms",
                          "conversation.send_email": "email",
                          "conversation.place_call": "voice"}

    def _refused_channels(self, context: Dict) -> set:
        out = set()
        for obs in self._observations(context):
            if obs.get("ok"):
                continue
            channel = self._SEND_TOOL_CHANNEL.get(obs.get("tool") or "")
            if channel:
                out.add(channel)
        return out

    def _toward_appointment(self, context: Dict, available, lead_id, why):
        """Read the calendar, then take a time it actually returned.

        The order is the requirement. Nothing here can produce a booking for a
        time the calendar did not just offer, because the only source of a
        `starts_at` is the observation itself.
        """
        if "calendar.get_availability" in available \
                and not self._read_calendar_yet(context):
            return ("calendar.get_availability",
                    {"lead_id": lead_id, "days_ahead": 7},
                    "%s — read real availability before offering a time." % why)
        slots = self._offered_slots(context)
        attempt = self._book_attempts(context)
        if slots and "appointment.book" in available and attempt < len(slots):
            # A refused booking moves to the NEXT offered slot rather than
            # retrying the same one: "that time is no longer available" is an
            # answer, and repeating it is how a run burns its budget.
            return ("appointment.book",
                    {"lead_id": lead_id,
                     "start_at": slots[attempt]["starts_at"]},
                    "%s — taking an opening the calendar returned." % why)
        if self._read_calendar_yet(context) and not slots:
            return ("handoff.create",
                    {"reason_code": "appointment_requires_human",
                     "summary": "They want to book and the calendar had no "
                                "openings to offer.",
                     "recommended_action": "Offer them a time by hand.",
                     "priority": "high"},
                    "No openings, so a person should arrange this.")
        return (None, {}, "")

    def plan(self, request: ModelRequest) -> ModelResponse:
        ctx = request.context
        facts = ctx.get("facts") or {}
        work = facts.get("work") or {}
        contact = facts.get("contact") or {}
        available = {t["key"] for t in (request.tools or [])}
        lead_id = contact.get("lead_id")
        reply = self._last_inbound(ctx)

        def out(tool, args=None, why="", confidence="high"):
            return ModelResponse(tool=tool, arguments=args or {}, rationale=why,
                                 confidence=confidence, provider=self.key,
                                 model=self.model_name)

        # 1-3. WHAT THEY SAID OUTRANKS WHAT WE WANTED.
        if reply:
            if self._has(reply, self.STOP_WORDS) and \
                    "lead.mark_do_not_contact" in available:
                return out("lead.mark_do_not_contact",
                           {"lead_id": lead_id, "detail": "opt-out keyword"},
                           "The reply contains an opt-out instruction.")
            if self._has(reply, self.ESCALATE) or self._has(reply, self.HOSTILE):
                return out("handoff.create",
                           {"reason_code": "legal_or_compliance"
                            if self._has(reply, self.ESCALATE) else "hostile",
                            "summary": "The contact raised something outside "
                                       "this employee's authority.",
                            "recommended_action": "Call them today.",
                            "priority": "urgent"},
                           "Money, law or hostility — a person must take this.")
            if self._has(reply, self.HUMAN):
                return out("handoff.create",
                           {"reason_code": "human_requested",
                            "summary": "The contact asked to speak to somebody.",
                            "recommended_action": "Call them back.",
                            "priority": "urgent"},
                           "They asked for a person.")
            if self._has(reply, self.NEGATIVE) and \
                    "lead.mark_not_interested" in available:
                return out("lead.mark_not_interested",
                           {"lead_id": lead_id, "detail": "declined by reply"},
                           "They said no. That is not an opt-out, and it ends "
                           "the work.")
            if self._has(reply, self.BOOKING) or self._has(reply, self.POSITIVE):
                why = ("They asked about a time"
                       if self._has(reply, self.BOOKING)
                       else "They replied positively")
                tool, args, rationale = self._toward_appointment(
                    ctx, available, lead_id, why)
                if tool:
                    return out(tool, args, rationale)
                if "employee.mark_qualified" in available:
                    return out("employee.mark_qualified",
                               {"summary": "Responded positively.",
                                "facts": {"interest": "positive reply"}},
                               "Interested, and no booking route is open to "
                               "this employee.")
            # 5. A REPLY NOBODY CAN CLASSIFY IS A REVIEW, NOT A GUESS.
            if "employee.request_review" in available:
                return out("employee.request_review",
                           {"reason": "The reply did not match anything this "
                                      "planner recognises."},
                           "Keyword classification could not read this reply.",
                           confidence="low")

        # 7-9. NO REPLY: the cadence.
        touches = int(work.get("touches") or 0)
        max_touches = int((ctx.get("authority") or {}).get("max_touches")
                          or request.context.get("max_touches") or 9)
        if touches >= max_touches and "employee.mark_exhausted" in available:
            return out("employee.mark_exhausted",
                       {"detail": "%d touches, no response" % touches},
                       "Every permitted touch has been used.")

        channels = (ctx.get("authority") or {}).get("channels") or []
        refused = self._refused_channels(ctx)
        if "conversation.send_sms" in available and "sms" in channels \
                and contact.get("has_phone") and "sms" not in refused:
            return out("conversation.send_sms",
                       {"lead_id": lead_id,
                        "body": self._compose_sms(ctx, touches)},
                       "Touch %d, by SMS." % (touches + 1))
        if "conversation.send_email" in available and "email" in channels \
                and contact.get("has_email") and "email" not in refused:
            return out("conversation.send_email",
                       {"lead_id": lead_id,
                        "subject": self._compose_subject(ctx),
                        "body": self._compose_email(ctx, touches)},
                       "Touch %d, by email%s."
                       % (touches + 1,
                          " (SMS was refused for this record)"
                          if "sms" in refused else ""))

        if "employee.request_review" in available:
            return out("employee.request_review",
                       {"reason": "No usable channel for this record."},
                       "Nothing can be sent, so a person should look.",
                       confidence="low")
        return out(None, {}, "Nothing to do.", confidence="low")

    # ── composition ─────────────────────────────────────────────────────────
    #
    # PLAIN, SHORT, AND IT NEVER INVENTS A FACT. No price, no claim about the
    # business, no appointment time — the only dynamic parts are the contact's
    # first name and the business's name, both read from the platform's own
    # records, and the booking placeholder the platform substitutes.
    def _business(self, ctx: Dict) -> str:
        return ((ctx.get("facts") or {}).get("business_name")
                or (ctx.get("facts") or {}).get("employee_name") or "us")

    def _first_name(self, ctx: Dict) -> str:
        return ((ctx.get("facts") or {}).get("contact") or {}).get("first_name") \
            or "there"

    def _compose_sms(self, ctx: Dict, touches: int) -> str:
        name = self._first_name(ctx)
        if touches == 0:
            return ("Hi %s, this is %s. We're reaching out about the enquiry "
                    "you made with us a while back. Would it help to talk it "
                    "through? Reply STOP to opt out."
                    % (name, self._business(ctx)))
        if touches < 3:
            return ("Hi %s, following up from %s — happy to answer any "
                    "questions whenever suits you. Reply STOP to opt out."
                    % (name, self._business(ctx)))
        return ("Hi %s, last note from %s — if now isn't the right time that's "
                "completely fine. Reply STOP to opt out."
                % (name, self._business(ctx)))

    def _compose_subject(self, ctx: Dict) -> str:
        return "Following up from %s" % self._business(ctx)

    def _compose_email(self, ctx: Dict, touches: int) -> str:
        return ("Hi %s,\n\nWe wanted to follow up on the enquiry you made with "
                "%s. If it would help to talk it through, just reply to this "
                "message and we'll arrange a time.\n\nIf you'd rather not hear "
                "from us again, reply and say so and we'll stop.\n"
                % (self._first_name(ctx), self._business(ctx)))


class ScriptedProvider(BaseProvider):
    """A provider that returns a queued list of decisions, in order.

    FOR THE EVALUATION HARNESS AND THE ADVERSARIAL SUITE. It is how a test
    makes the engine attempt something a sensible planner never would — call a
    tool it does not hold, name another tenant's lead, follow an injected
    instruction — and then asserts that the GATEWAY refused it. Without this,
    the security tests could only ever prove that a well-behaved planner
    behaves well, which proves nothing.
    """

    key = "scripted"
    label = "Scripted provider (tests and evaluation)"
    capabilities = (CLASSIFY, PLAN, CONVERSE, STRUCTURED, REASON)
    model_name = "scripted"

    def __init__(self, decisions: Optional[List[Dict]] = None,
                 fallback: Optional[BaseProvider] = None):
        self.decisions = list(decisions or [])
        self.fallback = fallback
        self.calls = 0

    def plan(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.decisions:
            step = self.decisions.pop(0)
            if step.get("_raise"):
                raise ProviderUnavailable(step.get("_raise"))
            return ModelResponse(tool=step.get("tool"),
                                 arguments=step.get("arguments") or {},
                                 rationale=step.get("rationale", "scripted"),
                                 confidence=step.get("confidence", "high"),
                                 provider=self.key, model=self.model_name,
                                 raw=step.get("raw"),
                                 parse_error=step.get("parse_error"))
        if self.fallback is not None:
            return self.fallback.plan(request)
        return ModelResponse(tool=None, rationale="script exhausted",
                             provider=self.key, model=self.model_name)


class OpenAIProvider(BaseProvider):
    """A live LLM provider. NOT RESOLVED IN THIS BUILD.

    Two independent conditions, both false here: a key must exist and
    AI_WORKFORCE_LLM_ENABLED must be set. The class is real so the router has
    something to route to and so the shape of a live provider is settled; the
    call is not made.
    """

    key = "openai"
    label = "OpenAI"
    capabilities = (CLASSIFY, PLAN, CONVERSE, REASON, STRUCTURED, EMBED)

    def __init__(self):
        self.model_name = os.environ.get("AI_WORKFORCE_OPENAI_MODEL",
                                         "gpt-4.1-mini")

    def available(self) -> bool:
        enabled = (os.environ.get("AI_WORKFORCE_LLM_ENABLED", "")
                   .strip().lower() in ("1", "true", "yes", "on"))
        return bool(enabled and os.environ.get("OPENAI_API_KEY"))

    def plan(self, request: ModelRequest) -> ModelResponse:
        if not self.available():
            raise ProviderUnavailable(
                "No live model provider is enabled in this deployment.")
        # The live call would go here. It is deliberately not implemented on
        # this branch: an untested network call on a dark-launch path is a
        # liability, and the router's fallback already produces a working
        # engine. Implementing it is one method, against the contract above.
        raise ProviderUnavailable(
            "The live provider is configured but not implemented on this "
            "build. The deterministic planner is serving this capability.")


# ── the router ──────────────────────────────────────────────────────────────

_DETERMINISTIC = DeterministicProvider()
_PROVIDERS: List[BaseProvider] = [OpenAIProvider(), _DETERMINISTIC]
_OVERRIDE: Optional[BaseProvider] = None


def providers_report() -> List[Dict]:
    """What God Mode shows: who could serve what, and who actually will."""
    out = []
    for p in _PROVIDERS:
        out.append({"key": p.key, "label": p.label,
                    "model": p.model_name,
                    "capabilities": list(p.capabilities),
                    "available": bool(p.available())})
    return out


def resolve(capability: str) -> BaseProvider:
    """The provider that will serve this capability right now.

    FALLS BACK RATHER THAN FAILING. A provider being unavailable is an
    operational fact, not a reason for a customer's work to stop; the
    deterministic planner is always available and always last.
    """
    if _OVERRIDE is not None:
        return _OVERRIDE
    for p in _PROVIDERS:
        if capability in p.capabilities and p.available():
            return p
    return _DETERMINISTIC


def plan(capability: str, context: Dict, tools: Optional[List[Dict]] = None,
         objective: str = "") -> ModelResponse:
    """Ask for a decision. Never raises; a failure becomes an empty proposal.

    A provider that throws must not take the run down: the run loop treats an
    empty proposal as "ask for review", which is the safe conclusion.
    """
    request = ModelRequest(capability, context, tools=tools, objective=objective)
    provider = resolve(capability)
    try:
        response = provider.plan(request)
    except ProviderUnavailable as exc:
        _log.info("workforce model router: %s unavailable (%s); falling back",
                  provider.key, exc)
        try:
            response = _DETERMINISTIC.plan(request)
        except Exception:                                    # noqa: BLE001
            _log.exception("workforce model router: fallback planner failed")
            return ModelResponse(tool=None, rationale="no provider could answer",
                                 provider="none", parse_error="provider_failed")
    except Exception:                                        # noqa: BLE001
        _log.exception("workforce model router: provider %s raised",
                       provider.key)
        return ModelResponse(tool=None, rationale="the provider failed",
                             provider=provider.key, parse_error="provider_error")
    return response


class use_provider:
    """Install a provider for the duration of a block. Restores on exit."""

    def __init__(self, provider: BaseProvider):
        self.provider = provider
        self._previous = None

    def __enter__(self):
        global _OVERRIDE
        self._previous = _OVERRIDE
        _OVERRIDE = self.provider
        return self.provider

    def __exit__(self, *exc):
        global _OVERRIDE
        _OVERRIDE = self._previous
        return False


# ── structured output validation ────────────────────────────────────────────

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_structured(raw: Any) -> Dict:
    """Turn whatever a provider returned into an object, or say it could not.

    SECTION 41: a parse failure must never become an unauthorized action. This
    returns `{"_parse_error": ...}` and the run loop routes that to Needs
    Review — it does not guess at what the model might have meant, because the
    guesses available are all destructive.
    """
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return {"_parse_error": "not text"}
    match = _JSON_BLOCK.search(raw)
    if not match:
        return {"_parse_error": "no object found"}
    try:
        val = json.loads(match.group(0))
    except (ValueError, TypeError) as exc:
        return {"_parse_error": "invalid JSON: %s" % exc}
    return dict(val) if isinstance(val, dict) else {"_parse_error": "not an object"}
