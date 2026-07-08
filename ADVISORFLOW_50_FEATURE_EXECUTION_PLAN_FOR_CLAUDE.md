# AdvisorFlow 50-Feature Execution Plan for Claude

## Purpose

AdvisorFlow is being pushed beyond a generic CRM into an AI revenue command center for cemetery, funeral, family-service, and pre-need sales teams.

This document turns the 50 enhancement ideas into a practical Claude/Cursor build plan. Do not attempt all 50 at once. Build in tight, tested packs.

## Non-Negotiable Build Rules

- Use the real project structure.
- Models live in `app/models/models.py`.
- Dependencies/auth/session live in `app/deps.py`.
- Routers live in `app/routers/`.
- Services live in `app/services/`.
- Frontend API calls must use `frontend/src/api/client.js`.
- Do not use raw `fetch()` in page components.
- Every new backend endpoint needs pytest coverage.
- Every advisor endpoint must be scoped to `get_current_user`.
- Every admin endpoint must be scoped to `require_admin`.
- Preserve organization isolation everywhere.
- No fake dashboard numbers.
- No mock chart data in production screens.
- Every score must have an explainable formula.
- Every destructive action must be transactional and tested.
- If new DB tables are added, document migration requirements.

## Recommended Build Sequence

### Phase 0 — Foundation and Review

Goal: Make sure the current branch is clean before adding new AI-heavy features.

1. Full local test run outside sandbox.
2. Add migrations for new tables.
3. Review compliance send-path enforcement.
4. Review lead merge transactional safety.
5. Review all newly added routes for org/user scoping.

Acceptance criteria:
- `python -m pytest` passes locally.
- `npm run build` passes.
- production migrations exist.

---

## Phase 1 — Core Revenue Command Foundation

### 1. Full Lead Timeline / Case File

Build first.

Why:
The timeline makes every lead understandable and supports every future AI explanation.

Backend:
- `GET /leads/{lead_id}/timeline`
- Aggregate existing rows from Lead, Message, Reply, CadenceState, BookingLink, LeadOutcome, AuditLogEntry, suppression/DNC records if available.

Frontend:
- Add a timeline panel to `LeadDetail.jsx`.

Tests:
- advisor scoping
- admin org scoping if supported
- sorted event order
- message/reply/outcome/booking events appear
- empty state works

### 2. Outcome Enforcement System

Why:
Booked appointments without outcomes are revenue leakage.

Backend:
- advisor endpoint for missing outcomes
- admin endpoint for org-wide missing outcomes
- optionally enrich lead detail with outcome-required status

Frontend:
- Lead Detail outcome-required panel
- Work Queue missing outcomes section
- Admin metric/alert

Tests:
- booked no outcome appears
- booked with outcome disappears
- non-booked excluded
- org/advisor isolation

### 3. Compliance Preflight Engine

Why:
Every message path must respect compliance.

Backend service:
- `app/services/compliance_preflight_service.py`

Endpoint:
- `POST /compliance/preflight`

Checks:
- DNC
- suppression
- valid phone/email
- message length
- duplicate lead
- advisor integration connection
- booking link validity
- over-contacting risk
- org/user scope

Tests:
- DNC blocked
- suppression blocked
- invalid phone blocked
- valid send clear
- cadence/manual/batch paths cannot bypass when integrated

---

## Phase 2 — Revenue Rescue and Opportunity Intelligence

### 4. Revenue Rescue Center

Admin page that shows where money is being lost.

Categories:
- hot replies not reviewed
- hot replies older than 24 hours
- booked appointments with no outcome
- cadence touches overdue
- new leads never contacted
- old pre-need leads untouched
- email-only leads with phone present
- duplicate cleanup opportunities

Endpoint:
- `GET /admin/revenue-rescue`

Frontend:
- `RevenueRescue.jsx`

Tests:
- each category exact count
- org isolation
- zero state

### 5. Opportunity Gap Finder

Deathcare-specific gap detection.

Gap types:
- no marker outcome
- no planning guide
- no veteran benefit conversation
- no permission/access form
- no aftercare follow-up
- no appointment outcome
- no recent touch

Endpoints:
- advisor-scoped `GET /leads/opportunity-gaps`
- admin-scoped `GET /admin/opportunity-gaps`

Frontend:
- show gaps on Lead Detail
- summary in Revenue Rescue Center

Tests:
- each gap type
- resolved gaps disappear
- org isolation

### 6. AI Next Best Action Engine

Core brain of AdvisorFlow.

Service:
- `app/services/next_best_action_service.py`

Endpoints:
- `GET /leads/{id}/next-best-action`
- `GET /workqueue/next-actions`

Actions:
- call now
- send reply
- send first text
- record outcome
- book appointment
- stop outreach due DNC
- manager review
- cadence follow-up

Tests:
- hot reply prioritizes response
- booked/no outcome prioritizes outcome
- DNC blocks outreach
- new lead prioritizes first contact
- cadence overdue prioritizes cadence touch

---

## Phase 3 — Advisor AI Power Tools

### 7. AI File Review Assistant

Endpoint:
- `POST /leads/{id}/file-review-summary`

Output:
- concise case summary
- last contact
- missing outcomes
- suggested call reason
- suggested text opener
- compliance status

Tests:
- no OpenAI fallback
- no invented facts
- org scoping

### 8. AI Objection Handler

