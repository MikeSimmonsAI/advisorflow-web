# Wholesale Real Estate Engine — Phase 2

## Product review, operational completion & deployment readiness

**Repo:** `C:\Dev\advisorflow-web` (branch `main`, uncommitted, nothing pushed)
**Date:** 23 September 2026
**Scope:** surgical review pass over the Phase 1 module. Phase 1 was not rebuilt,
the platform was not redesigned, and no second wholesale implementation exists.

---

## 1. What was actually done

Three capabilities were completed, five screens were rendered in a browser and
read, the module was attacked from another tenant, and the full 5,440-test suite
was run. Fifteen defects were found and fixed — **two of them serious, and
neither was visible from a passing test suite or a clean build.**

The two serious ones:

- **`GET /wholesale/properties/{id}/enrichment` answered 200 for any id,
  including another tenant's.** No data leaked (the query was already
  org-scoped), but the endpoint never checked the property belonged to the
  caller's organization. It was the one id-bearing endpoint in the module that
  did not 404 on somebody else's record — an existence oracle. Found by the new
  cross-tenant attack test, fixed, and the test now covers all 32 id-bearing
  endpoints with a runtime check that fails if a new one is added without a line.
- **Every metric label and note on the Command Center was drawn as its own
  bordered, elevated card**, because Phase 1's mechanical restyle script had
  prefixed `stat-card` onto the grid container and the three text rows inside
  each tile as well as the tile itself. The build passed. The tests passed. The
  screen was unusable. This is the defect that justifies the brief's insistence
  on looking at the rendered module.

---

## 2. Buyer outreach — it now actually sends

`app/services/wholesale_disposition.py` is the only path a deal sheet takes out
of the building, and it reuses the platform's own machinery rather than adding
a second sender: `outbound_email_gate` + `email_service.send_email` for email,
`sms_service._resolve_twilio_creds` for SMS, `test_records.is_outreach_eligible`
for the sandbox and DNC gate, `demo_guard.block_if_demo` for demonstration
tenants.

`preflight()` refuses in a fixed order and every refusal is recorded rather than
swallowed: **sandbox → opted out or inactive → no address on file → already
sent → demonstration tenant → deployment switch off.**

Two new environment switches, **both default OFF**, both named on the Settings
screen so an operator can see exactly what to set:

```
OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION
OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION
```

**Nothing sends automatically.** The screen composes and previews; a person ticks
the buyers and presses Send; the button names the count. There is no "send to all
matches" anywhere. Each attempt stores `provider_message_id`, `provider_error`,
`provider_result` (verbatim, as the provider returned it), `attempts` and
`last_attempt_at`. Resend is idempotent against an already-sent row.

`SELLER_FIELDS_NEVER_SENT` plus `seller_leak_check()` is a mechanical guard, not
a convention: seller phone, seller email, seller notes, motivation notes,
internal negotiation notes and the seller conversation cannot appear on a buyer
deal sheet, and a test asserts it.

---

## 3. Geography — normalized, and refusing to guess

`app/services/wholesale_geo.py` handles case, whitespace, punctuation, ZIP+4,
`Texas`/`TX`, `Ft.`/`Fort`, `St.`/`Saint`, and the `County` / `Co.` / `Parish` /
`Borough` suffixes. Every verdict carries a sentence a person can read, and the
misses are listed alongside the matches.

What it deliberately does **not** do: `resolve_containment()` returns `None` and
will keep returning `None` until a real geographic dataset is connected. **The
module does not claim a city is inside a county, because nothing available to it
knows that.** A lookup table typed from memory would be wrong somewhere and wrong
invisibly. The consequence is visible and intended — a county criterion reads
**unknown**, not satisfied, when only a city is on file.

Disqualification requires the property to be positively **outside every
constrained field, with none unknown**. A blank county column can never exclude a
buyer from a list.

> One real bug caught here: `Dallas Co.` was parsed as Dallas, **Colorado**.
> `co` is both a state code and the short form of County. The county-word check
> now runs before the state peel.

---

## 4. Seller cadence — a control surface, not a second engine

The 9-touch sequence remains the platform's existing `cadence_service`. Phase 2
adds only the control surface: start, pause, resume, stop, with the state, the
next touch and the blockers shown.

