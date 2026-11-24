"""ONE-TIME CHANGES THE OWNER ORDERED IN CHAT, applied once at deploy.

Each change runs at most once per organization (a WorkspaceResetRun row with
mode "owner_change" and its own run_key is the ledger), records every value it
changes (WorkspaceResetItem before/after) so it can be undone, and never
stops a deploy. Later choices made in the app are never overwritten: once the
ledger row exists the change never runs again.

2026-11-21, owner ("Restland full blast, no slow drip ... SCI up and running,
full blast ... running them at the maximum capacity"):
  * RESTLAND: a new family's first text every 3 minutes (8:30 AM - 7 PM
    Central, about 210 a day; was every 8 minutes, 79 a day) with follow-up
    texts in every minute between (was one per gap, 78 a day, already full).
    Every family still waiting for its first text moves up into the new
    slots, in the same order. Daily batch 200 a day, 3 minutes apart.
  * SCI: daily batch 200 a day (the most allowed), 2 minutes apart (was 40,
    10 minutes apart): the remaining new families and the start-over queue.

2026-11-22, owner ("I need you to turn off the cadence for Restland only on
Sundays ... I want everything to stop on Sundays, only on Sundays"):
  * RESTLAND: Sunday becomes a closed day (outreach_slots closed_days "7"):
    no texts, emails, AI answers, daily batch or survey go out on Sundays.
    Every text already reserved for a Sunday slot moves to the next open
    day in the same order - first texts to the next new-family slots,
    follow-ups to the next free follow-up slots. SCI is not touched.

2026-11-22 evening, owner (Restland handoff):
  * RESTLAND: families who asked us to follow up later ("after the
    holidays", "late January") go back on the follow-up list with their date
    (state waiting_future; a holiday or Sunday moves to the next working
    day). They had been parked as "needs a person", where the date was
    never acted on.
  * RESTLAND: out-of-town families can book a remote call (phone or video)
    - the "By phone or video call" choice is added to Restland's booking
    choices and the location offers phone appointments.
"""
import json
import re
import logging
from datetime import date, datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

MODE = "owner_change"
RESTLAND_KEY = "restland-full-speed-2026-11-21"
SCI_KEY = "sci-full-speed-2026-11-21"
RESTLAND_NEW_EVERY = 3
RESTLAND_DAILY, RESTLAND_SPACING = 200, 3
SCI_DAILY, SCI_SPACING = 200, 2
RESTLAND_SUNDAYS_KEY = "restland-sundays-off-2026-11-22"
RESTLAND_CLOSED_DAYS = "7"
# Only before these moments: an old build restarted weeks later never
# applies a stale order. Nov 21 changes: Mon Nov 23 midnight Central.
APPLY_UNTIL = datetime(2026, 11, 23, 6, 0)
SUNDAYS_APPLY_UNTIL = datetime(2026, 12, 6, 6, 0)
RESTLAND_HANDOFF_KEY = "restland-followups-remote-2026-11-22"
# The team's handoff board (Google Sheet, owner's Drive; shared with the
# sync service account as Editor). Not a secret.
RESTLAND_HANDOFF_SHEET_ID = "1o8gPHz0-tLcrN_IxSQsSQE3Mt6IUX0bQ1U4jlmDfPgo"
RESTLAND_HANDOFF_2_KEY = "restland-followups-acks-2026-11-22"
RESTLAND_HANDOFF_3_KEY = "restland-jan4-remote-on-request-2026-11-22"
# Owner, Nov 22 9:48 PM: "this was a test so undo everything about this test".
# His sheet test at 9:43 PM marked Adolfo Vila's Nov 19 appointment confirmed +
# showed (scheduling a thank-you survey) and closed Aftab Naseer's follow-up.
RESTLAND_SHEET_TEST_UNDO_KEY = "restland-sheet-test-undo-2026-11-22"
TEST_BOOKING_ID = "eccad87b-e484-4a29-959a-7023a9f4dbde"
TEST_CONV_ID = "3bff2f95-1cda-4ce9-8fe8-b8ac5c5b2665"
TEST_CONV_REASON = ("The family is upset due to receiving messages after Aftab's passing, and immediate attention "
                    "is needed to update records and stop further messages.")
TEST_NOTE_IDS = ("ef0e99d5-4d16-4a79-9b70-1a0e1d5ee4de", "e72cc136-01e8-41c5-9fff-a4debd909136")
TEST_SURVEY_ID = "ac5f7d15-9e3e-484f-90c0-946be2decab3"
# Owner, Nov 23 9:09 AM: Carmelita Alcala asked "Do have any around 3 today?" -
# "if the customer requested 3 o'clock, sure, we can do 3 o'clock ... book it
# yourself". Booked once, through the normal booking path, only while 3 PM
# today is still bookable (2-hour same-day notice) and she has nothing booked.
CARMELITA_KEY = "restland-carmelita-3pm-2026-11-23"
CARMELITA_LEAD = "4f7ef8fe-5007-4ccb-aadb-7957154eec00"
CARMELITA_SLOT = datetime(2026, 11, 23, 15, 0)          # Central
CARMELITA_UNTIL = datetime(2026, 11, 23, 19, 0)          # 1 PM Central, in UTC
# 3:00 was refused: the shared appointment calendar already has a 3:00-3:30
# entry. Owner, 9:36 AM: "Book her at 3:30 PM".
CARMELITA_330_KEY = "restland-carmelita-330pm-2026-11-23"
CARMELITA_330_SLOT = datetime(2026, 11, 23, 15, 30)      # Central
CARMELITA_330_UNTIL = datetime(2026, 11, 23, 19, 30)     # 1:30 PM Central, in UTC

