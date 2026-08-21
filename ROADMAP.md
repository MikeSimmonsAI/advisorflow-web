# BookaBoost / AdvisorFlow — Product Roadmap
*Last updated: Aug 20 2026*

---

## What BookaBoost Is

BookaBoost is not a CRM. It is a **full automated revenue system** — the only platform that takes a business from a ZIP code search all the way to a booked appointment with zero human touchpoints required.

**The full flow:**
```
Ad / Social / ZIP Search
        ↓
Funnel / Landing Page / Lead Scraper
        ↓
Lead Created in BookaBoost
        ↓
AI Outreach (SMS or Email, routed by channel)
        ↓
Automated Cadence (fires on schedule, no human)
        ↓
AI Auto-Reply (responds to inbound without human)
        ↓
No Response? → AI Voice Call (Tier 9)
        ↓
Booking Link Sent
        ↓
Appointment Booked
        ↓
Data retained at AdvisorFlow god layer forever (Tier 10)
```

No other platform does this end-to-end in one product at this price point.

---

## Current Feature Set (Live Today)

### Lead Management
- Lead dashboard with tier system: Hot / Warm / Cold / Dead
- Lead detail view: case file, notes, conversation history, outcome tracking
- Relationship type: cold lead, warm lead, previous prospect, existing customer, past customer, re-engagement
- Signal Pulse: lead engagement scoring and activity tracking
- Status tracking with reason codes

### SMS Outreach
- Send texts directly to leads from the platform
- Full inbound/outbound conversation history per lead
- Twilio integration (toll-free number pending verification)
- SMS opt-in compliance page (STOP/HELP handling, consent tracking)
- 160-character segment enforcement (one charge = one segment)

### AI Message Suggestions
- One-click AI-generated SMS draft per lead
- Tone engine: Cold / Warm / Hot / Urgent — changes how AI writes
- AI Direction field: advisor gives custom instruction to AI
- Sample message foundation: AI uses advisor's template as base
- URLs stripped automatically — booking link added cleanly at send time

### Automated SMS Cadence
- Drip sequences that fire on a schedule automatically
- No advisor action required after setup
- Cadence tied to lead tier and relationship type

### AI Auto-Response
- Automatically replies to inbound SMS using AI
- No human involvement required
- Pauses when human takes over, resumes when set back

### Email Outreach
- Send emails to leads from the platform
- AI generates 3 draft options with talking points per lead
- Tone-aware, relationship-type-aware
- Resend integration (verified domain sending)

### White-Label Booking Page
- Custom booking links generated per lead (JWT token-based)
- Google Calendar integration
- Appointment type auto-detection (pre-need, at-need, file review, etc.)
- "Include booking link" checkbox adds clean link at send time

### Multi-Tenant / Agency Architecture
- Multiple organizations per platform
- Multiple advisors per organization
- god_admin (AdvisorFlow layer) sits above all platforms invisibly
- Super admin scoped to their platform only
- Full white-label branding per organization

---

## Roadmap — In Priority Order

---

### TIER 1 — Fix & Stabilize (Immediate)

- Render health check: set `/health` as health check path → auto-restart on freeze
- UptimeRobot external monitoring — alerts when backend goes down
- "Mike the GOD" name fix in Settings → Profile
- Twilio toll-free +18449172171 — awaiting 24-48hr verification (submitted Aug 20 2026)
- Activity Log "Failed to fetch" — timeline_router route ordering bug
- Leads page slow load — missing DB indexes, needs pagination

---

### TIER 2 — Self-Service Booking Settings

Advisors need to control their own booking page without a code change.

- Available days and hours per advisor
- Appointment duration (15 / 30 / 45 / 60 min)
- Buffer time between meetings
- Max bookings per day
- Timezone selector
- Appointment types offered
- Confirmation message (customizable)
- Custom scheduling link field (Calendly/Cal.com escape hatch)

---

### TIER 3 — Lead Scraper / Import Engine

ZIP code + business type → Google Places API → phone validation → auto-import → cadence fires.

- Mobile numbers → SMS outreach queue
- Landline + email → email outreach queue
- Landline, no email → AI voice call queue (Tier 9)
- All leads retained in AdvisorFlow god-layer database (Tier 10)
- Cost: ~$17/1,000 results via Google Places API + pennies per phone lookup

---

### TIER 4 — Agency Model (Master + Sub-Accounts)

Master account scrapes and assigns leads to sub-accounts (individual service providers). Sub-accounts only see their own leads. Master sees everything.

- Lead assignment from master to sub-accounts
- Bulk lead import (paste a list, it loads instantly)
- Agency analytics — appointments booked per sub-account, conversion rates

---

### TIER 5 — Social Lead Capture (Speed to Lead)