It **stops on its own** for opt-out or DNC, an invalid or suppressed channel, a
closed deal, a dead or cancelled deal, and a manual stop — and the automatic stop
is audited as `automation`, not as whoever happened to be looking at the screen.

A sandbox deal **cannot be enrolled at all**. `CADENCE_SMS_SENDING` defaults off
and the screen says so rather than looking like it is working.

> One real bug caught here: `set_stage` — the path a person takes when they mark
> a deal dead from the UI — did not stop the cadence. Only the internal
> `_set_stage_unchecked` did.

---

## 5. The five screens, looked at

Rendered at 1512px and 820px against a mock API, screenshotted, and read.

| # | Defect | Cause |
|---|---|---|
| 1 | Metric labels and notes each drawn as a bordered, elevated card | `stat-card` applied to the grid and the inner text rows |
| 2 | Pipeline board clipped mid-word at 19 stages | flex row + `overflow-x` with no affordance → wrapping grid |
| 3 | Footnote overlapping the tiles it explained | `.ws-panel-note` carried a negative top margin meant for use under a title |
| 4 | Panel headers wrapping into two ragged lines | the action group sized to its widest child instead of taking the width the heading left |
| 5 | Wide tables running off the panel edge and being clipped | no scroll container above the 768px breakpoint → every table now in `.ws-scroll` |
| 6 | `single_family`, `qualified_opportunity`, `offer_submitted` shown to users | stored keys printed raw → `fmtLabel` / `fmtLabels` |
| 7 | The word `true` shown eight times on the seller tab | `String(true)` → `fmtBool`, which keeps `null` as **not stated** (not the same as *no*) |
| 8 | A buyer column headed **State** that held `reliability_rating` | mislabelled → **Rating**; **Typical close** now carries its unit |
| 9 | Import panel permanently open above the list on two screens | now behind a toggle, like Add |
| 10 | Three header buttons duplicating the left rail | removed from the Command Center |
| 11 | The last unstyled native control in the module | the deal-room stage `<select>` → `.ws-input`, and labelled |
| 12 | `Property type single_family in the buy box.` | missing verb **and** a raw key, in a backend reason string |
| 13 | A truncated placeholder in every empty target-area field | sentence moved out of the placeholder to where there is room |

**Does it feel like part of the platform?** Yes, now. It composes the platform's
own classes — `panel`, `panel-title`, `stat-card`, `btn--primary`, `badge`,
`page-shell__title` — on the platform's own token layer, and the module's CSS is
scoped entirely under `.ws-page`. The navigation lives in the existing rail
group, not in page headers.

**New:** a **"What is waiting"** panel on the deal room Overview — one line per
unfinished thing, each with the tab that answers it: pending approvals, an
opted-out owner, no contact method on file, outreach not started, a conversation
flagged for a person, no ARV, matched buyers nobody has contacted. Every line is
a fact already in the deal room payload. It predicts nothing, recommends nothing,
and when there is nothing outstanding it says so rather than inventing an errand.

---

## 6. Dashboard — real data, and it says so

Every figure on the Command Center is counted from that workspace's own rows,
filtered by `organization_id`, with sandbox records excluded by default and an
explicit opt-in checkbox that says what it does. The board carries the sentence
"Every number below is counted from this workspace's own records. Nothing here is
a sample figure."

Two figures carry their basis on the tile because they would otherwise be read as
something they are not: **gross fees collected** is "recorded by a person at
close", and **pipeline value** is "the sum of the expected wholesale fee on each
open deal — not a forecast and not a property value."

---

## 7. Documents — the platform's own capability, checked first

The brief said to inspect existing platform file capabilities before building
anything. **I did, and the Phase 1 handoff was wrong about this.** The platform
has one: `app/services/mobile_storage.py`, whose `store_upload()` accepts PDFs
and refuses with a plain reason until `MEDIA_STORAGE_BACKEND=s3` plus
`MEDIA_S3_BUCKET` and AWS credentials are set.

So no document-management platform was built, and no second storage layer. What
changed is that the deal room now **reports** that capability
(`document_storage`) and the Documents panel says, in this deployment's own
words, that the file name is a reference to where the operator keeps the file
rather than a file the module holds. Previously the panel asked for a file name
and let a person assume otherwise.