# Owner, Nov 23 4:55 PM: 19 emailed replies and call-center call-backs that
# never reached the system (Restland's emails carry Mike's NSMG address as
# Reply-To, so email replies land in his work inbox). Each one goes into the
# app (the family's words, why they're on the list, automatic outreach
# stopped, a note) and so onto the Follow-Ups tab. Plus Carmelita Alcala:
# Neoso's "followup 12/7" becomes a dated follow-up for the team - nothing is
# sent to her (a "needs a person" follow-up never texts the family).
EMAILED_KEY = "restland-emailed-replies-2026-11-23"
EMAILED_UNTIL = datetime(2026, 11, 26, 6, 0)
_NOV30, _DEC7 = datetime(2026, 11, 30, 15, 0), datetime(2026, 12, 7, 15, 0)     # 9 AM Central
# (lead id, state, why / window, follow-up date, what they said, channel, extra note)
EMAILED = [
    ("d41b343a-0769-45b2-a93c-9ec0c8c6b16c", "waiting_future", "after Thanksgiving", _NOV30,
     "I'll get in touch after Thanksgiving.", "email", None),
    ("02004df2-af60-4f88-b7f6-6d59a195e8a5", "waiting_future", "feeling unwell (colds / flu) - next month", _DEC7,
     "We are feeling well - colds maybe the flu. Next month might be better.", "email", None),
    ("13a12d5d-1ccd-49c0-a008-495a10a74e91", "callback_requested", "as soon as possible - before the date in the text",
     None, "(Call center) Replied to the text and hadn't heard back; wants to talk before tomorrow. Call 214-680-1065.",
     "call", None),
    ("fa2ddb87-b765-40c8-8967-e7af37c0014c", "callback_requested", "wants more info on the Permission to Access forms",
     None, "(Call center) Got the text; wants more info on the Permission to Access forms. Call 972-363-4361.",
     "call", None),
    ("f31e2f7b-48c1-4a46-bf4d-a022237be1ba", "callback_requested", "will call back to speak with Mike", None,
     "(Call center) Didn't say what he needs; asked for Michael. Call 469-810-1630.", "call", None),
    ("01c0f7a3-d06e-4860-9442-0b71f110df20", "human", "Asked what the file items are about - explain and set a time",
     None, "Hello Mike. What kind of things for the file are you talking about?", "email", None),
    ("483cfcdf-15ff-4e4b-ab20-c4d64a03a472", "human", "Doesn't live in Dallas - explain, offer a call if he asks",
     None, "I do not live in Dallas. Let me know what this is about.", "email", None),
    ("cb375146-276e-4076-9d00-49744b3438df", "human",
     "Wants Sunday Dec 13 at 2 PM (or 4) with her mom - Sundays are closed; find another time", None,
     "Booking page only went to Dec 5. Can come Sunday Dec 13, 2 o'clock better (or 4). Lives 2 hours away, works "
     "Saturdays and 3 jobs. Call or text after 10 PM any night. Mentions previous problems with purchases.", "email",
     None),
    ("b152343e-4f35-43c9-8f9b-bb27ce8714c5", "human", "Asked for Dec 1 or 2 in the afternoon - book it; asks if his wife "
     "needs to be there", None, "Could not make your suggested times. How about December 1 or 2 in the afternoon? "
     "Does my wife need to be present for this meeting?", "email", None),
    ("7f9559b0-264a-44e9-a90d-860ba228ff97", "human",
     "Annay (daughter-in-law) asks if there's an issue with the records/burial and what the form is - explain", None,
     "Is there an issue with our existing cemetery records or my father-in-law's burial arrangements? Please explain "
     "the Permission to Access form and whether the appointment is mandatory.", "email", None),
    ("c3effc9e-45c6-49d5-9fe1-27a7c907434f", "human",
     "Betty Miller passed away over 5 years ago - talk to her daughter Cazzie Husbands (executor)", None,
     "(Son Curtis W. Miller) My mother died and was buried in the family plot over 5 years ago. My sister Cazzie "
     "Husbands was the executor - cmhusbands65@gmail.com, (903) 216-0433. Spaces still available in the family plot.",
     "email", "Betty Miller has passed away (per her son Curtis). Contact: Cazzie Husbands (executor), "
     "cmhusbands65@gmail.com, (903) 216-0433."),
    ("d4a416b9-1020-4629-85e9-cb28fd588a05", "human", "Thinks she already did everything - check her file", None,
     "I think I already did everything.", "email", None),
    ("8a978d02-3161-40ed-8373-c2a14c0ed196", "human",
     "Says it's handled - going to the National Cemetery with her husband; all paid", None,
     "Have dealt with this in the past. I will be going to the National Cemetery with my husband. All is paid for "
     "and fine with the girls.", "email", None),
    ("8104deac-4c48-417e-a102-68e85c002a48", "human",
     "Lives in Virginia now; her son Dax and daughter Cecille Deason will handle arrangements", None,
     "I do not live in Dallas anymore, I live in Virginia now, but yes, I will be using my space in Dallas. My son "
     "Dax Deason and daughter Cecille Deason will communicate for arrangements. They have all my cemetery files.",
     "email", "New contact info she gave: phone 915-920-6538; 319 North First Street Apt 203, Bridgewater, VA 22812."),
    ("57e020ca-7b84-4ccc-8a02-cf15e8c87a73", "human",
     "Met with Deidra Cunningham on July 1 - says this was covered; check with Deidra", None,
     "On July 1 I met at Restland with Deidra Cunningham and I am sure we covered the items you list. Please discuss "
     "with her or check her files.", "email", None),
    ("c28238a1-0870-42a6-b53c-4dae95a8a6b7", "human",
     "No time to come in; wants it sent to him to decide; not interested in buying another site", None,
     "I don't have time to stop by your office. We went through this last year and your guy wanted to sell me another "
     "site, which I'm not interested in. Send me whatever you're trying to do and I'll decide. 469 325 8839",
     "email", "Phone he gave: 469-325-8839."),
    ("cd06936b-bad5-4e63-92a0-68596f6119bf", "human",
     "Already done with Leslie Castillo on Nov 9 (also contacted by Alanna Cherry) - confirm and close", None,
     "I already took care of the business with Leslie Castillo on Monday, November 9. Alanna Cherry asked too. Please "
     "report to your group that the project is complete and no one else needs to address it.", "email", None),
]
EMAILED_NOTES = [   # already in the system - the email / call adds to their file
    ("1c295bab-6345-4d72-9335-b6f841113b53", "(Call center, Nov 23 2:04 PM) Barbara Thompson called back - you had "
     "contacted her. Call her at 972-768-9097."),
    ("ce5f284b-d206-406d-bd9a-2801e2aac6b7", "(Call center, Nov 21) Daughter Adrianna Rojas asked for a call about her "
     "mother's paperwork / PN arrangements: 214-850-4431."),
]
# Owner, Nov 23 9:05 PM: more family replies that reached Mike's NSMG inbox
# (reply-to pointed there until 5 PM Nov 23). Each one - and the 17 entered
# earlier - goes into the Conversations inbox as an EMAIL reply, like a real one.
REPLIES2_KEY = "restland-emailed-replies-2-2026-11-23"
REPLIES2_UNTIL = datetime(2026, 11, 26, 6, 0)
# (lead id, state or None to keep, why, follow-up date, what they said, received (UTC), extra note)
EMAILED_2 = [
    ("dbcf4b2f-45d9-40a8-8501-eecd8d28ef33", None, None, None,
     "Hello Michael, I'm not in Texas. How else can we do this? Anne Wehler", datetime(2026, 11, 22, 12, 18),
     "She is NOT in Texas and asked how else this can be done - her Tue Nov 24 10 AM visit at Restland will not "
     "work as booked. Call or email her first (lokilani888@hotmail.com) to switch it to a phone call."),
    ("81f2c560-a7b6-4285-97d6-fa4900400fcb", "human",
     "Husband Frank Inman has power of attorney - they live near Seattle; everything by EMAIL only", None,
     "(Frank Inman, her husband) Be is unable to respond. I have full power of attorney. We both live on Mercer "
     "Island near Seattle, Washington. We are unable to visit your office. Anything that needs to be done will "
     "have to be by email. You can use Be's email address (beinman79@gmail.com) as I monitor it for her.",
     datetime(2026, 11, 22, 3, 45),
     "Frank Inman (husband) has full power of attorney and handles everything - email only, no visits, no texts."),
    ("e19663a4-c536-49ce-81b9-8790a8a26806", "closed", "Will not attend a meeting - says everything is as it "
     "should be", None, "No, I have told you. I will not be attending a meeting. Everything is as it should be. "
     "Marea Downey", datetime(2026, 11, 20, 0, 25), None),
    ("2b84f8f3-b931-4693-be8d-9a805f1f1c1b", "waiting_future", "Takuya Fukuda (family) is out of town - follow up "
     "next month for Akira", _DEC7, "(Takuya Fukuda) Thank you for reaching out. I'm currently out of town, but "
     "I'll follow up with you next month for Akira.", datetime(2026, 11, 19, 0, 55), None),
]
EMAIL_ONLY_2 = ("81f2c560-a7b6-4285-97d6-fa4900400fcb",)
# Beverly Schmedthorst (Mrs. Lee Roy) wrote in August; she is not in the system yet.
BEVERLY = {"first_name": "Beverly", "last_name": "Schmedthorst", "phone": "12146217290", "email": "bev@gsteps.com",
           "street_address": "3901 Kirkpatrick Lane, Apt 102", "city": "Flower Mound", "state": "TX",
           "zip_code": "75028"}
BEVERLY_SAID = ("I have moved to 3901 Kirkpatrick Lane, Apt 102, Flower Mound Tx 75028 (Pinewoods Sr Living). "
                "My daughter, Kaye Gullett, takes care of my business. One item we need is a map to Lee Roy's grave - "
                "we have been there twice and could not find it. Thanks for reminding us we need to update.")
