# WHOLESALE REAL ESTATE — PHASE 3 REVIEW REPORT

**Production-Usability Build.** 23 September 2026.
**Status: READY FOR HUMAN REVIEW. Nothing was pushed, deployed, or committed.**

---

## 0. What Phase 3 was for, in one paragraph

Phase 2 left a module that was correct and could not be worked in. Every number
was right and every refusal was honest, but a property could not be edited after
it was created, a photograph could not be attached to anything, a comp had one
button labelled "Remove" that might have meant either of two very different
things, the seller conversation was printed as two stacked lists instead of one
thread, a buyer's offer — the number the entire disposition turns on — could not
be typed in at all, and the assignment arithmetic that is the whole of a
wholesaler's business was left for the reader to do in their head. Phase 3 is the
pass that makes the module the thing somebody sits in all day. **No second
wholesale implementation exists. No working backend logic was replaced because I
would have designed it differently.**

---

## 1. Everything that was built, by workflow stage

### 1.1 The property (P0)

`frontend/src/pages/wholesale/wsProperty.jsx` — `PropertyWorkspace`, mounted on
the deal room Overview. The property is **always editable in place**: text,
number and choice fields, dirty tracking, Save disabled until something actually
changed, Cancel restores. An emptied box sends `null` rather than being skipped,
so clearing a wrong square footage clears it instead of silently keeping it.

Photographs: upload (drag or pick), caption, make cover, delete with
confirmation. The first photo uploaded becomes the cover; deleting the cover
promotes the next one. **A property with no photo shows "No photo", never a stock
house.**

### 1.2 Files, everywhere (P0)

`app/services/wholesale_files.py` (new) is the single storage path for property
photos, comp photos, deal documents and buyers' proof of funds. It delegates to
the platform's own `mobile_storage._put_object` for S3 and adds a `local`
backend, because `mobile_storage` refuses every upload unless S3 is configured,
which made local development impossible.

| Rule | How |
|---|---|
| No public URLs, ever | `storage_key` never leaves the server; there is no URL column. Retrieval is `GET /wholesale/files/{id}`, which re-resolves the organization on every read |
| The client's word is not trusted | Content type is sniffed from magic numbers, not from the declared header |
| The client's filename is never used | Object keys are `uuid4` + a suffix derived from the sniffed type |
| Size and type are bounded | 25 MB; jpeg/png/webp/heic/pdf/doc/docx |
| Traversal is impossible | `_local_path()` refuses anything that escapes the root |
| The screen never lies about it | `capability()` reports backend, whether uploads are enabled, whether storage is durable, and the env var to set. An upload control is **not rendered** when the deployment cannot store the file |

An `<img>` tag cannot send an Authorization header, so `AuthImage` fetches the
bytes with the session and renders from an object URL, revoked on unmount. The
alternative — making the objects public — is the thing the brief forbids.

### 1.3 Comps (P0)

`frontend/src/pages/wholesale/wsComps.jsx` (new).

**The distinction the file exists to make.** Phase 1 gave a comp a checkbox and a
button labelled "Remove", and nobody could tell which of two meanings was on
offer. They now have different controls, different colours and different words:

- **INCLUDE IN ARV / EXCLUDE FROM ARV** — changes the arithmetic, one click,
  reversible, never asks. An excluded comp is dimmed, **not hidden**: it is still
  a comp somebody found.
- **DELETE COMP** — destroys the record, asks first by name, says it cannot be
  undone.

Every comp is editable in place with an explicit Save and Cancel (Save disabled
until dirty, and it closes the editor **only on success** — a failed save keeps
the form open with the typing still in it). Each comp takes a photo, a year
built, and carries its source label.

**The subject-vs-comps comparison** (`analysis.comp_statistics`, computed in the
service so it is testable without a database): median **and** average $/sqft side
by side, the low-to-high range, median and average sale price, the subject's own
$/sqft at its current ARV, and each comp's distance from the median so an outlier
is a visible fact rather than an argument. Every figure is `None` when it cannot
be computed; nothing falls back to zero.

