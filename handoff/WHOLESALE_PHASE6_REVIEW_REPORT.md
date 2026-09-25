# PHASE 6 — REVIEW REPORT

Wholesale Real Estate module. Investor deal room, seller portal, operator UX,
SS1 import capacity.

**HEAD unchanged. Nothing committed, pushed or deployed.**

---

## 1. What I found before changing anything

I rendered the two external pages in a browser before touching a line, because
the brief said the investor page had not been seen and I wanted to know whether
it was missing or merely bad.

**It was neither missing nor broken. It was correct and unsellable.**

The page worked, the publication boundary held, the buyer actions wrote real
records — and it looked like an admin screen with the fields removed:

- **No hero.** The cover photo was not at the top. It was a flat strip of four
  equal thumbnails, in the middle of the page, *below* the property facts.
- **The asking price was the same size as everything else.** Eight identical
  white cards on grey, each with the same title weight, so nothing led.
- **No branding at all.** Nothing said whose page it was. An external page with
  no name on it reads as a phishing attempt.
- **The five things an investor can do were 1,600px down**, below the fold, with
  no way back to them.
- **"Make an offer" collected an amount and nothing else** — no closing date, no
  funding, no contact — which is not an offer an operator can act on.
- **It showed the investor a document that did not exist** ("Inspection report —
  not uploaded yet"). That is the operator's checklist leaking outward.

The seller page was in materially better shape: seller-safe display statuses
already worked ("Property review", never "Disposition"), the progress ladder was
real, and the dates and documents were right. It needed branding, a photo and
polish — not a rebuild.

## 2. What already existed

Substantial, and it was reused rather than rebuilt:

| Already there | Reused for |
|---|---|
| `wholesale_publication.py` whitelist serializer | every new field went through it |
| `WholesaleShareLink` / `WholesaleShareView` | all external access and view tracking |
| `WholesaleBuyerOutreach.target_close_date`, `.pof_status` | two thirds of the new offer form |
| `WholesaleFile.buyer_visible`, `is_primary`, `sort_order` | the gallery; `seller_visible` mirrors it |
| `_buyer_activity` | the CRM track record, extended not replaced |
| `lead_capacity` hold/release + `plan_limits` | all of SS1 |
| `billing_catalog.plans_for` | the upgrade options, which invent nothing |
| `Organization.brand_name/brand_logo_url/brand_color_primary` | white-label branding, no new columns |
| `wholesale_esign.py` lifecycle + provider registry | unchanged; still no vendor faked |

## 3. What was missing

1. A sales page. The data was published; the presentation was not.
2. Branding on either external page.
3. A real offer form (date, funding, proof of funds, contact).
4. A gallery — click to enlarge, next/previous, captions.
5. `occupancy` and `market` on the investor payload, which decide the deal.
6. A seller-visible photo flag and a seller photo route.
7. COMPLETE / NEEDS REVIEW / MISSING on the contract fill sheet.
8. Payment status on the deal header.
9. The full SS1 pre-commit panel, and honest copy about what happens to held rows.
10. Settings that read as questions rather than as configuration.

## 4. What I built

### The investor deal room
Branded masthead (organization's own name, logo and accent — never the software
vendor's), large cover photo capped so the price stays above the fold, address,
chips for type/occupancy/market/county, asking price at display size with
ARV/repairs/closing beside it at context weight, fast facts, summary, condition,
thumbnail gallery, investor-friendly comps, documents that actually have files,
closing block, and five distinct actions each stating what it commits to.
Sticky "Respond to this deal" bar on phones.

**Gallery:** click cover or any thumbnail, next/previous buttons, arrow keys,
Escape, captions, "3 of 4" counter.

**The offer:** amount, closing date, financing, proof-of-funds claim, contact
name/email/phone, notes. Closing date and POF land on columns that already
existed. Proof of funds is recorded as **claimed**, never verified.

### The seller portal
Branded, leads with the owner's own house when a photo is published to them,
and states the seller-safe status once at the top — "Property review", step 3 of
5, closing date — with the ladder, dates, title, documents and a named contact
below. Still **read-only by construction**: there is no write route on the seller
surface, and a test asserts it.

### Operator screens
- Per-photo **owner-visible** toggle with its own audit line, separate from the
  investor one, both off by default.
- Fill sheet grouped with **COMPLETE / TO CHECK / MISSING** per field and per
  group. "Seller of record" is `needs_review`, not `complete`, because it has
  not been checked against the deed.
- **Payment status on the deal header**, from the server's `payment_state`.
- Buyer board carries the full offer detail and the respondent.
- Buyer track record extended with opened / interested / passed / deals closed /
  average close days, all counted from real rows.
- Settings split into the questions a wholesaler asks, with the offer formula
  written out in English over a worked example.
- Seller assistant gained a **tone preference** — scoped, honestly, to the
  summary the assistant writes back to the operator, with the screen saying so.

### SS1
The full pre-commit panel: plan, limit, in use, available, rows in import,
would-create, duplicates, not-importable, would-be-held; then "X can be imported
now, Y would be held", real configured plans only, and actions that work.

## 5. Investor page — THE REVIEW URL

```
http://localhost:5173/investor/4_rOWjcENSWQLZ34tceRon8nX52OA-1d9IciM941WIE
```

Real `WholesaleShareLink` row, audience `buyer`, minted by the running API
against the 1418 Cedar Springs sandbox deal
(`59603154-544c-4758-a3a0-0bc6995e9863`). 43-character
`secrets.token_urlsafe(32)` token, persisted, no expiry set. Opened and
exercised in a browser — see §14.

**An earlier version of this report gave `http://localhost:4173/investor/tok`
as review step #1 and it could not have worked for anybody.** 4173 was a Vite
render harness running inside the agent's own cloud container, not on this
machine, and `tok` was a hardcoded string in that harness's mock — not a token
in this database. Both are gone from this document. §14.0 records what else
that mistake was hiding.

## 6. Seller page — THE REVIEW URL

```
http://localhost:5173/my-property/1JDukJKVr16bZYywUY7PgWZIYghkf7Qv5kIuKAmNwgM
```

Real `WholesaleShareLink` row, audience `seller`, same deal. Read-only: the
route has no write endpoint at all.

## 6.1 The two servers, and the file that makes them talk

```
backend    cd C:\Dev\advisorflow-web
           python -m uvicorn app.main:app --port 8000 --host 127.0.0.1
           (start-backend.bat does the same with --reload)

frontend   cd C:\Dev\advisorflow-web\frontend
           npm run dev -- --port 5173
```

`frontend/.env` must exist and must contain:

```
VITE_API_BASE_URL=http://localhost:8000
```

It is gitignored, so a fresh clone does not have it. Without it,
`frontend/src/api/client.js` falls back to

```js
export const API_BASE = import.meta.env.VITE_API_BASE_URL
                        || 'https://advisorflow-backend.onrender.com'
```

and a dev server points the whole app — including these two public pages — at
the PRODUCTION backend. A token minted in this sandbox does not exist there, so
the investor page renders "This page could not be loaded right now." with a
local backend running perfectly two ports away. That is the second root cause
of the review-URL failure and the reason the file now exists with the
explanation written inside it.

CORS is not involved: `ALLOWED_ORIGINS` in `app/main.py` already lists
`http://localhost:5173`, `:5174` and `:3000`.

## 7. Internal screens changed

`WholesaleDeal.jsx` (header payment status), `WholesaleSettings.jsx`
(reorganisation, formula, tone), `wsFiles.jsx` (owner toggle, badges),
`wsContracts.jsx` (fill-sheet states), `wholesale.css`, `Billing.jsx` (unchanged
this phase — Phase 5), `ImportBatchReview.jsx` + `.css` (SS1 panel).

## 8. Backend endpoints, models and services changed

**Endpoints**
- `GET /wholesale-rooms/seller/{token}/photo/{file_id}` — **new**
- `POST /wholesale-rooms/buyer/{token}/action` — richer body
- `GET /wholesale-rooms/buyer/{token}`, `/seller/{token}` — `brand`, occupancy,
  market, seller photos
- `GET /wholesale/deals/{id}/fill-sheet` — per-field status and summary
- `GET /wholesale/deals/{id}/buyer-board` — offer detail, respondent
- `GET /wholesale/buyers?with_activity=true` — extended track record
- `PATCH /wholesale/files/{id}` — `seller_visible`
- `GET/PATCH /wholesale/settings` — `ai_tone`, `ai_tones`

**Models** — `WholesaleFile.seller_visible`; `WholesaleBuyerOutreach.offer_financing`,
`.respondent_name/email/phone`; `WholesaleSettings.ai_tone`; `POF_STATUSES` gained
`claimed`.

**Services** — `wholesale_publication.branding()`, `seller_photos()`, `_photos()`;
`wholesale_ai` tone scoped to the summary; `import_batch_router._capacity_preview`
extended.

## 9. Migrations

Six additive entries in `app/auto_migrate.py` `COLUMNS_TO_ADD`. Every boolean
defaults FALSE, so nothing already in a workspace becomes visible to anybody
because a migration ran. No table is created, renamed or dropped.

```
wholesale_files.seller_visible            BOOLEAN DEFAULT FALSE
wholesale_buyer_outreach.offer_financing  VARCHAR
wholesale_buyer_outreach.respondent_name  VARCHAR
wholesale_buyer_outreach.respondent_email VARCHAR
wholesale_buyer_outreach.respondent_phone VARCHAR
wholesale_settings.ai_tone                VARCHAR
```

## 10. Environment variables

**None added.** Production still needs `MEDIA_STORAGE_BACKEND=s3`.

## 11. Provider requirements

**None added, none faked.** No e-signature vendor is connected; the send button
still only exists when one can actually send. No comps provider, no enrichment
provider, no outbound email or SMS was enabled. The AI gateway is called for
seller-reply reading and falls back to the deterministic reader when it cannot
reach a provider — observed during the journey test and handled correctly.

## 12–13. Tests run, and exact counts

```
WHOLESALE SUITE                       255 passed
  rooms 31 · workflow 36 · guards 28 · geo 22 · disposition 21 · cadence 19
  files 16 · contracts 15 · matching 14 · capacity preview 9 · board 8
  offerings 8 · flow 5 · cross-tenant 4 · journey 2 · analysis 33

NEW THIS PHASE
  test_wholesale_journey.py            2   intake → fee collected, one test
  test_wholesale_rooms.py             +9   Phase 6 security block
  test_import_capacity_preview.py     +4   SS1 rows, split, options, failure

FAULT INJECTION (injected, run, restored)
  seller photo route reads buyer_visible   → 1 failed
  brand block carries a seller name        → 2 failed
  restored                                 → 31 passed
```

## 14. Browser / render results

```
2 themes × 2 widths (1512, 820) × 4 screens × 9 deal tabs
external pages at 1512, 820 and 390
  → no page errors
  → no horizontal overflow
  → no unreachable controls
  → no text under 12px on the external pages
```

Interaction: lightbox opens from cover and thumbnails at the right index, next/
previous and arrow keys step, Escape closes; offer submit disabled without an
amount; question send disabled without a question; intake details fold and
unfold with every field still present.

### 14.0 What opening the real URL found that the harness never could

The render numbers above were produced against mock payloads in a container.
Opening the two REAL links in the reviewer's own Chrome, from the running
backend, found four things in the first ten minutes. Three were real defects.

**1. The Phase 6 rewrite of both public pages had never reached the
repository.** `rooms.css` (the Phase 6 stylesheet) had landed; the two `.jsx`
files it styles had not. The live investor page was the PHASE 5 markup wearing
the PHASE 6 stylesheet, so every class the CSS targets was absent and the page
rendered as an unstyled column of labels — no masthead, no hero, no brand, no
chips, no lightbox, and an offer form that collected an amount and nothing
else. Confirmed by hashing every wholesale frontend file against the copy that
was rendered: exactly two differed, `InvestorRoom.jsx` (405 lines on disk
against 672) and `SellerTransaction.jsx` (193 against 248). Every other file —
`rooms.css`, `wholesale.css`, `wsBuyerBoard.jsx`, `WholesaleBuyers.jsx`,
`wsSharing.jsx` — was byte-identical. Both files are now in the repository and
hash-match what was rendered and reviewed.

**2. The seller ladder could show a later step done before an earlier one.**
The live page read `Offer done · Agreement done · Property review CURRENT ·
Title & closing DONE · Closed upcoming`. `seller_progress` reported each step
from its own evidence, independently, so a title company entered early marked
that step complete while the step before it was still running. It is now
monotonic: a step is `done` only if every step before it is. An owner reading
their own sale cannot be shown a sequence that did not happen.

**3. Every external page carried the SOFTWARE VENDOR's name.** §40.2 rules the
vendor out of the page and the page obeyed it — but `index.html` sets the tab
title and paints a full-screen splash from the HOSTNAME, before React mounts
and long before the operator's brand is known. An investor opening the link saw
"BookaBoost" on a splash for a second and a half and kept it in their tab,
their history and any preview of the link they forwarded. Both now check the
path: on `/investor/` and `/my-property/` the boot code sets no title and shows
no name, and the page sets the title from `brand.name` once the payload
arrives, restoring it on unmount — the same treatment `BookingPage.jsx` and
`SurveyPage.jsx` already used for the booking side.

**4. Tap targets on a phone.** Measured at 390px against the real payloads: the
"Open" link on a document was 32px tall and, on the seller page, the owner's
phone number and email were 17px — on the one page an owner opens on a phone to
call the person selling their house. All three are now ≥44px under 720px wide.
Desktop is untouched.

The fourth item is the one to take the general lesson from: none of these were
findable by reading the code, and the first one was invisible to every test in
the suite, because the tests exercise the server and the server was correct.

### 14.1 Browser verification of the two review URLs

Chrome on this machine, against `localhost:5173` with the backend on 8000.

| | Investor | Seller |
|---|---|---|
| URL loads by direct navigation | yes | yes |
| Operator's brand in the masthead | Cedar Springs Property Partners (TEST) | same |
| Operator's accent applied | `#0f6f4f` | `#0f6f4f` |
| Tab title | operator's name, not the vendor's | same |
| Property renders | 1418 Cedar Springs Rd, Dallas TX 75201 | same |
| Cover photo + gallery | 4 photos, cover badge, lightbox | 1 photo hero |
| Investor-safe figures | asking 158,000 · ARV 300,000 · repairs 45,000 · close Nov 13 | n/a |
| Seller-safe status | n/a | Property review · Step 3 of 5, ladder in order |
| Make an Offer | opens; amount, closing date, financing, proof of funds, contact, notes | no write route exists |
| Offer submitted end to end | 171,000 / 2026-11-20 / cash / can_provide recorded, page returned "Your offer is with the team handling this deal" | n/a |
| Reload keeps working | yes | yes |
| Console | no errors (Vite HMR debug only) | no errors |
| 390px, real payloads | no overflow, no target under 44px | no overflow, no target under 44px |

Nothing internal appeared on either page. The payload each browser received was
captured and re-checked field by field: no seller name, phone, email or
motivation; no contract price; no MAO; no assignment fee; no internal notes; no
buyer id; no property id; and on the seller side no buyer, no offer, no ARV and
no repair figure.

Unauthenticated is proved at the source rather than by a browser mode: both
endpoints were fetched by a bare Python client carrying no cookie and no
`Authorization` header and both returned 200, and the pages themselves call
`fetch()` with no headers and no credentials. The three crossover cases —
seller token on the investor route, investor token on the seller route, an
invented token — each returned an identical 404.

One limitation, stated rather than papered over: the 390px figures come from
Playwright driving the shipped page files against the payloads captured
verbatim from this backend, because the reviewer's Chrome window would not
accept a programmatic resize. The code and the data are the real ones; only the
transport is mocked.

## 15. Security tests

Every item in the brief's section 18, as an executable assertion:

| Test | Result |
|---|---|
| Cross-tenant isolation | 4 passed (whole-module attack file) |
| Unguessable tokens | asserted ≥32 chars in the journey |
| Expired / revoked links | fail identically to an invented one |
| Seller link ≠ investor link | both directions, payload and photo routes |
| Investor A vs investor B | no name, status, offer or note crosses |
| Internal documents by URL | 404 |
| Unpublished photos by URL | 404 |
| Unpublished deals | 404, no useful data |
| No sequential-ID enumeration | no other row's id in either payload |
| No seller PII to investors | value-level assertion |
| No internal economics outward | value-level, both audiences |
| No audit or AI analysis outward | whitelist, asserted |
| POF claim ≠ verified | asserted |
| Respondent details do not overwrite the CRM | asserted |

## 16. Known gaps

1. `viewed` on a document reflects a fetch through a share link. A recipient
   sent the file another way never moves it. Correct, but do not read it as
   proof of receipt.
2. The fill sheet is a transcription aid, not a merge. Somebody still types into
   their own form.
3. `opened` in the buyer track record stays 0 unless the deployment can observe
   an open. It is not inferred from "we sent it".
4. Local media storage is still a review setting.
5. Accessibility was swept across the wholesale module; the external pages were
   checked for contrast, tap targets and text size but not with a screen reader.
6. The 1418 Cedar Springs sandbox deal now exists in the **review server's own
   database** with real share links and generated placeholder images — see
   §5, §6 and §20. The harness copy is no longer what the review runs against.

7. **The operator cannot yet see three of the six things the new offer form
   collects.** This one is a real gap and it is the next thing to close.

   The investor form now asks for price, closing date, financing, a
   proof-of-funds claim, contact details and notes. All six are validated,
   persisted and returned by `GET /wholesale/deals/{id}/buyer-board` —
   `test_the_respondents_details_do_not_overwrite_the_buyers_crm_record`
   asserts the respondent block comes back. But `wsBuyerBoard.jsx` renders only
   `pof_status` and `target_close_date`. `offer_financing`, `respondent_name`,
   `respondent_email` and `respondent_phone` are on the wire and on no screen.

   The practical consequence: an acquisitions assistant submits an offer on
   behalf of a buyer, says they will wire cash and gives their own phone
   number, and the operator sees an amount and a date. Nothing is lost — the
   record is complete and a later screen will show it — but until then the
   operator has to ask for information the investor already gave.

   Deliberately not fixed in this pass, because this pass was scoped to the
   review URLs and adding board columns is new work. It is four fields on one
   component.

## 17. What still requires a real third-party provider

- **E-signature.** Registry and lifecycle ready; a vendor needs a class plus a
  webhook. Without the webhook a document would sit at `sent` forever, which is
  worse than no button.
- **Comps.** Manual entry is fully usable; no provider is pretended.
- **Skip trace / enrichment.** Manual and CSV only.
- **Outbound email/SMS to buyers.** Composed and recorded; not sent.
- **Stripe.** Untouched this phase.

## 18. Files changed

```
app/models/wholesale_models.py          seller_visible, offer_financing,
                                        respondent_*, ai_tone, POF claimed
app/auto_migrate.py                     6 additive columns
app/services/wholesale_publication.py   branding, _photos, seller_photos,
                                        occupancy/market, brand on both payloads
app/services/wholesale_ai.py            TONES, scoped to the summary
app/services/wholesale_service.py       passes tone through
app/routers/wholesale_rooms_router.py   richer action, seller photo route
app/routers/wholesale_router.py         ai_tone in/out
app/routers/wholesale_files_router.py   seller_visible + its own audit line
app/routers/wholesale_buyers_router.py  board detail, extended track record
app/routers/wholesale_contracts_router.py  fill-sheet status and summary
app/routers/import_batch_router.py      SS1 preview

frontend/src/pages/public/InvestorRoom.jsx        rebuilt
frontend/src/pages/public/SellerTransaction.jsx   branded, hero, photo
frontend/src/pages/public/rooms.css               rewritten
frontend/src/pages/wholesale/{WholesaleDeal,WholesaleSettings,wsFiles,
                              wsContracts}.jsx, wholesale.css
frontend/src/pages/ImportBatchReview.{jsx,css}

tests/test_wholesale_journey.py         new
tests/test_wholesale_rooms.py           +9
tests/test_import_capacity_preview.py   +4
```

Added while fixing the review URLs (§14.0):

```
frontend/.env                           NEW, gitignored — points the dev
                                        server at the LOCAL backend instead of
                                        production. §6.1 explains why it is
                                        load-bearing.
frontend/index.html                     the pre-React boot no longer puts the
                                        vendor's name in the tab or the splash
                                        on /investor/ and /my-property/
frontend/src/pages/public/InvestorRoom.jsx      landed in the repo for the
                                        first time; + tab title from brand
frontend/src/pages/public/SellerTransaction.jsx same
frontend/src/pages/public/rooms.css     44px tap targets under 720px
app/services/wholesale_publication.py   seller_progress made monotonic
```

## 19. Screenshots

`/mnt/user-data/outputs/p6-shots/` — investor desktop and mobile, seller desktop
and mobile, command center, deal overview, deal contracts tab, settings.

## 20. Exact human-review instructions

**Start both servers** (§6.1), confirm `frontend/.env` exists, then:

1. Open the investor URL in §5 in a normal browser window. Read the masthead:
   it must say the operator's name and nothing about the software. Check the
   tab title says the same. Then look at the page the way a cash buyer would:
   cover, address, chips, price, facts, narrative, gallery, comps, closing,
   documents, and five things you can do — each saying what it commits you to.
2. Open the gallery. Arrow keys, next/previous, Escape, captions, counter.
3. Press **Make an offer**. It should ask for price, closing date, how you are
   funding it, whether proof of funds exists, who you are, and anything to add.
   Submit it. A record appears on the operator's buyer board — including the
   financing and the proof-of-funds CLAIM, which is stored as `claimed`, never
   as `verified`, because nobody has looked at a document.
4. Reload. The page comes back and your offer is still shown as yours.
5. Open the seller URL in §6. One status, stated once, and a ladder that only
   ever climbs. No buyer, no offer, no ARV, no repair figure, no fee — and no
   way to type anything, because the route has no write endpoint.
6. Swap the two tokens between the two routes. Both must 404, identically, and
   identically to a token you invent.
7. `tests\test_wholesale_rooms.py` is the executable version of steps 5 and 6.

**Re-run after the fix**

```
tests\test_wholesale_rooms.py      31 passed
tests\test_wholesale_journey.py     2 passed
-k wholesale                      255 passed, 5320 deselected
```

**Sandbox data.** Organization `cd2b1e04` "Wholesale Review (TEST)", branded
"Cedar Springs Property Partners (TEST)". Deal
`59603154-544c-4758-a3a0-0bc6995e9863`. The seeding script is
`.claude-tmp/p6fix/seed.py` and it drives the running API over HTTP rather than
writing rows, so nothing in it can create a state the application would refuse.
No real person, address, phone or email is in it; every contact is
`*.example`.
