"""RESTLAND — FAMILY FILE REVIEW: the approved 14-day sequence (Issue #507).

Owner decisions (Mike Simmons, Nov 7 2026):

  Day 1   text + email   introduce Mike and Restland; invite to a 45-minute
                         in-person Family File Review (review the file with
                         them, explain any permission-to-access paperwork that
                         applies, the pre-planning guide)
  Day 3   text           which day works?
  Day 4   email          what happens at the review and how to prepare
  Day 6   text           at Restland or at home, whichever is more convenient
  Day 8   email          what the review covers, in plain language (no private
                         file details; the permission-to-access paperwork is
                         only ever "explained if it applies" - its scope is
                         not stated anywhere because it has not been verified)
  Day 10  text           weekday or weekend?
  Day 12  email          brief reminder with the scheduling link
  Day 14  text           close the loop: schedule now or reconnect later

"Day 1" is the family's start date; offsets are 0, 2, 3, 5, 7, 9, 11, 13.
Every touch goes out 9:00 AM - 6:00 PM Central, seven days a week
(send_window). On Day 1 the email follows the text by at least four hours
(email_touches.CROSS_CHANNEL_GAP), so the two never land together.

Five texts and four emails. A family with no usable email gets the five
texts only - a missing email never becomes an extra text. A family that can
only be emailed gets the four emails.

Placeholders (rendered per family; nothing customer-visible is left blank):
  {first_name} {primary_contact_name} = Mike Simmons
  {location_name} = Restland Cemetery and Funeral Home
  {booking_line}  = "Pick a time that works for you: <the family's own link>"
                    (empty - and the sentence still reads - when links are off)

RETURN copy is for a family coming back after the 30-day rest: it says we
wrote before, never "first contact" wording. It lives in its own campaign
(RETURN_KEY), drafted and OFF.
"""
from typing import Dict, List, Tuple

CAMPAIGN_KEY = "restland_family_file_review"
CAMPAIGN_NAME = "Restland — Family File Review — Batch 1"
RETURN_KEY = "restland_family_file_review_return"
RETURN_NAME = "Restland — Family File Review — Return"

CADENCE_NAME = "Restland Family File Review (5 texts, 14 days)"
CADENCE_DESCRIPTION = ("Family File Review - 5 texts on days 1, 3, 6, 10 and 14; emails on days 1, 4, 8 and 12. "
                       "9 AM - 6 PM Central, every day. Draft until the campaign is switched on.")

# (touch, day_offset, send_hour Central). Day 1 = offset 0.
SMS_STEPS: List[Tuple[int, int, int]] = [(1, 0, 10), (2, 2, 11), (3, 5, 10), (4, 9, 11), (5, 13, 10)]
# (touch, day_offset). Day 1 = offset 0, after the first text.
EMAIL_STEPS: List[Tuple[int, int]] = [(1, 0), (2, 3), (3, 7), (4, 11)]

COOLDOWN_DAYS = 30

_SIG = "{primary_contact_name}\n{location_name}\n13005 Greenville Ave, Dallas, TX 75243"

SMS = {
    1: ("Hi {first_name}, this is {primary_contact_name} with {location_name}. I'd like to invite you to a "
        "45-minute Family File Review: we'll review your file with you, explain any permission-to-access "
        "paperwork that applies, and go over our pre-planning guide. Reply STOP to opt out."),
    2: ("Hi {first_name}, it's {primary_contact_name} at Restland. Which day would work for your Family File "
        "Review? {booking_line} Or just reply with a day. Reply STOP to opt out."),
    3: ("{first_name}, we can meet at Restland, or I'm happy to come to your home if that's more convenient. "
        "Either way it's about 45 minutes. Reply with what suits you. - {primary_contact_name}. "
        "Reply STOP to opt out."),
    4: ("Hi {first_name}, {primary_contact_name} at Restland. Would a weekday or a weekend be better for your "
        "Family File Review? I have times all seven days. {booking_line} Reply STOP to opt out."),
    5: ("Hi {first_name}, this is my last note for now about your Family File Review. If you'd like to set a "
        "time, just reply here, or I can reconnect with you later. Thank you. - {primary_contact_name}, "
        "Restland. Reply STOP to opt out."),
}

