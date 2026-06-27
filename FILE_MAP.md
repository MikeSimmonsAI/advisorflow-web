# AdvisorFlow Web — Where Every File Goes

**CURRENT VERSION: v1.9.** See SESSION_LOG_V1.9_EMAIL_QUEUE_VISUAL_REDESIGN.md
for the latest session's full changelog (real scorecards on the Email
Queue - In queue, Sent today, Open rate, Clicks - plus the queue list
rebuilt as richer cards instead of a plain table, per Mike's direct
feedback that the rebuilt page still "looked way too simple").

Prior version: v1.8, see SESSION_LOG_V1.8_EMAIL_QUEUE_REBUILD.md (the
full Email Queue rebuild - broadened to any lead with an email, a real
rich content composer with image embedding, open/click tracking on
both send paths). **Set TRACKING_BASE_URL if the backend's public URL
ever changes from the current default.**

Prior version: v1.7, see SESSION_LOG_V1.7_MIXED_CHANNEL_CADENCE.md
(cadence now intelligently mixes text and email for leads who have
both, never the same touch on both channels at once).

Before that: v1.6, see SESSION_LOG_V1.6_EMAIL_TIMELINE_FIX.md (a real
gap Mike caught directly: outbound emails were never shown in a lead's
Conversation timeline, only SMS. Fixed - email now appears interleaved
with SMS, correctly ordered, with a clear Email tag and subject line).

Before that: v1.5, see SESSION_LOG_V1.5_CERTIFICATION_WIRED_IN.md
(certification is now actually visible where advisors look most:
Overview shows a real "certified appointments waiting" count, and
Replies shows a per-reply badge - batched for performance, not one
query per reply).

Before that: v1.4, see SESSION_LOG_V1.4_CERTIFIED_APPOINTMENT_PIPELINE.md
(the Certified Appointment pipeline — Solicited → Contacted → Booked →
Confirmed → Waiting, a real auditable sequence of events, not a score;
shown on Lead Detail with a "Mark confirmed" action). This is the
universal, industry-agnostic foundation Mike wants future
industry-specific features built on top of.

Prior version: v1.3, see SESSION_LOG_V1.3_GOOGLE_CONTACTS_REFERRALS.md
(automatic Google Contacts sync, and a real referral-lead system —
adding a permission-to-access plus-one creates a genuine, separate
Lead record with its own cadence and outcomes, not a notes field).
**IMPORTANT: anyone who already connected Google Calendar needs to
RECONNECT once to grant the new Contacts scope.**

Prior version: v1.2, see SESSION_LOG_V1.2_ACTION_CENTER_TOGGLES_ROLES_TONE.md
(Replies action center, feature toggle system, role descriptions, email
tone control — completed the original 7-item priority list). Before
that: v1.1, see SESSION_LOG_V1.1_MANUAL_LEAD_EDIT_OUTCOMES.md (manual
lead entry, editable Lead Detail, mandatory outcomes). Before that:
v1.0, see SESSION_LOG_REPLY_TONE_SELECTOR.md (Suggest Reply tone
selector — Soft, Standard, Urgent, Direct).

**IMPORTANT: auto-migrations are now permanent.** As of the account-
management session, the app automatically adds any missing database
column or enum value on every startup (see app/auto_migrate.py) - the
old "run this manual migration command" notes in earlier session logs
below no longer apply. Just deploy and the app handles its own schema
catch-up.

Prior sessions: SESSION_LOG_ACCOUNT_MANAGEMENT_AND_IMPORT_ACCESS.md,
SESSION_LOG_CLOCK_AND_REALTIME_ALERTS.md,
SESSION_LOG_LEAD_CLEANUP_EMAIL_DNC_REPORTS.md,
SESSION_LOG_AUTONOMOUS_BACKLOG_PASS.md, and
SESSION_LOG_BUGFIXES_AND_FEATURES.md.

You've been collecting files in the order I gave them to you, which is exactly
right. This doc tells you the FINAL folder each file belongs in. If a file
name repeats, the most recently downloaded copy is the correct one — let it
overwrite the older copy with the same name.

## Top-level project folder: `advisorflow-web/`

Everything lives inside one folder called `advisorflow-web`. Inside it, there
are two main sections: `backend` (called just the project root in some of my
earlier messages) and `frontend`. Below is the exact tree.

