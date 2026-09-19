# Installing a vertical

Every step below is **data applied through an authoritative flow**. Nothing
here is code, nothing is seeded on deploy, and nothing creates a second
organization. That is the same posture as the launch runbook beside it, for
the same reason: configuration that only one customer can use is the thing
this design exists to avoid.

Two capabilities carry a vertical:

| What the customer sees | What it actually is |
|---|---|
| Their own workflow screens in the rail | `Organization.workspace_views`, or their industry's default. `app/services/workspace_views.py` |
| Their own public website, whose forms reach their own workspace | a `customer_sites` row. `app/services/customer_sites.py` |

Neither creates a record type. A view is the lead or appointment table asked a
different question; a site form becomes a `Lead` through the same
`public_capture` path every other public form uses, so consent evidence,
deduplication, provenance and plan capacity behave identically.

---

## 1. Point the organization at its industry

```
python scripts/configure_customer_workspace.py --org-id <id> --industry <key>
```

Dry run first — it prints what it would change and exits. Add `--apply` when
the diff reads right.

`--industry` is normalized through `industry_templates`, so a label works as
well as a key (`"Energy / Procurement"` → `energy`). Applying it also seeds the
organization's `TierDefinition` rows from that industry's set, and seeding is
idempotent: an organization that already has rows keeps them.

Known keys live in `app/services/industry_templates.py`. Two that matter here:

* **`energy`** — aliases include `utilities`, `power`, `electricity`,
  `light_and_power`, `energy_supply`. Tiers `new_inquiry`, `rate_review`,
  `proposal_sent`, `contract_signed`, `renewal_due`. Supplies the screens
  **Rate Requests**, **Renewals**, **Consultations**.
* **`home_services`** — `cleaning` is one of its aliases, so a commercial
  cleaning company needs nothing added to the registry. Tiers `new_lead`,
  `scheduled`, `quoted`, `job_booked`. Supplies **Prospects**, **Follow-Up**,
  **Walkthroughs**.

### If a screen is genuinely missing

Ask one question: will the *next* customer in this vertical want it too?

* **Yes** → add it to that template's `workspace_views` list in
  `industry_templates.py`. Every customer on that industry gets it.
* **No** → write a file under `config/workspace-views/` and apply it with
  `--views`. The column **replaces** the industry list rather than merging
  with it, so the file must name every screen that customer should see.

```
python scripts/configure_customer_workspace.py --org-id <id> \
    --views config/workspace-views/<file>.json [--replace-views] [--apply]
```

The script validates the file through the same parser the server uses and
refuses anything the server would silently drop.

## 2. Switch on the modules the customer bought

```
python scripts/configure_customer_workspace.py --org-id <id> \
    --features leads,reports,users,master_dashboard,branding_settings [--apply]
```

`enabled_features` is an **allow-list**. NULL means "everything" and is the
legacy state; `[]` means nothing. Unknown keys are refused rather than stored —
`app/services/entitlements.py` holds the vocabulary.

Workflow views are deliberately **not** feature-gated. A view only ever shows
records the reader can already reach through Leads, so gating it would hide a
screen from somebody who can see the same rows through another door.

## 3. Give the workspace the customer's own identity

```
python scripts/configure_customer_workspace.py --org-id <id> \
    --brand-name "..." --brand-logo-url "..." \
    --primary-color "#..." --accent-color "#..." [--apply]
```

These land on the organization row and drive the sidebar name, the logo and
the CSS custom properties. The white-label **brand** underneath (its name,
theme and support address) comes from the organization's `platform_id` and is
not set here — a customer belongs to a brand, and a script that could move one
between brands is a script that will.

## 4. Publish the customer's public website

The page lives in the repo at `public-sites/<slug>/index.html` so it is
versioned and diffable; the `customer_sites` row is what serves it, so
publishing needs no deploy.

```
python scripts/publish_customer_site.py --org-id <id> --slug <slug> \
    --file public-sites/<slug>/index.html \
    --consent-file public-sites/<slug>/consent.txt [--apply]
```

It refuses to publish a page that still carries prototype text, that quotes a
per-kWh rate, or whose form posts nowhere. `--force` overrides, and should be
rare enough to need explaining.

Republishing keeps the address. The URL is on the customer's stationery.

### What a live page may not do

* **State a price, rate, plan or availability the platform cannot prove.** A
  number on a customer's live site is a representation that customer has to
  stand behind. Where a provider feed is not connected, the page asks for the
  enquiry instead of inventing the answer.
* **Hold a password, a demo login or a fabricated record.** The client portal
  link goes to the real authenticated application.
* **Send anything.** An enquiry creates a record. No email, no SMS, no voice,
  no notification. Outreach begins when a person decides it does.

## 5. Check it

| Check | How |
|---|---|
| The rail shows the right screens | Sign in to the workspace; the items appear under their configured group |
| A screen shows only that workspace | `GET /workspace-views/<key>` as a member of another organization returns 404 |
| The page is live | `GET /site/<slug>` returns the page |
| A form files where it should | Submit it; the lead appears in that organization's Leads with `source` naming the customer's own site |
| Nothing went out | No `Message`, `EmailMessage` or `Notification` row follows the enquiry |

`tests/test_public_sites_ship_correctly.py` runs the last three against the
real markup in `public-sites/` on every test run, so a page that was never
rewired fails there rather than in front of a customer.

---

## What this does NOT replace

* **The Launch Experience.** A customer's onboarding is `/launch`, configured
  through `PUT /launch-experience/config/...`. See
  `ATLANTIS_PRODUCTION_RUNBOOK.md`. Do not rebuild it, and do not link a
  workspace to it with an organization id — `/launch` resolves the workspace
  from the session.
* **Provisioning.** `app/services/customer_provisioning.py` creates the
  organization and `app/services/provisioning.py` turns a won deal into one.
  This script configures an organization that already exists.
* **Tenancy.** A person reaches a workspace through a `Membership` with
  `scope_type="customer_org"`. One operator managing many customer
  organizations is that, many times over — see
  `app/services/workspace_access.py`. Nothing in this runbook grants access,
  and nothing here can widen it.
