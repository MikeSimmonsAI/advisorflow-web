# ATLANTIS — PRODUCTION LAUNCH CONFIGURATION

**Every step below is DATA applied through the authoritative flow. Nothing
here is code, nothing is seeded on deploy, and nothing creates a second
Atlantis organization.**

---

## 0. WHY ATLANTIS WAS MISSING — THE ACTUAL CAUSE

Not a missing organization. Atlantis exists. The Launch Engine could not
represent it.

```
Implementation.opportunity_id   NOT NULL  →  FK opportunities.id
```

The only code that created an `Implementation` was
`provisioning.provision` — the Won-opportunity → Customer crossing. So a
customer created any other way (an operator using God Mode → Create Customer,
or a tenant migrated from before the pipeline) **could not hold a launch
record at all**. Customer Launches was built by listing implementations, so
such a customer was not shown as "not started" — they were not shown.

The control plane had already half-noticed: `god_operations` counts
`customer_organizations_without_implementation`, with a comment saying
"pretending every customer came from an opportunity is how a control plane
starts lying to its owner". Those customers were counted, then locked out.

The ZZ Launch Verify records appeared because they had implementations.
Atlantis did not.

### What was changed, at the platform layer

| Change | Why |
|---|---|
| `Implementation.opportunity_id` is nullable | NULL now means "this customer did not come from a deal in this pipeline" — a fact, not a gap. The alternative is inventing an opportunity, which puts a fake sale in the pipeline and in the Won metrics. |
| `implementation_service.start_for_organization()` | Idempotent, audited. Creates one Implementation and its pending checklist. Nothing else. |
| `POST /god/launch/{organization_id}/start` | The operator action, scoped and audited. |
| Customer Launches lists customers with **no** launch | `launch_started: false`, all figures zero, blocker "Onboarding has not been started". Visible instead of invisible. |
| `POST /god/customers` starts the launch in the same transaction | So this cannot happen to the next customer. |
| `CustomerCreate.industry` default `"funeral"` → generic | An unstated industry is unstated, never somebody else's vertical. |

Uniqueness is unaffected — SQL unique indexes permit many NULLs — and the one
query asking the inverse question (`awaiting_provisioning`) already excluded
NULLs.

---

## 1. START THE ATLANTIS LAUNCH

**God Mode → Customer Launches → filter "All"**

Atlantis Light & Power now appears with **Start onboarding**. Click it.

That creates the onboarding checklist and nothing else:

- no user created, no invitation sent — **Josh is not contacted**
- no email, SMS or outreach of any kind
- no billing, no Stripe subscription, no payment record
- no demo or sample data
- no milestone marked done — every figure stays at 0%

It is idempotent: clicking twice cannot create a second record or reset one.

Equivalent API call, if preferred:

```
POST /god/launch/{atlantis_organization_id}/start
{ "reason": "Bringing the existing Atlantis customer into onboarding" }
```

---

## 2. SET THE INDUSTRY

Atlantis is **Energy / Energy Procurement**. The registry normalises that
exact string to the `energy` template (`industry_templates.normalize`
handles the slash and the spacing).

**God Mode → Customer → Settings → Business type**, or:

```
GET  /org-settings/industry/preview?industry=energy&org_id={id}
POST /org-settings/industry/apply
{ "industry": "energy", "reason": "Atlantis is an energy procurement business" }
```

Preview first. `apply` requires a reason, is audited, and — by design —
**will not overwrite configuration somebody has customised**: it classifies
each surface as EMPTY / UNTOUCHED / CUSTOMIZED and only writes the first two.
Anything Atlantis-specific that is already right stays.

Verify afterwards that no funeral vocabulary survives: no `pre_need`,
`at_need`, `imminent`, `Contract Sold`, no "At-Need Arrangement Conference".

---

## 3. APPLY THE CUSTOMER-FACING PRESENTATION

**As data, on the organization layer.** The brand stays EvoSys Pro; the
customer identity is Atlantis; the industry layer supplies the energy
questions.

```
PUT /launch-experience/config/organization/{atlantis_organization_id}
```

```json
{
  "name": "Atlantis Light & Power onboarding",
  "presentation": {
    "eyebrow": "Welcome to {brand}",
    "title": "{customer}",
    "subtitle": "Client Onboarding & Integration",
    "intro": "This guided onboarding collects what {brand} needs to build, integrate, test and launch your complete workspace.",
    "hero_overlay": "deep",
    "journey_title": "Your Onboarding Journey"
  },
  "journey": [
    { "key": "intake",       "label": "Complete",      "sublabel": "Intake" },
    { "key": "access",       "label": "Provide",       "sublabel": "Access & Files" },
    { "key": "build",        "label": "{brand}",       "sublabel": "Builds" },
    { "key": "integrations", "label": "Integrations",  "sublabel": "<Atlantis's own rate/supplier system, as they name it>" },
    { "key": "review",       "label": "Review & Test", "sublabel": "" },
    { "key": "training",     "label": "Training",      "sublabel": "" },
    { "key": "golive",       "label": "Go Live",       "sublabel": "" }
  ]
}
```

