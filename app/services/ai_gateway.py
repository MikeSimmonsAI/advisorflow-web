"""THE ONE DOOR TO OPENAI. Every model request in this codebase goes through here.

WHY THIS EXISTS — THE PRODUCTION FINDING. With nobody using the product, the
backend's `_ai_conversation_loop` asked OpenAI for a message every two minutes,
around the clock, because conversations stayed due. A provider circuit breaker
kept the web server alive when the account ran dry, but it did nothing about
the spending itself: the moment credit returned, the loop would resume paying
for work nobody asked for. Eighteen call sites, each constructing its own
client with its own hard-coded model, meant there was no single place to turn
that off, cap it, or see it.

WHAT THIS MODULE GUARANTEES

  1. BACKGROUND AI IS OFF UNLESS SOMEBODY TURNS IT ON.
     `AI_BACKGROUND_AUTOMATION_ENABLED` defaults to false when unset. A
     background request with the switch off never reaches the provider: no
     request, no retry. It logs ONE line per feature per process, not one per
     pass.

  2. MANUAL AI IS A SEPARATE SWITCH.
     `AI_MANUAL_ACTIONS_ENABLED` defaults to true. A manual request must name
     the authenticated user who made it (`actor`); a "manual" call with no
     actor is refused, so a background job cannot borrow the manual switch by
     calling itself manual.

  3. NO CALL INHERITS A MODEL.
     A call names a CAPABILITY; the model comes from CAPABILITY_MODELS. Passing
     `model=` is refused. An environment override (`AI_MODEL_<CAPABILITY>`) is
     honoured only if it names a model in APPROVED_MODELS — anything else
     refuses the request and says so in the log. APPROVED_MODELS changes in
     code, reviewed and tested, never by an environment variable: approving a
     new model is a spending decision.

  4. BACKGROUND SPEND IS BOUNDED.
     Per-pass, per-hour and per-day call caps, and a provider breaker that
     refuses further background calls for a cooldown after a provider-level
     failure (no credit, bad key, unreachable). Background requests are made
     with the SDK's automatic retries OFF, so a failure is one request, not
     three.

  5. EVERY REQUEST IS LOGGED, AND NOTHING SENSITIVE IS.
     feature, capability, mode, org, model, correlation id, duration, and token
     usage when the provider returns it. Never the key, never the prompt,
     never the completion.

The counters are per process. That is stated rather than hidden: a process
that restarts starts its hourly and daily windows again. They are a second
line behind the master switch, which is the one that matters and which is off.
"""
import contextvars
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger("ai_gateway")

BACKGROUND = "background"
MANUAL = "manual"
MODES = (BACKGROUND, MANUAL)