BEVERLY_WHY = ("Needs a map to Lee Roy's grave (could not find it twice). Daughter Kaye Gullett handles her business: "
               "214-293-5061, kaye@gsteps.com")
BEVERLY_NOTE = ("Owner, Nov 23: emailed Aug 13 2026 (bev@gsteps.com). Mrs. Lee Roy Schmedthorst, age 87. Moved to "
                "Pinewoods Sr Living, 3901 Kirkpatrick Lane Apt 102, Flower Mound TX 75028; phone 214-621-7290. "
                "Daughter Kaye Gullett has legal permission and handles her business: 214-293-5061, kaye@gsteps.com, "
                "1308 Saddleback Lane, Lewisville TX 75067. Needs a map to Lee Roy's grave. Aug 3 would have been "
                "their 69th anniversary.")
# Owner, Nov 23 4:59 PM ("the emails aren't being sent back into the system"):
# family replies go to the mailbox the system reads, not Mike's NSMG inbox.
REPLY_TO_SYSTEM = "support@evosyspro.live"
# Colleen Crawford emailed 4:56 PM: "1pm WEDNESDAY...by phone. I am in Indiana. 765 327 1488"
COLLEEN = ("fa0cd5b6-d595-4c41-9644-f32aad0b7f74", datetime(2026, 11, 25, 13, 0), "17653271488",
           "1pm WEDNESDAY...by phone. I am in Indiana. 765 327 1488")
# Owner, Nov 23 6:47 PM: "Sun Dec 13 is available, make the appointment" (Belinda
# Williams, 2 PM - her first choice, with her mom) and book Patrick Bartell
# for the afternoon he asked for (Dec 1 or 2).
# Owner, Nov 24 11:19 AM: morning visits get an 8:30 AM "see you today" text;
# the booking confirmation no longer cancels a reminder. Re-plans the
# reminders of every upcoming Restland appointment once, at deploy.
# Owner, Nov 24 12:14 PM: two more email replies that reached Mike's work inbox
# (emails sent before 5 PM Nov 23 carry his NSMG address as Reply-To). Each
# goes into the app like a real emailed reply: the Inbox, the Follow-Ups tab,
# automatic texts stopped, a note. No AI reply to them (a person answers).
NSMG3_KEY = "restland-nsmg-replies-2026-11-24"
NSMG3_UNTIL = datetime(2026, 11, 27, 6, 0)
NSMG3 = [
    ("ee463e85-e587-4cba-9d9d-0398f38362a0", "Wants the Permission to Access form emailed to review",
     "Hello Mike, Please send me the form to review. Thank you", datetime(2026, 11, 24, 17, 34)),
    ("7d595d50-1063-4386-96bf-ed2fd37a0a70", "Wants to schedule a meeting next week - offer times",
     "Can we schedule a meeting next week? Steven Kinder", datetime(2026, 11, 24, 18, 10)),
]
# Owner, Nov 24 11:51 AM: David Ahmad asked for a call ("you can call me at
# 817.723.4630") - his Sat Nov 28 10 AM appointment is a PHONE CALL, on the
# app, the sheet and the calendar. Nothing is sent to him by this change.
DAVID_KEY = "restland-david-ahmad-phone-2026-11-24"
DAVID_UNTIL = datetime(2026, 11, 28, 16, 0)
DAVID_BOOKING = "2d2d43e6-c9df-4c8a-86a2-2e82c4ae93bd"
MORNING_KEY = "restland-morning-reminders-2026-11-24"
MORNING_UNTIL = datetime(2026, 11, 27, 6, 0)
TWO_KEY = "restland-belinda-patrick-2026-11-23"
TWO_UNTIL = datetime(2026, 11, 26, 6, 0)
BELINDA = ("cb375146-276e-4076-9d00-49744b3438df", datetime(2026, 12, 13, 14, 0))
PATRICK = ("b152343e-4f35-43c9-8f9b-bb27ce8714c5", [date(2026, 12, 1), date(2026, 12, 2)])
# Owner, Nov 23 9:45 PM: Anne Wehler is not in Texas - her Tue Nov 24 10 AM
# visit becomes a phone call at the same time (Laura calls 817-946-9594).
ANNE_KEY = "restland-anne-phone-2026-11-23"
ANNE_UNTIL = datetime(2026, 11, 24, 15, 0)
ANNE = "dbcf4b2f-45d9-40a8-8501-eecd8d28ef33"
CARMELITA_FU = ("4f7ef8fe-5007-4ccb-aadb-7957154eec00", _DEC7,
                "Team follow-up Mon Dec 7 (Neoso Walker's note after her visit)")
# Owner, Nov 22 8:18 PM: these four "after the holidays" families are followed
# up the first Monday after New Year's - Mon Jan 4 2027, 9 AM Central.
AFTER_NEW_YEAR = datetime(2027, 1, 4, 15, 0)
AFTER_NEW_YEAR_LEADS = (
    "e129cb24-c807-42c9-bec5-447efb494538",   # Annette Birt
    "07dc8e9d-6ccb-4923-9245-58bdb1c86aaa",   # Brian Fehr
    "13d1df8b-4829-4f11-9755-45b49bcfc245",   # Alleyne Henderson
    "f4c9f1bf-9697-4183-bb46-5e29c4cc839c",   # Albert Faulkner
)
# A reply that only acknowledges what was agreed ("👍", "ok", "thanks") is not
# a new message: the later follow-up still stands.
_ACK = re.compile(r"^\W*(ok(ay)?|k|thanks?( you)?|thx|ty|sounds good|great|perfect|will do|yes|yep|sure|cool)?\W*$", re.I)


def _ledger(db: Session, org_id: str, key: str):
    from app.models.reset_models import WorkspaceResetRun
    return db.query(WorkspaceResetRun).filter_by(organization_id=org_id, run_key=key, mode=MODE).first()


def _open_run(db: Session, org_id: str, key: str):
    from app.models.reset_models import WorkspaceResetRun
    run = WorkspaceResetRun(organization_id=org_id, run_key=key, mode=MODE, status="started")
    db.add(run)
    db.flush()

    def item(entity, eid, action, before, after, reason):
        from app.models.reset_models import WorkspaceResetItem
        db.add(WorkspaceResetItem(run_id=run.id, organization_id=org_id, entity=entity, entity_id=eid,
                                  action=action, before_json=json.dumps(before, default=str),
                                  after_json=json.dumps(after, default=str), reason=reason))
    return run, item


def _batch(prog, item, count: int, spacing: int, reason: str) -> None:
    before = {"daily_batch_count": prog.daily_batch_count, "daily_batch_spacing": prog.daily_batch_spacing}
    prog.daily_batch_count, prog.daily_batch_spacing = count, spacing
    item("outreach_program", prog.id, "daily_batch", before,
         {"daily_batch_count": count, "daily_batch_spacing": spacing}, reason)