EMAIL = {
    1: ("Your Family File Review with {primary_contact_name} at Restland",
        "Hi {first_name},\n\n"
        "I'm {primary_contact_name}, a Family Service Advisor with {location_name}. I'd like to invite you to "
        "a Family File Review: a 45-minute, in-person visit where we:\n\n"
        "- review the information in your family's file with you,\n"
        "- explain any permission-to-access paperwork that applies, and\n"
        "- go over our pre-planning guide together.\n\n"
        "We can meet at Restland, or I'm happy to come to your home if that's more convenient.\n\n"
        "{booking_line}"
        "Or simply reply to this email with a day that works for you.\n\n" + _SIG),
    2: ("What happens at a Family File Review",
        "Hi {first_name},\n\n"
        "A quick note on what to expect when we meet. We'll sit down together for about 45 minutes, look over "
        "the information in your family's file at {location_name}, and make sure it's accurate and up to date.\n\n"
        "To prepare, it helps to have any Restland paperwork you've kept at home and a list of any questions. "
        "Nothing else is needed, and you're welcome to bring a family member.\n\n"
        "{booking_line}"
        "If you'd rather, reply with a day and time and I'll set it up.\n\n" + _SIG),
    3: ("What we'll cover at your Family File Review",
        "Hi {first_name},\n\n"
        "Two things we'll cover when we meet:\n\n"
        "Your family's file at {location_name}. We'll review it with you and explain any "
        "permission-to-access paperwork that applies.\n\n"
        "Our pre-planning guide. We'll go over it together, at your pace, and answer any questions.\n\n"
        "{booking_line}"
        "Reply here with any questions, or with a day that suits you.\n\n" + _SIG),
    4: ("Still glad to find a time, {first_name}",
        "Hi {first_name},\n\n"
        "A brief reminder that I'd be glad to meet for your Family File Review, at Restland or at your home, "
        "any day of the week.\n\n"
        "{booking_line}"
        "Or reply with a day that works and I'll take care of the rest.\n\n" + _SIG),
}

# A family returning after the 30-day rest: acknowledges we wrote before.
RETURN_SMS = {
    1: ("Hi {first_name}, it's {primary_contact_name} with {location_name} again. I wrote a while back about a "
        "45-minute Family File Review. If now is a better time, I'd be glad to set one up. "
        "Reply STOP to opt out."),
    2: ("Hi {first_name}, {primary_contact_name} at Restland. Is there a day that suits you for your Family File "
        "Review? {booking_line} Reply STOP to opt out."),
    3: ("{first_name}, we can meet at Restland, or I can come to your home if that's easier. Just reply with "
        "what works. - {primary_contact_name}. Reply STOP to opt out."),
    4: ("Hi {first_name}, {primary_contact_name} at Restland. Would a weekday or a weekend be better? I have times "
        "all seven days. {booking_line} Reply STOP to opt out."),
    5: ("Hi {first_name}, I'll leave it here for now. Whenever you'd like to set up your Family File Review, "
        "just reply. Thank you. - {primary_contact_name}, Restland. Reply STOP to opt out."),
}
RETURN_EMAIL = {
    1: ("Following up on your Family File Review",
        "Hi {first_name},\n\n"
        "This is {primary_contact_name} with {location_name}. I wrote a little while ago about a Family File "
        "Review, and I wanted to offer it again in case the timing is better now.\n\n"
        "It's a 45-minute visit to review your family's file with you, explain any permission-to-access "
        "paperwork that applies, and go over our pre-planning guide. We can meet at Restland or at your home.\n\n"
        "{booking_line}"
        "Or reply with a day that works for you.\n\n" + _SIG),
    2: ("How the review works",
        "Hi {first_name},\n\n"
        "We'll spend about 45 minutes together looking over your family's file at {location_name} and making "
        "sure it's up to date. Any Restland paperwork you've kept at home is helpful, along with any questions.\n\n"
        "{booking_line}"
        "Reply with a day and I'll set it up.\n\n" + _SIG),
    3: ("Your file review, briefly",
        "Hi {first_name},\n\n"
        "At your Family File Review we'll review your family's file at {location_name} with you, explain any "
        "permission-to-access paperwork that applies, and go over our pre-planning guide together.\n\n"
        "{booking_line}"
        "Reply here with any questions.\n\n" + _SIG),
    4: ("Whenever it suits you",
        "Hi {first_name},\n\n"
        "I'll leave this with you. Whenever you'd like to meet, at Restland or at home, any day of the week, "
        "just reply.\n\n"
        "{booking_line}" + _SIG),
}

