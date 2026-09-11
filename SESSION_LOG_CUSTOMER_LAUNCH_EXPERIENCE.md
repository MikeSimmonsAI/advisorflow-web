# THE CUSTOMER LAUNCH EXPERIENCE

**The approved onboarding design, built once at the platform layer.**

This document is the record of what was built, why it is shaped this way, and
exactly how a customer's onboarding is configured — as DATA, never as code.

---

## 1. WHAT THIS IS

```
        ADVISORFLOW              the platform capability
             |
        LAUNCH ENGINE            onboarding — implementations, intake, delivery
             |
   LAUNCH EXPERIENCE LAYER       how that engine LOOKS and READS  ← this work
             |
     WHITE-LABEL BRAND           whichever brand owns the relationship
             |
   CUSTOMER ORGANIZATION         the customer being onboarded
```

There is **one** customer onboarding shell. It is configured per brand, per
industry and per customer. There is no per-customer page, no per-customer
route, and no second onboarding engine — the intake, the implementation
record, the files, the users, the integrations, the checks, the training and
the go-live gate are all the machinery that already existed.

What was added is a **presentation, journey and form configuration layer** in
front of it, and an **internal preview** of the result.

---

## 2. WHAT WAS ADDED

| File | What it is |
|---|---|
| `app/models/launch_experience_models.py` | `launch_experience_configs` — one row per layer |
| `app/services/launch_experience.py` | defaults, the merge, `resolve()`, `compose()` |
| `app/routers/launch_experience_router.py` | `/launch-experience/*` — 5 routes |
| `frontend/src/pages/launch/LaunchFooter.jsx` | configured footer |
| `frontend/src/pages/launch/PreviewBanner.jsx` | the internal-preview strip |
| `tests/test_launch_experience.py` | 44 tests, 8 sections |

Rewritten to read configuration: `LaunchHero.jsx`, `LaunchSidebar.jsx`,
`OnboardingProgressPanel.jsx`, `LaunchProgress.jsx`, `LaunchPad.jsx`.

Extended: `app/services/industry_templates.py` gained an `experience` block
per template and an `experience()` accessor.

---

## 3. THE FOUR CONFIGURATION LAYERS

Resolved on the server, in this order, each overriding the one before:

```
1. PLATFORM DEFAULT   launch_experience.DEFAULT_PRESENTATION / _JOURNEY / _FORM
2. INDUSTRY           industry_templates.experience(<key>)   — code default
   INDUSTRY ROW       scope_type="industry",   scope_id=<industry key>
3. BRAND              scope_type="brand",      scope_id=<platform id>
4. CUSTOMER           scope_type="organization", scope_id=<organization id>
```

**Dictionaries deep-merge. Lists REPLACE.** A brand that configures a
five-stage journey gets five stages, not its five appended to the platform's
seven. Merging lists by position is how a configuration system starts
producing arrangements nobody wrote.

`resolve()` also returns `layers`, saying which rows actually contributed — so
an operator asking "why does this customer's page say that" is told which row
to edit rather than left guessing between four.

### Who may edit which layer

| Layer | Who |
|---|---|
| `platform_default` | god only — it is what every unconfigured customer on every brand sees |
| `industry` | god only — an industry template is platform policy, not one brand's preference |
| `brand` | god, or an operator of that brand |
| `organization` | god, or an operator of the brand that owns that customer |

**A customer may not configure their own shell.** They fill in their
onboarding; the shell it renders in belongs to the brand delivering it.

This uses the existing authority model (`sales_access.is_god`, brand-sales
memberships). No second permission system was introduced.

---

## 4. THE SEVEN-STAGE JOURNEY

```
1 Complete Intake        2 Provide Access & Files    3 <Brand> Builds
4 Integrations           5 Review & Test             6 Training
7 Go Live
```

Labels and sub-labels are configuration. **States are not.** Each stage's
state comes from the implementation's own status via
`launch_intake.lifecycle_for()`. A brand renaming a stage changes the words
and nothing else — it cannot make a customer look further along than they are.