def restland_full_speed(db: Session, prog, now: datetime) -> Dict:
    from app.services.programs import outreach_slots as OS
    from app.services.programs import restland_restart as RR
    org_id = prog.organization_id
    pol = json.loads(prog.booking_policy_json or "{}")
    o = pol.get("outreach")
    if not isinstance(o, dict) or not o.get("slots"):
        return {"skipped": "no text schedule"}
    run, item = _open_run(db, org_id, RESTLAND_KEY)
    before = dict(o)
    o["new_every_minutes"], o["followup_fill"] = RESTLAND_NEW_EVERY, True
    pol["outreach"] = o
    prog.booking_policy_json = json.dumps(pol)
    item("outreach_program", prog.id, "text_schedule", before, dict(o), "owner Nov 21: full blast")
    _batch(prog, item, RESTLAND_DAILY, RESTLAND_SPACING, "owner Nov 21: full blast")
    db.flush()
    OS.clear_cache()
    cfg = OS.config_from_policy(prog.booking_policy_json)
    # Everyone still waiting for a first text, in their current order.
    waiting = [w for w in RR._waiting(db, org_id, set()) if w["state"] is not None]
    S = OS._model()
    ids = {w["state"].id for w in waiting}
    for w in waiting:
        OS.release(db, w["state"].id, 1)
    db.flush()
    taken = {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= now,
                                                     S.status == "reserved").all()}
    first = OS.next_open(now + timedelta(minutes=5), cfg)
    slots = OS.new_family_slots(first, len(waiting), cfg, taken) if waiting else []
    for w, at in zip(waiting, slots):
        st = w["state"]
        item("cadence_state", st.id, "moved_up",
             {"cadence_started_at": st.cadence_started_at, "next_touch_due_at": st.next_touch_due_at},
             {"next_touch_due_at": at}, "new family every 3 minutes")
        st.cadence_started_at, st.next_touch_due_at = at, at
        OS.reserve(db, org_id, st, 1, at, "new")
        for r in w["recs"]:
            r.scheduled_start_at = at
    summary = {"new_every_minutes": RESTLAND_NEW_EVERY, "followup_fill": True,
               "daily_batch": RESTLAND_DAILY, "spacing": RESTLAND_SPACING, "moved_up": len(slots),
               "first_slot_utc": slots[0].isoformat() if slots else None,
               "last_slot_utc": slots[-1].isoformat() if slots else None, "moved_ids": len(ids)}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    OS.clear_cache()
    return summary


def restland_sundays_off(db: Session, prog, now: datetime) -> Dict:
    """Close Sundays for Restland and move every text reserved for a Sunday
    slot (from today on) to the next open day, keeping its order."""
    from app.models.models import CadenceState
    from app.services.programs import outreach_slots as OS
    from app.services.programs import restland_restart as RR
    org_id = prog.organization_id
    pol = json.loads(prog.booking_policy_json or "{}")
    o = pol.get("outreach")
    if not isinstance(o, dict) or not o.get("slots"):
        return {"skipped": "no text schedule"}
    run, item = _open_run(db, org_id, RESTLAND_SUNDAYS_KEY)
    before = dict(o)
    o["closed_days"] = RESTLAND_CLOSED_DAYS
    pol["outreach"] = o
    prog.booking_policy_json = json.dumps(pol)
    item("outreach_program", prog.id, "text_schedule", before, dict(o), "owner Nov 22: nothing on Sundays")
    db.flush()
    OS.clear_cache()
    cfg = OS.config_from_policy(prog.booking_policy_json)
    S = OS._model()
    today_local = OS._local(now).date()
    since = OS._utc(datetime.combine(today_local, datetime.min.time()).replace(tzinfo=OS._zone()))
    rows = (db.query(S).filter(S.organization_id == org_id, S.status == "reserved", S.slot_at >= since)
            .order_by(S.slot_at).all())
    closed = [r for r in rows if OS.closed_on(OS._local(r.slot_at).date(), cfg)]
    first = OS.next_open(now, cfg)
    # First texts keep their order: every first text from the first Sunday
    # one onward is re-laid on the new-family grid from the next opening, so a
    # Sunday family is never put behind Monday's.
    closed_new = [r for r in closed if r.kind == "new"]
    new_rows = [r for r in rows if r.kind == "new" and r.slot_at >= closed_new[0].slot_at] if closed_new else []
    fu_rows = [r for r in closed if r.kind != "new"]
    recs_by_state = {w["state"].id: w["recs"] for w in RR._waiting(db, org_id, set()) if w["state"] is not None}
    states = {st.id: st for st in db.query(CadenceState).filter(
        CadenceState.id.in_([r.cadence_state_id for r in closed + new_rows] or ["-"])).all()}
    moves_new = [(r.cadence_state_id, r.touch_number, r.slot_at) for r in new_rows]
    moves_fu = [(r.cadence_state_id, r.touch_number, r.slot_at) for r in fu_rows]
    for sid, touch, _at in moves_new:
        OS.release(db, sid, touch)
    db.flush()
    taken = {t for (t,) in db.query(S.slot_at).filter(S.organization_id == org_id, S.slot_at >= first,
                                                     S.status == "reserved").all()}
    slots = OS.new_family_slots(first, len(moves_new), cfg, taken) if moves_new else []
    moved_new = 0
    for (sid, touch, was), at in zip(moves_new, slots):
        st = states.get(sid)
        if st is None:
            continue
        item("cadence_state", st.id, "sunday_moved",
             {"next_touch_due_at": st.next_touch_due_at, "slot_at": was}, {"next_touch_due_at": at},
             "owner Nov 22: nothing on Sundays")
        if (st.current_touch_number or 0) == 0:
            st.cadence_started_at = at
            for r in recs_by_state.get(st.id, []):
                r.scheduled_start_at = at
        st.next_touch_due_at = at
        OS.reserve(db, org_id, st, touch, at, "new")
        moved_new += 1
    moved_fu = 0
    for sid, touch, was in moves_fu:
        st = states.get(sid)
        if st is None:
            continue
        before_due = st.next_touch_due_at
        at = OS.place_followup(db, org_id, st, touch, first, cfg)
        item("cadence_state", st.id, "sunday_moved", {"next_touch_due_at": before_due, "slot_at": was},
             {"next_touch_due_at": at}, "owner Nov 22: nothing on Sundays")
        moved_fu += 1
    summary = {"closed_days": RESTLAND_CLOSED_DAYS, "sunday_first_texts": len(closed_new),
               "first_texts_relaid": moved_new, "moved_follow_ups": moved_fu,
               "next_open_utc": first.isoformat(),
               "first_moved_utc": slots[0].isoformat() if slots else None,
               "last_moved_utc": slots[-1].isoformat() if slots else None}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    OS.clear_cache()
    return summary


