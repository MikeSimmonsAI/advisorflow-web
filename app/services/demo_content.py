"""WHAT THE PRESENTER SHOWS, SAYS AND CLICKS — the demo's script, in one file.

THE PROBLEM THIS SOLVES

Mike attends sales meetings he has no commercial reason to attend, because he
is the only person who can drive the software and answer "what does that
button do". The salesperson runs the relationship; the product knowledge is
locked in one head.

So the product carries its own explanation. A guided scenario says what to
show, where to click, what will happen, why the prospect should care, and a
sentence the presenter can say out loud. A help topic answers "what is this"
for any feature the presenter is asked about. Neither is decoration: they are
the deliverable, because the deliverable is a demo somebody else can run.

TWO RULES THIS FILE IS HELD TO

1. EVERY CLAIM IS TRUE OF THE SHIPPED PRODUCT. No step describes a feature
   that does not exist, and no talking point promises behaviour the platform
   does not have. A demo that oversells is not a demo, it is a future support
   ticket with a signature on it.

2. EVERY STEP THAT SAYS SOMETHING HAPPENS RUNS A REAL ACTION. A step with an
   `action` key drives `demo_actions`, which writes to the demo tenant's real
   tables. A step without one is a step where the presenter narrates something
   already on screen — and it says so, rather than pretending to do work.

WHY THE CONTENT IS CODE

The alternative is a content table, an editor, a draft state, and a story that
can silently reference a screen somebody renamed. In code, a step that names a
panel is checked by the same review that would catch the panel being renamed,
and adding a scenario is a pull request. If this ever needs editing without a
deploy, the tables under it do not change — only where this dict is loaded from.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# The panels the Demo Suite renders. A step names one of these, and the UI
# brings it forward. Deliberately a short closed vocabulary: a step that could
# name any route would eventually name a route that no longer exists.
PANEL_PRIORITY = "priority"        # AI-prioritised work queue
PANEL_LEADS = "leads"              # the lead list
PANEL_CONVERSATION = "conversation"  # one lead's message thread
PANEL_CALENDAR = "calendar"        # appointments
PANEL_PIPELINE = "pipeline"        # opportunity board
PANEL_TEAM = "team"                # manager's team view
PANEL_REVENUE = "revenue"          # executive revenue and performance
PANEL_CUSTOMERS = "customers"      # executive customer portfolio
PANEL_LAUNCH = "launch"            # implementation / customer launch

PANELS = {
    PANEL_PRIORITY: "Today's Priorities",
    PANEL_LEADS: "Leads",
    PANEL_CONVERSATION: "Conversation",
    PANEL_CALENDAR: "Calendar",
    PANEL_PIPELINE: "Pipeline",
    PANEL_TEAM: "Team",
    PANEL_REVENUE: "Revenue & Performance",
    PANEL_CUSTOMERS: "Customers",
    PANEL_LAUNCH: "Customer Launch",
}

# Who a scenario is for. Used to sort the catalogue, never to authorise it —
# entitlement is `demo_suite`, and a rep may legitimately want to show the
# executive view to a prospect's CFO.
AUDIENCE_OPERATOR = "operator"      # the customer's own front-line team
AUDIENCE_MANAGER = "manager"
AUDIENCE_EXECUTIVE = "executive"
AUDIENCE_IMPLEMENTATION = "implementation"


def _step(key: str, label: str, panel: str, show: str, click: str,
          happens: str, matters: str, notice: str, says: str,
          action: Optional[Dict[str, Any]] = None,
          deep_dive: Optional[str] = None,
          help_key: Optional[str] = None) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "panel": panel,
        "panel_label": PANELS.get(panel, panel),
        "what_we_show": show,
        "where_to_click": click,
        "what_happens": happens,
        "why_it_matters": matters,
        "prospect_should_notice": notice,
        "presenter_says": says,
        # None means "narrate what is already on screen". The UI renders those
        # steps without a primary action button rather than with a dead one.
        "action": action,
        "deep_dive": deep_dive,
        "help_key": help_key,
    }


SCENARIOS: List[Dict[str, Any]] = [
    {
        "key": "lead_to_appointment",
        "name": "Lead → AI → Appointment",
        "audience": AUDIENCE_OPERATOR,
        "minutes": 8,
        "summary": "The core story. An enquiry arrives, the system decides it "
                   "is the most important thing in the building, the first "
                   "reply goes out, the family answers, and a consultation is "
                   "on the calendar — without anybody deciding who to call.",
        "opening": "I want to show you what happens to one enquiry, start to "
                   "finish, and how much of it your team has to decide.",
        "closing": "That whole path took four clicks. The part your team used "
                   "to do — reading the list and choosing who mattered — is "
                   "the part the system did.",
        "steps": [
            _step("open_priority", "Open today's priorities", PANEL_PRIORITY,
                  show="The work queue as an advisor sees it at 8am.",
                  click="The Today's Priorities panel — it is already open.",
                  happens="Nothing yet. This is the starting state.",
                  matters="Every list in every CRM looks like this. The "
                          "difference is the order, and the order is the "
                          "product.",
                  notice="Nobody sorted this list. It arrived sorted.",
                  says="This is what your advisor opens in the morning. The "
                       "order isn't alphabetical and it isn't newest-first — "
                       "it's what needs a human today.",
                  help_key="ai_prioritisation"),
            _step("new_enquiry", "A new enquiry lands", PANEL_LEADS,
                  show="Terrence Blakely's enquiry, submitted through the "
                       "website form, with nobody assigned and no reply sent.",
                  click="Terrence Blakely in the Leads panel.",
                  happens="The record opens. Note the enquiry has never been "
                          "answered.",
                  matters="This is the lead that gets lost. It arrived outside "
                          "office hours, and by Monday the family has called "
                          "somebody else.",
                  notice="No response has gone out, and the record says so "
                         "plainly rather than looking the same as every other "
                         "row.",
                  says="Here's the one that costs you money — an enquiry that "
                       "came in when nobody was at a desk.",
                  help_key="speed_to_lead"),
            _step("qualify", "Qualify and prioritise it", PANEL_PRIORITY,
                  show="The system evaluating the enquiry against the rules "
                       "this organisation set.",
                  click="Qualify & Prioritise.",
                  happens="The lead is scored, given a priority band, and "
                          "moved to the top of the queue. Anything the rules "
                          "exclude — a do-not-contact, a bad number — is "
                          "excluded here and shown as excluded.",
                  matters="A salesperson deciding who to call next is a "
                          "salesperson not calling anybody.",
                  notice="Harold Mbeki does not move. He replied STOP, and no "
                         "amount of urgency overrides that.",
                  says="It just read every enquiry against your rules and put "
                       "the ones that need a person at the top. And notice "
                       "what it refused to touch.",
                  action={"action": "qualify_lead", "target": "terrence"},
                  deep_dive="Qualification rules are per organisation. Hard "
                            "exclusions — do-not-contact, suppression, missing "
                            "consent — are evaluated before scoring, so a "
                            "high-value lead can never be scored past a "
                            "compliance rule.",
                  help_key="ai_prioritisation"),
            _step("first_reply", "Send the first response", PANEL_CONVERSATION,
                  show="The outbound message, composed from the organisation's "
                       "own template, in the advisor's own voice.",
                  click="Send first response.",
                  happens="The message is written to the conversation and the "
                          "lead moves to Contacted. In this demonstration "
                          "nothing leaves the building — the record is created "
                          "exactly as it would be, and no carrier is called.",
                  matters="Five minutes versus next morning is the difference "
                          "between this family and the next funeral home.",
                  notice="The advisor didn't write it, and it doesn't read "
                         "like a robot wrote it either.",
                  says="That's the first reply out. In production this is an "
                       "actual text message; here it's simulated, so nobody's "
                       "phone rings during our meeting.",
                  action={"action": "send_sms", "target": "terrence"},
                  help_key="simulated_sends"),
            _step("family_replies", "The family answers", PANEL_CONVERSATION,
                  show="An inbound reply arriving on the thread.",
                  click="Simulate the family's reply.",
                  happens="An inbound message is recorded, the lead's "
                          "engagement moves to Hot, and it surfaces in the "
                          "advisor's queue as needing a person.",
                  matters="Replies are the only signal that matters, and they "
                          "are the thing most systems bury in a shared inbox.",
                  notice="The lead changed state on its own the moment the "
                         "reply landed.",
                  says="Someone replied. Watch what the system does with that "
                       "without anybody touching it.",
                  action={"action": "simulate_reply", "target": "terrence"},
                  help_key="engagement_temperature"),
            _step("ai_draft", "AI drafts the follow-up", PANEL_CONVERSATION,
                  show="A suggested reply, with the advisor's confirmation "
                       "still required.",
                  click="Draft a follow-up.",
                  happens="A draft is written to the thread as a suggestion. "
                          "It is not sent. A person still has to agree.",
                  matters="The value is the draft, not the autonomy. Nobody "
                          "wants a machine talking to a grieving family "
                          "unsupervised, and this one does not.",
                  notice="It stops and waits. The confirmation gate is a "
                         "product decision, not a limitation.",
                  says="It writes the reply; your advisor approves it. We "
                       "deliberately don't let it send on its own.",
                  action={"action": "ai_follow_up", "target": "terrence"},
                  deep_dive="Auto-send has an advisor confirmation gate and a "
                            "compliance preflight in front of every send path. "
                            "Both are server-side.",
                  help_key="ai_drafting"),
            _step("book", "Book the consultation", PANEL_CALENDAR,
                  show="The appointment being created against the advisor's "
                       "real availability.",
                  click="Book consultation.",
                  happens="An appointment is created for Terrence with a real "
                          "time, a real duration and a real owner, and appears "
                          "on the calendar.",
                  matters="This is the outcome the customer is buying. "
                          "Everything before it is how they get here.",
                  notice="From an unanswered form to a booked consultation, "
                         "nobody chose who to call.",
                  says="And that's the appointment. That's the whole point of "
                       "the system — not the messages, the appointment.",
                  action={"action": "book_appointment", "target": "terrence"},
                  help_key="appointments"),
        ],
    },
    {
        "key": "reactivation",
        "name": "Old lead → Reactivation → Appointment",
        "audience": AUDIENCE_OPERATOR,
        "minutes": 6,
        "summary": "The database the customer already owns. A lead that went "
                   "quiet fourteen months ago is re-engaged and books.",
        "opening": "Before we talk about new enquiries — how many old ones are "
                   "sitting in your system right now?",
        "closing": "You paid for those leads once. This is the second time "
                   "they earn.",
        "steps": [
            _step("dormant", "Find the dormant book", PANEL_LEADS,
                  show="Leads with no contact for over a year, filtered out of "
                       "the noise.",
                  click="The Leads panel, Dormant filter.",
                  happens="The list narrows to the leads nobody has touched.",
                  matters="Every customer has this list and nobody works it, "
                          "because working it by hand is nobody's job.",
                  notice="These are their own leads. Nothing was bought.",
                  says="These came from your own seminars and your own "
                       "website. They're paid for. They're just cold.",
                  help_key="reactivation"),
            _step("qualify_old", "Qualify the dormant leads", PANEL_PRIORITY,
                  show="The same qualification engine, run over old records.",
                  click="Qualify & Prioritise.",
                  happens="Yusuf Demir is scored and surfaced; the records "
                          "that must not be contacted are excluded and the "
                          "reason is shown.",
                  matters="Age is not the same as worthlessness, and the "
                          "system can tell the difference between the two.",
                  notice="It excluded records rather than quietly skipping "
                         "them, and it says why.",
                  says="Same engine, run backwards over your history.",
                  action={"action": "qualify_lead", "target": "yusuf"},
                  help_key="ai_prioritisation"),
            _step("reengage", "Send the re-engagement message", PANEL_CONVERSATION,
                  show="A message written for somebody who has not heard from "
                       "this business in over a year.",
                  click="Send first response.",
                  happens="The message is recorded on Yusuf's thread and his "
                          "status moves to Contacted.",
                  matters="The wording is the whole game on a cold record, and "
                          "it is a template the customer controls.",
                  notice="It does not pretend to be a continuing conversation.",
                  says="Note the tone — it acknowledges the gap instead of "
                       "pretending we spoke last week.",
                  action={"action": "send_sms", "target": "yusuf"},
                  help_key="simulated_sends"),
            _step("reply_old", "A reply comes back", PANEL_CONVERSATION,
                  show="The inbound reply and the state change it causes.",
                  click="Simulate the family's reply.",
                  happens="An inbound message is recorded and the lead becomes "
                          "Hot.",
                  matters="On a dormant book, a single reply pays for the "
                          "month.",
                  notice="Nothing was re-imported. It was always in there.",
                  says="One reply out of a list nobody was working.",
                  action={"action": "simulate_reply", "target": "yusuf"}),
            _step("book_old", "Book the appointment", PANEL_CALENDAR,
                  show="A consultation created from a fourteen-month-old lead.",
                  click="Book consultation.",
                  happens="The appointment is created and appears on the "
                          "calendar.",
                  matters="This is the number to put in the business case.",
                  notice="The lead was free. The appointment is not.",
                  says="That appointment came out of a list you already owned.",
                  action={"action": "book_appointment", "target": "yusuf"},
                  help_key="appointments"),
        ],
    },
    {
        "key": "new_lead_qualification",
        "name": "New lead → Qualification → Follow-up",
        "audience": AUDIENCE_OPERATOR,
        "minutes": 5,
        "summary": "For a prospect who wants to understand the rules engine "
                   "rather than watch a happy path.",
        "opening": "Let me show you what the system refuses to do, because "
                   "that's usually the question behind the question.",
        "closing": "The rules are yours. The enforcement is ours, and it is on "
                   "the server, not in the browser.",
        "steps": [
            _step("show_rules", "The compliance boundary", PANEL_LEADS,
                  show="Harold Mbeki, who replied STOP, sitting in the list "
                       "and marked.",
                  click="Harold Mbeki in the Leads panel.",
                  happens="The record opens showing do-not-contact status.",
                  matters="Every customer has been frightened by a compliance "
                          "story. Show them the boundary before they ask.",
                  notice="He is still visible. He is simply unreachable.",
                  says="He replied STOP. He's still in your database — you "
                       "just can't message him, and neither can we.",
                  help_key="compliance"),
            _step("qualify_all", "Run qualification across the book",
                  PANEL_PRIORITY,
                  show="The engine evaluating every lead at once.",
                  click="Qualify & Prioritise.",
                  happens="Leads are banded by priority; excluded records are "
                          "reported separately with their reason.",
                  matters="The exclusion report is the trust-builder. It shows "
                          "the system knows what it is not allowed to do.",
                  notice="Exclusions are reported, not hidden.",
                  says="Notice it tells you what it excluded and why, instead "
                       "of quietly dropping them.",
                  action={"action": "qualify_all"},
                  help_key="ai_prioritisation"),
            _step("follow_up", "Follow up the top of the list",
                  PANEL_CONVERSATION,
                  show="A drafted follow-up on the highest-priority record.",
                  click="Draft a follow-up.",
                  happens="A suggested reply is written to the thread and left "
                          "for approval.",
                  matters="Prioritisation without a next action is a report. "
                          "This is a queue.",
                  notice="The queue produced work, not a chart.",
                  says="It doesn't just rank them — it gives your advisor the "
                       "next thing to send.",
                  action={"action": "ai_follow_up", "target": "alice"},
                  help_key="ai_drafting"),
        ],
    },
    {
        "key": "manager_day",
        "name": "Sales manager → Team → Pipeline → Next action",
        "audience": AUDIENCE_MANAGER,
        "minutes": 6,
        "summary": "What a manager sees, and what they do about it. For the "
                   "person in the room who runs people rather than deals.",
        "opening": "You've seen what an advisor does. This is what you'd see.",
        "closing": "You didn't ask anybody for a status update to get any of "
                   "that.",
        "steps": [
            _step("team", "The team, this morning", PANEL_TEAM,
                  show="Each salesperson, their open work and what is overdue.",
                  click="The Team panel.",
                  happens="The team view loads from live pipeline data.",
                  matters="Managers spend Monday asking three people for "
                          "numbers they could have read.",
                  notice="Nobody typed this in. It is the deals.",
                  says="This is your Monday morning, without the meeting.",
                  help_key="manager_view"),
            _step("pipeline", "The pipeline board", PANEL_PIPELINE,
                  show="Every open deal by stage, with time in stage.",
                  click="The Pipeline panel.",
                  happens="The board renders each stage in order with the "
                          "deals in it.",
                  matters="Time in stage is where deals die, and it is the "
                          "number nobody keeps by hand.",
                  notice="Trellis Home Care has been in Prospect for eleven "
                         "days with nothing attempted.",
                  says="Look at Trellis — eleven days, no contact. That's the "
                       "conversation you'd want to have today.",
                  help_key="pipeline_stages"),
            _step("move", "Move a deal forward", PANEL_PIPELINE,
                  show="A stage change and the timeline entry it writes.",
                  click="Move Cordova Dental to Demo / Proposal.",
                  happens="The opportunity's stage changes, its time-in-stage "
                          "clock resets, and an event is appended to the "
                          "deal's timeline.",
                  matters="One continuous record from prospect to live "
                          "customer — the salesperson never re-creates the "
                          "deal to move it.",
                  notice="The history is appended to, never overwritten.",
                  says="One record, start to finish. Nothing gets re-keyed "
                       "when a deal progresses.",
                  action={"action": "move_stage", "target": "cordova",
                          "to_stage": "demo_proposal"},
                  help_key="pipeline_stages"),
            _step("next_action", "Clear the next action", PANEL_TEAM,
                  show="A due next action being completed.",
                  click="Complete on Halverson Roofing's next action.",
                  happens="The action is marked done, an event is written, and "
                          "the deal drops off the overdue list.",
                  matters="A pipeline that does not produce a to-do list is a "
                          "spreadsheet with colours.",
                  notice="The overdue count changed as a result.",
                  says="Every deal carries the next thing that has to happen. "
                       "That's what makes this a queue instead of a report.",
                  action={"action": "complete_task", "target": "halverson"},
                  help_key="next_actions"),
        ],
    },
    {
        "key": "executive_view",
        "name": "Executive → Revenue → Performance → Customers",
        "audience": AUDIENCE_EXECUTIVE,
        "minutes": 5,
        "summary": "For the owner or the CFO. No lead lists, no message "
                   "threads — money, portfolio and who needs attention.",
        "opening": "You wouldn't use the screens we've just been through. "
                   "This is yours.",
        "closing": "Same data, one query, no month-end.",
        "steps": [
            _step("revenue", "Revenue and performance", PANEL_REVENUE,
                  show="Pipeline value, weighted projection and closed "
                       "business, from the deals themselves.",
                  click="The Revenue & Performance panel.",
                  happens="The figures are computed from the demonstration "
                          "pipeline in front of you.",
                  matters="Executives do not want a CRM. They want the number "
                          "the CRM should already know.",
                  notice="Nothing here was entered twice.",
                  says="This isn't a report somebody builds. It's the deals, "
                       "added up.",
                  help_key="executive_authority"),
            _step("customers", "The customer portfolio", PANEL_CUSTOMERS,
                  show="Each customer, their state and whether they need "
                       "attention.",
                  click="The Customers panel.",
                  happens="The portfolio renders for the brand.",
                  matters="An executive sees exactly the organisations they "
                          "were assigned — the role is not the portfolio.",
                  notice="This is scoped. A second executive would see their "
                         "own list, not this one.",
                  says="Your executives see what you assign them. It isn't "
                       "all-or-nothing.",
                  help_key="executive_authority"),
        ],
    },
    {
        "key": "workspace_tour",
        "name": "Customer workspace → Leads → Communication → Calendar",
        "audience": AUDIENCE_OPERATOR,
        "minutes": 6,
        "summary": "The orientation tour, for a prospect who wants to see the "
                   "whole surface before they see a story.",
        "opening": "Let me walk you around before we do anything, so you know "
                   "what you're looking at.",
        "closing": "That's the whole surface your team touches. Three screens.",
        "steps": [
            _step("tour_leads", "Leads and contacts", PANEL_LEADS,
                  show="The full book, filterable, with ownership and state.",
                  click="The Leads panel.",
                  happens="The list renders.",
                  matters="Advisors live here. If this screen is wrong, "
                          "nothing else matters.",
                  notice="Every lead has an owner and a state — nothing is "
                         "unassigned by accident.",
                  says="This is where your team spends its day.",
                  help_key="lead_ownership"),
            _step("tour_conversation", "One conversation", PANEL_CONVERSATION,
                  show="A full thread — outbound, inbound and email, in one "
                       "timeline.",
                  click="Gloria Petrakis in the Leads panel.",
                  happens="The thread opens with its history.",
                  matters="One family, one thread. Not a shared inbox and a "
                          "separate text history.",
                  notice="Texts and emails are in the same story.",
                  says="Every channel with this family, in one place, in order.",
                  help_key="unified_thread"),
            _step("tour_calendar", "The calendar", PANEL_CALENDAR,
                  show="Booked consultations with owners and durations.",
                  click="The Calendar panel.",
                  happens="Appointments render for the workspace.",
                  matters="The appointment is the outcome; everything else is "
                          "the path to it.",
                  notice="Appointments are attached to the lead, not floating "
                         "in a separate calendar.",
                  says="And this is what all of it is for.",
                  help_key="appointments"),
        ],
    },
    {
        "key": "implementation_launch",
        "name": "Implementation → Customer launch",
        "audience": AUDIENCE_IMPLEMENTATION,
        "minutes": 4,
        "summary": "What happens after they sign. Answers the question every "
                   "serious buyer asks last.",
        "opening": "Say you sign. What actually happens on Monday?",
        "closing": "You are not handed a login and wished luck.",
        "steps": [
            _step("won", "The deal is won", PANEL_PIPELINE,
                  show="Meridian Senior Living, already through the far end of "
                       "the pipeline.",
                  click="Meridian Senior Living in the Pipeline panel.",
                  happens="The deal opens showing its full history through Won "
                          "and into Live.",
                  matters="The same record carries on past the sale. Nothing "
                          "is re-created for onboarding.",
                  notice="Won is a stage on the same record, not a new one.",
                  says="Notice the deal didn't end when it was won.",
                  help_key="pipeline_stages"),
            _step("launch", "The launch checklist", PANEL_LAUNCH,
                  show="The onboarding steps a new customer goes through, and "
                       "who owns each one.",
                  click="The Customer Launch panel.",
                  happens="The launch view renders for the won customer.",
                  matters="The buyer's real fear is being sold to and then "
                          "abandoned. This is the answer to it.",
                  notice="Every step has an owner and a state.",
                  says="This is what week one looks like, and who's doing "
                       "each part of it.",
                  help_key="customer_launch"),
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXTUAL HELP — "what does this do?", answered where the question is asked
#
# Five fields, always the same five, because a presenter under pressure needs
# to know where the sentence they want is going to be. The last one is the one
# that gets used most: something they can say out loud without translating.
# ─────────────────────────────────────────────────────────────────────────────

def _help(key: str, title: str, what_it_is: str, what_it_does: str,
          why_customer_cares: str, what_happens_next: str,
          presenter_says: str) -> Dict[str, Any]:
    return {
        "key": key, "title": title,
        "what_it_is": what_it_is,
        "what_it_does": what_it_does,
        "why_customer_cares": why_customer_cares,
        "what_happens_next": what_happens_next,
        "presenter_says": presenter_says,
    }


HELP_TOPICS: Dict[str, Dict[str, Any]] = {t["key"]: t for t in [
    _help("ai_prioritisation", "AI lead qualification and prioritisation",
          "A rules-and-scoring engine that reads every lead's available "
          "signals — source, recency, replies, tier, consent — and puts them "
          "in the order a person should work them.",
          "It evaluates hard exclusions first (do-not-contact, suppression, "
          "missing consent, bad contact details), then the organisation's own "
          "rules, then scores what is left into priority bands.",
          "A salesperson opening a list of four thousand leads and choosing "
          "who to call is a salesperson doing the least valuable minute of "
          "their day, every day.",
          "The queue is re-ordered and the excluded records are reported "
          "separately, with the reason for each exclusion.",
          "Instead of your advisor opening a giant list and guessing who to "
          "call, the system puts the right ones in front of them."),
    _help("speed_to_lead", "Speed to lead",
          "The elapsed time between an enquiry arriving and a human — or the "
          "system on a human's behalf — responding to it.",
          "Enquiries arriving from web forms, social lead forms and inbound "
          "calls are captured immediately and surfaced as needing a first "
          "response, including outside office hours.",
          "The business that answers first usually wins, and most enquiries "
          "arrive when nobody is at a desk.",
          "The enquiry is prioritised and a first response can go out without "
          "waiting for the next working day.",
          "The one that costs you money is the enquiry that came in at nine "
          "on a Saturday night."),
    _help("engagement_temperature", "Engagement temperature",
          "A hot / warm / cold classification driven by reply recency and "
          "engagement — deliberately separate from what kind of lead somebody "
          "is.",
          "It moves on its own as the conversation moves. A reply promotes a "
          "lead; a long silence cools it.",
          "It is the difference between 'this person is interested' and 'this "
          "person is the kind of lead we like', which most systems conflate.",
          "The lead's position in the work queue changes to match.",
          "It went hot on its own the second they replied. Nobody re-tagged "
          "anything."),
    _help("ai_drafting", "AI drafting with an advisor gate",
          "Suggested replies written by the system and held for a person to "
          "approve.",
          "It drafts the next message in the organisation's own voice using "
          "the thread's context, and stops. Sending requires a person, and "
          "every send path runs a compliance preflight on the server.",
          "Nobody wants an unsupervised machine talking to their customers — "
          "and nobody wants to write the same follow-up four hundred times "
          "either.",
          "The draft appears on the thread as a suggestion until somebody "
          "approves it.",
          "It writes it; your advisor approves it. We deliberately don't let "
          "it send on its own."),
    _help("simulated_sends", "Why nothing is actually sent in a demonstration",
          "A demonstration environment that creates the same records a real "
          "send creates, without calling a provider.",
          "A demo action writes the message row exactly as production would, "
          "marked as simulated. The outbound paths refuse a demonstration "
          "organisation outright, on the server, so a demo cannot make a phone "
          "ring even by mistake.",
          "Buyers ask this. The honest answer is better than a vague one.",
          "The conversation, the state change and the queue all behave "
          "identically. Only the carrier call is absent.",
          "In production that's a real text. Here it's simulated — I'm not "
          "going to make a stranger's phone ring in the middle of our "
          "meeting."),
    _help("appointments", "Appointments and booking",
          "A consultation attached to the lead it came from, with an owner, a "
          "type and a duration.",
          "Appointments are booked against a real advisor's availability, "
          "carry the appointment type the organisation defines, and drive "
          "confirmations and reminders.",
          "The appointment is the thing being bought. Messages are the path "
          "to it.",
          "The appointment appears on the calendar and on the lead's record, "
          "and reminder handling follows.",
          "That's the outcome — not the messages, the appointment."),
    _help("compliance", "Compliance and do-not-contact",
          "A set of boundaries the system enforces before any send: "
          "do-not-contact status, a suppression list, per-channel consent, "
          "and known-bad contact details.",
          "Every send path runs the same preflight. A do-not-contact record "
          "blocks every channel; a phone suppression blocks phone.",
          "One compliance incident costs more than the software does.",
          "The record stays visible and stays unreachable, with the reason on "
          "the record.",
          "He replied STOP. He's still in your database — you just can't "
          "message him, and neither can we."),
    _help("reactivation", "Reactivation of a dormant book",
          "Working leads the business already owns and has stopped "
          "contacting.",
          "The same qualification engine is run over historic records, and "
          "the ones worth a message are surfaced with wording appropriate to "
          "a long gap.",
          "Those leads are already paid for. It is the cheapest pipeline a "
          "business has.",
          "Re-engaged leads enter the ordinary queue and are worked like any "
          "other.",
          "You paid for those leads once. This is the second time they earn."),
    _help("lead_ownership", "Lead ownership and scope",
          "Who a lead belongs to, and who can see it.",
          "Every lead has an assigned owner. Being inside a workspace does "
          "not mean seeing everything in it — two advisors in one workspace "
          "each see their own book, and a manager sees the team's.",
          "Reps care that their pipeline is theirs. Owners care that nothing "
          "walks out the door.",
          "Scope is resolved on the server for every query, not by hiding "
          "rows in the browser.",
          "Being in the workspace and seeing everything in it are two "
          "different permissions here."),
    _help("unified_thread", "One family, one thread",
          "Every message with a contact — outbound text, inbound reply, "
          "email — in a single chronological record.",
          "Channels write to the same timeline, so the history is the "
          "history rather than three partial ones.",
          "Most teams reconstruct this by asking each other what happened.",
          "Anybody picking the record up sees the whole story without asking.",
          "Every channel with this family, in one place, in order."),
    _help("pipeline_stages", "Pipeline stages and time in stage",
          "One continuous commercial record from prospect through won, "
          "onboarding and live.",
          "Stages are ordered, so time-in-stage and forward or backward "
          "movement are real numbers, and every change appends to the deal's "
          "timeline rather than overwriting it.",
          "Deals die in a stage, not between them, and the age of a stalled "
          "deal is the number nobody keeps by hand.",
          "The stage changes, the clock resets, and the timeline records who "
          "moved it and when.",
          "One record, start to finish. Nothing gets re-keyed when a deal "
          "progresses."),
    _help("next_actions", "Next actions",
          "The single next thing that has to happen on a deal, with a due "
          "date.",
          "Next actions are derived from lifecycle state and due dates, and "
          "roll up into a manager's overdue view.",
          "A pipeline that does not produce a to-do list is a spreadsheet "
          "with colours.",
          "Completing one writes a timeline event and removes the deal from "
          "the overdue list.",
          "Every deal carries the next thing that has to happen."),
    _help("manager_view", "The manager's view",
          "Team, pipeline, activity and overdue work in one place, computed "
          "from the deals themselves.",
          "It reads the same records the reps work in, scoped to the "
          "manager's team.",
          "Managers spend Monday asking three people for numbers they could "
          "have read.",
          "The view updates as the team works — there is nothing to submit.",
          "This is your Monday morning, without the meeting."),
    _help("executive_authority", "Executive visibility",
          "A brand-level view of revenue, performance and the customer "
          "portfolio.",
          "An executive sees exactly the organisations they were assigned, "
          "inside exactly one brand. A role is not a portfolio, and the "
          "boundary is enforced on the server.",
          "White-label brands cannot survive an executive at one brand seeing "
          "another brand's customers.",
          "Assignments are made by the platform owner and take effect "
          "immediately.",
          "Your executives see what you assign them. It isn't "
          "all-or-nothing."),
    _help("customer_launch", "Customer launch",
          "The onboarding path a new customer goes through after a deal is "
          "won.",
          "The won deal carries into an implementation with named steps and "
          "owners, so the customer is not handed a login and left.",
          "The buyer's real fear is being sold to and then abandoned.",
          "The launch progresses through its steps and the salesperson can "
          "see where it is without asking.",
          "This is what week one looks like, and who's doing each part of "
          "it."),
    _help("demo_entitlement", "Who can run a demonstration",
          "A separate entitlement, granted per brand by the platform owner.",
          "Demo access lets somebody stand in the demonstration environment "
          "for one brand. It grants nothing else — no customer data, no "
          "platform administration, no access to another brand's demo.",
          "It is the mechanism that lets somebody other than the owner run "
          "the meeting.",
          "The owner grants or revokes it from God Mode, and it takes effect "
          "on the next request.",
          "I can run this demo. I can't see any of your data, and I couldn't "
          "if I wanted to."),
]}


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

def catalogue() -> List[Dict[str, Any]]:
    """The scenario list, without the step bodies. What the picker renders."""
    return [{
        "key": s["key"], "name": s["name"], "audience": s["audience"],
        "minutes": s["minutes"], "summary": s["summary"],
        "total_steps": len(s["steps"]),
        "panels": sorted({st["panel"] for st in s["steps"]}),
    } for s in SCENARIOS]


def get_scenario(key: str) -> Optional[Dict[str, Any]]:
    for s in SCENARIOS:
        if s["key"] == (key or "").strip():
            return s
    return None


def scenario_or_404(key: str) -> Dict[str, Any]:
    from fastapi import HTTPException
    s = get_scenario(key)
    if s is None:
        raise HTTPException(status_code=404, detail="No such demo scenario.")
    return s


def step_at(scenario: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
    steps = scenario.get("steps") or []
    if index < 0 or index >= len(steps):
        return None
    return steps[index]


def find_step(scenario: Dict[str, Any], step_key: str
              ) -> Optional[Dict[str, Any]]:
    for st in scenario.get("steps") or []:
        if st["key"] == step_key:
            return st
    return None


def help_topic(key: str) -> Optional[Dict[str, Any]]:
    return HELP_TOPICS.get((key or "").strip())


def all_help() -> List[Dict[str, Any]]:
    return [HELP_TOPICS[k] for k in sorted(HELP_TOPICS)]
