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
from typing import Dict, List, Optional, Tuple

CAMPAIGN_KEY = "restland_family_file_review"
CAMPAIGN_NAME = "Restland — Family File Review — Batch 1"
RETURN_KEY = "restland_family_file_review_return"
RETURN_NAME = "Restland — Family File Review — Return"

CADENCE_NAME = "Restland Family File Review (5 texts, 14 days)"
CADENCE_DESCRIPTION = ("Family File Review - 5 texts on days 1, 3, 6, 10 and 14; emails on days 1, 4, 8 and 12. "
                       "New families from 8:30 AM Central, one every 8 minutes; due follow-ups 2 minutes after "
                       "each, until 6 PM, every day.")

# (touch, day_offset, send_hour Central). Day 1 = offset 0.
SMS_STEPS: List[Tuple[int, int, int]] = [(1, 0, 10), (2, 2, 11), (3, 5, 10), (4, 9, 11), (5, 13, 10)]
# (touch, day_offset). Day 1 = offset 0, after the first text.
EMAIL_STEPS: List[Tuple[int, int]] = [(1, 0), (2, 3), (3, 7), (4, 11)]

COOLDOWN_DAYS = 30

_SIG = "{primary_contact_name}\n{location_name}\n13005 Greenville Ave, Dallas, TX 75243"

# Nov 15 (owner): it's been a while; Mike wants to review the family's
# Restland file with them this week, confirm it is current, go over any
# permission-to-access paperwork that applies, and get them a Personal Planning
# Guide if they haven't received one. In-person at Restland first; at their
# home if they can't come in. Never virtual. {time_offer} names two times the
# calendar really has open, or asks what day works when it can't verify any.
SMS = {
    # Owner, Nov 17: THE File Review opening - his words, aimed at getting a
    # reply (good, bad or ugly). {full_name} stands in for "Mr./Mrs. ___" (the
    # file holds no title); {tomorrow_offer} asks "What time tomorrow works
    # best for you, X or Y?" with two times really open.
    1: ("Hi {full_name}, this is Mike with Restland Cemetery and Funeral Home. The reason for my text is that we "
        "have a few things that must be taken care of with every family here at Restland. We need to review your "
        "file, get you your Personal Planning Guide and have you fill out your Permission to Access form. "
        "{tomorrow_offer} Reply STOP to opt out."),
    # Owner, Nov 17: every follow-up keeps the opening's voice and goal - the
    # three things every Restland family takes care of, and a time tomorrow.
    2: ("Hi {full_name}, it's Mike with Restland again. We still need to get your file reviewed, get you your "
        "Personal Planning Guide and have you fill out your Permission to Access form. {tomorrow_offer} "
        "Reply STOP to opt out."),
    3: ("{full_name}, this is Mike at Restland. It takes about 45 minutes at our office on Greenville Ave to "
        "review your file, get you your Personal Planning Guide and fill out your Permission to Access form. "
        "If coming in is hard, I'll come to you. {tomorrow_offer} Reply STOP to opt out."),
    4: ("Hi {full_name}, Mike at Restland. These are things we take care of with every family here, and I "
        "still need to get yours done: your file review, your Personal Planning Guide and your Permission to "
        "Access form. {tomorrow_offer} Reply STOP to opt out."),
    5: ("Hi {full_name}, it's Mike at Restland. I still need to take care of your file with you, get you your "
        "Personal Planning Guide and your Permission to Access form. {tomorrow_offer} If another day is "
        "better, just reply with it. Reply STOP to opt out."),
}

