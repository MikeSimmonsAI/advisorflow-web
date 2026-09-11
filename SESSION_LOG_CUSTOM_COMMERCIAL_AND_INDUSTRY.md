# Custom Commercial Agreements + Onboarding, and the Industry/Brand Provisioning Repair

Two threads, one branch (`feat/custom-commercial-onboarding`), because they
meet at the same place: the first customer whose commercial structure the
catalogue cannot express is also the customer that surfaced a provisioning
defect, and both are proved against the same organization.

---

## PART ONE — CUSTOM COMMERCIAL AGREEMENTS

### The problem

T2 answers one commercial question completely: what did this customer buy from
the catalogue, and what does Stripe collect for it. Some arrangements have no
answer to that question — no upfront, no monthly, a share of collections on
terms still being negotiated. Forcing one into a catalogue item means inventing
a price, and refusing to create the customer until the negotiation finishes
means their onboarding cannot start.

### What was built

| Piece | Where | What it does |
|---|---|---|
| Agreement model | `app/models/commercial_models.py` | Five commercial model types, seven lifecycle states, parties, allocations, terms, collection records, settlements, onboarding overrides |
| Question engine | `app/services/commercial/questions.py` | 18 platform-default question definitions, per-brand overridable by row, no vertical hard-coded in JSX |
| Terms + blocking | `app/services/commercial/terms.py` | Term state (`answered` / `required` / `unknown` / `not_applicable`) and **per-action** blocking |
| Revenue share | `app/services/commercial/revenue_share.py` | N-party allocation, validation, largest-remainder split |
| Settlement | `app/services/commercial/settlement.py` | Collections sources, preview, and the refusals |
| Overrides | `app/services/commercial/overrides.py` | Completed-previously / waived / not-applicable, with a named decider and a reason |
| Onboarding flow | `app/services/commercial/onboarding.py` | Fifteen independent steps over the launch engine's existing rows |
| Authority | `app/services/commercial/authority.py` | Capability table over the platform's existing memberships |
| API | `app/routers/commercial_router.py` | 33 routes; customer routes take no org id |
| UI | `frontend/src/pages/commercial/` | Customer page and internal console — two payloads, not one with a flag |

### The rules it holds

**UNKNOWN IS NOT ZERO.** A term nobody has answered is `required` or
`unknown`; a term answered as zero is `answered` with a value of 0. A period
with no approved collection record refuses to settle rather than settling at
nothing.

**BLOCKING IS PER ACTION.** Incomplete terms block `agreement_activation`,
`settlement_calculation`, `settlement_statement` and `settlement_distribution`
— and nothing else. Workspace setup, users, calendars, business profile,
integrations, lead import and AI configuration are never gated on a commercial
term, and a test asserts it.

**NOBODY DISTRIBUTES FUNDS.** `PAYOUT_EXECUTION_SUPPORTED = False`.
`distribute()` refuses for every caller including the platform owner, and
audits the attempt.

**T2 IS NOT DUPLICATED.** A hybrid agreement references the catalogue
subscription; no price, interval or Stripe id is restated. Stripe-sourced
collection records are summed from `billing_payments` rows T2 already wrote.

**T8 IS NOT BYPASSED.** The onboarding flow reads AI deployments and reports
them. An approved agreement does not activate an AI employee.

### Onboarding milestone overrides

The gap: a customer given a demo before this workflow existed could not tick a
box that did not exist, and the honest options were to invent a date or to make
them sit through a second demo.

`onboarding_milestone_overrides` records a mode, a named decider, the timestamp
of the DECISION, and a reason. `previously_completed_on` is nullable beside
`previously_completed_date_known`, because "some time in August" is the truth
for most of these. Supplying a date while flagging it unknown is refused.

A milestone override mirrors onto the milestone row (`done` / `skipped`); an
integration override mirrors only `not_applicable`; a check or training
override is **not** mirrored, because writing `pass` on an untested check would
assert something that did not happen.

---

## PART TWO — INDUSTRY DEFAULTS AND BRAND BOUNDARIES

### The production evidence

A newly created energy customer under one white-label brand showed
funeral-home lead tiers (Pre-Need, At-Need, Imminent, Contract Sold),
funeral-home appointment types ("At-Need Arrangement Conference"), and a
branding preview naming a different brand's product.

### Root causes — four, not one

1. **Three industry maps.** `org_settings_router.DEFAULT_TIERS`,
   `settings_router.INDUSTRY_APPT_TYPES` and
   `tier_config_service.INDUSTRY_TIER_SETS` each kept their own, and each fell
   back to funeral for an industry it did not recognise. Fixing one never fixed
   it.
2. **`Organization.industry` defaulted to `"funeral"`.** Every code path that
   created an organization without naming a business type produced a row
   claiming to be a funeral home.
3. **`create_customer(industry="funeral")`** — the God-side customer creation
   path had the same default in its signature.
4. **`brand_config.config_for_host` fell back to one specific brand** for any
   host it could not place, and the settings page hard-coded that brand's name
   in two places.

