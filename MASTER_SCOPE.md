# BookaBoost / EvoSys Pro — Master Scope & Architecture
*Last updated: Aug 15 2026*

---

## God Account (Master Owner)

**Email:** mike@simmonsstrong.com  
**Role:** `god_admin` — a new role that sits above `super_admin`

### Rules
- Super admins **cannot see** the god account exists — it is completely invisible to them
- God account has **full, unrestricted access** to everything in the system
- God account can **create, modify, and revoke** any super admin
- God account can set **granular permission caps** per super admin (e.g. no billing access, no lead export, read-only reports, etc.)
- God account sees **every platform, every org, every lead** simultaneously

### God Dashboard needs to show
- All platforms (BookaBoost, EvoSys Pro, Harmony Hustle...)
- All orgs under each platform
- All leads across every org — unified master database
- Lead data: name, email, phone, location, source platform, org, status, tier
- Ability to filter/search across everything at once
- Location-based targeting: push aggregated lead data to FB/TikTok/Instagram ad audiences

### Migration needed
- Current super admin `simmonsmj242@gmail.com` → migrate to `mike@simmonsstrong.com` at `god_admin` role

---

## Platform Hierarchy

```
mike@simmonsstrong.com  [god_admin]
│
├── BookaBoost  (platform)
│   Super admin: TBD
│   Email: support@bookaboost.live
│   Domain: app.bookaboost.live (planned)
│   │
│   ├── Restland Cemetery & Funeral Home  [org]
│   ├── Rich For Real Holdings  [org]
│   └── [future BookaBoost clients]
│
├── EvoSys Pro  (platform)
│   Super admin: TBD
│   Email: support@evosyspro.live
│   Domain: app.evosyspro.live (planned)
│   │
│   ├── SCI  [org]
│   └── [future EvoSys clients]
│
└── Harmony Hustle  (platform — future)
    Super admin: TBD
    │
    └── [real estate company orgs]
```

### Key rules
- Each brand's super admin sees **only their own platform's orgs** — no cross-brand visibility
- Each brand has its own email sender, its own login URL, its own branding
- Same backend/frontend — multi-tenant, domain-based branding detection

---

## Super Admin Granular Permissions (to build)

God account controls what each super admin can do. Configurable per super admin:

| Permission | Description |
|---|---|
| view_all_leads | See all leads across their platform |
| export_leads | Download/export lead data |
| manage_advisors | Add/remove/edit advisors |
| manage_orgs | Create/edit org settings |
| view_billing | See billing info |
| manage_billing | Change plans, payment methods |
| view_reports | Access analytics/reports |
| send_campaigns | Launch bulk outreach |
| manage_templates | Edit SMS/email templates |
| manage_integrations | Connect Twilio, Resend, Google, etc. |

---

## Client Login Portals (to build)

Each brand needs its own white-labeled login page:
- `app.bookaboost.live` → BookaBoost branding, login
- `app.evosyspro.live` → EvoSys Pro branding, login
- Future: `app.harmonyhustle.com` (or similar)

When a user visits either domain, the system detects the hostname and serves the correct branding, no AdvisorFlow or BookaBoost visible to EvoSys users and vice versa.

---

## Billing (Future — not yet scoped)

- Each brand bills its own clients independently
- BookaBoost bills Restland, Rich For Real, etc.
- EvoSys Pro bills SCI, etc.
- Stripe or similar payment processor
- Client self-service billing portal
- God account sees all billing across all brands

---

## Email Sender (DONE — Aug 15 2026)

Each org sends from its own verified domain:
- **BookaBoost:** `support@bookaboost.live` — domain verified in Resend ✅
- **EvoSys Pro:** `support@evosyspro.live` — domain being verified (DNS auto-configured, pending propagation)

After Render redeploys: go to OrgSettings → Email Sender → enter from_email + Resend API key for each org.

---

## Immediate Build Queue

### 1. God account (HIGH — start next)
- Add `god_admin` to User role enum/check
- God-level middleware: bypass all org scoping
- God dashboard page: platform selector + unified lead table
- Granular super admin permission editor
- Migrate mike@simmonsstrong.com to god_admin

### 2. Platform entity (needed for god account)
- Add `Platform` model: id, name, slug, brand (bookaboost/evosys/harmony)
- Link organizations to a platform
- Super admin login scoped to their platform

### 3. Domain-based branding routing
- `app.bookaboost.live` and `app.evosyspro.live` as custom Render domains
- Backend `/branding` endpoint detects hostname → returns correct brand theme
- Frontend reads hostname on load → applies branding

### 4. Client login portals
- White-labeled login page per brand domain
- No cross-brand branding visible

### 5. Billing
- Stripe integration
- Per-brand billing isolation

---

## Fixes Needed Now

| Issue | Priority | Notes |
|---|---|---|
| Activity Log "Failed to fetch" | HIGH | timeline_router route ordering on Render |
| Leads page slow load | HIGH | Missing DB indexes, needs pagination |
| Twilio A2P approval | HIGH | Waiting on carrier. Workaround: buy toll-free number ($2/mo, 1-2 day approval) |
| evosyspro.live Resend verification | PENDING | DNS auto-configured, waiting to propagate |

---

## Queued (not yet started)

- Social media lead capture (FB/TikTok/Instagram webhooks)
- Client auto-provisioning script
- Sub-60-second inbound lead response (currently 2-min poller)
- Post-appointment review request SMS
- Fiber Cartel door-knock FastAPI router
- Upload 12 website pages to GoDaddy
- Internal surveys pushed to website
- Location-based ad targeting from god dashboard
