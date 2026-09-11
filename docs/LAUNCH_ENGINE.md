# The Launch Engine

**What it is:** the one onboarding and implementation system in AdvisorFlow.
Built once at platform level, configured per white-label brand, used by every
brand for every customer.

```
ADVISORFLOW                the platform capability
     |
LAUNCH ENGINE              onboarding + delivery, owned by the platform
     |
WHITE-LABEL BRAND          Platform row: name, logo, support, launch template
     |
CUSTOMER ORGANIZATION      Organization row
     |
CUSTOMER USERS             the people who fill it in and sign it off
```

The customer sees the **brand**. AdvisorFlow is credited once, in the rail
footer of the Launch Pad, and nowhere else.

---

## 1. The two halves

| | Customer half | Delivery half |
|---|---|---|
| Who works in it | the customer | the implementation team |
| Where | `/launch` (Launch Pad) | God Mode → Customer Launches → **Delivery & go-live** |
| Tables | `implementation_intake_steps`, `_files`, `_submissions` | `implementation_integrations`, `_checks`, `_training`, `_blockers`, `_approvals` |
| Service | `app/services/launch_intake.py` | `app/services/launch_delivery.py` |

They are tracked as **two separate progress figures** and always have been —
"Customer intake 100% / Implementation 55%" is a normal, correct state, and
merging them into one number is the bug this design exists to avoid.

---

## 2. Data model

Nothing here owns a second "launch record". `Implementation`
(`app/models/implementation_models.py`) is the project; everything below hangs
off it.

| Table | Holds |
|---|---|
| `implementations` | the project: status, owner, target date, launched_at |
| `implementation_milestones` | the build checklist (seeded by `provisioning.milestone_template()` from the package sold) |
| `customer_activations` | the invitation to the customer's first admin (hashed token, shown once) |
| `implementation_intake_steps` | the customer's answers (JSON) + Fernet-encrypted secrets, write-only |
| `implementation_intake_files` | documents the customer uploaded (DB blob, soft delete) |
| `implementation_intake_submissions` | append-only snapshot of each Submit |
| `implementation_integrations` | one partner connection: required → verified |
| `implementation_checks` | UAT: our verdict **and** the customer's approval, separately |
| `implementation_training` | session, date, attendees, delivered, customer acknowledged |
| `implementation_blockers` | what is stuck, whose move, visible to the customer or not |
| `implementation_approvals` | the two go-live signatures |
| `launch_templates` | one row per brand: the programme its launches run |

All delivery tables carry `organization_id`, written from the implementation
and **never from a request body**, so tenant isolation is visible in every
query rather than inferred from a join path.

New tables are created by `python -m app.migrate` (Render `preDeployCommand`)
via `Base.metadata.create_all`. `app/models/registry.py` is the one import
list — a model module missing from it is a table that silently never exists.

---

## 3. The brand's launch template

`app/services/launch_template.py`

- `DEFAULT_TEMPLATE` is the floor: 5 integrations, 9 checks, 2 training
  sessions, and the 12 go-live requirements.
- `LaunchTemplate.config` (one row per `platform_id`) overrides it.
- `resolve(db, platform_id)` merges: a supplied section **replaces** that
  section; `golive` merges key by key; anything missing or malformed falls back
  to the default. A misconfigured template degrades to the standard programme,
  never to an empty one — an empty checklist reads as "ready to launch".

Edit it at `GET`/`PUT /god/launch-templates/{platform_id}` (god only).

`extra_milestones` is additive to the package milestone template in
`provisioning.py`. The Launch Engine does **not** own a second milestone list.

---

## 4. The go-live gate

`launch_delivery.readiness()` answers twelve questions, each **computed from
rows**, never from a box somebody ticked:

```
intake_submitted        files_received          milestones_complete
intake_reviewed         access_received         integrations_verified
uat_complete            training_complete       no_open_blockers
customer_uat_approval   customer_signoff        provider_signoff
```