Audit: `comp.included` / `comp.excluded` / `comp.updated` / `comp.deleted` are
now four different events. "Comp removed" told a reader nothing.

### 1.4 The seller conversation (P0)

`frontend/src/pages/wholesale/wsTimeline.jsx` (new). One thread, newest first,
merged on the timestamp each table names its own event after. Inbound left,
outbound right, with the delivery result the provider actually gave — an
outbound message with no recorded status reads "delivery not recorded", never
"delivered".

The entry box is relabelled **"Record what the owner said"** and carries a
**MANUAL ENTRY** pill and the sentence *"This is not an inbox and it sends
nothing."* Nothing in this module receives a text message on its own, and a box
that looked like a reply field would be a promise the product cannot keep.

### 1.5 The negotiation (P0)

`frontend/src/pages/wholesale/wsOffers.jsx` (new). Before Phase 3 a deal carried
exactly one number, `proposed_offer`, overwritten on every move — so the history
of a negotiation was unrecoverable.

Now: a ledger of every move in order, each with **the MAO as it stood at the
time**, so a later change to the repair estimate cannot retroactively make a bad
offer look disciplined. Above it, where the two sides currently stand: our last
offer, their last counter, the gap between them, and the MAO in force. The
over-MAO warning fires **as the number is typed**, not after it is recorded.

Recording is not sending and is not approving. The approval gate on `offer_sent`
is untouched.

### 1.6 Disposition (P0)

`frontend/src/pages/wholesale/wsBuyerBoard.jsx` (new) — every buyer on the deal,
side by side, with the four things the decision turns on in adjacent columns:
**what they offered · what we make · can they pay · how fast they close.**

- **Nothing ranks or recommends.** No "best buyer" badge, no sort by offer, no
  highlight on the biggest number. The highest offer from somebody with no proof
  of funds is not the best buyer, and that judgement is the user's.
- **Delivery tracking** from timestamps that exist — sent, delivered, replied —
  never inferred from a status word. A row whose deal sheet never left the
  building says so, in red, with the env var that would change that.
- **Recording a response** takes status, **their offer amount**, a target close
  date and a note. It works regardless of the delivery status, because a buyer
  who phoned in an offer after a failed send is the case this exists for.
- **Proof of funds**: per-deal status, attach or replace the letter, view it.
  `verified` is labelled "Verified by a person" — nothing here reads a bank
  letter, and a status that said "verified" because software looked at a PDF
  would be the most dangerous lie in the product.
- **Selecting a buyer** is confirmed, and the confirmation names the fee it
  implies and the buyer's proof-of-funds state. It records a decision a person
  made; the assignment still needs its own approval.

### 1.7 Assignment, title, closing, and the two ways a deal ends (P0)

`frontend/src/pages/wholesale/wsClosing.jsx` (new).

**The ledger**, written out rather than left in the reader's head:

```
   the buyer pays          $156,000
 − we pay the seller       $144,000
 = gross assignment fee     $12,000
 − other costs                 $800
 = expected net fee         $11,200
   fee actually collected        —     ← typed by a person, never computed
   difference from expected            ← its own row, because that is what
                                          people argue about after a closing
```

**Closing locks the economics.** A correction is still possible and is a
deliberate act: a sentence saying what is being corrected, and its own audit
event carrying the before and after. The contract endpoint now **refuses** a
price change on a closed deal with a 409 that names the correction route. The
lock is on the money only — fixing a typo in a closing location needs no
justification.

