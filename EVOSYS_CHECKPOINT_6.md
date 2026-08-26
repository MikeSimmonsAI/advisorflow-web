# Checkpoint 6 — God Mode Sales Operations, Won → Customer Provisioning,
# Implementation Handoff

**Base commit:** `2fec571` (Checkpoint 5.5 complete)
**Status:** built, tested, **not yet deployed** — awaiting owner review
**Backend suites:** 17 green, including a new 249-assertion Checkpoint 6 suite
**Frontend suite:** new, 96 static assertions, wired as a deploy gate

---

## 1. What this checkpoint closes

The loop from a signature to a working customer:

```
Opportunity → Won → [a human decides] → Provision
    → customer Organization (correct Platform, isolated)
    → Opportunity.customer_organization_id written, permanently
    → Implementation created, with milestones from the package that was sold
    → implementation owner assigned (never the salesperson by default)
    → customer admin created and invited (no password ever exists)
    → milestones worked → Ready for launch → Live
    → salesperson and manager see status only; God sees everything; the tenant
      stays isolated; every step is audited
```

Before this checkpoint, `Opportunity.customer_organization_id` had **no writer
anywhere in the codebase**. The bridge existed in the schema and nothing crossed
it. That is what changed.

---

## 2. The three trees, and where they touch

Unchanged and enforced:

| Tree | Root | Membership |
|---|---|---|
| Brand sales | `Platform → BrandSalesOrg` | `memberships` rows, `users.organization_id IS NULL` |
| Customer tenant | `Platform → Organization` | `users.organization_id` |
| Control plane | God owner | `users.role = 'god_admin'` |

They meet in exactly two places, both created deliberately by a human:

1. `Opportunity.customer_organization_id` — the original designated bridge.
2. `implementations` — one row spanning `opportunity_id` and `organization_id`,
   **unique on both**.

Nothing else crosses. Provisioning creates no Lead from a sales contact, grants
no brand-sales membership to a customer, and gives no salesperson access to a
tenant.

---

## 3. Won does not auto-provision

`stage = won` is an input, never a trigger. A Won deal appears in the
**Won — awaiting provisioning** queue and stays there until somebody with
authority opens the review, confirms the customer's details and asks for it.

What provisioning writes on the opportunity is exactly two fields:

* `customer_organization_id` — the bridge.
* `stage`, `won → onboarding`. `STAGE_ONBOARDING` has existed unused in
  `sales_models` since Checkpoint 1 for this moment.

`status` stays `"won"` and `won_at` keeps its original timestamp, so **every Won
metric in the codebase reports the same number after provisioning as before it**
— they all filter on `status`, not `stage`. Launch later advances `stage` to
`live` on the same terms.

---

## 4. Idempotency — where the guarantee actually lives

`provision_customer()` checks for an existing implementation and returns it with
`created: false`. That check is a convenience. **The guarantee is the database:**

```python
opportunity_id  = Column(..., nullable=False, unique=True, index=True)
organization_id = Column(..., nullable=False, unique=True, index=True)
```

Two concurrent Provision clicks cannot produce two customer organizations,
because the second insert cannot commit. The suite proves both halves: the
service returns the original, and a hand-written duplicate insert raises
`IntegrityError`.

A repeat request returns 200, not an error. The operator who double-clicked
wants to know where the customer is, not to be told off.

---

## 5. Platform matching (§41)

The customer organization is created on the platform that owns the
`BrandSalesOrg` that owns the opportunity. Never from the actor, never from a
request field, never defaulted to "the first platform".

A BookaBoost deal provisions onto the BookaBoost platform. Tested.

If the brand sales org has no platform, provisioning is **refused** rather than
guessed — and the schema makes that state unreachable anyway
(`brand_sales_orgs.platform_id` is `NOT NULL`). Both are asserted.

---

## 6. Customer admin without a password

Today four endpoints in this codebase create a user by generating a plaintext
password and returning it in an HTTP response body. Nothing emails it; it is
handed to whoever called the API to relay however they choose.

Checkpoint 6 does not do that anywhere.

* The customer admin is created with a random password that is generated,
  hashed and discarded inside one function. Nobody has ever known it.