def restland_jan4_and_remote_on_request(db: Session, prog, now: datetime) -> Dict:
    """Owner, Nov 22 8:18 PM: (1) the four families above follow up Mon Jan 4
    2027; (2) a phone/video call ONLY when the family asks - off the booking
    page, and the location no longer offers phone appointments itself."""
    from app.models.program_models import LocationProfile, ProgramConversation
    from app.services.programs import booking_policy as BP
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_3_KEY)
    moved = []
    for c in (db.query(ProgramConversation)
              .filter(ProgramConversation.organization_id == org_id,
                      ProgramConversation.lead_id.in_(AFTER_NEW_YEAR_LEADS),
                      ProgramConversation.state == "waiting_future").all()):
        item("program_conversation", c.id, "follow_up_date", {"next_followup_at": c.next_followup_at},
             {"next_followup_at": AFTER_NEW_YEAR}, "owner Nov 22: first Monday after New Year's")
        c.next_followup_at, c.followups_sent = AFTER_NEW_YEAR, 0
        moved.append(c.lead_id)
    pol = json.loads(prog.booking_policy_json or "{}")
    opts = pol.get("meeting_options")
    if isinstance(opts, list):
        new = []
        for o in opts:
            if o.get("key") == BP.REMOTE and not o.get("on_request"):
                item("outreach_program", prog.id, "remote_on_request", dict(o), None, "owner Nov 22: only on request")
                o = dict(BP.REMOTE_OPTION)
            new.append(o)
        pol["meeting_options"] = new
        prog.booking_policy_json = json.dumps(pol)
    phone_off = 0
    for prof in db.query(LocationProfile).filter(LocationProfile.organization_id == org_id).all():
        try:
            bs = json.loads(prof.brand_settings or "{}") if isinstance(prof.brand_settings, str) else dict(prof.brand_settings or {})
        except ValueError:
            bs = {}
        if bs.get("offer_phone_alternative"):
            item("location_profile", prof.id, "brand_settings", dict(bs), None, "owner Nov 22: calls only on request")
            bs.pop("offer_phone_alternative", None)
            prof.brand_settings = json.dumps(bs)
            phone_off += 1
    summary = {"moved_to_jan_4": moved, "locations_phone_off": phone_off}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_emailed_replies(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import Lead
    from app.models.work_models import LeadNote
    from app.services import cadence_service as CS
    from app.services.programs import ai_setter
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, EMAILED_KEY)
    out: Dict = {"added": 0, "noted": 0, "missing": []}

    def note(lead_id, body):
        db.add(LeadNote(organization_id=org_id, lead_id=lead_id, body=body[:4000], kind="internal"))

    for lead_id, state, why, when, said, channel, extra in EMAILED:
        lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
        if lead is None:
            out["missing"].append(lead_id)
            continue
        conv = ai_setter.conversation_for(db, lead)
        before = {"state": conv.state, "handoff_reason": conv.handoff_reason, "callback_window": conv.callback_window,
                  "followup_reason": conv.followup_reason, "next_followup_at": conv.next_followup_at,
                  "lead_status": lead.status}
        ai_setter._turn(db, conv, "family", said, action="received",
                        reason="%s reply that reached Mike's inbox, entered Nov 23" % channel)
        conv.state = state
        if state == "waiting_future":
            conv.followup_reason, conv.next_followup_at, conv.followups_sent = why, when, 0
        elif state == "callback_requested":
            conv.callback_window, conv.next_followup_at = why, None
        else:
            conv.handoff_reason, conv.next_followup_at = why[:250], when
        conv.last_family_at = now
        if lead.status in ("new", "sent"):
            lead.status = "replied"
        CS.stop_cadence_for_lead(db, lead.id, "stopped_replied")
        note(lead.id, "Owner, Nov 23: their %s reply reached Mike's work inbox, not the system - entered by hand. "
             "They said: %s%s" % (channel, said, (" | %s" % extra) if extra else ""))
        item("program_conversation", conv.id, "emailed_reply", before,
             {"state": state, "why": why, "when": when}, "owner Nov 23: emailed reply entered")
        out["added"] += 1
    for lead_id, body in EMAILED_NOTES:
        if db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first() is not None:
            note(lead_id, body)
            out["noted"] += 1
    lead = db.query(Lead).filter(Lead.id == CARMELITA_FU[0], Lead.organization_id == org_id).first()
    if lead is not None:
        conv = ai_setter.conversation_for(db, lead)
        before = {"state": conv.state, "handoff_reason": conv.handoff_reason, "next_followup_at": conv.next_followup_at}
        conv.state, conv.handoff_reason, conv.next_followup_at = "human", CARMELITA_FU[2], CARMELITA_FU[1]
        note(lead.id, "Owner, Nov 23: on the Follow-Ups list for Mon Dec 7 (Neoso Walker) - nothing is sent to her.")
        item("program_conversation", conv.id, "team_followup", before,
             {"state": "human", "next_followup_at": CARMELITA_FU[1]}, "owner Nov 23: Neoso's followup 12/7")
        out["carmelita"] = "Dec 7"
    # Replies come back into the system from now on.
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is not None and (org.reply_to_email or "") != REPLY_TO_SYSTEM:
        item("organization", org.id, "reply_to", {"reply_to_email": org.reply_to_email},
             {"reply_to_email": REPLY_TO_SYSTEM}, "owner Nov 23: email replies must reach the system")
        out["reply_to"] = {"before": org.reply_to_email, "after": REPLY_TO_SYSTEM}
        org.reply_to_email = REPLY_TO_SYSTEM
    db.commit()
    # Colleen Crawford: 1 PM Wednesday by phone, the number she gave.
    lead = db.query(Lead).filter(Lead.id == COLLEEN[0], Lead.organization_id == org_id).first()
    if lead is not None:
        from app.models.models import BookingLink
        from app.services.programs import appointments as A
        conv = ai_setter.conversation_for(db, lead)
        ai_setter._turn(db, conv, "family", COLLEEN[3], action="received",
                        reason="email reply that reached Mike's inbox, entered Nov 23")
        lead.callback_phone, lead.callback_phone_source, lead.callback_phone_at = COLLEEN[2], "email:reply", now
        CS.stop_cadence_for_lead(db, lead.id, "stopped_replied")
        has = db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                           BookingLink.status.in_(("booked", "confirmed"))).count()
        res = {"status": "already_booked"} if has else A.book(db, prog, lead, COLLEEN[1], A.PHONE)
        if res.get("ok") and not res.get("simulated"):
            conv.state, conv.booked_time = "booked", COLLEEN[1]
            if res.get("booking_id"):
                conv.booking_link_id = res.get("booking_id")
            try:
                A.notify_team_booked(db, prog, lead, COLLEEN[1], A.PHONE)
            except Exception:                            # noqa: BLE001
                log.exception("Colleen booked alert failed")
            note(lead.id, "Owner, Nov 23: emailed '%s' - booked a phone call Wed Nov 25 at 1:00 PM. "
                 "Call her at 765-327-1488 (she's in Indiana)." % COLLEEN[3])
        else:
            conv.state = "human"
            conv.handoff_reason = ("Asked for a phone call Wed 1 PM (in Indiana, 765-327-1488) - not booked: %s"
                                   % (res.get("reason") or res.get("status")))[:250]
            note(lead.id, "Owner, Nov 23: emailed '%s' - could not book automatically (%s); a person sets it."
                 % (COLLEEN[3], res.get("reason") or res.get("status")))
        out["colleen"] = res.get("status")
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_belinda_patrick(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import Lead
    from app.models.work_models import LeadNote
    from app.services.programs import ai_setter
    from app.services.programs import appointments as A
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, TWO_KEY)
    out: Dict = {}

    def done(lead, slot, kind, why_note):
        conv = ai_setter.conversation_for(db, lead)
        before = {"state": conv.state, "handoff_reason": conv.handoff_reason, "booked_time": conv.booked_time}
        res = A.book(db, prog, lead, slot, kind)
        if res.get("ok") and not res.get("simulated"):
            conv.state, conv.booked_time, conv.handoff_reason = "booked", slot, None
            if res.get("booking_id"):
                conv.booking_link_id = res.get("booking_id")
            db.add(LeadNote(organization_id=org_id, lead_id=lead.id, kind="internal",
                            body="Owner, Nov 23: booked %s. %s" % (A.describe(slot), why_note)))
            item("program_conversation", conv.id, "booked", before, {"state": "booked", "booked_time": slot},
                 "owner Nov 23: " + why_note[:120])
            try:
                A.notify_team_booked(db, prog, lead, slot, kind)
            except Exception:                            # noqa: BLE001
                log.exception("booked alert failed")
        db.commit()
        return res

    lead = db.query(Lead).filter(Lead.id == BELINDA[0], Lead.organization_id == org_id).first()
    if lead is not None:
        res = done(lead, BELINDA[1], A.VISIT, "Sunday Dec 13 opened by Mike for her (she asked for 2 PM, with her "
                   "mom; she lives 2 hours away). Best contact: call or text after 10 PM.")
        out["belinda"] = res.get("status") if res.get("ok") else "%s: %s" % (res.get("status"), res.get("reason"))
    lead = db.query(Lead).filter(Lead.id == PATRICK[0], Lead.organization_id == org_id).first()
    if lead is not None:
        out["patrick"] = "no afternoon time open Dec 1 or 2"
        for day in PATRICK[1]:
            slots, _ = A.all_slots_on_day(db, prog, day)
            pm = [x for x in slots if x.hour >= 13 and (x.hour, x.minute) in ((13, 0), (14, 30), (16, 0))]
            if not pm:
                continue
            res = done(lead, pm[0], A.VISIT, "He asked for Dec 1 or 2 in the afternoon. He also asked whether his "
                       "wife needs to be there - Laura, please answer him on that.")
            out["patrick"] = ("%s %s" % (res.get("status"), pm[0].isoformat()) if res.get("ok")
                              else "%s: %s" % (res.get("status"), res.get("reason")))
            if res.get("ok"):
                break
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_emailed_replies_2(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import Lead
    from app.models.work_models import LeadNote
    from app.models.program_models import ProgramResponse
    from app.services import cadence_service as CS
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, REPLIES2_KEY)
    out: Dict = {"inbox": 0, "set": 0, "missing": [], "beverly": None}

    def note(lead_id, body):
        db.add(LeadNote(organization_id=org_id, lead_id=lead_id, body=body[:4000], kind="internal"))

    def to_inbox(lead, said, when, key):
        # Same path as a real emailed reply: it shows under Conversations > Email.
        # No AI reply to an old email, no alert pages for it.
        rid = "manual-email:%s:%s" % (key, lead.id)
        if db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                            ProgramResponse.reply_id == rid).first() is not None:
            return
        R.on_inbound(db, lead, said, "email", reply_id=rid, now=when, staff_alerts=False, run_setter=False)
        out["inbox"] += 1

    def set_state(lead, state, why, when_fu, before_note):
        conv = ai_setter.conversation_for(db, lead)
        before = {"state": conv.state, "handoff_reason": conv.handoff_reason, "next_followup_at": conv.next_followup_at,
                  "followup_reason": conv.followup_reason}
        if state:
            conv.state = state
            if state == "waiting_future":
                conv.followup_reason, conv.next_followup_at, conv.followups_sent = why, when_fu, 0
            else:
                conv.handoff_reason, conv.next_followup_at = (why or "")[:250] or None, None
            item("program_conversation", conv.id, "emailed_reply_2", before, {"state": state, "why": why},
                 "owner Nov 23: " + before_note)
            out["set"] += 1
        return conv

    for lead_id, state, why, when_fu, said, received, extra in EMAILED_2:
        lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
        if lead is None:
            out["missing"].append(lead_id)
            continue
        conv = ai_setter.conversation_for(db, lead)
        ai_setter._turn(db, conv, "family", said, action="received",
                        reason="email reply that reached Mike's NSMG inbox, entered Nov 23")
        to_inbox(lead, said, received, "nov23b")
        set_state(lead, state, why, when_fu, "emailed reply")
        if lead.status in ("new", "sent"):
            lead.status = "replied"
        if lead_id in EMAIL_ONLY_2:
            lead.contact_channel = "email_only"
        CS.stop_cadence_for_lead(db, lead.id, "stopped_replied")
        note(lead.id, "Owner, Nov 23: their email reply reached Mike's NSMG inbox, not the system - entered by hand. "
             "They said: %s%s" % (said, (" | %s" % extra) if extra else ""))
        db.commit()
    # The 17 entered earlier: into the inbox too (their state stays as set).
    for lead_id, state, why, when_fu, said, channel, extra in EMAILED:
        if channel != "email":
            continue
        lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
        if lead is None:
            continue
        conv = ai_setter.conversation_for(db, lead)
        keep = (conv.state, conv.handoff_reason, conv.next_followup_at, conv.followup_reason, conv.callback_window)
        to_inbox(lead, said, now, "nov23a")
        conv.state, conv.handoff_reason, conv.next_followup_at, conv.followup_reason, conv.callback_window = keep
        db.commit()
    # Beverly Schmedthorst - not in the system: add her as a Restland family.
    b = BEVERLY
    lead = (db.query(Lead).filter(Lead.organization_id == org_id, Lead.email == b["email"]).first()
            or db.query(Lead).filter(Lead.organization_id == org_id, Lead.phone == b["phone"]).first())
    if lead is None:
        lead = Lead(organization_id=org_id, status="replied", source="email_reply",
                    source_detail="Emailed Mike Aug 13 2026 - entered by hand Nov 23", contact_channel="sms", **b)
        db.add(lead)
        db.flush()
        item("lead", lead.id, "created", None, {"name": "Beverly Schmedthorst"}, "owner Nov 23: Beverly's email")
        out["beverly"] = "added"
    else:
        out["beverly"] = "already there"
    conv = ai_setter.conversation_for(db, lead)
    ai_setter._turn(db, conv, "family", BEVERLY_SAID, action="received",
                    reason="email (Aug 13) that reached Mike's NSMG inbox, entered Nov 23")
    to_inbox(lead, BEVERLY_SAID, datetime(2026, 8, 14, 2, 11), "aug13")
    set_state(lead, "human", BEVERLY_WHY, None, "Beverly's email")
    note(lead.id, BEVERLY_NOTE)
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_anne_phone(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import BookingLink, Lead
    from app.models.work_models import LeadNote
    from app.services.programs import ai_setter
    from app.services.programs import appointments as A
    from app.services.programs import booking_policy as BP
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, ANNE_KEY)
    out: Dict = {}
    lead = db.query(Lead).filter(Lead.id == ANNE, Lead.organization_id == org_id).first()
    bk = (db.query(BookingLink).filter(BookingLink.lead_id == ANNE, BookingLink.status.in_(("booked", "confirmed")),
                                       BookingLink.booked_time >= now - timedelta(hours=12))
          .order_by(BookingLink.booked_time).first()) if lead is not None else None
    if bk is None:
        out["skipped"] = "no upcoming booking"
    else:
        before = {"meeting_type": bk.meeting_type, "appt_label": bk.appt_label, "visit_address": bk.visit_address,
                  "appt_duration": bk.appt_duration}
        bk.meeting_type, bk.visit_address = BP.REMOTE, None
        bk.appt_label = BP.REMOTE_OPTION["appt_label"]
        bk.appt_duration = A.visit_minutes(prog, A.PHONE)
        item("booking_link", bk.id, "to_phone", before,
             {"meeting_type": BP.REMOTE, "appt_label": bk.appt_label}, "owner Nov 23: Anne is not in Texas")
        conv = ai_setter.conversation_for(db, lead)
        conv.booked_time = bk.booked_time
        db.add(LeadNote(organization_id=org_id, lead_id=lead.id, kind="internal",
                        body="Owner, Nov 23: she is not in Texas - her %s appointment is now a PHONE CALL at the same "
                             "time. Call her at 817-946-9594 (email lokilani888@hotmail.com)." % A.describe(bk.booked_time)))
        out["changed"] = bk.booked_time.isoformat()
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_book_carmelita(db: Session, prog, now: datetime, key: str = CARMELITA_KEY,
                            slot: datetime = CARMELITA_SLOT) -> Dict:
    from app.models.models import BookingLink, Lead
    from app.models.program_models import ProgramResponse
    from app.services.programs import ai_setter
    from app.services.programs import appointments as A
    from app.services.programs import responses as R
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, key)
    lead = db.query(Lead).filter(Lead.id == CARMELITA_LEAD, Lead.organization_id == org_id).first()
    out: Dict = {}
    if lead is None:
        out["skipped"] = "family not found"
    elif db.query(BookingLink).filter(BookingLink.lead_id == lead.id,
                                      BookingLink.status.in_(("booked", "confirmed"))).count():
        out["skipped"] = "already booked"
    else:
        res = A.book(db, prog, lead, slot, A.VISIT, meeting_key="office")
        out = {"status": res.get("status"), "booking_id": res.get("booking_id"), "reason": res.get("reason")}
        if res.get("ok") and not res.get("simulated"):
            conv = ai_setter.conversation_for(db, lead)
            item("program_conversation", conv.id, "booked", {"state": conv.state}, {"state": "booked"},
                 "owner Nov 23: she asked for around 3 PM today")
            conv.state, conv.booked_time = "booked", slot
            if res.get("booking_id"):
                conv.booking_link_id = res.get("booking_id")
            newest = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                                       ProgramResponse.lead_id == lead.id,
                                                       ProgramResponse.handling_status != "closed")
                      .order_by(ProgramResponse.received_at.desc()).first())
            actor = A.program_advisor(db, prog)
            if newest is not None and actor is not None:
                R.mark(db, newest, "responded", actor)
            try:
                A.notify_team_booked(db, prog, lead, slot, A.VISIT)
            except Exception:                            # noqa: BLE001 - the booking stands
                log.exception("Carmelita booked alert failed")
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out, default=str)
    db.commit()
    return out