Wiring an actual upload through `mobile_storage.store_upload()` once that storage
is turned on is a small, well-defined follow-up. It is deliberately **not** done
here, because it is new product surface rather than a review fix.

---

## 8. Provider status

Every provider slot ships with a manual adapter and reports its own state:
`ready` / `not connected`, with the exact missing environment variables named.
The Settings screen groups them under readable headings (they were printing the
raw keys `enrichment`, `comps`, `esign`).

No provider needs to be connected for the module to work. With none connected,
manual entry and CSV import write exactly the records an API response would, and
the enrichment history counts them the same way — the manual path is the
supported path, not a degraded one.

---

## 9. Cost control

Caps apply **only to paid lookups**. Manual entry and CSV import are never
capped. The defaults are `0` per day and `0` per month, which means **no paid
calls at all** until someone changes them. A maximum-records-per-run limit
bounds a single bulk operation. Optional approval-before-paid-lookup is a
setting, off by default.

The one AI capability is governed by the platform's existing `ai_gateway` —
approved models, the existing manual/background switches, the existing spend caps
and the existing circuit breaker. No new AI switch was invented.

---

## 10. Lead capacity

Re-verified. `attach_seller` consults `plan_limits.require_capacity_for_org_id`
on the single path (402 when full), and the CSV import uses one
`plan_limits.CapacityCounter` for the whole file, creating the properties but
returning the blocked rows in `capacity_blocked` with a plain note. The
platform's own `test_lead_capacity_matrix.py` and `test_plan_limits_coverage.py`
pass unmodified — they are what caught the original bypass in Phase 1.

---

## 11. Security and tenant isolation

Every wholesale endpoint sits behind `require_feature("wholesale_real_estate")`
at the router level, resolves the workspace from the caller's own context, and
scopes every query by `organization_id`. No organization id is ever accepted from
a path or a body.

**The attack test.** `tests/test_wholesale_cross_tenant.py` has tenant A create
one of every object the module has an endpoint for. Tenant B — a real, signed-in
user of a different organization, with a valid token — then calls **every
id-bearing endpoint** with A's identifiers: reads, stage moves, seller rewrites,
approvals, contract and title edits, close, assign, comps, documents, buy boxes,
matching, disposition, resend. **DENIED, all 32.** A second test asserts 404
rather than 403 on the read paths, so an id cannot be probed for existence. A
third confirms A's deal is untouched afterwards. A fourth compares the attack
list against `app.routes` at runtime, so an endpoint added later without a line
in the attack list fails this file rather than going quietly untested.

That fourth test is what found the enrichment-history hole described in §1.

---

## 12. Sandbox mode

Verified, and it is enforced at the platform's own gate rather than by a
module-level convention:

- A sandbox deal **cannot be enrolled in a cadence at all** — refused with a
  reason, not silently skipped.
- A sandbox record **cannot be sent to**, on any channel, and the refusal is the
  first check in `preflight()`.
- Sandbox records are **excluded from the dashboard by default**, with an
  explicit opt-in that says what it does — so test data cannot quietly
  contaminate the reported pipeline.
- Demonstration tenants are blocked by the platform's existing `demo_guard`.
- **Nothing in the test suite sends anything.** Every outbound switch is unset
  in the test environment, and the tests that exercise a send path assert the
  refusal rather than the send.

---

## 13. Approval gates and autonomy

Unchanged from Phase 1 and re-verified. Three transitions refuse until a person
approves them — **offer sent**, **under contract**, **assignment pending** — and
the approval record stores the numbers it was built from, so a later edit cannot
rewrite what was approved.

No autonomous signing. No autonomous binding commitment. No autonomous transfer
of money. No automation in this module can do any of those three things.

---

## 14. Audit coverage

Every material action writes a `wholesale_events` row with the actor **type** —
user, AI, automation, API, system — and mirrors into the platform's
`audit_log_entries` whenever a real signed-in person did it. The module's own
table exists because the platform's requires a user id and some of these actors
are not people; an automatic cadence stop is recorded as `automation`, not
attributed to whoever was looking at the screen.

