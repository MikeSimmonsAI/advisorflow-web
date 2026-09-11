# SESSION LOG — ADVISORFLOW SUPPORT INTELLIGENCE

**Thread 5 · branch `feat/support-intelligence-autofixer` · worktree
`C:\Dev\advisorflow-support-intelligence`**

This is the pass-down. It is written so that somebody picking this up cold —
including a future session with no memory of building it — can work on it
without reconstructing anything.

---

## 1. WHAT THIS IS, IN ONE PARAGRAPH

A platform capability that lets a customer ask their brand's own assistant
what is wrong, has the PLATFORM look at their account for real, decides
whether the cause is a question, their configuration, their usage, a
third-party provider or our own defect, repairs what it is safe to repair,
**verifies** the repair, opens a ticket with all of that attached when it
cannot, measures a first-response SLA in business hours against what the
customer's package actually bought, correlates the same fault across
customers and across brands into one platform incident with a recommended
fix, and writes a daily brief telling the owner what to do about it.

It is one engine. A brand supplies the name on the front.

---

## 2. THE ARCHITECTURAL RULE, AND WHY IT IS NOT NEGOTIABLE

    THE BRAND OWNS THE FACE. ADVISORFLOW OWNS THE BRAIN AND THE FIXER.
    GOD MODE IS ROOT AUTHORITY. PERIOD.

There is **no** Platform Superadmin, Root Admin, Master Admin, separate
support root, or second permission architecture. Support authority is
expressed entirely through what already existed:

| Who | How it is expressed | What they get |
|---|---|---|
| `god_admin` | `deps.require_god` | Every brand, every customer, every action |
| Brand support operator | `capabilities.support_console`, granted at **brand** scope | One brand's customers; run the queue |
| Everybody else | — | Nothing |

`app/services/support_authority.py` is the whole of it, and it is 160 lines
because it delegates to the capability system rather than inventing one.

**A brand operator cannot approve a remediation.** That is God's, by risk
class, and it is enforced twice independently: the route guard
(`require_god_for_configuration`) and the registry
(`support_remediation.authorization_for`). One gate on the only operation
that can change billing or permissions is one careless review away from
being removed.

---

## 3. THE FILES, AND WHAT EACH ONE OWNS

### Backend — models

| File | Owns |
|---|---|
| `app/models/support_models.py` | All 19 tables, plus the vocabulary (Severity, TicketStatus, TicketCategory, SlaState, Queue, Cause, RiskClass, FixStatus, IncidentStatus, AuthorizationSource) |

**One owner per table.** These are ORM-declared and reach a database through
`Base.metadata.create_all()`. None of them appears in `auto_migrate`'s
`TABLES_TO_CREATE`; two owners is how `crm_contacts` drifted. The module is
imported by `app/models/registry.py` — **in both copies of that file**, which
contains the whole module twice as a merge artifact.

### Backend — services

| File | Owns |
|---|---|
| `support_entitlements.py` | What a package includes. The three commercial concepts. Assistance allowance, no rollover. |
| `support_sla.py` | Business-minute arithmetic, holidays, pause/resume, SLA state. |
| `support_branding.py` | Whose name is on the experience. Reads `brand_config`, adds three support-specific names. |
| `support_diagnostics.py` | The 9 registered checks. Two summaries per result: customer-safe and technical. Tool declarations for the AI. |
| `support_remediation.py` | The 8 registered repairs, risk classes, the execution contract, authorization, audit. |
| `support_tickets.py` | Ticket lifecycle. **The one internal/external filter.** Notifications. |
| `support_ai.py` | Ask [Brand]. Model picks checks and writes prose; nothing else. |
| `support_incidents.py` | Signatures, correlation, incidents, strong recommendations, recurring intelligence. |
| `support_knowledge.py` | Help centre, search, the learning loop (proposals only). |
| `support_brief.py` | The daily brief and the nightly intelligence pass. |
| `support_authority.py` | Who may operate support, over whom. |