def restland_sheet_test_undo(db: Session, prog, now: datetime) -> Dict:
    """Put both families back exactly as they were before the owner's test."""
    from app.models.models import BookingLink
    from app.models.program_models import HandoffSheetRow, ProgramConversation
    from app.models.reset_models import AppointmentNotification, AppointmentSurvey
    from app.models.work_models import LeadNote
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_SHEET_TEST_UNDO_KEY)
    done = {}
    b = db.query(BookingLink).filter(BookingLink.id == TEST_BOOKING_ID).first()
    if b is not None and b.status == "completed":
        item("booking_link", b.id, "undo_test", {"status": b.status, "confirmed_at": b.confirmed_at,
                                                 "completed_at": b.completed_at}, {"status": "booked"},
             "owner Nov 22: undo sheet test")
        b.status, b.completed_at, b.confirmed_at = "booked", None, None
        b.attendance_recorded_at, b.attendance_recorded_by = None, None
        done["appointment"] = "back to booked, not confirmed"
    n = 0
    for r in (db.query(AppointmentNotification).filter(AppointmentNotification.booking_id == TEST_BOOKING_ID,
                                                       AppointmentNotification.purpose == "survey",
                                                       AppointmentNotification.status.in_(("scheduled", "retry"))).all()):
        item("appointment_notification", r.id, "undo_test", {"status": r.status}, {"status": "superseded"},
             "owner Nov 22: undo sheet test")
        r.status, r.reason = "superseded", "owner's sheet test undone - never sent"
        n += 1
    done["surveys_stopped"] = n
    sv = db.query(AppointmentSurvey).filter(AppointmentSurvey.id == TEST_SURVEY_ID).first()
    if sv is not None:
        item("appointment_survey", sv.id, "undo_test", {"booking_id": sv.booking_id}, None, "owner Nov 22: undo sheet test")
        db.delete(sv)
    c = db.query(ProgramConversation).filter(ProgramConversation.id == TEST_CONV_ID).first()
    if c is not None and c.state == "closed":
        item("program_conversation", c.id, "undo_test", {"state": c.state, "handoff_reason": c.handoff_reason},
             {"state": "human"}, "owner Nov 22: undo sheet test")
        c.state, c.handoff_reason = "human", TEST_CONV_REASON
        done["follow_up"] = "open again (needs a person)"
    notes = db.query(LeadNote).filter(LeadNote.id.in_(TEST_NOTE_IDS)).all()
    for nt in notes:
        item("lead_note", nt.id, "undo_test", {"body": nt.body}, None, "owner Nov 22: undo sheet test")
        db.delete(nt)
    done["notes_removed"] = len(notes)
    for r in db.query(HandoffSheetRow).filter(HandoffSheetRow.organization_id == org_id,
                                              HandoffSheetRow.row_id.in_((TEST_BOOKING_ID, "FU-" + TEST_CONV_ID))).all():
        r.assigned_to, r.auto_assigned, r.assigned_at, r.staff_changed_at = None, False, None, None
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(done)
    db.commit()
    return done