Meta/Facebook, Instagram, TikTok, Google Ads lead forms → webhook fires instantly into BookaBoost → AI text goes out in seconds. No CSV download, no delay. Contact rate drops 80% after 5 minutes — this eliminates that gap entirely.

---

### TIER 6 — Funnels

Landing page builder inside BookaBoost. Lead fills out form → created in system instantly → cadence fires automatically. UTM tracking to know which ad sent the lead.

---

### TIER 7 — AdvisorFlow God Layer (In Progress)

- god_admin role and Command Center dashboard
- Unified view across all platforms (BookaBoost / EvoSys Pro / Harmony Hustle)
- Platform entity and domain-based branding routing
- White-label login portals per brand
- Granular super admin permission editor
- Location-based ad targeting from Command Center
- Migrate mike@simmonsstrong.com to god_admin

---

### TIER 8 — Monetization & Billing

- Stripe per platform, per-org billing isolation
- Client self-service billing portal
- Usage-based SMS/email billing pass-through
- Lead scraper credits (pay per 1,000 leads pulled)
- Agency plan tier with sub-account pricing

---

### TIER 9 — AI Voice Calling (Completes the Full Suite)

The last piece that closes every gap in the automated flow. Lead doesn't respond to SMS or email → AI voice agent calls them automatically.

**What it does:**
- AI introduces itself, qualifies the lead, answers questions
- Books the appointment directly on the calendar if interested
- Marks lead dead and stops outreach if not
- Full call transcript and outcome logged in lead's case file

**Technology options:** Bland AI, Vapi, Retell AI — all have clean APIs, plug in without rebuilding anything.

**Why this matters:** Every lead now has a path with no dead ends:
- Mobile → SMS → AI auto-reply → book
- Landline + email → email outreach → book
- No response → AI voice call → book

No competitor has scraping + SMS + email + AI auto-reply + AI voice natively in one platform at this price point. This is the complete suite.

---

### TIER 10 — Data Monetization (The Silent Business Inside BookaBoost)

Every lead ever scraped, imported, or captured through funnels and webhooks — across every client, every org, every ZIP code — lives in the AdvisorFlow god-layer database. Clients use the tool. AdvisorFlow owns the database.

This compounds silently over time and becomes worth more than the SaaS itself.

**Revenue streams:**
- **Lead packages** — sell verified lists by ZIP + industry directly to businesses
- **Audience targeting** — upload to Meta/TikTok as custom audiences, run ads against them
- **Appointment selling** — use your own tools on your own leads, sell booked appointments per appointment (not per month)
- **Data licensing** — license the verified database to other platforms or data brokers
- **Your own operation** — run outreach on your own leads, sell the outcome directly, never need a single BookaBoost customer to generate revenue

**This is how Zillow works.** Agents use the platform. Zillow owns the data and eventually competes with them. Clients get their leads. AdvisorFlow retains the aggregate at the god layer.

The SaaS pays for the infrastructure. The data is the asset that compounds forever.

---

## The One-Sentence Pitch

> *"The only platform where you enter a ZIP code and a business type, and appointments get booked automatically — no spreadsheets, no manual calling, no duct tape."*

---

## What No Competitor Has (The Moat)

| Feature | BookaBoost | GoHighLevel | Podium | Close CRM |
|---|---|---|---|---|
| ZIP code lead scraping built-in | ✅ (building) | ❌ | ❌ | ❌ |
| Phone validation + channel routing | ✅ (building) | ❌ | ❌ | ❌ |
| AI auto-reply on inbound SMS | ✅ | Partial | ❌ | ❌ |
| AI voice calling | ✅ (building) | Partial | ❌ | ❌ |
| Automated cadence | ✅ | ✅ | ❌ | Partial |
| White-label booking page | ✅ | ✅ | ❌ | ❌ |
| Social webhook → instant outreach | ✅ (building) | ✅ | ❌ | ❌ |
| Agency master + sub-accounts | ✅ (building) | ✅ | ❌ | ❌ |
| Proprietary lead database (god layer) | ✅ | ❌ | ❌ | ❌ |
| Price point | Low | $97–$297/mo | $300–400/mo | $65–145/mo |
| Full end-to-end automated flow | ✅ | ❌ | ❌ | ❌ |

---

## Summary: Two Businesses on One Platform

**Business 1 — The SaaS:** Agencies and service businesses pay monthly. Recurring revenue.

**Business 2 — The Data Asset:** Every lead ever touched by the platform lives in the AdvisorFlow god layer. Compounds silently. Worth more than the SaaS long term.

```
Scrape → Validate → Import
→ AI SMS → Auto Cadence → AI Auto-Reply
→ No response: AI Voice Call
→ Appointment Booked
→ Data retained at god layer forever
```

One product. One price. Zero spreadsheets. Zero humans required.