### Backend — routers

| File | Prefix | Guard |
|---|---|---|
| `app/routers/support_router.py` | `/support` | `require_tenant_user` |
| `app/routers/god_support_router.py` | `/god/support` | `require_support_operator` or `require_god_for_configuration` |

### Frontend

| File | Owns |
|---|---|
| `frontend/src/pages/HelpSupport.jsx` + `.css` | Customer Help & Support: Ask, requests, help centre, status, plan |
| `frontend/src/pages/god/GodSupport.jsx` | God → Support: queue, incidents, recurring, brief, fixer, configuration |

Wiring: `App.jsx` (two routes — `/god/support` **must stay above the `/god/*`
catch-all**), `components/Layout.jsx` (nav group "Help", `life-buoy` icon),
`pages/GodShell.jsx` (nav group "SUPPORT"), `pages/god/ProductStatus.jsx`.

### Tests

| File | Count | Covers |
|---|---|---|
| `tests/test_support_engine.py` | 32 | Entitlements, business hours, the clock, ticket lifecycle, the commercial rule, customer screens |
| `tests/test_support_security.py` | 33 | Tenant isolation, brand isolation, forged input, prompt injection, secret exposure, console authority |
| `tests/test_support_fixer.py` | 37 | Registry, safe/controlled/approval/engineering classes, verification, audit, correlation, recurrence, the brief |

---

## 4. THE SIX RULES THAT MAKE THIS SAFE

Read these before changing anything. Each one is load-bearing and each one
is asserted by a test.

**1. A fix is FIXED only when a second, independent read says so.**
`SupportFixRun.verified` is a separate column from `status` for exactly this
reason. `run_summary()["fixed"]` reads `verified`, never `status`. A repair
that ran and could not be verified counts as a FAILURE — in the brief, in
the signature counters, and in what the customer is told.

**2. The model has no authority and no arguments.**
`support_diagnostics.tool_definitions()` declares tools with
`"properties": {}` and `additionalProperties: false`. There is no
`organization_id` a prompt could aim. The org comes from the authenticated
request, and an invented tool name resolves to `None`.

**3. Two summaries are produced at write time, never filtered at read time.**
`CheckResult.customer_detail` and `.technical_detail` are built separately by
the check. A credential is never placed in the customer view in the first
place, so no serializer can leak it.

**4. `is_internal` is filtered in exactly one function.**
`support_tickets.customer_view`. Every customer-facing endpoint returns its
output. A route that assembled its own thread would be a second filter, and
the failure mode of forgetting is an internal note in front of a customer.

**5. Technical product support never consumes the assistance allowance.**
`support_entitlements.consumption_for()` is the only place this is decided,
and it answers `{counts_against_allowance: False, billable: False}` for
`TECHNICAL_PRODUCT_SUPPORT`. Callers do not get to pass it.

**6. NULL means inherit, everywhere in `support_entitlement_configs`.**
`queue`, the three response targets, `included_assistance_minutes` and
`emergency_override` are all nullable. A column default here would mean an
operator editing one field silently rewrites the others — which is exactly
the bug the first version of this table had, caught by
`test_config_row_overrides_field_by_field`.

---

## 5. TWO REAL DEFECTS FOUND AND FIXED DURING THE BUILD

Worth recording because both would have been very quiet in production.

**An automatic repair rolled itself back.** `audit_log_entries.actor_user_id`
is NOT NULL. A policy-authorized repair has no human actor, so the audit
write raised `IntegrityError` on flush — and because the caller owns the
transaction, that rolled back the repair as well. An audit attempt was
destroying the thing it was auditing. Fixed in
`support_remediation._audit`: no row is attempted without a human actor (the
fix run is the record of record for a system-authorized repair, and a fuller
one), and the write that does happen is inside a savepoint so it can never
poison the caller's transaction again.

