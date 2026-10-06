# SCI on EvoSys Pro — overnight 2 (2026-10-06)

Branch: `sci-program` only. Production (`main`) was not touched, nothing was
merged, no campaign was switched on, no customer was contacted. The one real
email sent went to the approved staging test address.

Read the earlier handoff, `SCI_OVERNIGHT_2026-10-05.md`, first. This file only
covers what changed tonight.

---

## 1. Staging live-loop status

| Step | Result | Evidence |
|---|---|---|
| Staging deploys from `sci-program` | PASS | `/health` returns the current commit with branch `sci-program` |
| Seed: SCI workspace, 39 locations, 39 aliases (0 mismatches), management recipients, Outlook root | PASS | seed response: `contacts_in_workspace = 1` (the approved test contact only) |
| support@evosyspro.live routes to the staging SCI workspace | PASS | mailbox `routes_to` = Service Corporation International |
| ONE test email sent | PASS | From/Reply-To `easterngategardens@evosyspro.live`, display name "Kerry Allan \| Eastern Gate Memorial Gardens" |
| Authentication at Gmail | PASS | dkim=pass (d=evosyspro.live, s=resend); spf=pass (send.evosyspro.live); dmarc=pass (p=QUARANTINE); List-Unsubscribe one-click; plain-text alternative included |
| Staging Resend webhook | PASS | the message status moved to `delivered` through the signed staging webhook |
| Inbox placement | NOTE | Not in Spam, but Gmail filed it **outside the Inbox** (All Mail, labels UNREAD + IMPORTANT, no INBOX). Gmail only does that when a filter on the account archives it, so check that account's filters for one matching evosyspro.live. |
| Mailbox read (Check Now) | **BLOCKED** | Reads worked at 05:57Z. After the 00:56 CT environment update, Microsoft rejects the staging client secret with `AADSTS7000215 Invalid client secret` (that error usually means the secret **ID** was pasted instead of the secret **value**). |
| Outlook folders (39) | BLOCKED by the item above | The folder check is built and ready: `GET /god/email/inbound-mailboxes/{id}/probe?folder=Inbox/Customers Folder/SCI/<Location>` |
| HOT reply → workspace/location/contact, cadence pause, HOT SLA, alerts, Outlook move | WAITING | Needs (a) the secret fixed and (b) Mike's reply. Every step is proven by simulation and tests; see §4. |

**Morning sequence:**
1. In Render → sci-staging-backend → Environment, re-enter MICROSOFT_CLIENT_SECRET as the secret **value**, then Save.
2. Reply to the "[SCI staging test]" email from simmonsmj242@gmail.com. Look under All Mail; it is not in the Inbox. Write something like "Yes, I'd like to set up an appointment this week."

The poller reads every 5 minutes, and its cursor still starts before both events, so nothing is lost. Claude then runs `POST /god/staging/sci/poll` and `GET /god/staging/sci/status`, checks the probe folder, and reports the PASS/FAIL checklist.

## 2. What was built tonight (commits on `sci-program`)

### Message brain — `app/services/programs/message_brain.py`
- **Context packet** per contact, built only from verified data:
  - who they are: name, Lead ID, location, alias and whether it receives;
  - where they came from: campaign family and the original source campaign, status and channel;
  - where they stand: CRM stage, last activity and days since, cadence state;
  - history: prior emails, texts, replies and responses, appointments;
  - gates: suppression, hold and review;
  - sender: Kerry, display name, sender health.
- **Playbooks** for every family:
  - Veteran Planning Guide, Veteran Official, Veteran (Spanish callouts), General Survey, Life Story, Cemetery, Cremation, Re-engagement.
  - Each playbook states why the person is in the database, what one useful fact and one common question to use, the approved facts, prohibited claims, subject variants and tone.
- **Touch strategies**, each touch different:
  1. Reference what they requested and ask for a reply.
  2. One useful fact.
  3. A common question.
  4. An easy appointment.
  5. A permission-based soft close.
  - Reactivation uses a fresh angle.
  - Every touch has an SMS version (under 320 characters).