# ── the two switches ────────────────────────────────────────────────────────

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _flag(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    # Unset, empty or unrecognised: the default. For the background switch the
    # default is OFF, so a typo can never turn unattended spending on.
    return default


def background_enabled() -> bool:
    """Scheduled and event-driven AI with no person at the keyboard."""
    return _flag("AI_BACKGROUND_AUTOMATION_ENABLED", False)


def manual_enabled() -> bool:
    """AI a signed-in person asked for, one action at a time."""
    return _flag("AI_MANUAL_ACTIONS_ENABLED", True)


# ── approved models, and which capability uses which ────────────────────────
#
# THE ONLY MODELS THIS PLATFORM MAY REQUEST. Adding one is a spending decision
# and a code review, not a configuration change. `gpt-6-astra` is deliberately
# absent, and a test holds it absent.
APPROVED_MODELS = frozenset({
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4o-realtime-preview",
})

# Capability -> model. Every value was the model its call site hard-coded
# before this module existed, so pinning changes no behaviour and no cost.
CAPABILITY_MODELS: Dict[str, str] = {
    "touch_email":            "gpt-4o",       # scheduled AI conversation touch
    "reply_response":         "gpt-4o",       # reply to an inbound lead message
    "post_booking_reply":     "gpt-4o",       # concierge after a booking
    "auto_send_reply":        "gpt-4o",       # auto-send draft to an inbound SMS
    "reengagement_draft":     "gpt-4o",       # proactive re-engagement drafts
    "concierge_chat":         "gpt-4o",       # concierge conversation
    "support_chat":           "gpt-4o",       # support assistant
    "pipeline_reply":         "gpt-4o",       # pipeline conversation reply
    "draft_reply":            "gpt-4o-mini",  # reply draft for a person
    "draft_reply_rich":       "gpt-4o",       # the richer draft path
    "campaign_copy":          "gpt-4o-mini",  # campaign message generation
    "objection_help":         "gpt-4o-mini",  # objection handling suggestions
    "lead_analysis":          "gpt-4o-mini",  # lead analysis
    "auto_send_eligibility":  "gpt-4o-mini",  # is this reply safe to auto-send
    "post_appointment":       "gpt-4o-mini",  # post-appointment follow-up
    "reply_classification":   "gpt-4o-mini",  # classify an inbound reply
    "template_copy":          "gpt-4o-mini",  # template generation
    # Reads one property owner's message into structured answers — intent,
    # price, timeline, condition, occupancy. Extraction, not conversation, so
    # the cheaper model is the right one: the richer model's advantage is in
    # what it WRITES, and this capability writes nothing. It has a real
    # deterministic fallback (app/services/wholesale_ai.py), so a refusal here
    # degrades the reading rather than stopping the module.
    "wholesale_seller_qualify": "gpt-4o-mini",
    "voice_call":             "gpt-4o-realtime-preview",
}


class AIRefused(Exception):
    """Base for every refusal this module makes. Nothing was sent to the provider."""


class AIDisabled(AIRefused):
    """The switch for this mode is off."""


class ModelNotApproved(AIRefused):
    """The resolved model is not in APPROVED_MODELS, or the capability is unknown."""


class SpendLimitReached(AIRefused):
    """A background cap was hit."""


class ProviderCircuitOpen(AIRefused):
    """The provider failed recently; background calls are paused."""


def resolve_model(capability: str) -> str:
    """The approved model for a capability, or a refusal. Never a default."""
    if capability not in CAPABILITY_MODELS:
        log.error("ai_gateway refused: unknown capability %r (no model is "
                  "assigned to it, and none is guessed)", capability)
        raise ModelNotApproved("unknown AI capability: %s" % capability)
    env_name = "AI_MODEL_" + capability.upper()
    override = (os.environ.get(env_name) or "").strip()
    model = override or CAPABILITY_MODELS[capability]
    if model not in APPROVED_MODELS:
        log.error("ai_gateway refused: %s=%r is not an approved model; the "
                  "request was not made. Approved: %s", env_name if override
                  else "CAPABILITY_MODELS[%s]" % capability, model,
                  ", ".join(sorted(APPROVED_MODELS)))
        raise ModelNotApproved("model %r is not approved" % model)
    return model


# ── background spend limits ─────────────────────────────────────────────────

def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int((os.environ.get(name) or "").strip()))
    except ValueError:
        return default


def limits() -> Dict[str, int]:
    return {
        "per_pass": _int_env("AI_BG_MAX_CALLS_PER_PASS", 10),
        "per_hour": _int_env("AI_BG_MAX_CALLS_PER_HOUR", 60),
        "per_day": _int_env("AI_BG_MAX_CALLS_PER_DAY", 300),
        "breaker_cooldown_s": _int_env("AI_BG_BREAKER_COOLDOWN_SECONDS", 900),
    }


# Provider failures that say the same thing to every request that follows.
PROVIDER_LEVEL_ERRORS = frozenset({
    "RateLimitError", "AuthenticationError", "PermissionDeniedError",
    "APIConnectionError", "APITimeoutError", "InternalServerError",
})