* The operator receives a **one-time activation link**, shown once, stored only
  as a SHA-256 hash plus a 12-character non-secret prefix — the same discipline
  as the Retell integration keys.
* Nothing is emailed automatically.
* Resending **revokes** the previous link and mints a new one, so a link that
  leaked cannot be rescued by whoever leaked it.
* Every rejection returns the same message, so a token cannot be probed for
  "expired" versus "never existed".
* The customer sets their own password at `/activate` and is then sent to the
  normal login screen — no session is minted there, so every login goes through
  one code path with one set of lockout, single-session and audit behaviour.

**Adding an existing identity to a tenant is a separate, deliberate act naming
the user by id.** Never by email match — that is the tenancy inference §1
forbids, and it is how a salesperson whose address shares a domain with the
customer ends up inside the customer's data.

### A limit stated plainly

Customer tenancy in this codebase is a single column, `users.organization_id`.
There is no customer membership table — `Membership` with `SCOPE_CUSTOMER_ORG`
exists but grants nothing. So a user belongs to **one** customer organization at
a time. `add_existing_user` therefore refuses to move a user who is already
inside a different tenant rather than silently transferring them. Making
customer membership genuinely additive is a schema change and is **not** done
here.

---

## 7. Implementation lifecycle

`Implementation` statuses: `not_started`, `kickoff_scheduled`, `configuration`,
`data_migration`, `integrations`, `testing`, `training`, `ready_for_launch`,
`live`, `blocked`.

Three different authorities, deliberately not the same:

| Question | Who |
|---|---|
| **read** (a projection) | god, the implementation owner, the brand's sales manager, the rep who sold it |
| **manage** (status, milestones, blockers, owner) | god, or the assigned implementation owner |
| **launch** (→ Live) | **god only** |

* Blocking requires a reason. Leaving `blocked` clears it.
* `live` is unreachable through the generic status route — `launch()` is the
  only way, so the launch audit trail cannot depend on which endpoint was called.
* A live customer is not quietly reopened.

### Milestones are rows, not columns

A fixed set of boolean columns would mean a migration for every new onboarding
step and would force every customer to carry every step whether or not they
bought it.

The core template is **industry-neutral** (§13): kickoff, business profile,
customer users, calendar, lead import, testing, training, launch. Package-aware
extras are spliced in *before* testing, not appended after launch:

| Package key | Adds |
|---|---|
| `starter` | — |
| `growth` | SMS number, cadence setup |
| `professional` | + AI configuration, voice configuration, integrations |
| `multi_tenant` / `custom` | + data migration |

Unknown package keys add nothing, which is correct for a package created after
this file was written. Milestones can be unchecked at provisioning time and
added later per customer.

`skipped` counts as **settled** when computing completion. A customer who did
not buy the voice module has not left the voice milestone unfinished.

### Launch warns; it does not blindly block

An unfinished **required** milestone produces a warning the actor must
acknowledge. An unfinished optional one produces nothing. A customer who has not
imported historical leads is not a customer who cannot go live, and a platform
that thinks otherwise makes its operators lie to it to get work done.

---

## 8. Post-Won visibility

The salesperson does not vanish at Won. They get a coarse server-side
projection: customer name, status label, implementation owner, target launch
date, percent complete, blocked yes/no, live yes/no.

They do **not** get: tenant leads, customer users, communications, milestone
detail, internal notes, or the blocker text. A blocker often names a customer's
staffing problem, and the person who sold the deal has no reason to hold it.
The server does not send those fields at all.

A manager sees every implementation in their own brand sales orgs. Cross-brand
probing returns **404, not 403**, so an id cannot be tested for existence.

---

## 9. Billing handoff — intent, not action

`Implementation` records what was agreed: `billing_status`,
`implementation_fee`, `recurring_amount`, `currency`, `billing_start_date`,
`trial_start`, `trial_end`, `billing_notes`, `external_billing_ref`.

**Nothing in Checkpoint 6 charges anybody.** No Stripe import exists in any file
added by this checkpoint — asserted statically in the suite.
`BrandPackage.billing_plan_key` remains deliberately unwired: the sales packages
and the legacy Stripe products are different things at different prices, and
mapping them blindly is how a customer gets billed the wrong amount.