**Both endings are deliberate.** Closing states what it will do ("records
$11,200 as collected, moves the deal to Closed and **locks the economics**").
Marking lost takes a reason from a fixed list, because free text loses the
ability to ask "how many did we lose on price" — the question that changes what
somebody does next week.

### 1.8 The Command Center (P0 after the visual correction)

Rebuilt to the corrected brief. There is **no grid of identical dark KPI cards**
and **no `.stat-card` anywhere in the module**. The hierarchy is:

1. Two large money cards — **Fees collected** (green; recorded by a person) and
   **Expected pipeline value** (with its basis stated: the expected fee on open
   deals, *not* revenue and *not* a property value).
2. Five small stat tiles — active, under contract, awaiting approval, closing in
   30 days, needs attention.
3. **Needs your attention** — property thumbnail, stage, expected fee, and the
   next action in words.
4. **The pipeline** — count *and* value by stage.
5. Active deals as a dense table beside upcoming closings and recent activity.
6. Secondary operational totals, collapsed.

Filters are behind a Filters button carrying an active count.

### 1.9 The property list (P1)

Photo, address and specification, owner with qualification band and DNC state,
stage, **ARV / max offer**, **contract / expected fee**, **next action** and
**last activity**, plus address search, a seller-band filter and a server-side
stage filter. The stage filter moved to the server because filtering a page in
the browser made the count above the table disagree with the rows under it.

### 1.10 The cash buyer CRM (P1)

Adds **where they buy** (read off their buy boxes rather than asked for a second
time — and a buyer with no box says *"No buy box — cannot be matched"*, which is
the single most useful fact on that screen), **their track record here** (deal
sheets sent, replies, offers made, times chosen, best offer, last contacted),
explicit **Edit** and **Delete buyer**.

The track record counts rows that exist. It is not a score and it is never mixed
up with the `past_deals_count` somebody typed when they added the buyer — both
are shown and the screen says which is which. A buyer with no history reads as
*no history*, not as a bad buyer.

**Deleting a buyer is refused when they have history**, with a 409 that says how
much history and what to do instead (mark them inactive). A wholesaler answering
"who did we sell 1418 Cedar to" a year later needs that name to still exist.

---

## 2. Every defect found and fixed during Phase 3

| # | Defect | Why it mattered | Fix |
|---|---|---|---|
| 1 | **Command Center rendered as dark navy cards on a cream page** | `theme.js` resolves localhost to the light BookaBoost brand; `shared.css` `.stat-card` hardcodes a dark gradient instead of `var(--bg-card)` — the only class in that file that does. My Phase 2 harness had no `data-theme`, fell back to the dark `:root`, and looked fine | `.stat-card` removed from the wholesale module entirely; harness pinned to `data-theme="bookaboost"`. The platform-wide `.stat-card` bug is **reported, not changed** — see §5 |
| 2 | **17 Phase 3 columns existed on the model with no endpoint that would write them** | A form field that silently never saves | `ContractIn` and `TitleIn` widened; all dates parsed through one list each so adding a field cannot skip the parse |
| 3 | **`REPLYCLASSIFICATION.HOT` drawn on the seller conversation** | `str(SomeEnum.HOT)` is the enum's repr. Same class as printing `single_family` at a person, one layer down | `_enum_text()` in the router; the value, never the repr |
| 4 | **The cross-tenant coverage guard keyed on path only** | A new *method* on a covered path passed silently. `DELETE /wholesale/buyers/{id}` was added and the guard stayed green — a delete is the verb you least want an untested stranger to reach | Guard now keys on **(method, path)**. It immediately went red on exactly that one endpoint, which is then explicitly attacked. No other method-level holes existed |
| 5 | **`fmtMoney` used on the property list and never imported** | `npm run build` does **not** catch an undefined identifier — the page compiled and threw in the browser | Imported. A static check was then run over all twelve wholesale JSX files; no other instance |
| 6 | **Buyer response auto-promote missed `failed` / `blocked`** | A buyer whose sheet never went out and who then phoned in an offer stayed on "failed", hiding a live offer behind a delivery problem | Both added to the promote list |
| 7 | **Disposition board overflowed its panel** | The Actions column — the reason the board exists — was pushed into the scroll container | Column widths measured, not guessed: table is now exactly the panel width |
| 8 | **Property list overflowed** after the first width pass | "Last activity" cut off | Four money columns paired into two labelled columns; measured to fit |
| 9 | **"0%" chips on three of five comps** | A ±0.3% deviation is *at* the median; rendering it turned the one figure that mattered (+39%) into just another chip | Deviation suppressed below 1% |
| 10 | **Delete rendered as a filled red button on every table row** | Red is reserved for something actually wrong; a column of red made the table look like a list of problems | Quiet outline until pressed; red is spent on the confirmation |
| 11 | **An excluded comp's "Include in ARV" button was dimmed with its data** | The way back looked disabled | Dimming scoped to the data cells |
| 12 | **Editors closed on a failed save** | Discarded what the person typed and showed an error about a form they could no longer see | `act()` now returns success; every editor closes only on `true` |
| 13 | **Photo drop zones advertised `.pdf` and `.docx`** | The control promised what the endpoint would refuse | Extensions filtered to the zone's own `accept` |
| 14 | **A status pill and a status `<select>` printed the same word twice** | Clutter | One control, tinted by state |
| 15 | **`title_status` accepted anything** | An unrecognised key prints raw on every screen that reads it | Validated against the vocabulary, with the two Phase 1 spellings retained so older rows still save |
| 16 | **A property field could not be cleared** | `_apply_property_fields` skipped nulls, which is right on create and wrong on edit. The workspace sends null for an emptied box, the save returned **200**, and the old value was still there. A save that reports success and changes nothing is worse than one that fails | `allow_clear` flag; the PATCH route passes it, create keeps the old behaviour |
| 17 | **A manually recorded seller reply never reached the conversation** | `apply_seller_reply` read the message, updated the profile and threw the message away. With no inbound channel connected — which is every deployment today — the new conversation thread was **permanently empty**, because typing it in is the only way a seller's words enter this system | The manual path writes the message to the platform's own `replies` table against the seller's Lead (not a wholesale copy), with `source="manual"`, and the thread labels it **"recorded by a person"** |

**Defects 16 and 17 were found by the Phase 3 end-to-end walk, not by looking at
a screen.** Clearing a field and reading back a conversation are exactly the two
things nobody thinks to try by hand, and both looked like they worked.

---

## 3. What is real and what is a slot

**Real, working, end to end:** property editing; photographs; document upload and
authenticated retrieval; comp CRUD with photos; the ARV working; the offer
formula; offer and counteroffer history; seller cadence control; buyer matching
with reasons; deal-sheet composition, preview and send (behind env switches);
delivery and response tracking; proof-of-funds tracking; buyer selection;
assignment economics; contract, title and closing detail; the close; the loss;
the economics lock and its correction; the audit trail.

**Honest slots, clearly labelled on screen:**

| Slot | What the screen says |
|---|---|
| Skip-trace / enrichment | "No skip-trace provider is connected" — manual entry is the intake, not a fallback |
| Comps provider | "No comps provider is connected, so every row here was typed by somebody in this organization" |
| E-sign | Manual signed-document upload |
| Buyer email / SMS | Names the exact env var that is unset |
| Cadence SMS | Names `CADENCE_SMS_SENDING` |
| File storage | Names the backend, whether it is durable, and the variable to set |
| City-to-county containment | A county criterion reads **unknown**, never satisfied |

**Nothing invents a comp, a seller, a buyer, a property figure, a provider
response, a metric or a unit of production activity.**

---

## 4. Security and tenancy

- Every route carries the same three gates: `require_tenant_user`,
  `require_feature("wholesale_real_estate")` on the router itself, and
  `require_not_observation` on writes.
- **70 wholesale routes.** `tests/test_wholesale_cross_tenant.py` has tenant B —
  a real signed-in user of another organization, with a valid token — call
  **every id-bearing endpoint** with tenant A's identifiers. Not one may return
  2xx. Four multipart endpoints are attacked separately, because an upload that
  lands in another tenant's deal is worse than a read.
- The coverage guard compares the attack list against `app.routes` at runtime on
  **(method, path)**. A new endpoint — or a new method on an existing path —
  fails this file rather than going quietly untested.
- Files: no public URLs; org re-checked on every read; magic-number sniffing;
  uuid object keys; size and type caps; `Content-Disposition` filename quotes
  stripped; `Cache-Control: private`; `X-Content-Type-Options: nosniff`.
- No new environment variable names were invented. The two outbound switches are
  the ones Phase 2 registered, both still defaulting **off**.

---

## 5. Known gaps and deliberate non-changes

1. **`.stat-card` in `frontend/src/styles/shared.css` hardcodes a dark navy
   gradient** rather than reading `var(--bg-card)`. It is the only class in that
   file that does, and it renders wrong on every light-themed white label — not
   just here. The wholesale module no longer uses it. **I did not change it**:
   it is platform-wide surface used by screens outside this module's scope, and
   the brief says not to redesign unrelated screens. It is a one-line fix when
   somebody owns that decision.
2. **City-to-county containment is still unresolved** (`resolve_containment()`
   returns `None` by design). A county criterion reads unknown for a property
   with only a city on file, and the buyer stays in the list rather than being
   excluded.
3. **The buyer deal sheet carries no images.** Attaching photographs to an
   outbound email would require publicly reachable URLs, which the file rules
   forbid. This is a deliberate refusal, not an oversight; it needs a signed-URL
   design and a decision about exposure before it is built.
4. **`with_next_action` clamps its page size to 50** on the list endpoints,
   because working out what a deal is waiting on costs a query or two per deal.
   Batching that into a single query is a clean follow-up.
5. **No paid provider is connected.** Cost caps default to 0.
6. **`localhost` renders as BookaBoost.** That is `theme.js` behaviour, not a
   wholesale decision — worth knowing before anybody reviews colour.

---

## 6. Test state

```
FULL BACKEND SUITE        5,486 passed · 14 skipped · 1 failed (51m 38s)
  The single failure is tests/test_zoom_integration.py::
  test_requires_video_not_overwritten_when_user_edited_row — confirmed
  pre-existing in Phase 2, unrelated to this module, untouched by it.

Wholesale suite                         199 passed
  test_wholesale_workflow.py             36   (22 new: comps, contract, title,
                                               the economics lock, buyer CRM)
  test_wholesale_analysis.py             33   (8 new: comp_statistics)
  test_wholesale_guards.py               28
  test_wholesale_geo.py                  22
  test_wholesale_disposition.py          21
  test_wholesale_cadence.py              19
  test_wholesale_files.py                16
  test_wholesale_matching.py             14
  test_wholesale_flow.py                  5   (1 new: the whole Phase 3 journey)
  test_wholesale_cross_tenant.py          4   (guard tightened to method+path)
  test_outbound_paths_end_to_end.py       1   (the platform's own, re-run)

Neighbouring suites re-run after the `replies` write
  wholesale + lead + cadence + reply + sms
                                        759 passed · 4 skipped · 0 failed

frontend  npm run build                  ✓ clean
          wholesaleNav.test.mjs          ✓ 11 passed
visual    5 screens × 7 deal-room tabs × 2 widths × 2 themes
                                         ✓ zero page or console errors
```

### The end-to-end walk

`test_the_phase_three_journey_lead_to_fee_collected` runs the brief's own
sequence against the real application in one test: property → edit it → clear a
field → photograph → owner → record what they said → comps with a photo →
exclude one from the ARV → offer → counter → our offer accepted → approvals →
contract with its dates → the signed contract stored and retrieved → two buyers
→ deal sheets out → both respond with amounts → proof of funds on one → **a
person picks the LOWER offer because that buyer can pay** → assignment approval
→ title → closing → the fee → the lock refusing a price edit → a written
correction → and finally an assertion that every one of eight audit events is
on the record.

**Nothing in this suite sends anything.** Every outbound switch is unset in the
test environment, and the tests that exercise a send path assert the refusal.

---

## 7. What was NOT done, on purpose

- No push, no deploy, `deploy.ps1` not run, nothing committed.
- No unrelated EvoSys/AdvisorFlow screen was redesigned.
- God Mode untouched.
- No other customer module modified.
- No white label broken — both themes rendered and inspected.
- Tenant isolation and outbound safety controls only ever tightened.
- Nothing auto-signs a contract, makes a binding commitment, or moves money.

---

## 8. The one-line verdict

**READY FOR HUMAN REVIEW.** The local test environment is still up and
unchanged; the sandbox organization, login and the route to the Command Center
are exactly as given before the review.