Each item reports `ok`, `required` (from the brand's template) and a `detail`
sentence. Turning a requirement off **does not fake it** — it still computes
and still displays, it just stops blocking.

`access_received` has two honest routes: every credential the intake asks for
is stored, **or** every required connection is settled (which is only possible
if somebody got in). That is the supported answer to "we will send you the
logins another way".

**A required key with no row counts as outstanding.** The denominator is the
brand's template unioned with existing rows, so an implementation whose
programme was never seeded reads as *not ready* rather than as "0 of 0 — done".

### How the gate stops a launch

`implementation_service.launch_warnings()` folds the gate's failures into the
warning list `launch()` already makes the actor acknowledge. One gate, one
confirmation, one audit entry (`customer_marked_live`) recording what was
overridden. Live remains **god only** and is unreachable through
`set_status()`.

A customer submitting their intake can never make themselves Live. There is a
test named exactly that.

---

## 5. Endpoints

### Customer (session-scoped — no org id is accepted anywhere)

```
GET    /launch/config                       the step schema
GET    /launch/me                           brand, customer, lifecycle, progress
GET    /launch/me/summary                   every step's answers at once
GET    /launch/me/steps/{step}              one step
PUT    /launch/me/steps/{step}              save draft / save & continue
POST   /launch/me/submit                    sign off and hand over
GET    /launch/me/files                     list
POST   /launch/me/files                     upload (10 MB, inert types only)
GET    /launch/me/files/{id}/download       attachment, nosniff
DELETE /launch/me/files/{id}                soft delete
GET    /launch/me/delivery                  what we are doing, what they owe us
POST   /launch/me/checks/{id}/approve       their approval of a passing check
POST   /launch/me/training/{id}/acknowledge their confirmation of a session
```

The customer may write exactly two things on the delivery side — the approval
and the acknowledgement — because those are the only two statements only they
can make.

### Staff (god, or the assigned implementation owner, per endpoint)

```
GET    /god/launch                                  every launch, both progress figures
GET    /god/launch/{org}                            the intake, labelled
GET    /god/launch/{org}/files/{id}/download
POST   /god/launch/{org}/review                     acknowledge + reopen for edits
GET    /god/launch/{org}/delivery                   the command centre (seeds on open)
GET    /god/launch/{org}/readiness                  the gate
POST   /god/launch/{org}/integrations               add a connection
PATCH  /god/launch/{org}/integrations/{id}
PATCH  /god/launch/{org}/checks/{id}
PATCH  /god/launch/{org}/training/{id}
POST   /god/launch/{org}/blockers
POST   /god/launch/{org}/blockers/{id}/resolve
POST   /god/launch/{org}/approvals/{kind}           customer_signoff | provider_signoff
DELETE /god/launch/{org}/approvals/{kind}
GET    /god/launch-templates/{platform_id}
PUT    /god/launch-templates/{platform_id}
```

Provisioning, owner, status, milestones, customer users, invitations and the
launch action itself stay where they already lived, in
`/god/ops/implementations/...`.

---

## 6. Security properties, and the tests that hold them

- **No org id on a customer route.** The workspace comes from the session
  (`lead_scope.active_workspace_org_id`). There is no id to guess because there
  is no id parameter.
- **404, never 403, on another tenant's id.** A 403 confirms the id exists.
- **Every delivery row is fetched filtered on implementation AND organization**
  (`launch_delivery._one`). A wrong id resolves to nothing rather than
  resolving and then being checked.
- **Secrets have no read path.** Intake credentials are Fernet-encrypted into
  `secrets_encrypted`; every read returns `secrets_set` — key names only. An
  empty string means "leave it alone", not "clear it".
- **Activation tokens are hashed and shown once.** The staff surface reports
  invitation status and counts, never a token.
- **Internal blockers are never fetched into the customer response.**
  `customer_view()` is built from the customer's entitlements rather than
  filtered down from the staff object.
- Every state change is audited on the existing audit log with actor, before
  and after. Nothing logs a secret.

`tests/test_launch_delivery.py` (46 tests) and `tests/test_launch_engine.py` /
`tests/test_launch_productization.py` (73) cover all of the above, including a
guard test that greps the engine modules so no brand or customer name can be
hard-coded into them.

---

## 7. Seeding a launch's programme

`launch_delivery.seed()` is idempotent and additive:

- runs after provisioning commits (outside its transaction — a malformed
  template must not roll back a customer)
- runs again on every staff open of `/god/launch/{org}/delivery`
- inserts only keys that are absent; **never touches a row somebody has
  worked on**, and never deletes

So a launch created before this feature picks up its programme the first time
somebody opens it, and a brand that adds an integration gets it on every open
launch without disturbing verified work.

---

## 8. Front end

| File | What it is |
|---|---|
| `frontend/src/pages/launch/LaunchPad.jsx` | the customer's branded Launch Pad |
| `frontend/src/pages/launch/DeliveryPanel.jsx` | "where your launch stands" + what they owe us |
| `frontend/src/pages/god/GodLaunches.jsx` | the staff list, two tabs per launch |
| `frontend/src/pages/god/GodLaunchDelivery.jsx` | the command centre: gate, connections, testing, training, blockers, signatures |
| `frontend/src/pages/GodImplementationDetail.jsx` | the implementation record (status, milestones, owner, launch) |

The staff detail opens on **Delivery & go-live**, not on the customer's
answers: the intake is read once, the programme is read every day.

Routes: `/launch`, `/launch/:stepKey`, `/god/launches`,
`/god/implementations`, `/god/implementations/:implId`.

---

## 9. Adding a brand

1. Create the `Platform` row (name, short_name, logo, accent, support email).
2. Optionally `PUT /god/launch-templates/{platform_id}` with the programme its
   customers should run. Skip it and they get the default.
3. Nothing else. There is no per-brand code path, and a guard test enforces it.

## 10. Onboarding a customer

1. Win the opportunity, then **Provision** it (`/god/ops/opportunities/{id}/provision`).
   This creates the organization, the implementation and the milestones, and
   seeds the delivery programme.
2. Create the customer's first admin and send the activation link
   (`/god/ops/implementations/{id}/customer-admin`, then `/activations/{id}/resend`).
   The link is shown once.
3. The customer completes their intake at `/launch` and submits.
4. Staff mark it reviewed (which reopens editing), then work the delivery
   programme: connections → testing → training, opening blockers as needed.
5. Record the two go-live approvals.
6. When the gate reads **Ready for go-live**, mark Live from the implementation
   detail screen. God only, audited, one-way.