**A daily cap of 1 meant a repair could never run.** `execute_fix` creates
the run row *before* it asks for authority, so `_runs_today` counted the
in-flight attempt and refused the first run. Now it counts executions rather
than attempts — which is also the right semantics: a refusal changed nothing
and must not spend a budget that a real problem later in the day needs.

---

## 6. WHAT IS DELIBERATELY NOT BUILT

Each of these is a decision, not an omission.

- **Email-to-ticket ingestion.** The architecture supports it and the
  notification path is provider-neutral, but `email_poller_service` polls a
  per-advisor LEAD mailbox. Routing inbound support mail would need a brand
  support mailbox and a routing rule that does not exist yet, and inventing
  one would mean guessing which address a brand wants customers writing to.
- **SMS support chat.** Explicitly out of scope for v1. The notification
  path is channel-neutral, so adding SMS ticket notifications later is a
  delivery change, not an architecture change.
- **Autonomous production coding.** `RiskClass.ENGINEERING` exists precisely
  so that "no runtime remediation exists" is a first-class outcome.
  `platform.engineering_fix_required` has an `execute()` that raises by
  construction, so no future caller can turn it into one by supplying a
  handler.
- **Prices.** Not one dollar amount ships in code. `support_service_offerings`
  defaults to `pricing_mode='quoted'` with a NULL amount, and the customer
  page renders "Quoted".
- **A mobile support UI.** The mobile worktree is active. The canonical
  consumption path is documented in section 7.
- **Attachment download links for customers.** The endpoint exists and is
  tenant-guarded; the customer page renders attachments as chips rather than
  links because an `<a href>` sends no Authorization header and would 401. A
  dead link is worse than no link. A signed one-time URL is the next step.

---

## 7. CANONICAL MOBILE CONSUMPTION PATH

Mobile consumes the SAME backend. There is no second support engine and no
mobile-specific support endpoint.

    GET  /support/me                     brand names, plan, open count
    GET  /support/status                 customer-safe service status
    GET  /support/tickets                list
    GET  /support/tickets/{id}           full customer view
    POST /support/tickets                raise (runs diagnostics first)
    POST /support/tickets/{id}/reply     reply
    POST /support/ask                    Ask [Brand]

Every one of them answers for the authenticated caller's own organization
and accepts no organization identifier, so a mobile client needs only the
existing JWT. `support_tickets.customer_view` is the response shape for
anything ticket-shaped; do not build a second serializer.

---

## 8. HOW TO OPERATE IT (God Mode → Support)

1. **Configure a brand.** `CONFIGURATION → CONFIGURE` on the brand. Set the
   assistant name ("Ask Evo"), the help centre name, support hours, and the
   per-package response targets. A package showing `DEFAULT` is running on
   the frozen rule in `support_entitlements.INITIAL_EVOSYSPRO_RULES` — from
   the customer's SLA page that is indistinguishable from a decision.
2. **Seed the help centre.** `POST /god/support/knowledge/seed` installs five
   platform-wide articles tied to what the diagnostics actually detect.
   Deliberately a manual action: seeding on deploy would overwrite edits.
3. **Decide what may self-repair.** `AUTO-FIXER → Automation policy`. Nothing
   is automatic by default. Only CONTROLLED repairs are policy-gated, only
   for a named brand or customer, optionally with a daily cap.
4. **Work the queue.** Every metric on the board filters the list.
5. **Run the intelligence pass** any time with `RUN INTELLIGENCE PASS`; it
   also runs by itself every 6 hours (`JobName.SUPPORT_INTELLIGENCE`).

---

## 9. RUNNING THE TESTS

    cd C:\Dev\advisorflow-support-intelligence
    python -m pytest tests/test_support_engine.py tests/test_support_security.py tests/test_support_fixer.py -q

Frontend production build:

    cd frontend
    node node_modules\vite\bin\vite.js build

(The worktree's `node_modules` is a junction to `advisorflow-web`'s, because
the two `package.json` files are byte-identical. `npm ci` also works.)
