"""Follow-up emails read like one person writing to another, and every campaign's first email passes."""
from app.services.programs import message_brain as brain


def _packet(**over):
    p = {"first_name": "Ollie", "kerry": "Kerry Allan", "location": "Eastern Gate Memorial Gardens",
         "campaign_family": "veteran_planning_guide", "campaign_active": True, "sender_healthy": True,
         "display_name": "Kerry Allan | Eastern Gate Memorial Gardens", "responses": [],
         "on_hold": False, "in_review": False, "suppressed": False, "appointment_booked": False,
         "postal": "Eastern Gate Memorial Gardens, 1 Test Way, Testville, FL 32500"}
    p.update(over)
    return p


def test_subjects_are_personal_and_distinct():
    subs = [brain.compose(_packet(), t, "email")["subject"] for t in (1, 2, 3, 4, 5)]
    assert subs[0] == "Following up, Ollie" and len(set(subs)) == 5
    for s in subs:
        assert "quick tip" not in s.lower() and "why families" not in s.lower()
    no_name = brain.compose(_packet(first_name=None), 1, "email")["subject"]
    assert no_name == "Following up"


def test_every_campaign_passes_every_touch():
    for fam in ("veteran_planning_guide", "veteran_official", "veteran_spanish", "general_survey",
                "life_story", "cemetery_x_sell", "cremation", "re_engagement", "seminar", "web_lead"):
        bodies = []
        for t in (1, 2, 3, 4, 5):
            m = brain.compose(_packet(campaign_family=fam), t, "email")
            q = brain.quality(_packet(campaign_family=fam), m, prior_bodies=bodies)
            assert q["ok"], (fam, t, q)
            bodies.append(m["body"])


def test_seminar_and_web_lead_first_emails_say_why():
    for fam, why in (("seminar", "you signed up for one of our planning seminars"),
                     ("web_lead", "you asked for information on our website")):
        msg = {"touch": 1, "channel": "email", "subject": "Following up, Ollie",
               "body": ("Hi Ollie,\n\nIt's Kerry Allan at Eastern Gate Memorial Gardens. A while back %s, "
                        "and I want to make sure it actually got to you.\n\nWould you like me to send it "
                        "over? Just reply yes and I'll take care of it.\n\nKerry Allan" % why)}
        q = brain.quality(_packet(campaign_family=fam), msg)
        assert q["ok"], (fam, q)
