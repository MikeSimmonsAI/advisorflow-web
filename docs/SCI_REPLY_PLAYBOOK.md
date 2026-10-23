# SCI reply playbook: what happens when a family writes back

The mission is an appointment. Every reply either gets the family a time or puts a person on it with one clear job.

**The rule (Mike, Oct 22):** the AI sends a family something on its own **only** when it offers real open times or books a visit. For anything else (a question, a soft no, "check back later", "I already have a plan", a goodbye, anything it can't turn into an appointment) **nothing is sent**. Mike and Michael Schlueter get a "Needs a human" alert with the family's words and the AI's draft, which they can use or ignore. STOP / DNC is still handled automatically. Automatic follow-ups only go out when they offer real times.

| The family says | What the system does by itself | What the person does |
|---|---|---|
| **Yes to coming in** ("I can come by tomorrow", "yes I'll come in", "we could do Friday") | Texts two real open times (on the day they named, if they named one) plus their pick-a-time link. It never sends "see you tomorrow" without a time. | Nothing unless they don't pick. If there's still no booking an hour later, you get a **"wants to come in, no time sent yet"** alert. Tap **Text them times to pick** or book a time. |
| **Picks one of the offered times** ("the first one", "Thursday at 10") | Books it, texts the confirmation and puts it on the calendar. | Nothing. |
| **Arriving now** ("I'll run by in 15 minutes") | Sends a short "we'll be ready for you" and alerts you right away. | Greet them. |
| **Soft no** ("close it for now", "not right now", "maybe later", "hold off") | **Sends nothing.** Outreach pauses. Alerts you: *Soft no. Call them once and offer a short visit.* | Call once and offer a short visit. If it's still no, set a check-back date. |
| **STOP / unsubscribe** | Opts them out. Nothing more is sent, ever. | Nothing. |
| **Wants a visit at home** | **Sends nothing.** Gives the request its own alert. Books nothing. | Call to set the time, then mark it arranged. |
| **Upset, grieving, complaint, legal, or changing/cancelling a booking** | **Sends nothing.** Alerts you. | Call them. |
| **A question** (benefits, what this is, cost) | Sends the answer plus times when it can write one with real times. Otherwise sends **nothing** and alerts you. | Answer them yourself. The AI's draft is on the family page. |
| **Anything else** (existing customer, "check back next month", unclear) | **Sends nothing.** Alerts you: "Needs a human". | Contact them personally. |

## Outreach emails and texts

Every touch asks for a short visit. Every email carries the family's pick-a-time link, with "reply with a day" as the fallback. There is no "should I close this out?" message.

## Safety nets

- **Stuck-family alert.** Runs every 30 minutes, 8 AM to 8 PM Central. It catches anyone who said yes to coming in and has no booking, times or link an hour later. One text and one email per family per day.
- **Nightly practice check.** Runs at 6 AM Central. The AI replays every conversation it has got wrong before, using practice contacts only: Henry's "come by tomorrow", James's "close it for now", and 20 others. You hear about it only if one goes wrong.
- **Text box.** It warns you if a text says "link" but has no link in it. The **Add booking link** button inserts the family's link.

## Where this lives in the code

| Area | Location |
|---|---|
| Reply rules | `app/services/programs/ai_setter.py` (`wants_visit`, `future_visit_day`, `is_soft_no`, and the hard rule in `decide`'s caller) |
| Stuck-family alert | `app/services/programs/stuck_visits.py` |
| Practice check | `app/services/programs/practice_run.py`, with scenarios in `setter_eval_scenarios.py` (`REGRESSIONS`) |
| Outreach copy | `app/services/programs/message_brain.py` |
