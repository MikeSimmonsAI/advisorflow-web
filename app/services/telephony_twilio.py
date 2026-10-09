"""The ONLY place the telephony stream talks to Twilio's REST API.

Three operations, each a thin wrapper so tests replace them with fakes
(the conftest also refuses to construct a real twilio Client anywhere in app.*):

    create_call(creds, **params)       -> call SID
    update_call(creds, call_sid, twiml) (redirects a live call - voicemail drop)
    fetch_recording(creds, url)        -> (bytes, content_type) for the audio proxy

TwiML builders live here too. They are pure functions of their inputs and
escape every value through twilio's VoiceResponse, so a lead's name or an
organization greeting can never inject markup.

Nothing in this module decides WHETHER a call may be placed - callers run the
compliance gates first (voice_bulk_gate) and resolve the number through
number_resolution. There is no purchase or provisioning call here.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional, Tuple

from twilio.twiml.voice_response import Dial, VoiceResponse

log = logging.getLogger(__name__)

AMD_MACHINE_END = ("machine_end_beep", "machine_end_silence", "machine_end_other")
AMD_MACHINE_ANY = AMD_MACHINE_END + ("machine_start", "fax")


def _client(creds: Tuple[str, str]):
    from twilio.rest import Client  # lazy: never at import time
    return Client(creds[0], creds[1])


def create_call(creds: Tuple[str, str], **params) -> str:
    call = _client(creds).calls.create(**params)
    return call.sid


def update_call(creds: Tuple[str, str], call_sid: str, twiml: str) -> None:
    _client(creds).calls(call_sid).update(twiml=twiml)


_RECORDING_SID = re.compile(r"^RE[0-9a-f]{32}$")
_ACCOUNT_SID = re.compile(r"^AC[0-9A-Za-z]{16,40}$")


def valid_recording_sid(sid) -> bool:
    return bool(sid and _RECORDING_SID.match(str(sid)))


def recording_media_url(account_sid: str, recording_sid: str) -> str:
    """The recording's media URL, built SERVER-SIDE from two validated SIDs.
    A URL that arrived in a webhook is never fetched."""
    if not valid_recording_sid(recording_sid):
        raise ValueError("Not a Twilio RecordingSid.")
    if not account_sid or not _ACCOUNT_SID.match(account_sid) or any(c in account_sid for c in "?#/"):
        raise ValueError("Not a Twilio Account SID.")
    return "https://api.twilio.com/2010-04-01/Accounts/%s/Recordings/%s.mp3" % (account_sid, recording_sid)


def fetch_recording(creds: Tuple[str, str], recording_sid: str) -> Tuple[bytes, str]:
    """Download a recording with the owning account's credentials."""
    import httpx
    url = recording_media_url(creds[0], recording_sid)
    r = httpx.get(url, auth=(creds[0], creds[1]), timeout=20.0, follow_redirects=False)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "audio/mpeg")


def twiml_say_code(code: str, org_name: str) -> str:
    """The spoken verification code (digits separated so they are read singly)."""
    r = VoiceResponse()
    spaced = ", ".join(code)
    r.say("This is %s. Your callback phone verification code is %s. Again, %s." % (org_name, spaced, spaced))
    r.hangup()
    return _xml(r)


# ── outbound call parameters ────────────────────────────────────────────────

def outbound_call_params(*, to: str, from_: str, url: str, status_callback: str,
                         recording_callback: Optional[str] = None,
                         amd_callback: Optional[str] = None,
                         record: bool = True, timeout: int = 30) -> dict:
    """Twilio Calls.create kwargs for an automated outbound call.

    With `amd_callback` (only passed when the organization has an APPROVED
    voicemail drop): asynchronous answering-machine detection that waits for
    the end of the greeting, so the drop starts after the beep. Without it:
    synchronous DetectMessageEnd, so the call's TwiML request carries
    AnsweredBy and a machine is hung up on instead of being talked to.
    """
    params = {
        "to": to, "from_": from_, "url": url,
        "status_callback": status_callback, "status_callback_method": "POST",
        "timeout": timeout, "machine_detection": "DetectMessageEnd",
    }
    if record:
        params["record"] = True
        if recording_callback:
            params["recording_status_callback"] = recording_callback
    if amd_callback:
        params["async_amd"] = "true"
        params["async_amd_status_callback"] = amd_callback
        params["async_amd_status_callback_method"] = "POST"
    return params


def human_bridge_params(*, user_phone: str, from_: str, url: str,
                        status_callback: str, timeout: int = 25) -> dict:
    """Leg 1 of the human dialer: ring the USER's own phone from the org number."""
    return {
        "to": user_phone, "from_": from_, "url": url,
        "status_callback": status_callback, "status_callback_method": "POST",
        "status_callback_event": ["initiated", "ringing", "answered", "completed"],
        "timeout": timeout,
    }


# ── TwiML ──────────────────────────────────────────────────────────────────

def _xml(resp: VoiceResponse) -> str:
    return str(resp)


def twiml_hangup(message: Optional[str] = None) -> str:
    r = VoiceResponse()
    if message:
        r.say(message)
    r.hangup()
    return _xml(r)


def twiml_voicemail_drop(*, text: Optional[str] = None, recording_url: Optional[str] = None) -> str:
    """Play the organization's APPROVED message, then hang up."""
    r = VoiceResponse()
    if recording_url:
        r.play(recording_url)
    elif text:
        r.say(text)
    r.hangup()
    return _xml(r)


def twiml_human_bridge(*, lead_e164: str, caller_id: str, action_url: str,
                       lead_label: Optional[str] = None, timeout: int = 30) -> str:
    """Leg 2: the user answered - dial the lead, showing the org business number."""
    r = VoiceResponse()
    r.say("Connecting your call%s." % ((" to " + lead_label) if lead_label else ""))
    d = Dial(caller_id=caller_id, action=action_url, method="POST", timeout=timeout,
             answer_on_bridge=True)
    d.number(lead_e164)
    r.append(d)
    return _xml(r)


def twiml_inbound(*, ring_numbers: Iterable[str], timeout: int, dial_action_url: str,
                  caller_id: Optional[str] = None) -> str:
    """Ring the configured people; Twilio posts DialCallStatus to dial_action_url."""
    r = VoiceResponse()
    d = Dial(timeout=timeout, action=dial_action_url, method="POST",
             **({"caller_id": caller_id} if caller_id else {}))
    for n in ring_numbers:
        d.number(n)
    r.append(d)
    return _xml(r)


def twiml_record_voicemail(*, greeting_text: Optional[str], greeting_recording_url: Optional[str],
                           recording_callback_url: str, finish_url: str,
                           max_length: int = 120,
                           transcribe_callback_url: Optional[str] = None) -> str:
    """Greeting, then <Record>. The recording arrives on recording_callback_url."""
    r = VoiceResponse()
    if greeting_recording_url:
        r.play(greeting_recording_url)
    else:
        r.say(greeting_text or "Please leave a message after the tone.")
    extra = ({"transcribe": True, "transcribe_callback": transcribe_callback_url}
             if transcribe_callback_url else {})
    r.record(max_length=max_length, play_beep=True, timeout=5, trim="trim-silence",
             action=finish_url, method="POST",
             recording_status_callback=recording_callback_url,
             recording_status_callback_method="POST",
             recording_status_callback_event="completed", **extra)
    r.say("We did not receive a recording. Goodbye.")
    r.hangup()
    return _xml(r)


def twiml_generic_unknown_number() -> str:
    """The called number belongs to no organization: say nothing about anyone."""
    return twiml_hangup("Thank you for calling. This number is not accepting calls right now. Goodbye.")