### What was built

`app/services/industry_templates.py` is now the ONE registry. The other three
maps import from it; the tier-definition sets stay in `tier_config_service`,
which owns those rows, and are referenced by key rather than copied.

- **Templates:** generic, funeral, energy, roofing, real estate, insurance,
  fiber, home services, dental. Each carries lead tiers, appointment types, CRM
  stages, custom fields, AI vocabulary, segments and onboarding questions.
- **Energy / energy procurement** is new, including its own
  `ENERGY_DEFAULT_TIERS` with AI tracks: new inquiry → rate review → proposal
  sent → contract signed → renewal due.
- **The fallback is GENERIC.** An unknown, missing or unrecognised industry
  resolves to a neutral service-business configuration. Never to funeral, never
  to whichever template is first in the file.
- **Resolution is forgiving.** "Energy / Energy Procurement", "ENERGY_PROCUREMENT"
  and "utilities" all resolve; punctuation and phrasing do not matter.
- **`industry_matched`** is reported, so a screen can say it is showing generic
  defaults rather than presenting them as somebody's decision.

**Funeral is still a first-class industry.** The fix removed a fallback, not a
vertical — a test asserts a funeral home still gets every tier it had.

### Brand boundary

- `config_for_host` now returns the neutral identity for an unplaceable host.
- `GET /org-settings/platform-identity` and the `platform` block on
  `GET /org-settings/` resolve **AdvisorFlow → brand → organization** from the
  organization's own `platform_id`.
- The settings page renders the hierarchy, and the two hard-coded brand strings
  are gone: the display-name hint names the real parent brand, and the preview
  bar shows the organization's own name.
- `Organization.brand_name` is documented as the CUSTOMER's display name. It
  does not replace the brand above it.

### Settings ownership

`GET /org-settings/sections` classifies every surface into five sections —
organization profile, business configuration, communications/integrations,
platform/brand, advanced/dangerous — with the owner, the risk level, the guard
that already enforces it, and `can_edit` for the caller. It is **descriptive**;
each endpoint still enforces its own guard. No second permission system.

### Non-destructive industry change

`app/services/industry_migration.py` classifies each configuration surface as
`empty`, `untouched_default` (byte-equal to some template's defaults) or
`customized`, then:

- replaces the first two,
- **preserves and reports** the third,
- demands a reason,
- writes an audit entry naming exactly which surfaces it rewrote.

`PATCH /org-settings/industry` used to overwrite tier configuration
unconditionally. It now runs through this path. Overwriting customized
configuration is a separate, explicit request (`replace_customized`) and is
platform-owner only.

---

## THE REPAIR RUNBOOK FOR AN ALREADY-PROVISIONED CUSTOMER

Nothing in this branch touches production data on deploy. No seed, no
migration of any existing organization, no invitation, no billing, no AI
activation. The repair is an authorised action somebody takes deliberately.

**Step 1 — look, without changing anything.**

```
POST /org-settings/industry/preview?org_id=<ORG_ID>
{ "industry": "energy" }
```

Returns `planned` (what would be replaced, and why it is safe to replace) and
`preserved` (what was customized and will be left alone).

**Step 2 — apply, with a reason.**

```
POST /org-settings/industry/apply?org_id=<ORG_ID>
{ "industry": "energy",
  "reason": "Provisioned before the industry template registry existed." }
```

Replaces only the surfaces the preview listed as `planned`, sets the industry,
and writes `org_industry_migration_applied` to the audit log with before/after
and the reason.

**Step 3 — verify.**

```
GET /org-settings/?org_id=<ORG_ID>
GET /settings/appointment-types?org_id=<ORG_ID>
GET /org-settings/platform-identity?org_id=<ORG_ID>
```

Expect: `industry: "energy"`, `industry_matched: true`, energy tiers, energy
appointment types, and a `platform.hierarchy` of AdvisorFlow → the correct
brand → the customer.

**What the repair does not do:** it does not create an organization, delete
customer-created configuration, send anything, charge anything, invite anybody,
or touch the customer's commercial agreement. A test asserts the agreement's
status, type and version are unchanged by a migration.

---

## TESTS

| File | What it holds |
|---|---|
| `tests/test_commercial_agreements.py` | Model types, allocation shapes (75/25, 80/20, three-party, residual), unknown-is-not-zero, lifecycle, authority, money safety, overrides |
| `tests/test_commercial_api.py` | Tenant and brand isolation, customer surface, unauthorized edits, concurrency, resume, settlement routes, audit |
| `tests/test_commercial_atlantis.py` | The first real arrangement end to end, as DATA; plus a test that fails if any customer's name, contact or split reaches the commercial package |
| `tests/test_industry_and_brand_boundaries.py` | Generic fallback, per-brand identity isolation, no shared brand default, funeral still works |
| `tests/test_industry_migration.py` | Classification, preview writes nothing, customization survives, audit, commercial agreement untouched |

Every refusal test also asserts that nothing changed.
