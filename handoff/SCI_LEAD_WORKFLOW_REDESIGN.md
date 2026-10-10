# SCI / AdvisorFlow lead workflow redesign — handoff (Oct 9–10, 2026)

Branch `desktop/consent-center` → pushed to `sci-program` (staging). **Not on production.**
Commits: 752bd2be, f2209017, 308b1b65, 59e3f261, d23345ba.
Rollback: `git revert` those commits on sci-program (or redeploy 875de4dc). No schema changes.

## Screens (all on real APIs — no mock data)
| Screen | Route | Reads |
|---|---|---|
| Lead Command Center | /leads/:id (?tab=conversation/calls/activity/history/overview; old ?tab=timeline opens Activity) | /leads/{id}/timeline, /compose/{id}/context, /voice/readiness, /ai-conversation/status, /leads/{id}/activity, /cadence/lead/{id}/history, /dialer/leads/{id}/history, /conversation-intel/leads/{id} |
| Leads Directory | /leads (?view=needs_reply/mine/waiting/appointments/blocked/test) | /leads/ (+ server totals per view), /work/identity |
| Replies | /replies (added Unassigned filter, new skin) | /communications/* (unchanged) |
| My Work | /workqueue (new Tasks panel: Priority/Today/Upcoming/All open/Completed) | /work/tasks (PATCH status done/open) + /workqueue/today |
| Activity & call history | /activity ("All activity" + old "Sends & delivery") | NEW GET /activity/feed |
| Appointments & visits (Family Service Center bookings) | /appointments (nav: Appointments) | /pipeline/appointments?include_pending=true (NEW param) |

## Backend changes
- `GET /activity/feed` (activity_router): texts/emails both ways, calls, voicemails; one row per event (an inbound call and its voicemail are matched by call SID and shown once); scoped by lead_scope.authorized_lead_query; record id only shown to admins in the UI.
- `/pipeline/appointments?include_pending=true` adds `pending` (links sent, no time picked). Default response unchanged.
- `/leads/` list now returns `is_test`.
- `/leads/{id}/timeline` voice_calls now carry direction / is_human_call / provider.
- `lead_call_history` links each inbound call to its voicemail (`voicemail_id` / `call_id`).
- **Test records (Mike's call, Oct 10):** `test_records.manual_send_allowed()` = not production. Production refuses EVERY send to a test record, manual included (compliance gate, SMS send, booking-link resend). Staging/demo (APP_ENV) still allow a deliberate manual send so live tests keep working.

## Safety on the lead page
- Test record: no live send/call/AI/booking control at all; one reason shown; details under "View details".
- Human active (Conversation Brain mode): the AI conversation panel cannot start/resume; hand back only via Conversation Brain "Resume AI".
- Bulk selection on Leads never includes test or removed records.

## Tests
- New: tests/test_activity_feed.py (3), tests/test_lead_workflow_redesign.py (6), 1 new in test_test_records_never_contacted.py.
- Targeted run: 216 passed. Full suite: see the completion report.

## Known limits / next
- Call recordings live with the phone provider; no in-app secure player yet for AI-call recordings (voicemails have /voicemails/{id}/audio but the lead page doesn't play it yet).
- Lead "location" shows the workspace unless the lead record carries a location field; SCI location per lead is not on the Lead row.
- Activity feed filters run on the newest 500 events in the chosen window.
- Appointment "Completed" is not recorded anywhere (pipeline_router says so); the screen shows Past + "Outcomes needed" from My Work instead.