```
advisorflow-web/
│
├── DEPLOY.md                          ← how to put this online
├── SUMMARY.md                         ← plain-English overview of everything
├── FILE_MAP.md                        ← this file
├── requirements.txt                   ← list of Python packages needed
├── pytest.ini                         ← test runner config
├── render.yaml                        ← one-click deploy blueprint for Render
├── .env.example                       ← reference list of every setting the backend needs
│
├── app/                                ← THE BACKEND BRAIN
│   ├── __init__.py
│   ├── main.py                        ← the file that starts everything
│   ├── deps.py
│   ├── seed.py                        ← run this once to create accounts
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   └── models.py                  ← the database blueprint
│   │
│   ├── routers/                       ← one file per "category" of action
│   │   ├── __init__.py
│   │   ├── admin_router.py
│   │   ├── ai_router.py
│   │   ├── audit_log_router.py
│   │   ├── auth_router.py
│   │   ├── cadence_router.py
│   │   ├── calendar_router.py
│   │   ├── campaign_router.py
│   │   ├── compliance_router.py
│   │   ├── email_router.py
│   │   ├── health_router.py
│   │   ├── leads_router.py
│   │   ├── microsoft_router.py
│   │   ├── notification_router.py
│   │   ├── outcomes_router.py
│   │   ├── sample_data_router.py
│   │   ├── settings_router.py
│   │   ├── sms_router.py
│   │   ├── templates_router.py        ← now also has /templates/ai/generate and /templates/ai/rewrite
│   │   └── workqueue_router.py
│   │
│   ├── jobs/                          ← scheduled background jobs (cron)
│   │   ├── __init__.py
│   │   └── run_cadence_job.py
│   │
│   ├── services/                      ← the actual "how things work" logic
│   │   ├── __init__.py
│   │   ├── ai_analysis_service.py
│   │   ├── auth_service.py
│   │   ├── cadence_service.py
│   │   ├── calendar_service.py
│   │   ├── dedup_service.py
│   │   ├── draft_reply_service.py
│   │   ├── email_service.py
│   │   ├── import_service.py
│   │   ├── notification_service.py
│   │   ├── sms_service.py
│   │   ├── template_ai_service.py     ← NEW: AI template generate/rewrite
│   │   └── template_service.py
│   │
│   └── utils/
│       ├── __init__.py
│       └── crypto.py
│
├── scripts/                            ← one-time helper scripts, run by hand
│   ├── clean_wupa_spam.py
│   ├── clean_wupa_spam.bat
│   ├── rebuild_sent_log.py
│   └── seed_registry_from_sent_log.py
│
├── tests/                               ← the automated safety-net tests
│   ├── conftest.py
│   ├── test_admin_router.py
│   ├── test_ai_analysis_service.py
│   ├── test_auth_service.py
│   ├── test_cadence_router.py
│   ├── test_cadence_service.py
│   ├── test_calendar_router.py
│   ├── test_dedup_service.py
│   ├── test_email_service.py
│   ├── test_import_service.py
│   ├── test_leads_router.py
│   ├── test_notification_service.py
│   ├── test_run_cadence_job.py
│   ├── test_sms_service.py
│   └── test_template_service.py
│
└── frontend/                            ← THE WEBSITE YOU ACTUALLY SEE
    ├── README.md
    ├── index.html
    ├── package.json
    ├── vite.config.js
    │
    └── src/
        ├── App.jsx                    ← the file that connects every page
        ├── main.jsx
        ├── index.css                  ← global colors/fonts
        │
        ├── api/
        │   └── client.js              ← talks to the backend
        │
        ├── components/                ← reusable pieces used on many pages
        │   ├── Layout.jsx
        │   ├── Layout.css
        │   ├── NotificationBell.jsx
        │   ├── NotificationBell.css
        │   ├── SignalPulse.jsx
        │   ├── SignalPulse.css
        │   ├── StatCard.jsx
        │   ├── StatCard.css
        │   ├── StatusBadge.jsx
        │   └── StatusBadge.css
        │
        ├── pages/                     ← one file pair per screen
        │   ├── Admin.jsx
        │   ├── Admin.css
        │   ├── Cadence.jsx
        │   ├── ChangePassword.jsx
        │   ├── EmailQueue.jsx
        │   ├── LeadDetail.jsx
        │   ├── LeadDetail.css
        │   ├── Leads.jsx
        │   ├── Leads.css
        │   ├── Login.jsx
        │   ├── Login.css
        │   ├── Overview.jsx
        │   ├── Overview.css
        │   ├── Replies.jsx
        │   ├── Replies.css
        │   ├── Settings.jsx
        │   ├── Settings.css
        │   ├── Templates.jsx
        │   └── Templates.css
        │
        └── styles/
            └── shared.css
```

## How to actually do this on your computer

1. Make ONE folder called `advisorflow-web` somewhere easy to find, like your
   Desktop.
2. Inside it, make the folders shown above: `app`, `app/models`,
   `app/routers`, `app/services`, `app/utils`, `scripts`, `tests`, `frontend`,
   `frontend/src`, `frontend/src/api`, `frontend/src/components`,
   `frontend/src/pages`, `frontend/src/styles`.
3. Go through everything you've downloaded from our conversations, and drop
   each file into its matching folder above, based on the file name.
4. If you find two files with the exact same name, keep the one from the
   MOST RECENT message and delete the older one — it's an old version of the
   same file with improvements added later.

## Important: empty folders need a placeholder

A few folders (`app`, `app/models`, `app/routers`, `app/services`,
`app/utils`) each need a file called `__init__.py` inside them — even though
it's basically empty, Python needs it there to recognize the folder as part
of the program. Make sure those aren't accidentally skipped.

## When in doubt

If you're ever unsure where a file goes, just ask me "where does
[filename] go?" and I'll tell you exactly which folder, instantly.