def restland_followups_acks(db: Session, prog, now: datetime) -> Dict:
    """Second pass of the Nov 22 restore: families whose only later reply was
    an acknowledgement ("👍") also go back on the follow-up list."""
    from app.models.program_models import ProgramConversation, ProgramConversationTurn
    from app.services.programs import holidays as H
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_2_KEY)
    back = []
    for c in (db.query(ProgramConversation)
              .filter(ProgramConversation.organization_id == org_id,
                      ProgramConversation.next_followup_at.isnot(None),
                      ProgramConversation.next_followup_at > now,
                      ProgramConversation.state.in_(("human", "ai_active"))).all()):
        turns = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == c.id)
                 .order_by(ProgramConversationTurn.created_at).all())
        fu = [t for t in turns if t.role == "ai" and t.action == "follow_up_later"]
        if not fu:
            continue
        later = [t for t in turns if t.role == "family" and t.created_at > fu[-1].created_at]
        if not later or not all(_ACK.match(t.text or "") for t in later):
            continue
        when = c.next_followup_at
        new_when = datetime.combine(H.next_working_day(when.date()), when.time())
        item("program_conversation", c.id, "follow_up_list",
             {"state": c.state, "next_followup_at": when}, {"state": "waiting_future", "next_followup_at": new_when},
             "owner Nov 22: an acknowledgement keeps the agreed follow-up")
        c.state, c.next_followup_at, c.followups_sent = "waiting_future", new_when, 0
        back.append({"lead_id": c.lead_id, "follow_up_at": new_when.isoformat()})
    summary = {"follow_ups_restored": back}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_followups_and_remote(db: Session, prog, now: datetime) -> Dict:
    from app.models.program_models import LocationProfile, ProgramConversation, ProgramConversationTurn
    from app.services.programs import booking_policy as BP
    from app.services.programs import holidays as H
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, RESTLAND_HANDOFF_KEY)
    # 1. the remote-call booking choice
    pol = json.loads(prog.booking_policy_json or "{}")
    opts = pol.get("meeting_options")
    added_remote = False
    if isinstance(opts, list) and opts and not any(o.get("key") == BP.REMOTE for o in opts):
        item("outreach_program", prog.id, "meeting_options", list(opts), None, "owner Nov 22: remote calls")
        pol["meeting_options"] = list(opts) + [dict(BP.REMOTE_OPTION)]
        prog.booking_policy_json = json.dumps(pol)
        added_remote = True
    if not (pol.get("handoff_sheet") or {}).get("id"):
        item("outreach_program", prog.id, "handoff_sheet", pol.get("handoff_sheet"), None, "owner Nov 22: handoff board")
        pol["handoff_sheet"] = {"id": RESTLAND_HANDOFF_SHEET_ID}
        prog.booking_policy_json = json.dumps(pol)
    phone_on = 0          # superseded the same evening: calls only on the family's request
    # 2. later follow-ups back on the follow-up list
    back = []
    convs = (db.query(ProgramConversation)
             .filter(ProgramConversation.organization_id == org_id,
                     ProgramConversation.next_followup_at.isnot(None),
                     ProgramConversation.state.in_(("human", "ai_active"))).all())
    for c in convs:
        turns = (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == c.id)
                 .order_by(ProgramConversationTurn.created_at).all())
        fu = [t for t in turns if t.role == "ai" and t.action == "follow_up_later"]
        if not fu:
            continue
        if any(t.role == "family" and t.created_at > fu[-1].created_at and not _ACK.match(t.text or "")
               for t in turns):
            continue                                   # they wrote again since: that reply rules
        when = c.next_followup_at
        day = H.next_working_day(when.date())
        new_when = datetime.combine(day, when.time())
        item("program_conversation", c.id, "follow_up_list",
             {"state": c.state, "next_followup_at": when}, {"state": "waiting_future", "next_followup_at": new_when},
             "owner Nov 22: never lose 'after the holidays'")
        c.state, c.next_followup_at, c.followups_sent = "waiting_future", new_when, 0
        back.append({"lead_id": c.lead_id, "follow_up_at": new_when.isoformat()})
    summary = {"remote_choice_added": added_remote, "locations_phone_on": phone_on, "follow_ups_restored": back}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_nsmg_replies(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import Lead
    from app.models.work_models import LeadNote
    from app.models.program_models import ProgramResponse
    from app.services import cadence_service as CS
    from app.services.programs import ai_setter
    from app.services.programs import responses as R
    org_id = prog.organization_id
    run, item = _open_run(db, org_id, NSMG3_KEY)
    out: Dict = {"entered": [], "missing": []}
    for lead_id, why, said, received in NSMG3:
        lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
        if lead is None:
            out["missing"].append(lead_id)
            continue
        rid = "manual-email:nov24:%s" % lead.id
        if db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                            ProgramResponse.reply_id == rid).first() is not None:
            continue
        conv = ai_setter.conversation_for(db, lead)
        ai_setter._turn(db, conv, "family", said, action="received",
                        reason="email reply that reached Mike's NSMG inbox, entered Nov 24")
        R.on_inbound(db, lead, said, "email", reply_id=rid, now=received, staff_alerts=True, run_setter=False)
        before = {"state": conv.state, "handoff_reason": conv.handoff_reason}
        conv.state, conv.handoff_reason, conv.next_followup_at = "human", why[:250], None
        item("program_conversation", conv.id, "nsmg_reply", before, {"state": "human", "why": why},
             "owner Nov 24: emailed reply entered")
        if lead.status in ("new", "sent"):
            lead.status = "replied"
        CS.stop_cadence_for_lead(db, lead.id, "stopped_replied")
        db.add(LeadNote(organization_id=org_id, lead_id=lead.id, kind="internal",
                        body=("Owner, Nov 24: their email reply reached Mike's NSMG inbox, not the system - "
                              "entered by hand. They said: %s" % said)[:4000]))
        db.commit()
        out["entered"].append(lead_id)
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(out)
    db.commit()
    return out


