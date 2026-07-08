"""
AI-assisted template writing for the Templates page.

Lets an org_admin generate a starting draft for a track+channel's message
copy, either from scratch ("Generate") or by giving a free-text instruction
against the current draft ("make this warmer", "shorter", "add urgency").

This intentionally does NOT follow the same silent-fallback pattern as
draft_reply_service.py. A reply draft has a safe generic fallback because an
advisor always needs *something* usable to send right now. A template
generation request has no equivalent safe substitute - silently returning
the existing default text would look like a successful generation that
produced no actual change, which is worse than a clear error the admin can
see and retry. So this raises a TemplateAIError on failure instead of
swallowing it, and the router translates that into a 502 with a clear
message.
"""

import json
import os
from typing import Any

from openai import OpenAI

from app.models.models import MessageTrack

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


class TemplateAIError(Exception):
    """Raised when AI template generation/rewrite fails for any reason."""


# Plain-English context per track so the model writes appropriately for the
# situation instead of guessing from the enum name alone.
TRACK_CONTEXT = {
    MessageTrack.PRE_NEED_LOCK_PRICE: (
        "Pre-Need: the lead is planning ahead for future cemetery/funeral "
        "arrangements, not facing an active loss. Tone should be helpful "
        "and focused on locking in today's pricing before it changes, not urgent or somber."
    ),
    MessageTrack.AT_NEED_SUPPORT: (
        "At-Need: the lead's family is currently arranging services for a "
        "recent loss. Tone should be warm, supportive, and unhurried - never salesy."
    ),
    MessageTrack.IMMINENT_SUPPORT: (
        "Imminent: a loss is expected very soon or has just occurred. Tone "
        "should be gentle and supportive, prioritizing a direct phone call "
        "over a booking link, since this family needs a human now."
    ),
    MessageTrack.UPSELL_EXISTING_CUSTOMER: (
        "Contract Sold / Upsell: the lead already has a contract with us. "
        "Message should introduce additional options (memorials, markers, "
        "additional plots/services) without sounding like a hard sell to "
        "someone who's already a customer."
    ),
    MessageTrack.EMAIL_ONLY_NURTURE: (
        "Email-only nurture: the lead has no phone on file, only email. "
        "Tone should be informative and low-pressure, since this is a "
        "longer-cycle relationship-building track, not a quick-response one."
    ),
    MessageTrack.NEEDS_REVIEW: (
        "Needs review (fallback): used only until an advisor manually "
        "assigns the correct tier. Keep this generic and warm - it should "
        "work reasonably for almost any situation."
    ),
}

SMS_PLACEHOLDERS = ["{first_name}", "{advisor_name}", "{tone_phrase}", "{booking_link}", "{advisor_cell}"]
EMAIL_PLACEHOLDERS = ["{first_name}", "{advisor_name}", "{booking_link}", "{advisor_cell}"]

GENERATE_PROMPT = """You are writing outreach copy for a cemetery/funeral-home sales advisor.

Channel: {channel}
Situation: {track_context}

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape for sms: {{"body_template": "..."}}
- JSON shape for email: {{"subject_template": "...", "body_template": "..."}}
- Use ONLY these placeholders, exactly as written, where relevant: {placeholders}
- Do not invent new placeholders.
- Sound human and appropriate to the situation above - never pushy.
- For sms, keep the body concise (it's a text message).
- For email, body_template may include simple HTML like <p> tags, matching how the existing templates are written.
{instruction_block}
"""

REWRITE_PROMPT = """You are revising outreach copy for a cemetery/funeral-home sales advisor.

Channel: {channel}
Situation: {track_context}

Current template:
{current}

Instruction from the admin: {instruction}

Rules:
- Respond with ONLY JSON, no markdown and no preamble.
- JSON shape for sms: {{"body_template": "..."}}
- JSON shape for email: {{"subject_template": "...", "body_template": "..."}}
- Use ONLY these placeholders, exactly as written, where relevant: {placeholders}
- Do not invent new placeholders, and don't drop placeholders the current template relies on unless the instruction says to.
- Apply the instruction faithfully while keeping the tone appropriate to the situation above.
"""


def _safe_parse_json(raw: str) -> dict[str, Any]:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise TemplateAIError(f"AI response wasn't valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise TemplateAIError("AI response was not a JSON object")
    return parsed


def _call_openai(prompt: str) -> dict[str, Any]:
    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=400,
        )
    except Exception as exc:
        raise TemplateAIError(f"AI request failed: {exc}") from exc

    raw = response.choices[0].message.content
    return _safe_parse_json(raw)


def generate_template(track: MessageTrack, channel: str, instruction: str | None = None) -> dict[str, Any]:
    """
    Generates a new template draft from scratch for this track+channel.
    If `instruction` is given, it's folded in as additional guidance on top
    of the default situational context (e.g. "make it shorter").
    """
    placeholders = SMS_PLACEHOLDERS if channel == "sms" else EMAIL_PLACEHOLDERS
    instruction_block = f"\nAdditional instruction from the admin: {instruction}\n" if instruction else ""

    prompt = GENERATE_PROMPT.format(
        channel=channel,
        track_context=TRACK_CONTEXT.get(track, "General outreach."),
        placeholders=", ".join(placeholders),
        instruction_block=instruction_block,
    )
    parsed = _call_openai(prompt)
    return _normalize_result(parsed, channel)


def rewrite_template(track: MessageTrack, channel: str, current_body: str, current_subject: str | None, instruction: str) -> dict[str, Any]:
    """
    Rewrites the admin's current draft per a free-text instruction, e.g.
    "make this warmer" or "shorter" or "add more urgency".
    """
    if not instruction or not instruction.strip():
        raise TemplateAIError("An instruction is required to rewrite a template.")

    placeholders = SMS_PLACEHOLDERS if channel == "sms" else EMAIL_PLACEHOLDERS
    current_display = current_body
    if channel == "email" and current_subject:
        current_display = f"Subject: {current_subject}\n\n{current_body}"

    prompt = REWRITE_PROMPT.format(
        channel=channel,
        track_context=TRACK_CONTEXT.get(track, "General outreach."),
        current=current_display,
        instruction=instruction.strip(),
        placeholders=", ".join(placeholders),
    )
    parsed = _call_openai(prompt)
    return _normalize_result(parsed, channel)


def _normalize_result(parsed: dict[str, Any], channel: str) -> dict[str, Any]:
    body = (parsed.get("body_template") or "").strip()
    if not body:
        raise TemplateAIError("AI response did not include a body_template.")

    result = {"body_template": body}
    if channel == "email":
        subject = (parsed.get("subject_template") or "").strip()
        if not subject:
            raise TemplateAIError("AI response did not include a subject_template for an email template.")
        result["subject_template"] = subject
    return result
