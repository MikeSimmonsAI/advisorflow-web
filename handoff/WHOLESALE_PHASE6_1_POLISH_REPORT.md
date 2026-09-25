# PHASE 6.1 — EXTERNAL EXPERIENCE POLISH

Phase 6 passed its functional and security review. The machinery was right and
the page presenting it was not: on a wide monitor both external pages were a
narrow centred column of small stacked cards with a third of the screen empty
on either side. This phase is about the canvas, not the boundary — the
publication whitelist is untouched and re-tested.

Everything below was verified against **the running application in
`C:\Dev\advisorflow-web`**, opened in a browser. Where a measurement comes
from the render harness instead, it says so and says why.

---

## 1. Human-review issues addressed

| # | What the review said | What was done |
|---|---|---|
| 2 | Both pages trapped in a ~500px column at ~1550px, enormous dead space | Real responsive shell: 1280px content at 1550, 1400px above 1700, split hero at 900, two columns with a sticky decision rail at 1180 |
| 3 | Investor hero does not sell the opportunity | Photograph and deal side by side; asking price at 50px; ARV, repairs and target close beside it; **Make an offer** in the hero |
| 4 | Gallery undersells the property | Cover is the hero at full height; thumbnails 230px minimum; count badge; lightbox with arrows, keyboard, captions and a counter |
| 5 | No investment snapshot | Published figures only, in the hero. No derived ROI, no upside, no spread |
| 6 | Property facts read as a table | Four-column fact grid at 18px values, 12.5px labels |
| 7 | Story reads as database fields | `About this property` and `Condition and repairs` are now two headings in ONE panel, operator's words verbatim |
| 8 | Comps look like a spreadsheet fragment | 15.5px, right-aligned tabular numerics, row hover, provenance line kept |
| 9 | Actions at the bottom of a long page | Sticky decision rail above 1180px; sticky bar below it; **Make an offer** is the primary and PASS is the quietest thing on the panel |
| 10 | Offer form states | Required markers, disabled-reason text, sending state, success and error states, "you can change it below" on re-submit |
| 11 | Buyer board hides submitted fields | Fixed — see §4 |
| 12 | Seller hero looks like a form | Split hero, status stated once with a plain-language description of the step |
| 13 | Progress not visually meaningful | Horizontal rail with a connector line on desktop, vertical on a phone, three distinct states |
| 14 | Verify seller progress logic | Fixed and tested — see §5 |
| 15 | Update looks like a system note | Its own accented panel, 19px, with a neutral empty state |
| 16 | Important dates | Own panel in the rail, seller-safe labels, honest empty state |
| 17 | Title and closing | Company, officer, file number, date, time and location in one panel |
| 18 | Documents | Name, type, date and Open |
| 19 | Contact card | Name, role if configured, tappable phone and email, ≥44px on a phone |
| 20 | Brand experience | Unchanged resolution chain, larger masthead. Nothing hardcoded |
| 21 | Typography too small | Deliberate scale — see §2 |
| 22 | Whitespace and card soup | Fewer boxes, grouped sections, real columns |

---

## 2. Investor page changes

**Layout.** Three breakpoints, each doing a different job.

```
< 900px    one column · cover on top · sticky action bar
≥ 900px    split hero: photograph | address, chips, price, actions
≥ 1180px   two columns: property detail | sticky decision rail (372px)
≥ 1700px   content 1400px, rail 400px — then it stops growing
```

The page stops widening on purpose. A property description is prose, and prose
has a comfortable measure; past about 1400px the choice is between a 100-
character line and more grey, and more grey is the better of the two. On a
3840px review monitor the page will still centre with wide margins — that is
the design, not the bug that was reported. Judged at 1550px, where the review
was done, the content is 1280px with 135px of gutter.

**Type scale**, chosen per role rather than scaled uniformly:

```
address        40px / 800    the thing you are looking at
asking price   50px / 800    the thing you are deciding about
ARV, repairs   22px / 700    context beside it
fact values    18px / 650
body prose     16px / 1.68   prose a stranger reads once
section labels 12px / 800 / uppercase — deliberately quiet
```

**The decision rail.** Above 1180px the five actions sit in a sticky panel
beside the property and stay on screen at every scroll position. It is the
same DOM node the narrow layout puts in the flow — one panel, one form, one
submit path. Two copies of an offer form is two ways to send two different
offers.