class _Budget:
    """Process-wide background counters. Thread-safe: passes run off the loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._calls = deque()          # monotonic timestamps of background calls
        self._breaker_until = 0.0
        self._breaker_reason = None
        self.refused: Dict[str, int] = {}
        self.attempted = 0

    def reset(self):
        with self._lock:
            self._calls.clear()
            self._breaker_until = 0.0
            self._breaker_reason = None
            self.refused = {}
            self.attempted = 0

    def _count_since(self, horizon: float) -> int:
        return sum(1 for t in self._calls if t >= horizon)

    def acquire(self, feature: str):
        lim = limits()
        now = time.monotonic()
        with self._lock:
            if now < self._breaker_until:
                self._refuse("breaker")
                raise ProviderCircuitOpen(
                    "background AI paused for %ds after a provider failure (%s)"
                    % (int(self._breaker_until - now), self._breaker_reason))
            while self._calls and self._calls[0] < now - 86400:
                self._calls.popleft()
            if self._count_since(now - 3600) >= lim["per_hour"]:
                self._refuse("per_hour")
                raise SpendLimitReached("background AI hourly cap (%d) reached"
                                        % lim["per_hour"])
            if len(self._calls) >= lim["per_day"]:
                self._refuse("per_day")
                raise SpendLimitReached("background AI daily cap (%d) reached"
                                        % lim["per_day"])
            p = _PASS.get()
            if p is not None:
                if p["calls"] >= p["max"]:
                    self._refuse("per_pass")
                    raise SpendLimitReached(
                        "background AI per-pass cap (%d) reached in %s"
                        % (p["max"], p["feature"]))
                p["calls"] += 1
            self._calls.append(now)
            self.attempted += 1

    def trip(self, reason: str):
        with self._lock:
            self._breaker_until = time.monotonic() + limits()["breaker_cooldown_s"]
            self._breaker_reason = reason

    def _refuse(self, why: str):
        self.refused[why] = self.refused.get(why, 0) + 1

    def refuse_disabled(self):
        with self._lock:
            self._refuse("disabled")

    def snapshot(self) -> Dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {
                "background_calls_last_hour": self._count_since(now - 3600),
                "background_calls_last_day": len([t for t in self._calls
                                                  if t >= now - 86400]),
                "background_calls_attempted_this_process": self.attempted,
                "breaker_open_for_s": max(0, int(self._breaker_until - now)),
                "breaker_reason": self._breaker_reason,
                "refused": dict(self.refused),
            }


_BUDGET = _Budget()
_PASS: contextvars.ContextVar = contextvars.ContextVar("ai_background_pass",
                                                       default=None)


@contextmanager
def background_pass(feature: str, max_calls: Optional[int] = None):
    """Bound one pass of a background job to a number of provider calls.

    A ContextVar, so it follows the pass into the worker thread `_off_loop`
    runs it in (asyncio.to_thread copies the context).

    Nested passes join the outer one: the loop opens a pass around every org
    it walks, and process_scheduled_touches opens one for callers that did
    not, so the cap is per loop pass, not per org.
    """
    if _PASS.get() is not None:
        yield
        return
    token = _PASS.set({"feature": feature, "calls": 0,
                       "max": limits()["per_pass"] if max_calls is None else max_calls})
    try:
        yield
    finally:
        _PASS.reset(token)


# ── logging the disabled state once ─────────────────────────────────────────

_disabled_logged = set()
_disabled_lock = threading.Lock()


def note_background_disabled(feature: str):
    """One line per feature per process — not one per pass, every two minutes."""
    _BUDGET.refuse_disabled()
    with _disabled_lock:
        if feature in _disabled_logged:
            return
        _disabled_logged.add(feature)
    log.warning("ai_gateway: background AI is DISABLED "
                "(AI_BACKGROUND_AUTOMATION_ENABLED is not true) - %s makes no "
                "provider requests, sends nothing and changes nothing. Logged "
                "once per process.", feature)


def check_background(feature: str) -> bool:
    """For a background JOB, before it queries or touches anything: may it run?"""
    if background_enabled():
        return True
    note_background_disabled(feature)
    return False


# ── the call ────────────────────────────────────────────────────────────────

_clients: Dict[str, Any] = {}
_clients_lock = threading.Lock()


def _client(mode: str):
    """One client per mode. Background: SDK retries OFF — a failure is one
    request. Manual: the SDK's default, because a person is waiting."""
    with _clients_lock:
        c = _clients.get(mode)
        if c is None:
            from openai import OpenAI
            kwargs = {"api_key": os.environ.get("OPENAI_API_KEY")}
            if mode == BACKGROUND:
                kwargs["max_retries"] = 0
            c = OpenAI(**kwargs)
            _clients[mode] = c
        return c


