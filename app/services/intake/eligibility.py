"""OUTREACH STATUS, per channel. Separate from importing and from classifying.

SMS
---
A number with ten digits is valid-FORMAT and nothing more. The platform has
no carrier line-type lookup and no reassigned-number check today, so an
import can almost never prove a number may be texted. The honest default is
PENDING_VALIDATION. `READY` is reachable only when EVERY one of these holds:

    * a valid-format number
    * the line is known to be mobile (a dedicated mobile column, or a line-
      type column / verification value that says mobile)
    * explicit SMS consent in the source (allow_sms = True)
    * not DNC, not suppressed, not opted out

Anything that says NO wins over anything that says yes.

EMAIL
-----
READY needs a well-formed, non-placeholder address with no bounce,
unsubscribe, invalid or suppression signal. A source verdict of "risky",
"unknown", "catch-all" or a role address (info@, sales@) is REVIEW. No
address at all is NO_EMAIL.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.models.intake_models import EmailStatus, SmsStatus

LINE_MOBILE = "mobile"
LINE_LANDLINE = "landline"
LINE_VOIP = "voip"
LINE_UNKNOWN = "unknown"


def line_type(raw_line_type: Optional[str], sms_verification: Optional[str],
              from_mobile_column: bool) -> str:
    """Line type from the only evidence an import can carry."""
    for v in (raw_line_type, sms_verification):
        s = (v or "").lower()
        if not s:
            continue
        if re.search(r"\b(landline|land line|fixed|wireline)\b", s):
            return LINE_LANDLINE
        if re.search(r"\b(voip|non.?fixed|virtual)\b", s):
            return LINE_VOIP
        if re.search(r"\b(mobile|wireless|cell)\b", s) and "pending" not in s:
            return LINE_MOBILE
    return LINE_MOBILE if from_mobile_column else LINE_UNKNOWN


def sms_status(*, phone_state: str, line: str, dnc: bool, suppressed: bool,
               opted_out: bool, consent_sms: Optional[bool],
               sms_verification: Optional[str]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    if phone_state == "missing":
        return SmsStatus.NO_PHONE, ["no_phone"]
    if phone_state == "invalid":
        return SmsStatus.INVALID, ["invalid_phone"]
    if dnc:
        return SmsStatus.DNC, ["dnc"]
    if suppressed:
        return SmsStatus.SUPPRESSED, ["phone_suppressed"]
    if opted_out or consent_sms is False:
        return SmsStatus.OPTED_OUT, ["sms_opted_out"]
    v = (sms_verification or "").lower()
    if re.search(r"\b(dnc|do not call|blocked|opt.?out|stop)\b", v) and "pending" not in v:
        return SmsStatus.DNC, ["source_says_dnc"]
    if re.search(r"\b(invalid|disconnected|inactive|bad)\b", v):
        return SmsStatus.INVALID, ["source_says_invalid"]
    if line == LINE_LANDLINE:
        return SmsStatus.LANDLINE, ["landline"]
    if line == LINE_MOBILE and consent_sms is True:
        return SmsStatus.READY, []
    if line == LINE_VOIP:
        reasons.append("voip_line")
        return SmsStatus.REVIEW, reasons
    if line != LINE_MOBILE:
        reasons.append("line_type_unverified")
    if consent_sms is not True:
        reasons.append("no_sms_consent_on_record")
    return SmsStatus.PENDING_VALIDATION, reasons


_ROLE_PREFIXES = {"info", "sales", "admin", "office", "contact", "support", "hello",
                  "billing", "accounts", "accounting", "service", "team", "help",
                  "marketing", "hr", "jobs", "careers", "reception", "noreply", "no-reply"}


def email_status(*, email_norm: Optional[str], email_raw_present: bool,
                 format_problem: Optional[str], quality_problem: Optional[str],
                 verification: Optional[str], hard_bounce: bool, unsubscribed: bool,
                 marked_invalid: bool, suppressed: bool,
                 consent_email: Optional[bool]) -> Tuple[str, List[str]]:
    if not email_norm and not email_raw_present:
        return EmailStatus.NO_EMAIL, ["no_email"]
    if format_problem or not email_norm:
        return EmailStatus.INVALID, ["invalid_email_format"]
    if marked_invalid:
        return EmailStatus.INVALID, ["source_marked_invalid"]
    if hard_bounce:
        return EmailStatus.HARD_BOUNCE, ["hard_bounce"]
    if unsubscribed:
        return EmailStatus.UNSUBSCRIBED, ["unsubscribed"]
    if suppressed or consent_email is False:
        return EmailStatus.SUPPRESSED, ["email_suppressed"]
    v = (verification or "").lower().strip()
    if v:
        if re.search(r"hard.?bounce|bounced", v):
            return EmailStatus.HARD_BOUNCE, ["source_says_hard_bounce"]
        if re.search(r"unsub|opted.?out|opt.?out", v):
            return EmailStatus.UNSUBSCRIBED, ["source_says_unsubscribed"]
        if re.search(r"invalid|undeliverable|bad|rejected", v):
            return EmailStatus.INVALID, ["source_says_invalid"]
        if re.search(r"suppress|quarantin|spam|complain", v):
            return EmailStatus.SUPPRESSED, ["source_says_suppressed"]
        if re.search(r"risky|unknown|catch.?all|accept.?all|unverified", v):
            return EmailStatus.REVIEW, ["source_says_" + re.sub(r"[^a-z]+", "_", v).strip("_")]
        if v in ("missing", "none", "no email"):
            # The source says there is no usable address although a value is
            # present: trust the more cautious reading.
            return EmailStatus.REVIEW, ["source_says_missing"]
    local = email_norm.split("@", 1)[0]
    if quality_problem == "system_address" and local in _ROLE_PREFIXES:
        # "info@" is a system address to a consumer business and frequently
        # THE address of a commercial one. A person decides, not the importer.
        return EmailStatus.REVIEW, ["role_address"]
    if quality_problem:
        return EmailStatus.INVALID, ["email_quality:" + quality_problem]
    if local in _ROLE_PREFIXES:
        return EmailStatus.REVIEW, ["role_address"]
    return EmailStatus.READY, []


USABLE_EMAIL_STATUSES = (EmailStatus.READY, EmailStatus.REVIEW, EmailStatus.PENDING)
USABLE_SMS_STATUSES = (SmsStatus.READY, SmsStatus.PENDING_VALIDATION, SmsStatus.REVIEW)