A customer's own third-party system is named in a stage **sub-label**, from
configuration, for the customers it applies to. It is never in the component.

---

## 5. PROGRESS IS EARNED, NOT COUNTED

`overall_pct` comes from `launch_intake.overview()`, which counts **required
fields actually answered** across the stored sections.

* A customer who clicks through every screen without typing sees **0%**.
* Opening the internal preview moves nothing.
* There is no browser-side percentage anywhere in the shell.

`TestProgressIsEarned` asserts both halves: looking never moves it, and
answering always does.

---

## 6. THE INTERNAL PREVIEW

**Route:** `/launch/preview/:organizationId` (and `/:stepKey`)
**Endpoint:** `GET /launch-experience/preview/{organization_id}`
**Entry point:** God Mode → Customer Launches → *Preview their onboarding*

It is the **same component and the same design** the customer gets. Not a
staff rendering of onboarding — a staff rendering would drift, and the whole
point is that an operator sees exactly what the customer will see.

### Why it is safe, structurally rather than by promise

* **It runs the same composer.** `launch_experience.compose()` serves both the
  customer's page and the preview. There is no second path to keep in step.
* **The composer is read-only.** Everything it calls reads rows and returns a
  payload. There is no write behind the preview because there is none behind
  the customer's own read either.
* **Nobody is logged in as anybody.** The caller stays themselves — their own
  token, their own audit identity. No customer session is minted.
* **Every write in the component is off.** `persist`, `uploadFile`,
  `removeFile` and `submit` each return immediately in preview, and the step
  bodies render `readOnly`.
* **No customer answers are in the payload.** `answers_included: false`, and
  the banner says so. Judging the experience does not require reading what
  somebody wrote; reading their answers is the staff review screen, which is
  separately scoped and separately audited.
* **The delivery panel is composed server-side.** The customer's own shell
  fetches `/launch/me/delivery`, which is session-scoped — inside a preview
  that would paint the *operator's* integrations onto the customer's page.

### What a preview does NOT do

No invitation. No customer login. No impersonation. No completion event. No
acknowledgment or signature. No message sent. No row created of any kind.

`TestPreviewChangesNothing` snapshots **every** table the surface can write to
before and after, rather than asserting about the one row the test expected
not to appear — a preview that starts writing something nobody anticipated is
exactly the failure that is for.

### What it DOES record

One audit entry, `launch_experience_previewed`, against the organization —
so an operator reviewing a customer's history can tell a preview apart from
the customer's own first visit. That confusion is what an unlogged preview
would create.

### Who may preview whom

God, or staff of the brand that owns the customer. Anyone else gets **404,
never 403** — a distinguishable refusal is a customer-enumeration oracle with
extra steps.

---

## 7. INDUSTRY AWARENESS

A funeral home is never asked an energy question. An energy business is never
shown funeral vocabulary. An industry nobody recognises resolves to
**generic**, never to a vertical.

This is the defect this platform already shipped once, and
`TestIndustryAwareness` fails if any industry becomes any other industry's
fallback — including via the blank, empty and unknown cases.

---

## 8. NO CUSTOMER IS IN THE CODE

`TestNoCustomerIsInTheCode` scans the experience service, its router, its
models and **every** `.jsx` under `frontend/src/pages/launch/` for word-
boundary matches on customer names, partner names, brand names and revenue
splits. It also asserts that no mockup placeholder — phone number, street
address or email — reached the shipped defaults.

A design's example values are PLACEHOLDERS. Shipping one puts a stranger's
details on a real customer's onboarding page.

The customer's own name arrives as the token `{customer}`; the brand as
`{brand}`; the year as `{year}`. Those are the only three tokens.

---

## 9. HOW TO CONFIGURE A REAL CUSTOMER

**This is DATA. Do not put it in code, and do not seed it during deploy.**

An operator with authority over the customer's brand sends:

```
PUT /launch-experience/config/organization/{organization_id}
{
  "name": "<customer name> onboarding",
  "presentation": {
    "eyebrow":   "Welcome to {brand}",
    "title":     "{customer}",
    "subtitle":  "<the customer's own subtitle>",
    "intro":     "<the customer-facing paragraph>",
    "hero_image_url":   "<the customer's own imagery, supplied by them>",
    "hero_logo_url":    "<the customer's own logo, supplied by them>",
    "hero_overlay":     "deep",
    "customer_tagline": "<the line they describe themselves with>",
    "rail_image_url":   null,
    "rail_tagline":     ["<short>", "<lines>"],
    "help":  { "title": "Need Help?", "body": "...", "cta_label": "Contact {brand}" },
    "guide": { "title": "Download Guide", "body": "...", "url": "<a real document, or omit>" },
    "quote": { "text": "...", "attribution": "..." },
    "footer": { "links": [ ... ], "copyright": "© {year} {brand}. All rights reserved." }
  },
  "journey": [ ... seven stages, sub-labels naming THEIR systems ... ],
  "form":    { "sections": [ ... extra questions for THIS customer ... ] }
}
```

Rules that are not negotiable:

* **Imagery must be supplied by the customer.** `hero_image_url` and
  `hero_logo_url` default to `null` and the shell renders a finished page
  without them. A shell that invents a hero picture shows somebody else's
  photograph on their launch page; an invented logo gets mistaken for the
  real one and has to be found and removed later.
* **`guide.url` is either a real document or absent.** When absent the card
  says the checklist has not been published yet rather than offering a link
  that 404s.
* **Industry-wide changes go on the industry layer, not the customer's.** If
  the second customer in that vertical will want it too, it is an industry
  row.
* **Nothing here invites anybody.** Configuration is configuration.

### Verifying a customer before they are invited

1. God Mode → **Customer Launches**
2. Find the customer → **Preview their onboarding** (opens in a new tab)
3. Step through all eight intake sections using the right-hand checklist
4. Confirm: their brand at the top, their identity in the hero, their journey
   labels, their industry's questions, their real progress, the preview strip
   visible throughout

Nothing in that sequence invites the customer, activates AI outreach, creates
billing, or seeds data. Inviting them remains a separate, explicit,
authorized action.

---

## 10. WHAT WAS DELIBERATELY NOT BUILT

* **No second onboarding database.** The intake, implementation, file, user,
  integration, check, training and go-live records are the existing ones.
* **No competing implementation engine.** `compose()` reads the launch
  engine; it does not reimplement it.
* **No free-form page builder.** Configuration supplies *content* — copy,
  imagery, labels, extra questions. The shell supplies the *design*. A brand
  cannot rearrange the page by editing a settings row.
* **No customer-specific React route or component.** One shell, four layers.
* **No second permission system.** Existing authority, existing scoping.
* **No second go-live state machine.**
* **No impersonation.** The preview is a read, not a session.

---

## 11. TEST COVERAGE

`tests/test_launch_experience.py` — 44 tests:

| Section | What it locks shut |
|---|---|
| 1. Precedence | four layers, deep merge, list replacement, inactive rows, `layers` reporting |
| 2. Industry awareness | no cross-vertical leakage, unknown → generic, industries can still differ |
| 3. Preview changes nothing | full row-count snapshot, no invitation, no submission, no answers, audit written, delivery composed, identical payload to the customer's |
| 4. Preview isolation | anonymous refused, cross-brand 404, indistinguishable refusal, owning brand allowed, customers cannot preview |
| 5. Config authority | platform/industry are god's, brands own their own, cross-brand refused, customers refused, listing scoped |
| 6. Progress is earned | zero when nothing typed, looking never moves it, answering always does |
| 7. The approved design | every shell part exists, seven stages, Save Draft / Save & Continue, components read configuration, every write is off in preview |
| 8. No customer in the code | word-boundary scan of service, router, models and every launch `.jsx`; no mockup placeholders in the defaults |

Also green: `test_launch_engine.py`, `test_launch_delivery.py`,
`test_launch_productization.py`, `test_industry_and_brand_boundaries.py`,
`test_industry_migration.py`.