**Make an offer is the primary**, which it was not. Phase 6 gave the primary
treatment to "I'm interested", so the strongest button on a page whose purpose
is producing offers committed the reader to nothing. PASS keeps its place in
the list — hiding it would be a dark pattern — drawn as the quietest control
on the panel.

**Nothing new is published.** The snapshot renders `asking_price`, `arv`,
`estimated_repairs` and `closing.target_close`, each only when the operator
switched it on, and each exactly as the serializer sent it. No upside, no ROI,
no yield, no spread, and no arithmetic over fields the operator did not
publish.

## 3. Seller page changes

Split hero with the owner's own photograph, the status stated once, and — new
— one sentence saying what that step means. Those sentences describe the STEP,
not this transaction ("The property is being inspected and reviewed before
closing"), so nothing in them can contradict the deal: they do not refer to it.
An unmapped step renders with no description rather than with a guess.

The ladder moved directly under the hero at full width, because "where am I" is
the question the page answers and it should not be competing with an escrow
file number. On desktop it is a horizontal path with a connector line; on a
phone it stays a vertical list. Three states, each labelled: DONE, IN PROGRESS,
NOT STARTED.

**Latest update** is now the largest body text on the page, in its own accented
panel, and it has an honest empty state that says no update is posted rather
than showing an empty box. The operator's words are rendered verbatim —
nothing writes, summarises or softens a message to an owner.

Documents now carry their type and date. Contact gains a role field when one is
configured, and the phone and email are tappable with ≥44px targets.

## 4. Buyer-response visibility fix

The Phase 6 gap, closed.

The deal room collects six things. The board rendered two. `offer_financing`,
`respondent_name`, `respondent_email` and `respondent_phone` were validated,
stored and returned by `GET /wholesale/deals/{id}/buyer-board` — and shown on
no screen. An acquisitions assistant could say "we will wire cash, call me on
this number" and the operator saw an amount and a date.

It is now a **sub-row under the buyer it came from**, not four more columns on
a table that is already seven wide, because the point is that the person who
answered may not be the person in the CRM — and nesting says that without a
paragraph explaining it.

Verified end to end in the browser. An offer submitted through the real
investor link produced, on the real board:

```
FROM THEIR DEAL ROOM LINK
Funding                              Hard money
Wants to close by                    2026-11-25
Answered                             9/24/2026, 6:28:02 AM
Answered by (not the listed contact) Priya Raman (TEST assistant)
                                     · priya@loneoak.example.com
                                     · (214) 555-0188
Recorded against this response only — Lone Oak Capital's contact
details are unchanged.
```

and the CRM row still read `Ray Ellison / ray@loneoak.example.test`.

### 4.1 A second defect found while doing it

`POF_STATUSES` in `wsBuyerBoard.jsx` never learned the `claimed` value Phase 6
added on the server. A row already sitting on `claimed` — which is what the
deal room writes when an investor ticks "already on file with you" — rendered a
`<select>` with no matching `<option>`. It showed blank, so the operator's next
change looked like a correction of something they never set. Added, between
`requested` and `received`, because that is where it sits in reality: asked
for, asserted, not seen. The pill reads **"Buyer says it is on file — not
seen"**.

## 5. Seller-progress logic

Phase 6 fixed the first half of this. Phase 6.1 found and fixed the second.

**Already fixed (Phase 6):** each step was judged on its own evidence, so title
— which opens the moment the file is sent over, routinely before the earnest
money lands — could show DONE above a Property review that was still running.
A step may now only be DONE when every step before it is.

**Fixed here:** the mirror image. A deal whose recorded stage has run ahead of
its paperwork produced `Offer NOT STARTED · Agreement IN PROGRESS` — an
agreement being worked on for an offer that never happened. A progress ladder
means one thing: everything before where you are is behind you. Steps before
the current one are now marked done. This can only fire on a record with a gap
in it, can only move a step forward, and can never move a finished step back or
past the current one.

Every requirement in §14 of the brief is now an assertion:

| Requirement | Test |
|---|---|
| Monotonic | `test_the_ladder_always_climbs_in_order` — 8 scenarios |
| No DONE after a not-done | same |
| Exactly one current step | `test_there_is_exactly_one_current_step_unless_the_sale_is_finished` |
| No gaps in the sequence | `test_nothing_is_upcoming_before_the_current_step` |
| Closing data alone cannot complete earlier steps | `test_closing_information_alone_does_not_complete_the_earlier_steps` |
| `Closed` needs real closing state | `test_a_closed_at_with_no_transaction_behind_it_does_not_show_closed` + `test_closed_shows_done_only_when_the_whole_transaction_supports_it` |
| Unknown stage does not guess forward | `test_an_unknown_internal_stage_does_not_guess_forward` |
| No internal stage name leaks | `test_the_published_step_labels_are_seller_safe_and_fixed` + `test_the_seller_endpoint_publishes_no_internal_stage_name` |
| A written update cannot move state | `test_a_written_update_to_the_owner_does_not_move_the_transaction` |
| The endpoint publishes the same ladder | `test_the_endpoint_publishes_the_same_ladder_the_function_computes` |

**Fault injection**, because a test never seen red is a test nobody checked:

```
remove the monotonic guard           → 3 failed, 29 passed
remove the one-boundary normalisation → 1 failed, 31 passed
restored                              → 32 passed
```

---

## 6. Files changed

```
FRONTEND — the external pages
  frontend/src/pages/public/rooms.css              rebuilt: layout system,
                                                   type scale, four
                                                   breakpoints, contrast
  frontend/src/pages/public/InvestorRoom.jsx       split hero, hero CTA,
                                                   two-column body, sticky
                                                   decision rail, merged
                                                   prose panel, comps
                                                   alignment, form states
  frontend/src/pages/public/SellerTransaction.jsx  split hero, step meanings,
                                                   progress rail, latest
                                                   update, dates rail, title
                                                   panel, document types,
                                                   contact card

FRONTEND — the operator side
  frontend/src/pages/wholesale/wsBuyerBoard.jsx    WhatTheySent sub-row;
                                                   `claimed` added to
                                                   POF_STATUSES and its
                                                   meaning; FINANCING_LABEL
  frontend/src/pages/wholesale/wholesale.css       styles for that sub-row

BACKEND
  app/services/wholesale_publication.py            seller_progress: the
                                                   one-boundary normalisation

TESTS
  tests/test_wholesale_seller_progress.py          NEW — 32 tests
```

Nothing else was touched. No service was replaced, no route added or removed,
no model changed, no migration.

## 7. Backend changes

One function, one addition. `seller_progress` in
`app/services/wholesale_publication.py` gained the normalisation described in
§5. It reads the same fields it already read, emits the same shape, and the
publication whitelist is byte-for-byte unchanged.

## 8. Frontend changes

Covered in §2, §3 and §4. Two points worth stating explicitly:

- **The sticky rail and the sticky bar are never both live.** Above 1180px the
  rail does the job and the bar is hidden; below it the reverse. One panel, one
  form, one submit path at every width.
- **The brand chain is untouched.** `wholesale_publication.branding()` still
  resolves `organization.brand_name → organization.name`, logo and accent the
  same way, and the page applies the accent as `--wr-accent`. Nothing in this
  phase hardcodes a name, a colour or a vendor. The sandbox organization is
  branded through that same configuration, which is why the review pages say
  "Cedar Springs Property Partners (TEST)" and no code does.

## 9. Tests

```
tests/test_wholesale_rooms.py             31 passed
tests/test_wholesale_seller_progress.py   32 passed   (new)
tests/test_wholesale_journey.py            2 passed
tests/test_wholesale_disposition.py       21 passed
tests/test_wholesale_cross_tenant.py       4 passed
-k wholesale                             287 passed, 5320 deselected
frontend build                            358 modules, clean
full suite                             5,591 passed, 14 skipped, 2 failed
```

The full suite was run because §28 of the brief asks for a broader regression
where shared infrastructure is touched. In fact none was: the only backend
change is one function in `wholesale_publication.py`, which nothing outside the
two external room routes calls. The two failures are named and diagnosed in
§10.1 and are not in this module.

## 10. Exact pass/fail counts

```
WHOLESALE SUITE   287 passed   0 failed   (255 at the end of Phase 6)
                   +32  tests/test_wholesale_seller_progress.py

FAULT INJECTION (injected, run, restored)
  monotonic guard removed                 → 3 failed, 29 passed
  one-boundary normalisation removed      → 1 failed, 31 passed
  restored                                → 32 passed

FRONTEND BUILD    ✓ 358 modules transformed, no errors

FULL SUITE        5,591 passed   14 skipped   2 FAILED   (1:11:31)
```

### 10.1 The two failures, named

They are not in this module and they are not new, but they are failures and
they are not being written up as "expected".

```
tests/test_meeting_type_backfill_guard.py::test_the_stamp_is_set_and_matches_updated_at
tests/test_zoom_integration.py::test_requires_video_not_overwritten_when_user_edited_row
```

Both are the same underlying thing. The first fails on

```
assert datetime(2026, 9, 24, 7, 55, 18, 249254)
    <= datetime(2026, 9, 24, 7, 55, 18, 248259)
```

— 995 microseconds apart. The meeting-type backfill stamps a row and then
compares that stamp against the row's `updated_at` to decide whether a human
has since edited it. On Windows the system clock this uses has about
millisecond resolution, so a row written and stamped inside the same
millisecond can come back with the stamp fractionally *after* `updated_at`, and
the guard reads its own write as a user edit. The second test asserts the
consequence in words: *"User-edited rows must not be overwritten by the
backfill."*

Run in isolation three times: the first fails once in three, the second fails
three times in three.

**Pre-existing.** Phase 5's regression recorded the same count — 2 failed — and
diagnosed the first of these as a Windows clock issue, deliberately without
editing it. Nothing in Phase 6 or 6.1 touches meeting types, Zoom, or that
backfill: this phase changed `wholesale_publication.py`, three external page
files, one operator component and two stylesheets.

**Not fixed here, on purpose.** The brief says do not refactor unrelated
platform systems, and the honest fix is a change to how that guard compares
timestamps — a real decision about a real scheduling feature, not a line to
slip into a CSS pass. It is written down here so the next person does not
rediscover it, and so nobody reads "5,591 passed" as "everything passed".

## 11. Browser widths tested

Both pages, both in the real application and in the harness against the
payloads this backend actually returned.

```
             overflow  content  dead/side  tiny text  targets <44px
investor 1550   none     1280      135        none        none
investor 1280   none     1280        0        none        none
investor  820   none      820        0        none        none
investor  390   none      390        0        none        none
seller   1550   none     1280      135        none        none
seller   1280   none     1280        0        none        none
seller    820   none      820        0        none        none
seller    390   none      390        0        none        none
```

No page errors, no clipped content, no unreachable control, no broken sticky
element, no overlap.

One console note, so it is not mistaken for a page defect later: the reviewer's
Chrome logs *"A listener indicated an asynchronous response by returning true,
but the message channel closed"* a few times. It carries no page stack frame,
it keeps its original timestamps after navigating away from the app entirely,
and it appears on the backend's own `/privacy-policy` page too. It is a browser
extension, not this application. The pages themselves log nothing but Vite's
hot-reload chatter.

Two things found by measuring and fixed here rather than left:

- **Horizontal overflow at 820px on the seller page.** A grid item's default
  `min-width` is `auto`, so the contact email — one long unbreakable token —
  refused to shrink and pushed the page sideways. `min-width: 0` plus
  `overflow-wrap: anywhere` on fact values.
- **Two-column dates in a 372px rail** wrapped mid-value ("Sun, September 20,
  2026"). One column per line in the rail.

## 12. Accessibility results

Measured, at 1400px and 390px, on both pages.

```
                 h1  heading order  unlabelled  contrast fails  focus rings
investor 1400     1       ok            0             0          12 / 12
investor  390     1       ok            0             0          14 / 14
seller   1400     1       ok            0             0           3 / 3
seller    390     1       ok            0             0           3 / 3

lightbox by keyboard   open → 1 of 4 → ArrowRight → 2 of 4
                       → ArrowLeft → 1 of 4 → Escape → closed
```

**One real contrast defect, fixed.** `--wr-ink-3` was `#737a85`, which is
4.33:1 on white — under the 4.5 AA threshold, and it is the colour of every
label, eyebrow and caption on both pages. Almost all the small text on an
external page was failing by a hair. Now `#666d78`: 5.0:1 on white, 4.8:1 on
the page background it actually sits on in the footer.

The accent chip also changed: a 9% tint with the text darkened toward black,
so the pair stays readable whatever colour an operator has configured, rather
than assuming a dark brand.

**Two things the checker got wrong, worth recording** because both would have
been reported as page defects:

1. A `color-mix()` resolves to `color(srgb 0.048 0.357 0.254)`, not to
   `rgb(12 91 65)`. Reading those floats as 0–255 made every branded element
   look like a 1.00:1 failure. The checker was broken, not the page.
2. The sticky bar's buttons are `display: inline-block` inside a
   `display: none` parent above 1180px. A display-only filter "found" two
   unreachable controls and reported them as missing focus rings. `offsetParent`
   is the right test.

Not done: no screen-reader pass. Carried forward from Phase 6 §16.5.

## 13. Security regression

The boundary was not touched and it was re-attacked anyway.

```
tests/test_wholesale_rooms.py   31 passed
```

covering, unchanged from Phase 6: cross-tenant isolation; unguessable tokens;
revoked, expired, unpublished and invented tokens all failing identically; a
seller token refused on every investor route and the reverse, payload and photo;
one investor unable to see another's name, status, offer or note; the brand
block carrying nothing but a name, a mark and a colour; an offer coming back
un-enriched; proof of funds recorded as claimed rather than verified; a
respondent's details never overwriting the buyer's CRM record; and no other
row's database id in any external payload.

Independently re-verified against the live sandbox through the real endpoints,
unauthenticated:

```
investor payload   no seller name, phone, email or motivation
                   no contract price · no MAO · no assignment fee
                   no internal notes · no buyer id · no property id
seller payload     no buyer · no offer · no ARV · no repair estimate
                   no fee · no spread · no disposition activity
                   no internal stage name
crossover          seller token on investor route          404
                   investor token on seller route          404
                   invented token                          404
```

`test_the_seller_endpoint_publishes_no_internal_stage_name` makes the last of
those permanent: the stage decides the ladder and must never travel with it.

## 14. Remaining gaps

1. `viewed` on a document reflects a fetch through a share link. A recipient
   sent the file another way never moves it.
2. The contract fill sheet is transcription, not a merge.
3. `opened` in the buyer track record stays 0 where the deployment cannot
   observe an open. It is not inferred from "we sent it".
4. Local media storage is still a review setting; production needs S3.
5. **No screen-reader pass.** Contrast, tap targets, focus rings, labels and
   heading order are measured; behaviour under a screen reader is not.
6. No e-signature vendor. Adding one is a class plus a webhook.
7. **Proof-of-funds upload from the investor side is not offered**, because
   nothing receives it on that route. The form asks the investor to state
   whether a letter exists and records that as a claim. The operator attaches
   the document from their own side, where the upload endpoint is. Adding the
   external upload is a route plus a storage decision, not a form field.
8. The page stops widening at 1400px. On a 3840px monitor it will centre with
   wide margins. §2 says why.
9. The contact card renders a role when the payload carries one. The seller
   room currently publishes name, phone and email; a role would need a field
   on the deal's seller-room contact block.

---

## 15. Exact REAL local investor URL

```
http://localhost:5173/investor/4_rOWjcENSWQLZ34tceRon8nX52OA-1d9IciM941WIE
```

## 16. Exact REAL local seller URL

```
http://localhost:5173/my-property/1JDukJKVr16bZYywUY7PgWZIYghkf7Qv5kIuKAmNwgM
```

Both are persisted `WholesaleShareLink` rows in `advisorflow.db`, on the 1418
Cedar Springs sandbox deal `59603154-544c-4758-a3a0-0bc6995e9863`, minted
through the running API. `secrets.token_urlsafe(32)`, 43 characters, no expiry
set. Both were opened in a browser during this phase, not inferred.

**These are not harness URLs.** Nothing on port 4173 appears in this document
as something to review. The harness exists, it runs inside the agent's cloud
container, it cannot be reached from this machine, and its only role here is
stated where it is used: repeatable width and accessibility measurement of the
shipped page files against payloads captured verbatim from this backend. Every
claim about what the pages look like and do was made against `localhost:5173`.

## 17. Frontend / backend ports

```
frontend   http://localhost:5173    Vite dev server, C:\Dev\advisorflow-web\frontend
backend    http://localhost:8000    uvicorn,        C:\Dev\advisorflow-web
```

```
cd C:\Dev\advisorflow-web
python -m uvicorn app.main:app --port 8000 --host 127.0.0.1

cd C:\Dev\advisorflow-web\frontend
npm run dev -- --port 5173
```

Vite binds IPv6 `::1`. An IPv4-only port probe will report 5173 closed while a
browser reaches it perfectly — check with `http://localhost:5173/`, not with a
`127.0.0.1` socket test.

## 18. Required local environment configuration

`frontend/.env` must exist and must contain:

```
VITE_API_BASE_URL=http://localhost:8000
```

It is gitignored, so a fresh clone does not have it. Without it,
`frontend/src/api/client.js` falls back to
`https://advisorflow-backend.onrender.com` and the dev server points the whole
app — including these two public pages — at the PRODUCTION backend. A token
minted in this sandbox does not exist there, so the investor page renders
"This page could not be loaded right now." with a local backend running
perfectly two ports away. This was one of the root causes of the Phase 6
review-URL failure; the file now carries that explanation inside it.

CORS needs nothing: `ALLOWED_ORIGINS` in `app/main.py` already lists
`http://localhost:5173`.

## 19. Screenshots and render locations

```
handoff/p61-shots/p61-investor-1550.png   handoff/p61-shots/p61-seller-1550.png
handoff/p61-shots/p61-investor-1280.png   handoff/p61-shots/p61-seller-1280.png
handoff/p61-shots/p61-investor-820.png    handoff/p61-shots/p61-seller-820.png
handoff/p61-shots/p61-investor-390.png    handoff/p61-shots/p61-seller-390.png
```

Rendered from the shipped page files against `src/realPayloads.js` — the bytes
this backend returned for the two real share links, captured with
`.claude-tmp/p6fix/dump.py` and pasted in unedited. The measurement scripts are
`p61look.mjs` (layout) and `p61a11y.mjs` (accessibility).

The browser evidence is in this document rather than in a file: §4 quotes the
operator board's own rendering of an offer submitted through the real investor
link, and §11 and §12 are measurements.

## 20. Exact human-review order

Start both servers (§17), confirm `frontend/.env` exists (§18), then:

**The investor page.**

1. Open §15 in a normal browser window, sized around 1550px. You should see a
   photograph on the left and the deal on the right: the operator's name in the
   masthead, the address, four chips, **$158,000** at 50px, ARV, repairs and
   the target close beside it, and **Make an offer** in the hero.
2. Scroll. The five actions stay with you in the right-hand rail. Check that
   **Make an offer** is obviously the primary and that **Pass on this one** is
   the quietest thing on the panel.
3. Click the cover. Arrow keys step, the counter reads *n* of 4, captions show,
   Escape closes.
4. Press **Make an offer**. Price, closing date, funding, proof of funds, your
   name, email, phone, notes. Leave the amount blank: the submit is disabled
   and the page says why. Fill it in and submit.
5. Reload. Your offer is still shown as yours, and nothing about what the
   operator makes on the deal has appeared.
6. Narrow the window to a phone width. One column, the action bar at the
   bottom, nothing under 44px, no sideways scroll.

**The operator side, for the same offer.**

7. Open the deal, **Buyer matching**. Under the buyer's row there is now a line
   beginning **FROM THEIR DEAL ROOM LINK** carrying the funding, the date they
   want to close by, when they answered, and who answered with their email and
   phone. If the name differs from the contact in your buyer list it says so,
   and says the buyer's own details are unchanged.
8. Check the proof-of-funds control reads **Claimed by buyer** and not a blank
   select.

**The seller page.**

9. Open §16. The owner's house, the status said once with a sentence explaining
   what that step means, and a horizontal path showing where they are.
10. Check the ladder only ever climbs — done, then in progress, then not
    started, with no gaps and nothing finished after something unfinished.
11. **Latest update** should be the largest body text on the page.
12. Confirm there is no buyer, no offer, no ARV, no repair figure, no fee, and
    no way to type anything: the route has no write endpoint.
13. Tap the phone number and the email on a phone width.

**The boundary.**

14. Swap the two tokens between the two routes. Both 404, identically, and
    identically to a token you invent.
15. `tests\test_wholesale_rooms.py` and
    `tests\test_wholesale_seller_progress.py` are the executable versions of
    steps 10, 12 and 14.

**Sandbox data.** Organization `cd2b1e04` "Wholesale Review (TEST)", branded
through the normal white-label configuration as "Cedar Springs Property
Partners (TEST)" with accent `#0f6f4f` — no code hardcodes either. Deal
`59603154-544c-4758-a3a0-0bc6995e9863`. The seeding script
`.claude-tmp/p6fix/seed.py` drives the running API over HTTP rather than
writing rows, so nothing in it can create a state the application would refuse.
No real person, address, phone or email is in it; every contact is `*.example`.
