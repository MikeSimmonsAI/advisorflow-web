"""Pure normalization of an AI compose result. No imports beyond the stdlib.

Two jobs, both so that "AI returned no message" is never the whole story:

  parse_compose_output(raw)  turn the model's raw text into body/subject/stop,
                             accepting a fenced or bare JSON object, a few
                             alternative keys, or plain prose. Never invents
                             customer-facing text: no usable text -> body "".
  describe_failure(kind)     map an exception class name (or a parse outcome)
                             to a specific, retryable-or-actionable reason.
"""
import json
import re

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*(.*?)\s*```\s*$", re.DOTALL)
_BODY_KEYS = ("body", "message", "reply", "text", "content", "sms", "email_body")
_REFUSAL = re.compile(
    r"^\s*(i['’]?m sorry,? but i (can['’]?t|cannot|am unable)|i (can['’]?t|cannot|am unable to) "
    r"(help|assist|comply|write|generate)|sorry, i (can['’]?t|cannot))", re.I)


def _first_text(data: dict) -> str:
    for key in _BODY_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Structured alternatives: {"alternatives": ["...", {"body": "..."}]}
    for key in ("alternatives", "options", "drafts", "choices"):
        alts = data.get(key)
        if isinstance(alts, list):
            for alt in alts:
                if isinstance(alt, str) and alt.strip():
                    return alt.strip()
                if isinstance(alt, dict):
                    got = _first_text(alt)
                    if got:
                        return got
    return ""


def parse_compose_output(raw) -> dict:
    """-> {body, subject, should_stop, stop_reason, status}.

    status: ok | empty | refusal | stopped. body is "" unless status == ok.
    """
    out = {"body": "", "subject": "", "should_stop": False, "stop_reason": "", "status": "empty"}
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        return out
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    data = None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if isinstance(data, dict):
        out["subject"] = str(data.get("subject") or "").strip()
        out["should_stop"] = bool(data.get("should_stop", False))
        out["stop_reason"] = str(data.get("stop_reason") or "").strip()
        out["body"] = _first_text(data)
        if out["should_stop"]:
            out["status"] = "stopped"
        elif out["body"]:
            out["status"] = "ok"
        elif data.get("refusal") or data.get("error"):
            out["status"] = "refusal"
        return out
    if text.startswith(("{", "[")):
        return out  # truncated / malformed JSON is not prose to send
    if _REFUSAL.match(text):
        out["status"] = "refusal"
        return out
    out["body"] = text
    out["status"] = "ok"
    return out


_FAILURES = {
    "empty_generation": ("The AI returned no usable text for this lead.", True,
                         "Try again, or add a short direction and regenerate."),
    "refusal": ("The AI declined to write this message.", False,
                "Edit the direction or write the message yourself."),
    "AuthenticationError": ("The AI provider rejected the account key.", False,
                            "Ask an administrator to check the AI key."),
    "PermissionDeniedError": ("The AI provider denied access for this account.", False,
                              "Ask an administrator to check AI access."),
    "RateLimitError": ("The AI provider is out of credit or rate-limited.", True,
                       "Wait a minute and retry; if it persists, check AI credits."),
    "APITimeoutError": ("The AI provider timed out.", True, "Retry in a moment."),
    "APIConnectionError": ("Could not reach the AI provider.", True, "Retry in a moment."),
    "InternalServerError": ("The AI provider had an internal error.", True, "Retry in a moment."),
    "AIDisabled": ("AI actions are switched off for this account.", False,
                   "Ask an administrator to enable AI."),
    "SpendLimitReached": ("The AI spend limit has been reached.", False,
                          "Ask an administrator to review the AI limit."),
    "ModelNotApproved": ("The AI model is not approved for this feature.", False,
                         "Ask an administrator to review AI settings."),
    "JSONDecodeError": ("The AI reply could not be read.", True, "Retry; the AI may answer correctly."),
}
_DEFAULT = ("The AI could not draft this message.", True, "Retry, or write the message yourself.")


def describe_failure(kind) -> dict:
    reason, retryable, action = _FAILURES.get(kind or "", _DEFAULT)
    return {"error_kind": kind or "empty_generation", "error_message": reason,
            "retryable": retryable, "action": action}
