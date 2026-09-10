"""THE TRAINING PATHS. Content, in code, grounded in what the product does.

THE ONE RULE

    A path may only teach something the platform actually does.

Every step below names a real screen, a real capability or a real Demo Suite
scenario. There is no step about a feature on a roadmap, because a person who
finishes training believing in a feature that does not exist will promise it to
a customer within the week.

WHAT MAKES THIS NOT AN LMS

There is no authoring tool, no quiz engine, no score and no certificate. A path
is an ordered list of things to read and — where it matters — things to DO in
the Demo Suite. Completion is per step, so somebody coming back after a week is
put where they left off, and a manager asking "is the team ready" gets an
answer naming the step everybody is stuck on rather than an average.

PRACTICE IS THE POINT OF THE PRESENTER PATH

A step carrying `practice` is not complete until the person has actually run
that scenario in the demonstration environment. Reading about how to run a demo
produces somebody who has read about running a demo. The whole reason this
exists is to stop Mike attending sales meetings, and that is not achieved by
anybody's reading comprehension.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

AUDIENCE_EXECUTIVE = "executive"
AUDIENCE_MANAGER = "manager"
AUDIENCE_SALES = "salesperson"
AUDIENCE_WORKSPACE = "workspace"
AUDIENCE_IMPLEMENTATION = "implementation"
AUDIENCE_PRESENTER = "presenter"

AUDIENCE_LABELS = {
    AUDIENCE_EXECUTIVE: "Executive",
    AUDIENCE_MANAGER: "Sales Manager",
    AUDIENCE_SALES: "Salesperson",
    AUDIENCE_WORKSPACE: "Customer Workspace",
    AUDIENCE_IMPLEMENTATION: "Implementation",
    AUDIENCE_PRESENTER: "Demo Presenter",
}


def _s(key: str, title: str, body: str,
       practice: Optional[str] = None,
       help_key: Optional[str] = None,
       minutes: int = 3) -> Dict[str, Any]:
    return {"key": key, "title": title, "body": body,
            # A scenario key from demo_content. The step is not completable
            # until that scenario has actually been run.
            "practice_scenario": practice,
            "help_key": help_key, "minutes": minutes}


PATHS: List[Dict[str, Any]] = [
    {
        "key": "running_a_demo",
        "name": "Running a product demonstration",
        "audience": AUDIENCE_PRESENTER,
        "requires_demo": True,
        "summary": "Everything needed to run the standard demonstration "
                   "without the platform owner in the room.",
        "why": "This is the path that changes what the company can do. Until "
               "somebody other than the owner can run a demo, every meeting "
               "needs him.",
        "steps": [
            _s("what_you_are_selling", "What you are actually selling",
               "You are not selling a CRM. Prospects already have somewhere to "
               "keep names.\n\n"
               "You are selling two things:\n\n"
               "1. Nobody has to decide who to call. The system reads every "
               "enquiry against the customer's own rules and puts the ones "
               "that need a person at the top.\n"
               "2. The first response does not wait for office hours.\n\n"
               "Every part of the demo should come back to one of those two. "
               "If a feature you are showing does not, you are giving a "
               "product tour instead of a demonstration, and the difference "
               "is whether the prospect can repeat back why they would buy "
               "it.",
               help_key="ai_prioritisation"),
            _s("where_to_start", "Where to start, and what to open",
               "Open the Demo Suite, choose the brand you are presenting, and "
               "pick a scenario before the meeting starts — not during it.\n\n"
               "For a first meeting, use **Lead → AI → Appointment**. It is "
               "eight minutes, it ends on the outcome the customer is buying, "
               "and it needs no setup.\n\n"
               "If the room is the owner or a CFO rather than the people who "
               "would use it, use **Executive → Revenue → Performance → "
               "Customers** instead. They do not want to see a lead list.\n\n"
               "Check the environment says READY before the call. If it says "
               "anything else, ask an operator to rebuild it — that takes "
               "seconds and cannot be done mid-meeting."),
            _s("the_coach", "Using the presenter coach",
               "The coach panel travels with you through the scenario. For "
               "every step it gives you five things:\n\n"
               "* what you are showing\n"
               "* where to click\n"
               "* what will happen when you do\n"
               "* why the prospect should care\n"
               "* a sentence you can say out loud\n\n"
               "The last one is written to be said, not read. Say it in your "
               "own words if you prefer — but if you go blank, it is there.\n\n"
               "Collapse the coach when you want the prospect to see a clean "
               "screen. It remembers where you are."),
            _s("explain_this", "Answering \"what does that do?\"",
               "Every panel and most controls carry an **Explain this** "
               "affordance. It answers five questions in the same order every "
               "time: what it is, what it does, why the customer cares, what "
               "happens next, and a sentence you can say.\n\n"
               "Use it live. Opening it in front of a prospect is not a "
               "weakness — you are showing them the product explains itself, "
               "which is a feature of the product.\n\n"
               "What you must not do is guess. If you are asked something the "
               "help does not cover, say you will find out and move on. A "
               "wrong answer in a demo becomes a support ticket with a "
               "signature on it.",
               help_key="ai_drafting"),
            _s("practise_core", "Practise: Lead → AI → Appointment",
               "Run the whole scenario end to end in the demonstration "
               "environment. Click every action. Watch what changes on screen "
               "after each one.\n\n"
               "Do it twice. The second time, say the talking points out loud "
               "even though nobody is listening — the first time you say them "
               "should not be in front of a prospect.\n\n"
               "This step completes when the scenario is complete.",
               practice="lead_to_appointment"),
            _s("practise_reactivation", "Practise: reactivation",
               "Run **Old lead → Reactivation → Appointment**.\n\n"
               "This is the scenario that closes deals with prospects who "
               "already have a database, because it costs them nothing to "
               "test the claim. Know it as well as the first one.",
               practice="reactivation"),
            _s("what_not_to_promise", "What not to promise",
               "Say these plainly if they come up, and do not soften them:\n\n"
               "* **The AI does not send on its own.** There is an advisor "
               "confirmation gate in front of the send paths. That is a "
               "product decision, not a gap — sell it as one.\n"
               "* **Nothing is sent during a demonstration.** The records are "
               "created exactly as production creates them; the carrier call "
               "is not made. Say so before you are asked.\n"
               "* **Do not quote a timeline for something that does not "
               "exist.** \"I do not know, I will find out\" costs you nothing. "
               "A promised feature costs an implementation.\n"
               "* **Do not quote pricing you have not been given.** Pricing is "
               "per deal and there are approval floors.",
               help_key="simulated_sends"),
            _s("reset_and_next", "Resetting, and what happens after the demo",
               "**Reset your session** when you are done, or before you run "
               "the same scenario again. That puts your step counter back to "
               "the beginning and touches nobody else's demonstration.\n\n"
               "**Rebuilding the environment** is different — it re-seeds the "
               "shared world for everybody presenting that brand. You will "
               "only see that button if you have been given demo environment "
               "administration. If the world looks wrong, ask; do not "
               "improvise.\n\n"
               "After the meeting the next step is a discovery conversation, "
               "not a proposal. The proposal comes out of what discovery finds "
               "— that is why the pipeline has a Discovery stage before Demo / "
               "Proposal.",
               help_key="pipeline_stages"),
        ],
    },
    {
        "key": "salesperson_basics",
        "name": "Salesperson basics",
        "audience": AUDIENCE_SALES,
        "requires_demo": False,
        "summary": "The deal record, the pipeline, and what the system expects "
                   "of you.",
        "why": "A rep who does not trust the pipeline keeps their real "
               "pipeline in a notebook, and then nobody can help them.",
        "steps": [
            _s("one_record", "One deal, one record",
               "A deal is a single record from first contact through won, "
               "onboarding and live. You never re-create it to move it "
               "forward.\n\n"
               "That matters because everything else hangs off it: the "
               "timeline, the discovery answers, the proposal, the "
               "compensation, and the customer it eventually becomes. Starting "
               "a new record because a deal changed shape is how a company "
               "loses its own history.",
               help_key="pipeline_stages"),
            _s("stages", "Stages, and time in stage",
               "The stages are ordered, so \"days in stage\" is a real number "
               "and a stalled deal is visible without anybody reporting it.\n\n"
               "Move a deal when the deal moves — not at the end of the week. "
               "A stage that is updated in batches is a stage nobody can act "
               "on.\n\n"
               "Lost is an exit, not a column. Recording why a deal was lost "
               "is the only way the next quarter is different from this one.",
               help_key="pipeline_stages"),
            _s("next_action", "The next action is the job",
               "Every open deal carries the next thing that has to happen and "
               "when. That list is your day.\n\n"
               "If a deal has no next action, it is not a deal — it is a name. "
               "Either give it one or move it to Lost and say why.",
               help_key="next_actions"),
            _s("discovery", "Discovery before demo",
               "Discovery is a structured set of questions, and the answers "
               "are what the demo is built from. A demo given before discovery "
               "is a product tour.\n\n"
               "Fill it in during the call, in the customer's words. The "
               "person who builds the demo has only what you wrote."),
            _s("pricing_limits", "Pricing, discounts and approvals",
               "There are floors. If a deal needs to go below one, you request "
               "approval in the system rather than asking a manager on Slack — "
               "the request is what makes it visible and what makes the answer "
               "auditable.\n\n"
               "A one-time implementation fee and a monthly subscription are "
               "two separate figures and are billed separately. Do not quote "
               "them as one number."),
        ],
    },
    {
        "key": "sales_manager_basics",
        "name": "Sales manager basics",
        "audience": AUDIENCE_MANAGER,
        "requires_demo": False,
        "summary": "Running a team from the deals rather than from status "
                   "meetings.",
        "why": "The information a manager asks for on Monday is already in the "
               "system on Friday.",
        "steps": [
            _s("team_view", "Your team, from the deals",
               "The team view is computed from the deals your people own — "
               "open work, pipeline value and what is overdue. Nobody submits "
               "anything.\n\n"
               "Use it before the meeting, not as the meeting.",
               help_key="manager_view"),
            _s("stalled", "Finding the stalled deals",
               "Time in stage is the number that finds a dying deal. A deal "
               "that has not moved in ten days is a conversation, not a "
               "report.\n\n"
               "Look for two things: deals with no next action, and next "
               "actions that are overdue. Both mean the same thing.",
               help_key="next_actions"),
            _s("approvals", "Approving pricing",
               "A rep who needs a discount raises a request against the deal. "
               "You see what they asked for, what it breaches and why they "
               "asked.\n\n"
               "Approving applies the change to the deal itself, so the "
               "proposal and the timeline stay the single record of what the "
               "price actually is."),
            _s("coverage", "Making sure somebody can demo",
               "You do not need the platform owner to run a demonstration, "
               "and you should not plan as though you do.\n\n"
               "Make sure at least two people on your team hold Demo Suite "
               "access and have completed the Demo Presenter path. The "
               "platform owner grants that access; you ask for it.",
               help_key="demo_entitlement"),
        ],
    },
    {
        "key": "executive_basics",
        "name": "Executive basics",
        "audience": AUDIENCE_EXECUTIVE,
        "requires_demo": False,
        "summary": "What the executive surfaces show, and what they are "
                   "deliberately scoped to.",
        "why": "An executive who does not know their view is scoped will "
               "assume a gap in the data is a gap in the business.",
        "steps": [
            _s("what_you_see", "What your view is",
               "Revenue, performance and a customer portfolio for one brand. "
               "Not lead lists, not message threads — those belong to the "
               "people doing the work.\n\n"
               "The figures come from the deals themselves. There is no "
               "month-end assembly step and nothing to reconcile.",
               help_key="executive_authority"),
            _s("scoped", "Your portfolio is assigned, not inherited",
               "You see exactly the organisations you were assigned inside "
               "exactly one brand. Holding an executive role does not hand you "
               "every customer on the platform, and that is deliberate — a "
               "white-label brand cannot survive one brand's executive seeing "
               "another's customers.\n\n"
               "If a customer you expect is missing, it is an assignment, not "
               "a bug. Ask the platform owner.",
               help_key="executive_authority"),
            _s("not_god", "What you cannot do, and why",
               "The Executive Suite consumes authority; it does not grant it. "
               "Creating users, changing anybody's access, granting Demo Suite "
               "access and rebuilding demonstration environments are all "
               "platform-owner operations.\n\n"
               "That is not a limitation of your account. There is exactly one "
               "root authority on this platform and everything else is granted "
               "by it — which is the property that makes the brand boundaries "
               "worth anything."),
            _s("demo_access", "Presenting the product yourself",
               "If you sit in sales meetings, ask for Demo Suite access and "
               "complete the Demo Presenter path. Executive authority does not "
               "include it, on purpose — they are different jobs.",
               help_key="demo_entitlement"),
        ],
    },
    {
        "key": "customer_workspace_basics",
        "name": "Customer workspace basics",
        "audience": AUDIENCE_WORKSPACE,
        "requires_demo": False,
        "summary": "For somebody working inside a customer's workspace: leads, "
                   "conversations, calendar and the compliance boundary.",
        "why": "The people using the product every day are the ones who decide "
               "whether it was worth buying.",
        "steps": [
            _s("the_queue", "Start with the queue, not the list",
               "The priority queue is ordered by what needs a person today. "
               "Working it top-down is the whole method.\n\n"
               "The full lead list is there when you need to find somebody "
               "specific. It is not where you start your morning.",
               help_key="ai_prioritisation"),
            _s("thread", "One family, one thread",
               "Texts, replies and emails with a contact are one "
               "chronological record. Anybody picking it up sees the whole "
               "story without asking.",
               help_key="unified_thread"),
            _s("gate", "The AI drafts; you approve",
               "Suggested replies wait for you. Read them before you send "
               "them — they are usually right and they are not always right, "
               "and you are the person who knows the family.",
               help_key="ai_drafting"),
            _s("compliance_line", "The compliance boundary is real",
               "A do-not-contact record stays visible and stays unreachable. "
               "You cannot message them, and neither can the system.\n\n"
               "Do not work around it — not with a personal phone, not with a "
               "different address. The rule exists to protect the business "
               "that employs you.",
               help_key="compliance"),
            _s("appointments", "Booking is the outcome",
               "Everything else is the path to a booked consultation. If a day "
               "produced no appointments, the messages did not matter.",
               help_key="appointments"),
        ],
    },
    {
        "key": "opportunity_workflow",
        "name": "Opportunity workflow end to end",
        "audience": AUDIENCE_SALES,
        "requires_demo": True,
        "summary": "Prospect through live, on one record, practised in the "
                   "Demo Suite.",
        "why": "Reps learn the pipeline by moving deals, not by reading about "
               "stages.",
        "steps": [
            _s("read_first", "The shape of the record",
               "Prospect → Contacted → Discovery → Demo Build → Demo / "
               "Proposal → Closing → Won → Onboarding → Live. Lost sits beside "
               "the board as an exit.\n\n"
               "Everything that happens to the deal appends to its timeline. "
               "Nothing overwrites.",
               help_key="pipeline_stages"),
            _s("practise_manager", "Practise: move a deal and clear an action",
               "Run **Sales manager → Team → Pipeline → Next action** in the "
               "Demo Suite. Move a deal forward and complete a next action, "
               "then look at what each one wrote to the timeline.",
               practice="manager_day"),
            _s("after_won", "What happens after Won",
               "The record does not end at Won. It carries into onboarding "
               "and the customer launch, which is why a rep can answer \"what "
               "happens on Monday\" without asking anybody.",
               help_key="customer_launch"),
        ],
    },
    {
        "key": "implementation_launch_basics",
        "name": "Implementation and customer launch",
        "audience": AUDIENCE_IMPLEMENTATION,
        "requires_demo": False,
        "summary": "What a won deal becomes, and who owns each part of it.",
        "why": "The handover between sales and implementation is where "
               "customers are lost, and it is a process rather than a "
               "personality.",
        "steps": [
            _s("intake", "The launch intake",
               "A won deal carries into a structured intake: company "
               "information, branding and assets, website and hosting access, "
               "integrations, team and current systems, the customer's own "
               "process, files and documents, then review and submit.\n\n"
               "The customer is never asked what automation they want. The "
               "brand is the expert and states what it will build. That is a "
               "deliberate product decision — a customer asked to design their "
               "own automation will design the process they already have.",
               help_key="customer_launch"),
            _s("owners", "Every step has an owner",
               "Some steps are the customer's, some are ours, some are both. "
               "A step with no owner is a step that does not happen.\n\n"
               "The salesperson can see where the launch is without calling "
               "anybody, which is what stops the customer being asked the same "
               "question twice."),
            _s("credentials", "Handling access and credentials",
               "Hosting and registrar credentials are collected during intake "
               "for the first customers. Treat them accordingly: they are "
               "somebody's business, they are stored deliberately, and they "
               "are never pasted into a chat message or an email."),
        ],
    },
]


def catalogue(include_steps: bool = False) -> List[Dict[str, Any]]:
    out = []
    for p in PATHS:
        entry = {
            "key": p["key"], "name": p["name"], "audience": p["audience"],
            "audience_label": AUDIENCE_LABELS.get(p["audience"], p["audience"]),
            "summary": p["summary"], "why": p["why"],
            "requires_demo": p["requires_demo"],
            "total_steps": len(p["steps"]),
            "minutes": sum(s["minutes"] for s in p["steps"]),
            "practice_scenarios": sorted({s["practice_scenario"]
                                          for s in p["steps"]
                                          if s["practice_scenario"]}),
        }
        if include_steps:
            entry["steps"] = p["steps"]
        out.append(entry)
    return out


def get_path(key: str) -> Optional[Dict[str, Any]]:
    for p in PATHS:
        if p["key"] == (key or "").strip():
            return p
    return None


def path_or_404(key: str) -> Dict[str, Any]:
    from fastapi import HTTPException
    p = get_path(key)
    if p is None:
        raise HTTPException(status_code=404, detail="No such training path.")
    return p


def find_step(path: Dict[str, Any], step_key: str) -> Optional[Dict[str, Any]]:
    for s in path.get("steps") or []:
        if s["key"] == step_key:
            return s
    return None


ALL_PATH_KEYS = tuple(p["key"] for p in PATHS)