---

## 10. Audit

`AuditLogEntry` was **extended, not replaced**. `organization_id` relaxed to
nullable (control-plane actions do not always belong to a tenant), plus
`platform_id`, `brand_sales_org_id`, `before_state`, `after_state`, `note`, and
three indexes.

`log_action()` gained five optional keyword arguments and `commit=False`.
**Every existing call site keeps working untouched** — `organization_id` is still
the first positional argument and still lands in the same column.

`commit=False` lets provisioning write its audit entry inside the same
transaction as the rows it describes, so an audit entry can never survive a
provisioning that rolled back.

Actions covered: `customer_provisioned`, `customer_admin_created`,
`customer_admin_invited`, `customer_admin_invite_revoked`,
`customer_admin_activated`, `customer_user_added`,
`implementation_owner_assigned`, `implementation_status_changed`,
`implementation_ready_for_launch`, `implementation_milestone_changed`,
`implementation_milestone_added`, `customer_marked_live`,
`billing_configuration_changed`.

**No secrets are recorded.** The activation token appears in no audit row; only
its non-secret 12-character prefix does, and the suite asserts that nothing
longer than a prefix ever lands there.

The implementation timeline reads the **same** audit table through a filter.
There is no second activity engine that could disagree with the first.

---

## 11. Routes added

### `/god/ops/*` — god only unless noted

| Method | Path | Notes |
|---|---|---|
| GET | `/god/ops/sales-operations` | §2 — every owner question in one payload |
| GET | `/god/ops/brands` | |
| GET | `/god/ops/brands/{id}` | §3/§4 — summary + real configuration |
| GET | `/god/ops/queues` | §37 |
| GET | `/god/ops/staff` | implementation-owner candidates |
| GET | `/god/ops/won-queue` | **sales member**; scoped per role |
| GET | `/god/ops/opportunities/{id}/provisioning-review` | **sales member** + record check |
| POST | `/god/ops/opportunities/{id}/provision` | **sales member** + record check |
| GET | `/god/ops/implementations` | filters: brand, status, owner, blocked, overdue, live |
| GET | `/god/ops/implementations/{id}` | detail, milestones, handoff, timeline, billing |
| POST | `/god/ops/implementations/{id}/owner` | |
| POST | `/god/ops/implementations/{id}/status` | cannot reach `live` |
| POST | `/god/ops/implementations/{id}/milestones` | add |
| POST | `/god/ops/implementations/{id}/milestones/{key}` | update |
| POST | `/god/ops/implementations/{id}/launch` | god only |
| POST | `/god/ops/implementations/{id}/billing` | intent only |
| POST | `/god/ops/implementations/{id}/customer-admin` | returns the one-time link |
| POST | `/god/ops/implementations/{id}/customer-user` | by id, never email |
| POST | `/god/ops/activations/{id}/resend` | revokes the old link |
| POST | `/god/ops/activations/{id}/revoke` | |
| GET | `/god/ops/customer-organizations` | §20 |
| GET | `/god/ops/audit` | §23 |

### Sales workspace

| Method | Path |
|---|---|
| GET | `/sales/implementations` |
| GET | `/sales/opportunities/{id}/implementation` |

### Public — rate limited

| Method | Path | Limit |
|---|---|---|
| GET | `/auth/activation?token=` | 20/hour |
| POST | `/auth/activation/accept` | 10/hour |

Public by necessity: the invited customer has no session yet.

---

## 12. Frontend

| Screen | Route |
|---|---|
| Sales Operations | `/god/sales-operations` |
| Brand drilldown | `/god/brands/:brandId` |
| Provision review | `/god/provision/:oppId` |
| Implementation command centre | `/god/implementations` |
| Implementation detail | `/god/implementations/:implId` |
| Customer organizations | `/god/customers` |
| Control-plane audit | `/god/audit` |
| Sold / Onboarding (sales) | `/sales/onboarding` |
| Customer activation (public) | `/activate` |

CSS scoping convention preserved: `gm-` God Mode shell, `sw-` Sales Workspace,
`dc-` Demo Console, **`go-` God Ops**, **`act-` activation page**.

