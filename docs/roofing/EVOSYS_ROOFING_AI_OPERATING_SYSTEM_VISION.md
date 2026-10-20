# EvoSys Pro Roofing — AI Operations System (Design Handoff)
**Date:** October 20, 2026  
**Status:** Owner-directed course correction for the in-progress Roofing vertical  
**Reference:** Approved four-panel "EvoSys Pro for Roofing" concept image supplied to owner in ChatGPT. **That image is NOT attached to this GitHub document**; owner should attach it to Claude's session separately. Use its light, polished navy/white/orange roofing style, not its CRM-first information architecture.

## The critical correction
The prior mockup looks beautiful, but the product still *feels like a CRM*. That misses the differentiator.

**A CRM records what your salespeople do. EvoSys Pro actively works your existing leads to create conversations and booked inspections.** The experience should feel like an **AI roofing growth/operations command center**, powered by the shared EvoSys engine, with contact database and opportunity records available behind the scenes. Do not turn the center of the experience into a static contact table, Kanban board, or six tiny KPI boxes.

**North star:** *Bring your own leads → AI works them → interested homeowners respond → inspection slots are offered/confirmed → your roofing team steps in → conversion is measured.*

**No invented lead generation.** Current promise is to convert *existing* imported or already-owned leads. Roofing campaign templates are re-engagement and follow-up workflows for customer-provided contacts, not a lead-sourcing service.

## What the owner must see first: Roofing Mission Control
First screen on `/program/roofing` should answer, in five seconds:
1. **What's my AI team doing right now?** Eligible leads queued, actively in conversation, next touches due, and actions performed today.
2. **What needs a person?** Newly interested homeowners, proposed appointments, exceptions, high-priority replies, and handoffs. Sorted by urgency.
3. **What is the work producing?** Conversations, inspection requests/confirmed inspections, missed opportunities, estimates, and won work when tracked.

### Suggested large-format layout
- **Hero strip:** real roofing visual / elegant roofline texture at right; left headline **"Your AI Team Is Working Your Roofing Leads."** Under it: current live state, campaign activity, and two actions: **Import My Leads** and **Review Hot Opportunities**.
- **Large primary panel, 60–65% of content:** **AI Workboard / Live Activity**, showing meaningful *next actions*, not just generic contact rows. Each card: lead name, roof issue if known, AI touch number, short conversation summary, next step/time, and current disposition. Status lanes may be **Working**, **Homeowner Replied**, **Inspection Requested**, **Booked**, **Human Needed**.
- **Adjacent priority panel, 35–40%:** **Needs Your Attention**, with unread new replies first, appointment issues, owner decisions, and human handoffs. One-click **Open Conversation**, **Take Over**, **Assign**, or **Review Booking**. Don't make owners hunt for today's new activity.
- **Outcome ribbon or single strong performance section:** today's contact attempts, engaged households (with denominator), inspections confirmed, and follow-ups still in motion. More advanced funnel/charts below fold or on Performance.
- Avoid top-heavy rows of generic metric tiles, repeating left-nav tables, and overwhelming charts on the hero.

## Proposed roofing-first navigation
- **Mission Control** — active AI work, hot signals, blockers, inspection outcomes
- **AI Work Queue** — who AI is contacting next, touch histories, safety/eligibility states, pause/resume where supported
- **Conversations** — priority inbox with AI/human handoff, intelligent filters, unread and newly replied first
- **Inspections** — appointment requests, actual confirmed bookings, assigned reps, reminders, calendar linkage
- **Performance** — funnel and percentages, touches, response, bookings, opt-outs; comparison by cohort/period
- **Leads & Data** — import, mapping, classification, dedupe, source details; operationally important but not the front door
- **Settings / Playbooks** — business configuration, approved messaging sequences, staff and permissions

Keep compatibility with existing shared component architecture; names can be adapted to real routes. Don't independently build a second CRM.