- **Quality gate** that runs before every automated email. Any failure **holds** the message; it is recorded as blocked, "held by quality check: …", and never sent. It checks:
  - **Identity:** the copy must say Kerry Allan and the location name, the display name must be exactly "Kerry Allan \| Location", and no other location may be named.
  - **Why:** the first touch must say why they are hearing from us.
  - **Grounding:** no invented veteran status ("thank you for your service"), eligibility, free items, benefit promises, prices, family circumstances, property ownership, pressure language or corporate parent name.
  - **Call to action:** one reply-first CTA; "call us" and "click here" are refused.
  - **Tone:** no robotic or corporate phrases ("just checking in", "do not hesitate", …).
  - **Shape:** length limits, no repeated sentences, and no near-duplicate of an earlier email to the same contact.
  - **Gates:** hold, review, suppression, booked appointment, already replied, campaign off, no verified sender.
- **Email touches** now run up to `PROGRAM_EMAIL_MAX_TOUCHES` (default 5), spaced `PROGRAM_EMAIL_FOLLOWUP_DAYS` apart (default 4).
  - Touch 1 uses the family's own email copy when a person has edited it; approved creative wins, but it still has to pass the gate.
  - Otherwise touch 1, and every follow-up, comes from the playbook.
  - The campaign preview ("dry run") shows each message's strategy, quality score and failures.

### Response intelligence — `responses.py`
- **14 intents:**
  - Appointment Intent
  - Information Request
  - Pricing Question
  - Benefits Question
  - Veteran Planning Question
  - Cemetery Interest
  - Cremation Interest
  - General Planning
  - Objection
  - Not Interested
  - Wrong Person
  - Opt-Out
  - Bad Data
  - Simple Acknowledgment
- **Urgency is separate:** HOT, ACTIVE or LOW, stored in `program_responses.urgency`.
  - Information requests are now **ACTIVE**: pause, summary and suggested answer, with no emergency page.
  - Appointment, pricing, interest and follow-up requests stay HOT.
  - Bereavement is always HOT.
- Suggested replies cover the new intents. They make no promises and are never sent automatically.
- **Response timing:**
  - The responses list shows urgency, minutes to open and minutes to respond.
  - Opening a response records **KERRY OPENED** automatically, but only when the viewer is the program's primary contact. A manager looking does **not** stop a HOT SLA clock.

### Customer-facing identity — the unbreakable rule
- New `outreach_programs.customer_identity_locked` (default **on**):
  - The primary contact name cannot be changed (409).
  - Only the platform owner can unlock it.
  - A location's sender-name or SMS sign-off override must still read "Kerry Allan \| <that location>". The API refuses anything else, and `display_name()` ignores anything else even if written straight to the database.
- **Internal audit:** every program email and SMS that goes out writes `program.sent_on_behalf` ("Sent by <real person> on behalf of Kerry Allan / <Location>"). It records the real actor, or *automation*, the channel and the message ID.
- Program settings changes are audited with before and after values.

### Housekeeping
- **booking_links:** removed two startup indexes that named columns the table does not have (`organization_id`, `slug`). They logged "Index skipped" on every boot. A test now pins that booking_links indexes only name real columns.
- **`/health`:** reports `APP_ENV` (`render-staging.yaml` now sets `APP_ENV=staging`). Nothing else reads APP_ENV=staging.
- **Staging harness:**
  - `POST /god/staging/sci/remove-simulation-contacts` deletes the synthetic example.com contacts and everything recorded about them, and nothing else.
  - Seeding is test-contact-only unless `simulation_contacts=true`.
- **Diagnostics copy:** no longer says "read-only". The mailbox is Mail.ReadWrite: it reads, then files processed replies.
- **Mailbox probe:** reports the access the sign-in really grants, and with `?folder=` whether a filing folder exists. It changes nothing.

## 3. Prepared, not done (needs Mike or a decision)

### GitHub command relay
- The issue text is ready: `handoff/MIKE_COMMAND_RELAY_ISSUE.md`. Title: **MIKE COMMAND RELAY — ChatGPT ↔ Claude**.
- Posting it from Mike's browser session was refused by the safety system, and this session has no GitHub API access to the repo.
- Mike's one action: open github.com/MikeSimmonsAI/advisorflow-web/issues/new, paste the title and the file's text, and submit. Alternatively, give Claude repo access in a future session.