def _admit(*, feature: str, capability: str, mode: str,
           actor: Optional[str]) -> str:
    """Every check that runs before a provider request. Returns the model."""
    if mode not in MODES:
        raise AIRefused("mode must be %s or %s" % MODES)
    if mode == MANUAL:
        if not actor:
            log.error("ai_gateway refused: %s called as manual with no "
                      "authenticated actor", feature)
            raise AIRefused("a manual AI action must name the user who asked")
        if not manual_enabled():
            raise AIDisabled("manual AI actions are disabled "
                             "(AI_MANUAL_ACTIONS_ENABLED=false)")
    else:
        if not background_enabled():
            note_background_disabled(feature)
            raise AIDisabled("background AI automation is disabled")
    model = resolve_model(capability)
    if mode == BACKGROUND:
        _BUDGET.acquire(feature)
    return model


def chat_completion(*, feature: str, capability: str, mode: str,
                    messages: Iterable[Dict[str, Any]],
                    actor: Optional[str] = None, org_id: Optional[str] = None,
                    client: Any = None, **params):
    """Chat Completions through the gateway. Returns the SDK response unchanged.

    Raises an AIRefused subclass without contacting the provider when any
    check fails; re-raises the provider's own exception when the request was
    made and failed.

    `client` is a TEST SEAM. Callers pass `client=_get_client()`, and each
    module's `_get_client` returns None in production — so the gateway's own
    client is used — while a test can patch it to return a fake. Every check
    above still runs first either way: a fake client is never reached with
    the switch off.
    """
    if "model" in params:
        raise TypeError("chat_completion takes a capability, never a model")
    model = _admit(feature=feature, capability=capability, mode=mode, actor=actor)
    corr = uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    try:
        resp = (client if client is not None else _client(mode)).chat.completions.create(
            model=model, messages=list(messages), **params)
    except Exception as exc:                                  # noqa: BLE001
        kind = type(exc).__name__
        if mode == BACKGROUND and kind in PROVIDER_LEVEL_ERRORS:
            _BUDGET.trip(kind)
        log.error("ai_call feature=%s capability=%s mode=%s org=%s model=%s "
                  "corr=%s status=error kind=%s ms=%d", feature, capability,
                  mode, org_id or "-", model, corr, kind,
                  int((time.monotonic() - t0) * 1000))
        raise
    usage = getattr(resp, "usage", None)
    log.info("ai_call feature=%s capability=%s mode=%s org=%s model=%s corr=%s "
             "status=ok ms=%d tokens_in=%s tokens_out=%s", feature, capability,
             mode, org_id or "-", model, corr,
             int((time.monotonic() - t0) * 1000),
             getattr(usage, "prompt_tokens", "-"),
             getattr(usage, "completion_tokens", "-"))
    return resp


def admit_realtime(*, feature: str, mode: str, actor: Optional[str] = None,
                   org_id: Optional[str] = None) -> str:
    """For the one path that is not Chat Completions: the voice Realtime socket.
    Runs every check and returns the approved model to put in the URL."""
    model = _admit(feature=feature, capability="voice_call", mode=mode, actor=actor)
    log.info("ai_call feature=%s capability=voice_call mode=%s org=%s model=%s "
             "corr=%s status=admitted", feature, mode, org_id or "-", model,
             uuid.uuid4().hex[:12])
    return model


# ── what God Mode can see ───────────────────────────────────────────────────

def config_report() -> Dict[str, Any]:
    """The resolved configuration, for an operator. No secrets: the key is
    reported only as present or absent, and only *_MODEL-style variables are
    echoed, because a model name is not a credential."""
    _deny = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "DSN", "URL", "AUTH")
    model_env = {k: v[:80] for k, v in os.environ.items()
                 if "MODEL" in k.upper()
                 and not any(d in k.upper() for d in _deny)}
    resolved = {}
    for cap in sorted(CAPABILITY_MODELS):
        try:
            resolved[cap] = resolve_model(cap)
        except ModelNotApproved as exc:
            resolved[cap] = "REFUSED: %s" % exc
    return {
        "background_enabled": background_enabled(),
        "manual_enabled": manual_enabled(),
        "approved_models": sorted(APPROVED_MODELS),
        "capability_models": resolved,
        "model_environment": model_env,
        "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
        "limits": limits(),
        "counters": _BUDGET.snapshot(),
    }


def _reset_for_tests():
    _BUDGET.reset()
    with _disabled_lock:
        _disabled_logged.clear()
    with _clients_lock:
        _clients.clear()