EMAIL = {
    # Owner, Nov 17: the same opening as the first text, laid out for email.
    1: ("A few things we need to take care of with your Restland file",
        "Hi {full_name},\n\n"
        "This is Mike with Restland Cemetery and Funeral Home.\n\n"
        "The reason for my email is that we have a few things that must be taken care of with every family "
        "here at Restland. We need to:\n\n"
        "- review your file,\n"
        "- get you your Personal Planning Guide, and\n"
        "- have you fill out your Permission to Access form.\n\n"
        "It takes about 45 minutes at our office, 13005 Greenville Ave, Dallas.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Or just reply with a time that works for you.\n\n" + _SIG),
    2: ("We still need to take care of your Restland file",
        "Hi {full_name},\n\n"
        "It's Mike with Restland Cemetery and Funeral Home again. We still have a few things to take care of "
        "with your file, the same things we take care of with every family here at Restland:\n\n"
        "- review your file,\n"
        "- get you your Personal Planning Guide, and\n"
        "- have you fill out your Permission to Access form.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Or just reply with a time that works for you.\n\n" + _SIG),
    3: ("About 45 minutes to take care of your Restland file",
        "Hi {full_name},\n\n"
        "This is Mike at Restland. Taking care of your file only takes about 45 minutes at our office, 13005 "
        "Greenville Ave, Dallas. We'll review your file, I'll get you your Personal Planning Guide, and we'll "
        "get your Permission to Access form filled out.\n\n"
        "If it's hard to come in, tell me and I'll come to you.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Or just reply with a time that works for you.\n\n" + _SIG),
    4: ("Your Restland file, {first_name}",
        "Hi {full_name},\n\n"
        "Mike at Restland again. I still need to get you in to review your file, give you your Personal Planning "
        "Guide and get your Permission to Access form filled out. It's something we take care of with every "
        "family here.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "If another day is better, just reply with it and I'll make it work.\n\n" + _SIG),
}

# A family returning after the 30-day rest: acknowledges we wrote before.
RETURN_SMS = {
    # Owner, Nov 17: the same voice for a family coming back after the rest.
    1: ("Hi {full_name}, this is Mike with Restland Cemetery and Funeral Home. I wrote to you a while back. We "
        "still have a few things that must be taken care of with every family here at Restland: review your "
        "file, get you your Personal Planning Guide and have you fill out your Permission to Access form. "
        "{tomorrow_offer} Reply STOP to opt out."),
    2: ("Hi {full_name}, it's Mike at Restland. Your file review, your Personal Planning Guide and your "
        "Permission to Access form still need to be taken care of. {tomorrow_offer} Reply STOP to opt out."),
    3: ("{full_name}, Mike at Restland. It takes about 45 minutes at our office on Greenville Ave, or I can come "
        "to you if that's easier. {tomorrow_offer} Reply STOP to opt out."),
    4: ("Hi {full_name}, Mike at Restland. I have times all seven days to review your file, get you your "
        "Personal Planning Guide and your Permission to Access form. {tomorrow_offer} Reply STOP to opt out."),
    5: ("Hi {full_name}, it's Mike at Restland. I still need to take care of your file with you. "
        "{tomorrow_offer} If another day works better, just reply with it. Reply STOP to opt out."),
}
RETURN_EMAIL = {
    1: ("A few things we still need to take care of with your Restland file",
        "Hi {full_name},\n\n"
        "This is Mike with Restland Cemetery and Funeral Home. I wrote to you a while back.\n\n"
        "We still have a few things that must be taken care of with every family here at Restland. We need "
        "to:\n\n"
        "- review your file,\n"
        "- get you your Personal Planning Guide, and\n"
        "- have you fill out your Permission to Access form.\n\n"
        "It takes about 45 minutes at our office, 13005 Greenville Ave, Dallas, or I can come to you.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Or just reply with a time that works for you.\n\n" + _SIG),
    2: ("Your Restland file still needs to be taken care of",
        "Hi {full_name},\n\n"
        "It's Mike at Restland. Your file review, your Personal Planning Guide and your Permission to Access "
        "form still need to be taken care of.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Or just reply with a time that works for you.\n\n" + _SIG),
    3: ("About 45 minutes, at Restland or at your home",
        "Hi {full_name},\n\n"
        "Mike at Restland. Taking care of your file takes about 45 minutes at our office on Greenville Ave, or "
        "I can come to your home if that's easier. We'll review your file, I'll get you your Personal Planning "
        "Guide, and we'll get your Permission to Access form filled out.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "Reply with what works and I'll set it up.\n\n" + _SIG),
    4: ("Your Restland file, {first_name}",
        "Hi {full_name},\n\n"
        "It's Mike at Restland. I still need to take care of your file with you, any day of the week.\n\n"
        "{tomorrow_offer}\n\n"
        "{booking_line}"
        "If another day works better, just reply with it.\n\n" + _SIG),
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
            "window": "8:30 AM - 6:00 PM Central, every day",
            "slots": "a new family's first text at 8:30 AM and every 8 minutes; a due follow-up 2 minutes after each",
            "cooldown_days": COOLDOWN_DAYS}


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


def _render(t: str, f: Dict) -> str:
    try:
        return " ".join(t.format(**f).split())
    except (KeyError, IndexError, ValueError):
        # Saved copy may use the platform's {{token}} style - fill those too.
        out = t
        for k, v in f.items():
            out = out.replace("{{%s}}" % k, v).replace("{%s}" % k, v)
        return " ".join(out.split())


def family_segments(texts: Dict[int, str], f: Dict) -> int:
    return sum(segments(_render(t, f))["segments"] for t in texts.values() if t)


def forecast(sample_link: str, *, new_per_day=(5, 10, 15, 25), email_share: float = 1.0,
             booking_rates=(0.05, 0.10, 0.20), first_name: str = "Margaret",
             texts: Optional[Dict[int, str]] = None, return_texts: Optional[Dict[int, str]] = None,
             first_names: Optional[List[str]] = None) -> Dict:
    """Upper-bound steady-state load: every family stays all 14 days on every
    channel (no reply/booking exits, no missing channels). Real load is lower.

    Each family gets at most 5 texts and 4 emails IN TOTAL over its 14 days.
    With N new families starting every day, 14 overlapping groups are in
    sequence, so a steady day carries about 5N texts and 4N emails.
    `texts` = the SAVED campaign copy (falls back to the code drafts);
    `first_names` = the real ready audience, for the segment range."""
    source = "saved campaign copy" if texts else "code drafts"
    texts = texts or dict(SMS)
    return_texts = return_texts or dict(RETURN_SMS)
    f = {"first_name": first_name, "primary_contact_name": "Mike Simmons",
         "location_name": "Restland Cemetery and Funeral Home",
         "booking_line": ("Pick a time that works for you: %s" % sample_link) if sample_link else "",
         "booking_link": sample_link or "",
         # A realistic full name and the longest offer wording, so segment
         # counts are measured on what really goes out.
         "full_name": "%s Whitfield-Henderson" % first_name,
         "tomorrow_offer": "What time Wednesday works best for you, 10:00 AM or 2:30 PM?",
         "time_offer": ("I have Wednesday, Nov 18 at 10:00 AM or Thursday, Nov 19 at 2:30 PM open at our office. "
                        "Would either of those work?")}
    rows_t = []
    for copy, label in ((texts, "first round"), (return_texts, "return round")):
        for n, t in sorted(copy.items()):
            if not t:
                continue
            body = _render(t, f)
            rows_t.append(dict(segments(body), touch=n, round=label, chars=len(body)))
    first = [t for t in rows_t if t["round"] == "first round"]
    seg_per_family = sum(t["segments"] for t in first)
    # Real range across the ready audience's first names (name length and any
    # non-GSM character change the count; the link length is fixed per org).
    names = [x for x in (first_names or []) if x] or [first_name]
    per = sorted(family_segments(texts, dict(f, first_name=x, full_name="%s Whitfield-Henderson" % x))
                 for x in names)
    rng = {"families_measured": len(names), "min": per[0], "max": per[-1],
           "mean": round(sum(per) / len(per), 2),
           "unicode_families": sum(1 for x in names if any(c not in _GSM and c not in _GSM_EXT for c in x)),
           "longest_first_name": max(len(x) for x in names)}
    rows = []
    for nday in new_per_day:
        rows.append({"new_families_per_day": nday,
                     "texts_per_day": 5 * nday,
                     "text_segments_per_day": seg_per_family * nday,
                     "text_segments_per_day_range": [rng["min"] * nday, rng["max"] * nday],
                     "emails_per_day_max": 4 * nday, "emails_per_day_at_share": round(4 * nday * email_share, 1),
                     "families_in_sequence": 14 * nday,
                     "bookings_per_week": {("%d%%" % int(r * 100)): round(7 * nday * r, 1) for r in booking_rates},
                     # per booking: 1 confirmation + up to 2 reminders + 1 survey (if completed), x up to 2 channels
                     "appointment_messages_per_week_max": {("%d%%" % int(r * 100)): round(7 * nday * r * 4 * 2, 1)
                                                           for r in booking_rates}})
    return {"assumptions": "Upper bound: no early exits, every channel present. Each family gets at most "
                           "5 texts and 4 emails in total over 14 days; a steady day carries about 5 texts "
                           "and 4 emails per family that starts each day. Appointment messages, conversations "
                           "and staff alerts are separate from campaign volume.",
            "copy_source": source,
            "text_segments": rows_t, "segments_per_family_first_round": seg_per_family,
            "segments_per_family_range": rng,
            "billing": {
                "message": "one text as written (one row in the conversation)",
                "segment": "the unit carriers and Twilio count: 160 GSM-7 characters (153 per part when "
                           "split) or 70 UCS-2 (67 per part)",
                "billable_attempt": "every segment Twilio accepts is charged, delivered or not; a retry is "
                                    "a new attempt and is charged again",
                "charge_formula": "segments x (Twilio per-segment rate + the destination carrier's per-segment "
                                  "toll-free fee) + monthly number lease. Read the current rates from the "
                                  "Twilio US pricing page before quoting a dollar amount."},
            "controls": {
                "daily_intake": "how many NEW families start each day (the batch size; 0 now)",
                "outbound_workload": "texts and emails due that day across every family already in sequence",
                "provider_throughput": "how fast Twilio accepts messages from this toll-free number; set by the "
                                       "verification, not by this app"},
            "appointment_capacity": {"starts_per_week_for_one_person": 35,
                                     "note": "5 starts x 7 days before calendar conflicts; staff reassignment "
                                             "adds a second person's capacity"},
            "by_volume": rows}