Covered and tested: stage changes, approvals requested and decided, seller
qualification, enrichment (including manual), buyer matching, disposition
composed and sent, cadence start/pause/resume/stop and every automatic stop.

---

## 15. What was run

```
137 wholesale tests                       PASSED
  analysis 25 · matching 14 · flow 4 · guards 28 · geo 22
  disposition 21 · cadence 19 · cross-tenant 4

platform capacity tests (unmodified)      PASSED   26
frontend  npm run build                   PASSED   345 modules, 5.0s
frontend  wholesaleNav.test.mjs           PASSED   11

FULL REGRESSION — 5,440 tests, 44 minutes
  5,423 passed · 14 skipped · 3 failed
```

**The three failures, and what happened to them.**

Two were **mine, and they were the platform working as designed.** The platform
keeps two tests that enumerate every gated outbound source and assert that each
one defaults to OFF, and that each has exactly one environment variable:
`test_outbound_source_switches.py` and `test_outbound_paths_end_to_end.py`.
Adding the wholesale buyer channel broke both — which is the entire point of
those tests: **a new outbound path cannot be introduced without somebody
deciding, in those files, that it is off.** I registered the new source in both
(declaring it a non-lead source, because a cash buyer is a business contact, not
a family in the customer's tenant). Both files now pass, along with
`test_compliance_preflight.py`. 89 tests green.

The third, `test_zoom_integration.py::test_requires_video_not_overwritten_when_user_edited_row`,
is **pre-existing and not mine.** It was verified in Phase 1 by stashing all
wholesale work and re-running it — it still failed — then restoring. Nothing in
either phase touches Zoom, and `git status` confirms no Zoom file is modified.

**Re-run status:** the three affected files pass. The remaining 5,423 passed on
the full run; I have not re-run the whole 44-minute suite after those two
one-line test registrations, which touch nothing else.

---

## 16. Repository hygiene

`git status` was recorded before any Phase 2 work. Your pre-existing uncommitted
changes — `app/models/master_contact_models.py` and
`app/routers/god_master_router.py` — are **untouched and still modified**. No
destructive git command was run. Nothing was committed. Nothing was pushed.
`deploy.ps1` was not run. Scratch scripts used during the pass were removed.

Two files outside the wholesale module were modified, both tests, both for the
reason described in §15: `tests/test_outbound_source_switches.py` and
`tests/test_outbound_paths_end_to_end.py` now list the new outbound source. No
production code outside the module changed in Phase 2 beyond the additive
entries in `app/auto_migrate.py` and `app/services/outbound_email_gate.py`.

All Phase 2 changes are additive: no column was dropped or renamed, no data was
deleted, and the five new outreach columns go through `app/auto_migrate.py`'s
existing additive `COLUMNS_TO_ADD` mechanism.

---

## 17. Verdict

### READY FOR HUMAN REVIEW

Not "production-ready" — tests passing is not that claim, and the brief was right
to say so. What this means is: the module does what it says, refuses what it
should refuse, says so when it cannot do something, and I have now looked at
every screen rather than trusting that it compiled.

**Before it carries real sellers and real buyers, three decisions are yours:**

1. **Turn on the outbound switches deliberately, one at a time.** Both buyer
   channels and the cadence SMS default off. Nothing will send until you set
   them, and the Settings screen names each variable.
2. **Decide the documents question.** The file name is a reference, not a held
   file, until `MEDIA_STORAGE_BACKEND=s3` is configured — and wiring the upload
   through the platform's existing `mobile_storage` is a follow-up I did not
   build unasked.
3. **Accept or reject the county limitation.** A buy box constrained by county
   will read *unknown*, not *outside*, for a property that has only a city on
   file, so that buyer stays in the list. That is the honest answer, and it stays
   that way until a geographic dataset is connected. I would rather a buyer sees
   one deal too many than the module tells you two places are the same when it
   does not know.

**One thing I changed that you may want changed back:** the Command Center's
header no longer carries Properties / Buyers / Settings buttons, because those
three items sit in the left rail two inches away. If you liked them there, it is
one edit to restore.

**One thing I deliberately did not change:** *Approve* is still the primary
button on an approval row. It is the expected action, but it is also a money
decision, and you may reasonably want both buttons neutral.
