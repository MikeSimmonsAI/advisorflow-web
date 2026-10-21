# Sales Workspace redesign — what was built (Lane F, #281)

Approved design: Mike's package **"Simplified Sales Workflow – My Day (New Design)"**
(six desktop screens + mobile, received 2026-10-21 01:21 CT). Colours, sizes and
layout are taken from that package's prototype; a handful of greys and the
green/blue button colours were darkened slightly so text meets the 4.5:1
contrast standard.

## Screens and routes

| Screen | Route | Built from (existing, unchanged) |
|---|---|---|
| My Day | `/sales` | `GET /sales/my-day`, `GET /sales/opportunities` |
| Follow Ups | `/sales/follow-ups` (new) | `GET /sales/opportunities`, `/sales/my-day` recent activity |
| Lead Details | `/sales/leads/:oppId` (new) | `GET/PATCH /sales/opportunities/{id}`, `POST …/notes`, `GET …/implementation` |
| Book a Demo | `/sales/book` (new) | `GET /sales/meeting-types`, `POST /sales/availability/find`, `POST /sales/appointments` |
| Pipeline | `/sales/pipeline` | `GET /sales/opportunities?include_lost=true` |
| Appointments | `/sales/appointments` (new) | `GET /sales/calendar/view?scope=mine`, existing Reschedule and Outcome dialogs |
| Reports | `/sales/my-results` (new) | same data as My Day |

Nothing was removed. The original every-stage board moved to
`/sales/pipeline/stages`; the full deal record (`/sales/opportunities/:id`) is one
click from Lead Details; Availability, Demo Suite, Sold / Onboarding, My
Compensation and Team Availability sit under **More** in the sidebar. The
manager's **MY TEAM** group is unchanged.

## The simple pipeline

| Column | Real stages |
|---|---|
| New Leads | prospect |
| Follow Up | contacted, discovery |
| Demo Scheduled | demo_build, demo_proposal, closing |
| Won | won, onboarding, live |

Lost and any unknown stage are listed under the board. Every card shows its
real stage; the stage picker on Lead Details offers the real stages grouped by
column. Won and Lost ask for one confirming click.

## Calls, texts and "What happened?"

- **Call** hands the number to the rep's phone (`tel:`) and opens *What happened?*:
  Connected, No Answer, Left Voicemail, Interested, Not Interested, Book a Meeting,
  an optional note, and next action + date (suggested from the choice).
- Saving writes one timeline entry (`event_type: call_outcome`) and, when something
  changed, one deal update (next action, due date; Prospect → Contacted after a real
  conversation). *Book a Meeting* then opens Book a Demo for that contact.
- **Text** opens the phone's messages app (`sms:`) with a starting message. It is
  only added to history when the rep clicks *I sent it*.
- No messaging provider is called from these screens.

## Won → onboarding

Marking Won shows the existing onboarding status and links to the existing
**Sold / Onboarding** area. No customer organisation is created here — that stays
the existing, deliberate provisioning step (owner/manager).

## Known limits

- *Completed* on Follow Ups reads the last 15 timeline events from My Day, so a rep
  logging more than that in one day will see an undercount.
- For a manager, My Day's *Demos to Build* and *Appointments today* cards count the
  team (unchanged server behaviour); Follow Ups and its badge count only their own.
- Booking still emails the prospect the usual invitation, exactly as before.