Endpoint:
- `POST /sms/replies/{id}/suggest-responses`

Objection types:
- busy
- not interested
- spouse needed
- callback
- price concern
- grief-sensitive
- wrong person
- neutral

Frontend:
- show 3 response styles
- click fills message box, does not auto-send

Tests:
- fallback without OpenAI
- DNC reply does not produce pushy response
- org isolation

### 9. Deathcare-Specific Playbooks

Playbooks:
- file review
- veteran family
- marker opportunity
- no-show recovery
- spouse needed
- old pre-need follow-up
- planning guide
- permission/access form
- cremation memorialization

Backend:
- Playbook model
- PlaybookStep model
- admin CRUD
- recommendation endpoint

Frontend:
- admin playbook builder
- Lead Detail suggested playbook checklist

Tests:
- admin-only edit
- advisor view
- org isolation

---

## Phase 4 — Manager Intelligence

### 10. Manager Intervention Feed

Endpoint:
- `GET /admin/intervention-feed`

Signals:
- advisor behind pace
- hot replies ignored
- cadence overdue
- missing outcomes
- unassigned leads
- campaign underperforming

Tests:
- severity sorting
- exact categories
- org isolation

### 11. Advisor Coaching Scorecard

Endpoint:
- `GET /admin/advisor-scorecards`

Metrics:
- speed to lead
- reply rate
- booking rate
- show rate
- outcome completion
- DNC rate
- overdue cadence rate
- hot reply response time

Tests:
- exact math
- zero division
- org isolation

### 12. AI Morning Manager Briefing

Endpoint:
- `GET /admin/morning-briefing`

Includes:
- overnight replies
- hot leads
- overdue cadence
- bookings
- missing outcomes
- compliance blocks
- advisor risks

Tests:
- real counts
- no fake narrative facts
- org isolation

---

## Phase 5 — Sales Execution Engine

### 13. AI Campaign Recommender

Endpoint:
- `GET /campaigns/recommendations`

Recommendations:
- old pre-need recovery
- no outcome recovery
- marker opportunity
- veteran benefit follow-up
- cold lead revival
- email-only phone found

Tests:
- filters accurate
- DNC excluded
- org isolation

### 14. Smart Lead Routing

Service:
- `app/services/lead_routing_service.py`

Inputs:
- workload
- availability
- reply rate
- booking rate
- lead type
- queue size

Tests:
- workload balance
- unavailable advisor skipped
- org isolation

### 15. Appointment Show-Up Protection

Features:
- confirmation
- reminders
- what-to-bring
- reschedule link
- no-confirmation manager alert
- outcome prompt after appointment

Tests:
- reminders due
- no duplicate reminders
- outcome required after appointment

---

## Phase 6 — Enterprise Moat

### 16. Family Relationship Map

Detect:
- same phone
- same email
- same last name
- same address if available
- linked appointments
- household grouping

Frontend:
- Lead Detail relationship panel

### 17. Property / Memorial Opportunity Layer

Future integration layer for cemetery/property systems.

Signals:
- property owned
- marker missing
- spouse space
- cremation memorialization
- balance due

### 18. Predictive Sale Probability

First version should be rule-based and explainable.

Outputs:
- booking likelihood
- show likelihood
- sale likelihood
- reasons

### 19. AI Manager Copilot

Natural language query panel over approved metrics/endpoints.

Example prompts:
- Which advisors are behind?
- Show hot leads untouched over 24 hours.
- What campaign should we run?

### 20. Enterprise Multi-Site Benchmarking

Compare:
- sites
- advisors
- campaigns
- reply rate
- show rate
- close rate

Requires site/location modeling if not already present.

---

## Remaining 30 Feature Ideas to Fold Into the Above Phases

21. Advisor Autopilot Command Center
22. Lead Intelligence Score
23. Deal Room / Close Plan for Hot Leads
24. AI Reply Coach With Tone Options
25. Conversation Intelligence for Text + Calls
26. Smart Follow-Up Calendar
27. AI Agent Queue Builder With Human Approval
28. Manual SMS Send Center Pro
29. Reply Inbox 3.0 Board
30. Booking Pipeline Board
31. Speed-to-Lead Timer
32. Lead Merge & Household Cleanup Pro
33. Import Mapping Wizard
34. Template Intelligence
35. System Health Pro
36. Audit Log Pro
37. Campaign Builder Pro
38. Manager Quality Dashboard
39. Mobile Advisor Mode
40. Live Activity Stream
41. AI Sales War Room
42. Predictive Sale Probability Expansion
43. AI Lost Revenue Detector
44. Voice Call Intelligence
45. AI Manager Copilot Expansion
46. Pre-Need Revenue Forecasting
47. Family Opportunity Graph
48. AI Training Simulator
49. Auto-Built Weekly Sales Plan
50. Enterprise Multi-Site Benchmarking Expansion

## Fastest Practical Next Step

Do not build all 50.

Build these next, one pack at a time:

1. Full Lead Timeline / Case File
2. Outcome Enforcement System
3. Compliance Preflight Engine
4. Revenue Rescue Center
5. AI Next Best Action Engine

These five create the foundation for almost everything else.

## Claude Prompt Rule

For every feature, Claude should first provide an implementation plan with:

- files to inspect
- files to change
- endpoints/services
- frontend changes
- tests
- risks
- acceptance criteria
- migration notes

Only after approval should code be written.