def restland_david_phone(db: Session, prog, now: datetime) -> Dict:
    from app.models.models import BookingLink, Lead
    from app.services.programs import booking_policy as BP
    run, item = _open_run(db, prog.organization_id, DAVID_KEY)
    b = (db.query(BookingLink).join(Lead, Lead.id == BookingLink.lead_id)
         .filter(BookingLink.id == DAVID_BOOKING, Lead.organization_id == prog.organization_id).first())
    summary = {"changed": False}
    if b is not None and (b.status or "") in ("booked", "confirmed") and b.meeting_type != BP.PHONE:
        before = {"meeting_type": b.meeting_type, "appt_label": b.appt_label}
        b.meeting_type, b.visit_address = BP.PHONE, None
        b.appt_label = BP.PHONE_OPTION["appt_label"]
        item("booking_link", b.id, "meeting_type", before,
             {"meeting_type": b.meeting_type, "appt_label": b.appt_label}, "owner Nov 24: David asked for a call")
        summary = {"changed": True}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def restland_morning_reminders(db: Session, prog, now: datetime) -> Dict:
    from app.services.programs import appointment_notifications as AN
    run, item = _open_run(db, prog.organization_id, MORNING_KEY)
    res = AN.replan_reminders(db, prog.organization_id, now=now)
    for c in res["changed"]:
        item("appointment_notification", c["id"], "reminder_timing", c["before"], c["after"],
             "owner Nov 24: 8:30 AM morning-of reminder")
    summary = {"reminders_replanned": len(res["changed"])}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def sci_full_speed(db: Session, prog) -> Dict:
    run, item = _open_run(db, prog.organization_id, SCI_KEY)
    _batch(prog, item, SCI_DAILY, SCI_SPACING, "owner Nov 21: SCI full blast")
    summary = {"daily_batch": SCI_DAILY, "spacing": SCI_SPACING}
    run.status, run.finished_at, run.summary_json = "applied", datetime.utcnow(), json.dumps(summary)
    db.commit()
    return summary


def apply_pending(db: Session, now: Optional[datetime] = None) -> Dict:
    """Run every owner change not yet applied. Never raises."""
    from app.models.program_models import OutreachProgram
    from app.services import sms_programs
    from app.services.programs import outreach_slots as OS
    now = now or datetime.utcnow()
    out: Dict = {}
    if now >= max(APPLY_UNTIL, SUNDAYS_APPLY_UNTIL, MORNING_UNTIL, DAVID_UNTIL, NSMG3_UNTIL):
        return out
    for prog in db.query(OutreachProgram).all():
        try:
            if sms_programs.is_sci_org(db, prog.organization_id):
                if now < APPLY_UNTIL and _ledger(db, prog.organization_id, SCI_KEY) is None:
                    out[prog.organization_id] = sci_full_speed(db, prog)
            elif OS.config_from_policy(prog.booking_policy_json) is not None:
                res = {}
                if now < APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_KEY) is None:
                    res["full_speed"] = restland_full_speed(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_SUNDAYS_KEY) is None:
                    res["sundays_off"] = restland_sundays_off(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_KEY) is None:
                    res["handoff"] = restland_followups_and_remote(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_2_KEY) is None:
                    res["handoff_acks"] = restland_followups_acks(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_HANDOFF_3_KEY) is None:
                    res["jan4_remote"] = restland_jan4_and_remote_on_request(db, prog, now)
                if now < SUNDAYS_APPLY_UNTIL and _ledger(db, prog.organization_id, RESTLAND_SHEET_TEST_UNDO_KEY) is None:
                    res["sheet_test_undo"] = restland_sheet_test_undo(db, prog, now)
                if now < CARMELITA_UNTIL and _ledger(db, prog.organization_id, CARMELITA_KEY) is None:
                    res["carmelita_3pm"] = restland_book_carmelita(db, prog, now)
                if now < EMAILED_UNTIL and _ledger(db, prog.organization_id, EMAILED_KEY) is None:
                    res["emailed_replies"] = restland_emailed_replies(db, prog, now)
                if now < ANNE_UNTIL and _ledger(db, prog.organization_id, ANNE_KEY) is None:
                    res["anne_phone"] = restland_anne_phone(db, prog, now)
                if now < REPLIES2_UNTIL and _ledger(db, prog.organization_id, REPLIES2_KEY) is None:
                    res["emailed_replies_2"] = restland_emailed_replies_2(db, prog, now)
                if now < TWO_UNTIL and _ledger(db, prog.organization_id, TWO_KEY) is None:
                    res["belinda_patrick"] = restland_belinda_patrick(db, prog, now)
                if now < CARMELITA_330_UNTIL and _ledger(db, prog.organization_id, CARMELITA_330_KEY) is None:
                    res["carmelita_330pm"] = restland_book_carmelita(db, prog, now, CARMELITA_330_KEY,
                                                                     CARMELITA_330_SLOT)
                if now < NSMG3_UNTIL and _ledger(db, prog.organization_id, NSMG3_KEY) is None:
                    res["nsmg_replies"] = restland_nsmg_replies(db, prog, now)
                if now < DAVID_UNTIL and _ledger(db, prog.organization_id, DAVID_KEY) is None:
                    res["david_phone"] = restland_david_phone(db, prog, now)
                if now < MORNING_UNTIL and _ledger(db, prog.organization_id, MORNING_KEY) is None:
                    res["morning_reminders"] = restland_morning_reminders(db, prog, now)
                if res:
                    out[prog.organization_id] = res
        except Exception as exc:                         # noqa: BLE001 - never stops a deploy
            db.rollback()
            log.exception("owner change failed for program %s", prog.id)
            out[prog.organization_id] = {"error": type(exc).__name__}
    return out
