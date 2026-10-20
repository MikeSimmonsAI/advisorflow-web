# Wholesale (Lane B) — handoff, Oct 19 2026 (evening)

Branch `feature/wholesale-development` (started at production main d46743f). Status issue #236; lane rules #239.
Worker: Claude cloud session (a cloud clone of the repo; `C:\Dev\lanes\wholesale` on Mike's PC was not touched — `git pull` there picks this up). SCI branch, folder and staging untouched.
Budget: $0. No paid calls, no messages, no imports, no production changes. Production database read with SELECT only.

## What is true today (production, 2026-10-19)

| Area | State | Evidence |
|---|---|---|
| Daily searches: New Check Out (unchanged), Behind on Taxes, Tired Landlord | WORKING | Ran every day Oct 15–19, last 13:34–14:12 UTC, 0 failures in a row, $0 |
| Free county sources (DCAD, Dallas TRW, Tarrant roll, TAD upload, LGBS, Dallas 311, FW code + open cases, Collin CAD, Denton GIS, Census) | WORKING | Signals from each source; TAD file survives deploys |
| Properties | 9,033 | Tarrant 3,330 · Dallas 3,302 · Collin 1,415 · Denton 972 · Parker 13 |
| Scores | honest, low ceiling | 65+: 1 (max 66) · 55–64: 16 · 45–54: 243 · excluded (land/institutional): 266 |
| Owner phone/email lookup | BUILT, OFF (money) | Paid limits $0. Contact points in the whole system: 1 |
| AI seller texting, permission links, hand-offs | BUILT, OFF | 0 messages / links / hand-offs in production; auto-outreach off on every search; cold-text sign-off not given; no texting number |
| Seller page evosyspro.live/sell | WORKING | live; 19 inquiries (last Sep 27) |
| Deals / offers / contracts / e-sign | built, barely used | 20 deals, 1 offer, 0 contract templates |
| Buyers | data in | 101 buyers, 100 buy boxes, 0 matches run |
| Field team / canvass | built, never used | 0 appointments, visits, routes |
| wholesale.evosyspro.live | NOT HOSTED | code in main; site does not answer |
| ~14.7k held leads | still on HOLD | not in production as wholesale rows or leads — nothing imported |
| App stores | PAUSED (left alone) | |

### Why so few high scores
Not a bug, and no weights were changed. Free signals (tax delinquent 12, tax suit 8, absentee 15, out-of-state 8, long ownership 10, code violation 8, CDU poor 4) stack to the 50s–60s. Vacant (18), pre-foreclosure (16), equity (up to 14) and probate (12) have no free automated DFW source; `equity_pct` is empty on every property.
Volume leak: Collin and Denton have no free tax data, so their properties top out at 33 / 37. They are 2,387 of 9,033 properties; Tired Landlord's discovery focus puts `collin_cad` and `denton_gis` first and spends ~200 of ~500 daily records there.

## Done tonight (PR #242 to main — NOT merged, needs Mike's production GO)

| Commit | What |
|---|---|
| 777143e | **Investor form → EvoSys Buyers.** `POST /site-intake/wholesale/{intake_key}/buyer-inquiry` (same key, rate limit, neutral refusal as /sell). Buyer + structured buy box; a form that names an EXISTING buyer's email/phone never changes that buyer (criteria saved as an inactive 'confirm with buyer' box); double submit writes nothing; linked to the contact database as a partner; in-app notice only; contact permission ≠ SMS consent. `intake.php` writes the private file first, then posts to `WHOLESALE_BUYER_INTAKE_URL` and records whether it worked. Verified PHP → API → DB locally. Two field-login tests fixed (they called `/leads`, which doesn't exist; the fence returns 403 on `/leads/`). |
| edec5aa + 9663ace | **End-to-end proof + 2 fixes** (9663ace = fixes from an independent review: buyer-form hijack closed, owner-name completion only when every cut word is in the full name, reader ignores 'passed the inspection' and negations). `tests/test_wholesale_source_to_deal.py` runs county records → one property → score → lookup stays off → outreach refused → hand-added 555 number → owner's own permission tick → seller's words → hand-off → promoted deal (no ARV invented, nothing sent/charged). Run on production's top property's real records (read-only, not committed): passes. Fix 1: the tax roll's cut-off owner name now gets completed by the appraisal district's full name for the SAME owner (same mailing + shared name word); before, contact lookup refused such properties forever (146 in production, incl. the 66). Fix 2: the seller-reply reader hears "my mom passed", "needs a new roof", "foundation issues", "close in 30 days"; same words: intent 48 → 81. |

Tests: 72 wholesale/evosense files → 865 passed. Full suite: see the PR.
Rollback: revert the two commits (no migration, no new table, no setting). The buyer route is additive; `intake.php` without `WHOLESALE_BUYER_INTAKE_URL` behaves exactly as before.

### What runs by itself vs needs a person (after this PR)
- **Automatic:** daily discovery from free county records, dedupe, scoring, owner identity (now with full names), status/next action, buyer-form intake, seller-form intake, hand-off creation when the seller's words cross the threshold.
- **A person:** getting the owner's number (door knock / call / paid lookup when funded), sending the permission link, typing in what the seller said on a call, taking the hand-off, promoting to a deal, ARV/comps, offers, contracts.
- **Blocked (owner):** paid owner lookup, texting number + cold-text sign-off, microsite hosting/DNS, wholesale@ alias.

## Owner-only actions (Mike)
1. **Production GO** for the PR (then it merges and deploys; I verify live).
2. **Money:** turn on owner phone/email lookup (Tracerfy credit + Wholesale Settings limits) — without it, every high property waits for a person to find the number.
3. **Texting:** a number on the EvoSys account + cold-text sign-off on the searches — only if EvoSense should text sellers itself.
4. **GoDaddy:** host `wholesale.evosyspro.live` (DEPLOYMENT.md steps 1–4), set `WHOLESALE_BUYER_INTAKE_URL` (step 5), create `wholesale@evosyspro.live`.
5. **Search focus (decision):** drop `collin_cad` / `denton_gis` from Tired Landlord's discovery focus, or keep them for volume knowing they can't qualify on free data.
6. **The 146 already-stuck properties (decision):** the fix applies to records as they arrive; an already-matched record is not re-read, and the Controls re-derive keeps "cut off" if any record says so. Completing those 146 names needs a one-time change to production rows — say the word and it gets built as a dry-run-first tool, like re-derive.

## Smaller things noticed (not fixed)
- Canvass routes order by score but still include score-0 excluded lots (land/institutional) when a ZIP is thin.
- A property can show "high opportunity" while the contact lookup says "insufficient" for reasons like a cut-off name — the screen's next action could say why.
- Contact lookup for an owner who already said no / estate names still needs a person — by design.