Responsive (§31): tables become labelled cards below 900px rather than scrolling
sideways; nothing is hidden at any width. The God Mode rail now collapses to an
overlay drawer on phones — see Defect 4 below.

---

## 13. Files

**Added**

```
app/models/implementation_models.py        313 lines
app/services/provisioning.py
app/services/implementation_service.py
app/services/customer_activation.py
app/services/god_operations.py
app/routers/god_ops_router.py
scripts/smoke_checkpoint6.py               249 assertions
scripts/smoke_checkpoint6_frontend.py       96 assertions
scripts/seed_cp6_local.py                  local demonstration data, SQLite only
frontend/src/pages/GodSalesOps.jsx
frontend/src/pages/GodBrandDetail.jsx
frontend/src/pages/GodProvision.jsx
frontend/src/pages/GodImplementations.jsx
frontend/src/pages/GodImplementationDetail.jsx
frontend/src/pages/GodCustomers.jsx
frontend/src/pages/GodControlAudit.jsx
frontend/src/pages/SalesImplementations.jsx
frontend/src/pages/Activate.jsx  +  Activate.css
frontend/src/pages/god/GodOps.css
frontend/src/pages/god/GodOpsShared.jsx
docs/checkpoint6/*.png                     19 screenshots
```

**Changed**

```
app/models/models.py            AuditLogEntry extended
app/routers/audit_log_router.py log_action extended, backward compatible
app/auto_migrate.py             5 columns + 1 nullability relaxation
app/main.py                     model import + router mount
app/routers/sales_router.py     2 post-Won read-only routes
app/routers/auth_router.py      2 public activation routes
app/services/environment.py     proxy refusal (Defect 2)
scripts/smoke_demo_firewall.py  proxy refusal tests + honest skip
frontend/src/App.jsx            9 routes
frontend/src/pages/GodShell.jsx nav entries + mobile drawer (Defect 4)
frontend/src/pages/sales/SalesShell.jsx  Sold/Onboarding no longer "soon"
frontend/src/pages/sales/OpportunityDetail.jsx  post-Won panel
frontend/src/api/client.js      structured error detail (Defect 5)
deploy.ps1                      2 new gates
.gitignore                      Checkpoint 6 scratch
```

## 14. Migrations

`Base.metadata.create_all()` creates the three new **tables**. Column additions
to an existing table go through `auto_migrate`, which is append-only forever:

```python
COLUMNS_TO_ADD += [
    ("audit_log_entries", "platform_id",        "VARCHAR"),
    ("audit_log_entries", "brand_sales_org_id", "VARCHAR"),
    ("audit_log_entries", "before_state",       "TEXT"),
    ("audit_log_entries", "after_state",        "TEXT"),
    ("audit_log_entries", "note",               "TEXT"),
]
NULLABILITY_TO_RELAX += [("audit_log_entries", "organization_id")]
```

`app.models.implementation_models` is imported in `app/main.py`. Without that
import `create_all` never sees the tables — the same trap already documented for
`sales_models`, `scheduling_models`, `calendar_models`, `meeting_models`,
`integration_models` and `demo_models`.

---

## 15. Defects found and fixed

These are real, they were found by building and testing, and none of them are
cosmetic.

### 1. `deal_value_override` is a boolean, not an amount — **pipeline totals read $0**

`Opportunity.deal_value_override` is `Column(Boolean)`. It records that a manager
set the value by hand instead of deriving it from the package. I initially read
it as an overriding amount:

```python
v = opp.deal_value_override if opp.deal_value_override is not None else opp.deal_value
```

`False is not None` is `True`, so this returns `float(False or 0)` → **0.0** on
almost every row. Every pipeline total, won total and deal value in God Mode
would have read zero. Caught by the suite asserting a known sum. Fixed in three
places; the review screen now surfaces the flag honestly as
`deal_value_was_overridden`.

### 2. The demo firewall is bypassable behind a loopback proxy — **now refuses to boot**

The Checkpoint 5.5 firewall matches on the **socket's destination**. A proxied
client's socket destination is the proxy. When the proxy listens on loopback —
which every sidecar and corporate agent does — the destination is `localhost`,
which the firewall must allow so the app can reach its own database and health
checks. The connection is permitted, and the proxy then forwards it to Twilio.