PLAYBOOK = {
    "label": "Family File Review",
    "why": "we're inviting families to a Family File Review at Restland",
    "topic": "your family's file",
    "useful": "The review confirms your family's file is accurate and goes over the pre-planning guide.",
    "question": "A common question is whether the visit means deciding anything. It doesn't.",
    "approved": ["Mike Simmons invites the family to a 45-minute Family File Review at Restland or at home"],
}


def schedule_summary() -> Dict:
    days = {}
    for n, d, h in SMS_STEPS:
        days.setdefault(d + 1, []).append("text %d" % n)
    for n, d in EMAIL_STEPS:
        days.setdefault(d + 1, []).append("email %d" % n)
    return {"days": [{"day": k, "touches": v} for k, v in sorted(days.items())],
            "texts": len(SMS_STEPS), "emails": len(EMAIL_STEPS), "span_days": 14,
            "window": "9:00 AM - 6:00 PM Central, every day", "cooldown_days": COOLDOWN_DAYS}


# ── volume forecast (Issue #507, Nov 7 correction) ──────────────────────────

_GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
           "¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM_EXT = set("^{}\\[~]|€")


def segments(text: str) -> Dict:
    """SMS segments for this exact text: GSM-7 (160 / 153 per part, extension
    characters count twice) or UCS-2 (70 / 67) when any character needs it."""
    if all(c in _GSM or c in _GSM_EXT for c in text):
        units = sum(2 if c in _GSM_EXT else 1 for c in text)
        return {"encoding": "GSM-7", "units": units, "segments": 1 if units <= 160 else -(-units // 153)}
    units = sum(2 if ord(c) > 0xFFFF else 1 for c in text)
    return {"encoding": "UCS-2", "units": units, "segments": 1 if units <= 70 else -(-units // 67)}


def forecast(sample_link: str, *, new_per_day=(5, 10, 15, 25), email_share: float = 1.0,
             booking_rates=(0.05, 0.10, 0.20), first_name: str = "Margaret") -> Dict:
    """Upper-bound steady-state load: every family stays all 14 days on every
    channel (no reply/booking exits, no missing channels). Real load is lower."""
    f = {"first_name": first_name, "primary_contact_name": "Mike Simmons",
         "location_name": "Restland Cemetery and Funeral Home",
         "booking_line": ("Pick a time that works for you: %s" % sample_link) if sample_link else ""}
    texts = []
    for copy, label in ((SMS, "first round"), (RETURN_SMS, "return round")):
        for n, t in copy.items():
            body = " ".join(t.format(**f).split())
            texts.append(dict(segments(body), touch=n, round=label, chars=len(body)))
    first = [t for t in texts if t["round"] == "first round"]
    seg_per_family = sum(t["segments"] for t in first)
    rows = []
    for nday in new_per_day:
        rows.append({"new_families_per_day": nday, "texts_per_day": 5 * nday,
                     "text_segments_per_day": seg_per_family * nday,
                     "emails_per_day_max": 4 * nday, "emails_per_day_at_share": round(4 * nday * email_share, 1),
                     "families_in_sequence": 14 * nday,
                     "bookings_per_week": {("%d%%" % int(r * 100)): round(7 * nday * r, 1) for r in booking_rates},
                     # per booking: 1 confirmation + 2 reminders + 1 survey (if completed), x up to 2 channels
                     "appointment_messages_per_week_max": {("%d%%" % int(r * 100)): round(7 * nday * r * 4 * 2, 1)
                                                           for r in booking_rates}})
    return {"assumptions": "Upper bound: no early exits, every channel present. Appointment messages, "
                           "conversations and staff alerts are separate from campaign volume.",
            "text_segments": texts, "segments_per_family_first_round": seg_per_family,
            "appointment_capacity": {"starts_per_week_for_one_person": 35,
                                     "note": "5 starts x 7 days before calendar conflicts; staff reassignment "
                                             "adds a second person's capacity"},
            "by_volume": rows}