### What must NOT be filled in here

- **`hero_image_url` / `hero_logo_url`** — leave absent until Atlantis
  supplies their own artwork. The shell renders a finished page without
  them. An invented logo gets mistaken for the real one; a stock photograph
  puts somebody else's building on their launch page.
- **`guide.url`** — leave absent until a real checklist document exists. The
  card then says so rather than offering a link that 404s.
- **Any name, address, phone number, email or date from a mockup.** Those are
  placeholders. `{customer}`, `{brand}` and `{year}` are the only tokens.
- **The integrations sub-label** — use the system Atlantis actually names in
  their intake, in their words. Do not assume it.

### What belongs on the industry layer instead

Anything the *next* energy customer will also want (energy questions, the
"Rate & Supplier Systems" stage wording) goes on
`PUT /launch-experience/config/industry/energy`, not on Atlantis's row.
Configuration that only one customer can use is the thing this whole design
exists to avoid.

---

## 4. THE COMMERCIAL ARRANGEMENT — FACTUAL, WITH THE GAPS LEFT OPEN

Atlantis is a **REVENUE_SHARE** arrangement. Record only what is actually
agreed:

| Fact | Value |
|---|---|
| Upfront payment | **$0** — none currently |
| Monthly base | **$0** — none currently |
| Proposed split | **75% / 25%** between the two parties as stated |

Create the agreement, bind it to the Atlantis organization, add the two
parties, and set the allocation.

### The four terms that must stay UNKNOWN

Do **not** answer these. They are genuinely unresolved, and the engine has a
state for exactly that:

1. **Collections basis** — gross vs adjusted/net
2. **Eligible collections / attribution** — what counts, and whose it is
3. **Order of receipt** — who receives money first
4. **Settlement frequency**

Leave each `TERM_REQUIRED`. The agreement will sit in **TERMS_REQUIRED** and
that is the correct state — it blocks settlement approval and payout while
leaving onboarding free to proceed. Inventing any of the four would fabricate
commercial terms nobody agreed to, and "UNKNOWN is not zero" is enforced
throughout: no collection amount may be assumed, and `distribute()` refuses
for everyone including god.

**No money moves.** No payout to anyone. No invoice. No Stripe subscription.
Settlement preview is a preview and is never a payout.

---

## 5. VERIFY — THE SIXTEEN CHECKS

**God Mode → Customer Launches → Atlantis → Preview their onboarding**
(opens in a new tab).

| # | Check |
|---|---|
| 1 | Atlantis listed as its own real customer, under EvoSys Pro |
| 2 | "Preview their onboarding" opens |
| 3 | Same component and composer as the customer's own page |
| 4 | Purple PREVIEW strip visible throughout, says read-only |
| 5 | No intake answers created — every section still empty |
| 6 | Progress unchanged by looking (stays at its real figure) |
| 7 | No user invited — **Josh is not contacted** |
| 8 | No email, SMS or outreach triggered |
| 9 | No billing or payment record created |
| 10 | No demo data seeded |
| 11 | No impersonation — the operator stays themselves, their own audit identity |
| 12 | Atlantis presentation correct — their name, EvoSys Pro as the brand |
| 13 | No funeral defaults anywhere (no pre-need / at-need / arrangement conference) |
| 14 | No BookaBoost branding anywhere |
| 15 | Industry reads Energy / Energy Procurement |
| 16 | Existing Atlantis data intact — name, slug, users, settings unchanged |

The preview writes exactly one row: an audit entry saying somebody looked.
That is deliberate — it is how a preview is told apart from the customer's
own first visit.

---

## 6. THE ZZ LAUNCH VERIFY RECORDS

**Do not delete them yet.** They are the only two launches in production, so
until Atlantis is started and verified they are the sole evidence that the
list, the review flow and the preview work at all. Deleting them before
Atlantis is green removes the control case.

After Atlantis passes all sixteen checks, they can go — but check first
whether either one is referenced by a saved test, a runbook or a dashboard
screenshot. They are named "ZZ" to sort last, which suggests they were meant
to be permanent fixtures rather than disposable; if that is what they are,
leave them and they cost nothing but two rows.

Whichever way: deleting a customer organization is not something to do from a
list screen, and there is no delete action here. If they should go, that is a
deliberate, separate, audited act.