Found while running `smoke_demo_firewall.py` inside a proxied sandbox: the
provider assertions failed with `ProxyError` instead of `OutboundBlocked`.

There is no way to distinguish "loopback because it is the database" from
"loopback because it is a proxy to the whole internet" at the socket layer. So
`environment.assert_safe()` now **refuses to boot a demo** when any of
`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `FTP_PROXY`, `GRPC_PROXY` (or their
lowercase forms) is set — exactly as it already refuses when the firewall failed
to install. The refusal names the variables and never their values, because a
proxy URL routinely carries credentials.

**Render sets none of these, so this changes nothing about how the demo runs.**
The suite now tests the refusal, and skips the provider assertions **loudly**
when a proxy is present rather than passing a test that would only prove the
proxy works.

### 3. The implementation-owner picker listed customer admins

The first version read `/god/users`, which filters to `god_admin / super_admin /
org_admin`. In practice that is mostly **customer** administrators and almost
none of the internal people who do implementations — and it omitted the actual
assigned owner, so the picker silently displayed "unassigned" for an
implementation that had an owner.

Added `GET /god/ops/staff`: internal identities (`organization_id IS NULL`,
active), plus the current owner always, so a picker can never lose them.

### 4. God Mode was unusable on a phone

The rail is a fixed 248px column in a flex row. On a 390px phone that leaves
142px for the whole control plane; the first mobile screenshots showed a
full-height nav with the content sheared off the right edge. This was
pre-existing — Checkpoint 6 screens simply inherit the shell — but §31 requires
the UI to work on mobile, so it is fixed rather than reported.

Below 900px the rail leaves the flex row entirely and becomes an overlay drawer
that starts closed, with a menu button in the header. Any navigation closes it.

### 5. Structured API errors were being thrown away

`api/client.js` did `throw new Error(detail)`. FastAPI allows `detail` to be an
object, and the launch route uses that to return a message plus a list of
warnings. `new Error(object)` produces the message `"[object Object]"` — losing
the warnings at exactly the moment somebody needs to read why their launch was
refused. The client now attaches `err.detail` and `err.status`; a string detail
still becomes the message, so no existing caller changes behaviour.

### 6. A tenant theme rule was painting God Mode's dropdowns white

`index.css` carries `[data-theme="bookaboost"] select { background: #fff }`, and
the app sets that attribute on the document root. It has the same specificity as
`.go-field select`, so on a tie the later sheet won — white dropdowns on a black
page. Every selector in the God Ops sheet is now prefixed with `.go-scope`,
settling it by specificity rather than by import order, which is not something a
page should rely on.

### 7. The customer activation page rendered as dark boxes on cream

It borrowed God Mode's `.go-scope`, which sets a text colour but paints no
background — so on the app's light theme the heading was invisible. It is the
first screen a customer ever sees, on an unknown brand, before any branding
resolves. It now has its own self-contained `Activate.css`.

---

## 16. Regressions explicitly re-proven

| Area | Result |
|---|---|
| Tenant Retell bridge (`/integrations/retell/tenant/*`) | mounted, still refuses unauthenticated and bogus keys |
| Brand-sales Retell bridge | same |
| Credential kind + scope checking | `scope_kind` / `_require_kind` intact |
| Demo Mode production 404s | intact |
| Demo firewall, reset, cascade rules | intact, **plus** the proxy hardening above |
| Proposal / Deal Room | provisioning reads the accepted proposal server-side only and never writes a Proposal |
| Calendar / scheduling | untouched; `availability` helpers intact |
| Nullable user tenancy | `u-mgr`, `u-rep`, `u-bbmgr` still `organization_id IS NULL` |
| No provider SDK in Checkpoint 6 code | asserted statically |
| No Lead or Membership created by provisioning | asserted statically |

**No automated test contacts anything external.** There is no provider call in
the Checkpoint 6 code path at all — provisioning writes rows, and that is the
whole of it — so there is nothing to mock and nothing that could reach a real
customer or vendor.

---

## 17. Screenshots

`docs/checkpoint6/` — 19 captures from working software against
`scripts/seed_cp6_local.py` data. Nothing is a mockup and nothing is a
hardcoded placeholder; the provisioning, invitation, milestone, ready-for-launch
and launch shots were produced by driving the real UI through the real services.

---

## 18. Not done, and why

* **Demo Mode Checkpoint 6 scenario steps (§47).** The architecture supports it:
  provisioning is a normal service that writes rows and makes no outbound call,
  so the existing scenario engine can drive it inside the firewall with no
  parallel fake engine. It is deferred because the reset table list in
  `demo_runner.DELETE_ORDER` is hand-maintained, and adding three tables plus
  their cascade rules deserves its own tested change rather than riding along at
  the end of this one.
* **Additive customer memberships.** See §6 — a schema change, deliberately not
  faked here.
*(§30 legacy hardening was completed in a follow-up pass — see §19 below.)*

---

## 19. §30 legacy hardening — COMPLETED

The owner confirmed the prerequisite survey was already done: the Vercel booking
app calls `/calendar/booking/{token}`, `/calendar/slots` and
`/calendar/booking-confirmed`, and does **not** call
`GET /availability/slots/{advisor_id}`.

### A. `/calendar/slots` — TWO fail-open bugs, not one

The reported bug was real: the Google branch imported
`calendar_service._get_google_credentials`, which has never existed. The
`ImportError` was caught by a bare `except Exception` and the candidate slot list
was left **untouched** — so a Google-connected advisor with a full calendar was
offered to the public at every slot.

**The Microsoft branch had the identical shape and the identical consequence.**
It made a raw Graph call whose failure path — expired refresh token, non-200,
timeout — also logged a warning and left the slot list untouched. Nobody
reported it because it only misbehaves when the token breaks. Fixing only Google
would have left the same hole open on the provider EvoSys Pro actually runs on,
so both are gone.

Both branches are replaced by one call to `tenant_scheduling.external_busy`,
which routes through the `calendar_providers` registry already backing the tenant
Retell bridge and the sales scheduler. **No new Google code was written.**

Consequences:

* One call for the whole day instead of the legacy route's up-to-255 live HTTP
  probes, each with a 10-second timeout, inside a request a family is waiting on.
* An error is **returned**, not swallowed. A connected-but-unreadable calendar
  yields **zero slots** plus `reason` and `calendar_error`, never a free day.
* No external calendar connected at all stays a legitimate state, not an error —
  the advisor's own `BookingLink` rows still apply.
* The 9am–5pm `America/Chicago` window is preserved **exactly**. Re-timezoning
  the public booking page is a separate change; doing both at once would make a
  regression impossible to attribute. The hardcoded Central zone stays on the
  open-items list.

### B. `GET /availability/slots/{advisor_id}` — now authenticated

`get_current_user` added, plus `_assert_can_read_advisor`:

* god_admin reads any advisor.
* Everyone else reads advisors **inside their own organization**.
* A brand-sales identity (`organization_id IS NULL`) reads only itself.
* **Cross-tenant and non-existent return byte-identical 404s.** A 403 for a real
  id and a 404 for a fake one would turn the endpoint into an oracle confirming
  whether an advisor id exists — the very leak closing it was meant to prevent.

The only caller, `Availability.jsx`, already sent its JWT and already
`.catch()`es failures. Our own frontend never calls `/calendar/slots` at all.

### D. The regression, proven against the old code

`scripts/smoke_legacy_hardening.py` (42 assertions) was run against
`git show 2fec571:app/routers/*.py` restored into a scratch tree. **17 of its 42
assertions fail there.** Measured, same fixture, same fake provider:

| Scenario | Old code | New code |
|---|---|---|
| Google busy 10:00–11:00 | **16 slots, 10:00 offered** | 14 slots, 10:00 blocked |
| Calendar connected but unreadable | **16 slots, no reason** | **0 slots** + reason |
| Anonymous `/availability/slots/{id}` | **HTTP 200** | **HTTP 401** |
| Cross-tenant `/availability/slots/{id}` | **HTTP 200** | **HTTP 404**, identical to a fake id |

No test contacts Google, Microsoft or any vendor; every read goes through a fake
registered in the existing provider registry.