### Michael — manager access on his existing login
- There is no customer-workspace "manager" role today. The workspace roles are org_admin, advisor and viewer.
- The existing, audited path that gives an EXISTING login access to a second workspace without creating a user is: **God → Manage Access → find the identity by email → Preview → Apply**, with scope `customer_org` = Service Corporation International and role `org_admin`.
- That gives what was asked for, scoped to SCI only:
  - SCI locations, contacts, conversations and replies;
  - reassign, stage, appointments, tasks, pause/resume;
  - HOT and unhandled responses, response times, campaign/location/health views.
- It does **not** give:
  - platform administration;
  - other tenants;
  - secrets or environment access;
  - billing outside SCI.
- Caveat: inside SCI, org_admin can also manage SCI's own users and settings.
- Not done tonight: identifying his account means reading production users, and the grant itself is a production access change. Mike should confirm the one account. If there is more than one candidate, do not create another.

### 39 local numbers (location-number model)
No numbers were bought. Two reasons:
1. **Code gap found and FIXED tonight:** inbound SMS used to resolve the workspace only from user/organization number columns, so a reply to a per-location number in `phone_numbers` would have been dropped.
   - It now resolves the `To` number exactly as voice does. The number's `workspace_id` names the location: the reply is attached to the contact's conversation with the location recorded, and the cadence pauses.
   - An unknown texter goes to the program's unmatched queue with the location known. It is never attached to anyone, and a Twilio retry is kept once.
   - Tests: `tests/test_sci_platinum.py`.
2. **Registration:** sending SCI-location messages from 39 numbers needs a 10DLC campaign whose description matches that traffic. Whether that fits the existing EvoSys/EvoSense brand, or needs an SCI or multi-location registration, is a compliance attestation Mike has to make. It must not be made on his behalf.

The purchase map, per location:
- Location (39, as in `scripts/sci_location_aliases.csv`)
- City/state: **verify from SCI**; addresses are not in the source data and none were invented
- Local area code: from the verified city
- Capabilities needed: SMS + voice + voicemail
- Status: *registration needed*

Keep the existing toll-free number as backup, overflow and special-program line.

### Voice
- Existing: an inbound call creates a log and rings or goes to voicemail; voicemail is recorded.
- **Transcription is not implemented** (`transcript_status = not_enabled`).
- Still to build:
  - location-aware greeting;
  - a configurable per-location live forwarding number. Kerry's cell is unknown and must not be invented;
  - missed call → activity + suggested Kerry SMS (never auto-sent);
  - transcription + AI summary.

## 4. Simulated proof

### Staging, commit f12a70e
- Real staging database and real signed webhook; synthetic example.com contacts were removed afterwards.
- Staff alerts were patched to "simulation - not sent", so nobody was messaged.

| Case | Result |
|---|---|
| normal "ok thanks" | LOW, Simple Acknowledgment; filed to Inbox/Customers Folder/SCI/Eastern Gate Memorial Gardens (simulated mover) |
| active question | ACTIVE, Information Request + Veteran Planning Question; filed |
| opt-out | OPT-OUT, email opt-out of record, cadence `stopped_dnc` |
| wrong person | WRONG PERSON, record sent to Data Review |
| unknown sender to an alias | kept in the unmatched queue (open), **not** filed |
| right contact, wrong alias | the contact keeps their own location |
| duplicate inbound | the second read matched 0, one reply row |
| duplicate outbound | refused (409), a test email was already delivered |
| missing location | refused: LOCATION REVIEW |
| held contact | refused: ON HOLD |
| flyer missing | held (6 held, 0 would send); campaign left off |
| Resend errors | 429/503 temporary, 422 permanent, timeout unknown; backoff 10/45/180 min |
| mailbox 503 | recorded as error, cursor kept |
| bounce (real signed webhook, staging secret) | message `bounced`, address flagged `bad_email`; redelivery deduplicated |
| HOT SLA escalation | HOT, re-alerted once (alert kinds hot + sla_breach) |

After `remove-simulation-contacts` the workspace again holds only the approved test contact: held 0, unmatched 0, campaigns off, 0 touches.

### Tests
- 16 new tests in `tests/test_sci_platinum.py`.
- Updated `tests/test_outreach_program.py`.
- Windows targeted run: 401 passed.
- Container full suite: see the final report.

## 5. Do not

- Merge `sci-program` to main.
- Switch any campaign on.
- Delete the staging database (keep it until Mike approves cleanup).
- Delete held SCI records.
- Touch the Restland FSA Command system.