## Roofing-specific AI playbooks (using customer-provided contacts)
Examples of selectable workflows for **eligible existing leads**:
- **Old estimate follow-up:** prospect received an estimate but no decision recorded; invite them to reconnect.
- **Previous inspection interest:** unanswered requests or stalled inspections.
- **Repair vs replacement inquiry:** ask a few routing questions and offer suitable inspection options.
- **Storm-related inquiries in their own existing list:** follow up with people who have already inquired; do NOT scrape homeowners or claim storm-triggered lead acquisition.
- **Past customer check-in:** appropriate follow-up and referral request only through permissible/approved messaging.

Show playbooks as campaigns with clear goal, target audience, running status, touches, replies, inspections booked, and stop/opt-out logic. Reuse real conversation engine and qualification.

## Conversation and booking behavior
An AI action should lead to a tangible outcome. Show real stages:
`Imported → Eligible → Queued → Contacted → Replied → Qualified → Inspection Offered → Inspection Confirmed → Rep Handoff`. Estimates and won/lost results may be tracked after human follow-up; do not pretend roof work gets completed inside EvoSys or that outside job-management systems are integrated if they aren't.

- Prioritize **new replies** over old activity.
- Allow the owner to see *what AI said, what homeowner said, what comes next, and when*.
- Highlight ready-to-book leads and urgent human takeover.
- Book against actual calendar availability; do not display merely requested times as confirmed.
- Route a single actionable alert for one homeowner event, not duplicate emails/texts.
- Respect real eligibility, contact permissions, opt-outs, tenant scope, and channel constraints.

## Performance that's understandable at a glance
Show percentages **with their actual denominators**:
- Response % = unique responding contacts ÷ successfully contacted eligible contacts (clarify date/cohort window).
- Engagement % = meaningfully engaged contacts ÷ successfully contacted eligible contacts.
- Inspection booking % = confirmed inspections ÷ meaningfully engaged contacts (and optionally ÷ contacted for full-funnel rate).
- Opt-out % = opt-outs ÷ delivered or successfully contacted leads, using a consistently defined denominator.
- Touch 1/2/3/4 performance: respond-by-touch cohorts with accurate attribution and time filters.
- Revenue, estimates, jobs won only when tracked by a supported pipeline/field; otherwise omit/label integration required.

Show charts that tell the owner what to improve, not charts just for decoration. Real data only; explicit sample-data mode for demos; informative empty states.

## Visual design — preserve what the owner loved
The owner **approved the original visual direction**: premium white/light-blue surfaces, strong deep-navy typography and navigation, muted bright orange accents, tasteful roof photography, generous whitespace, professional shadows and rounded cards, elegant hero-side-panel treatment.

**Change the information architecture and interaction center, not the visual quality.** Larger distinct panels, clear narrative, fewer generic cards; powerful desktop experience and thoughtful mobile layout. The home screen should feel like an active operating floor. Put tables and detailed pipeline grids deeper into the product where they belong.

## Build safely, without resetting existing progress
This is a **course correction to the active roofing build**, not authorization to erase completed work.
- Inspect the current `feature/roofing-development` implementation and reuse working code.
- Work only in Claude's designated roofing lane. SCI, Wholesale, Atlantis, and production are off limits.
- Don't merge, redeploy, or modify unrelated branches without explicit approval.
- Validate operations with actual APIs and test fixtures. No fabricated live numbers, imaginary CRM integrations, or unauthorized outbound sends.
- Implement the functional command center first; then adjust queue/inbox/navigation and analytics to match the experience.
- Show real screenshots of the built screens, note what is connected vs still planned, and keep the owner updated with time-stamped check-ins consistent with the original build instruction.

## Acceptance test
A roofing owner with 1,000 previously collected, eligible leads logs in and immediately understands:
1. Which leads EvoSys is working and what the AI is doing;
2. Which interested homeowners need attention;
3. Which inspections were actually confirmed;
4. What follow-up sequence is performing best; and
5. What the owner should do next.

If this merely looks like a well-designed Salesforce dashboard, the design missed the goal.
