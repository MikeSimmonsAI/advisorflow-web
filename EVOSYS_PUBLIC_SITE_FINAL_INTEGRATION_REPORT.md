# EVOSYS PRO PUBLIC SITE — FINAL INTEGRATION REPORT

**Repo:** `C:\Dev\advisorflow-web` · **Branch:** `main`
**Date:** 2026-09-18

| | |
|---|---|
| **1. Starting HEAD** | `1b36298` — matched the expected booking history (`3c0e8ad` → `1b36298`) |
| **2. Ending HEAD** | `760b6df` — this document's own commit is the tip, so the SHA above is the commit that produced the final text |
| **30. Local commit SHA** | **`cec8975`** — the integration itself. `df7cca4` and `760b6df` carry this report. |
| **31. Pushed?** | **NO** — 47 commits remain local |
| **32. Deployed?** | **NO** |

`git worktree list` shows 29 additional worktrees, all marked `prunable`. None
was touched. Mike's WIP — `app/auto_migrate.py`, `app/models/
master_contact_models.py`, `app/routers/god_master_router.py` — remains
**unstaged and unmodified**.

---

## 3. FILES CHANGED

One new top-level directory, `public-site/`. **No backend file was modified** —
`git diff --cached` showed zero files under `app/`. Staging used explicit paths
only; no `git add .`, `-A` or `-u`.

| Area | Files |
|---|---|
| V8 site | `index.html`, 7 legal/compliance pages, `404.html`, `assets/`, `robots.txt`, `sitemap.xml`, `.htaccess` |
| Booking | `request-demo/index.php` |
| Careers | `careers/index.php`, `careers/admin/index.php`, `private/careers.php` |
| Shared | `private/site.php`, `private/form-utils.php`, `private/config.example.php` |
| Existing intake | `sms-optin/`, `support/`, `api/ask.php` |
| New here | `DEPLOYMENT.md`, `.gitignore` |

34 files. The 8 pre-built pages are ~650 KB each because V8 inlines its assets
as data URIs — that is the approved build, not bloat introduced here.

## 4. PACKAGE INTEGRATED

`EvoSys_Public_Site_FINAL_INTEGRATED_READY_FOR_DEPLOYMENT_REVIEW.zip`, 50 files.
The six status/README markdown files were **not** carried into the repo — they
describe the handoff, not the site. `DEPLOYMENT.md` replaces them.

## 5. V8 PRESERVED? **YES**

The only edit to any V8 page is the Careers navigation link (§ below). Verified
at **exactly +60 bytes per file** — two 30-byte insertions — with every page
otherwise byte-identical. No redesign, no re-theming, no asset changes.

## 6–7. DISCOVERY + DEMO INTEGRATED, API CONTRACT VERIFIED? **YES**

Slug `evosyspro`. Endpoints unchanged and unrenamed:

```
GET  /public-booking/evosyspro/meeting     ← query param: code
GET  /public-booking/evosyspro/slots       ← query param: code
POST /public-booking/evosyspro/book        ← JSON field:  code
POST /site-intake/evosyspro/demo-request   ← fallback
```

All four are called **server-side from PHP**, so the browser never learns the API
host and no CORS entry is required.

A synthetic backend implementing the exact §19.3 shapes captured the real
outgoing request. All eighteen field names asserted present:

`code` · `meeting_type` · `start_utc` · `full_name` · `company` · `email` ·
`phone` · `industry` · `primary_challenge` · `primary_challenge_detail` ·
`current_system` · `locations` · `lead_volume` · `notes` · `timezone` ·
`submission_id` · `page_url` · `referrer`

And asserted **absent**: `organization_id`, `participant_user_ids`,
`owner_user_id`, `meeting_url` — the fields a public form has no business
sending.

## 8–9. CAREERS INTEGRATED, SELF-SERVICE VERIFIED? **YES**

Public `/careers/`, admin `/careers/admin/`. Every required capability is
present and was exercised: create/edit/publish/draft/close/delete a job,
featured flag, sort order, all job content fields, hero and section copy,
projected-income visibility/text/numbers, applicant status, private notes,
resume download, source attribution, CSV export, password change.

Proven end to end rather than asserted: a new role created in the admin appeared
on the public page; hero copy edited; projected-income scenarios changed — all
with no code edit.

**Mike does not need a developer, Claude or ChatGPT for routine Careers changes.**

## 10. APPLICANT SEPARATION VERIFIED? **YES**

Asserted, not assumed: after submitting an application the test checks that the
site made **no backend call at all** (the request-capture file is never
created), and that the stored record contains no `lead_id`, `opportunity_id` or
`brand_sales_org` vocabulary. A job application creates no Lead, no Opportunity
and no sales-pipeline record.

## 11. RESUME SECURITY VERIFIED? **YES — and two defects were fixed**

Already correct in the package: 5 MB cap, `is_uploaded_file()`, real MIME sniff
via `finfo` with the extension derived from the **sniffed** type (never from the
filename), server-generated filename, `move_uploaded_file()`, `chmod 0640`,
storage behind `Require all denied`.

**Fixed (defect 1 — path traversal).** `careers_clean()` collapses whitespace and
truncates; it does not remove `/` or `..`. So `?resume=../../admin-auth` walked
out of the applications directory and read any `.json` the web user could reach —
including this feature's own password hash. Application ids must now match the
`APP-<YYYYMMDD>-<8 hex>` shape they are minted with. Four traversal probes now
return 404 and no hash leaks.

**Fixed (defect 2 — second-order traversal).** The stored `resume.path` was
concatenated onto a directory unchecked. Server-generated today, so not
exploitable — but it is read back out of a JSON file, which is the shape that
becomes an arbitrary-file-read the moment anything upstream changes. Now
resolved with `realpath()` and required to land inside the resumes directory, so
a symlink cannot escape either. The download filename is also sanitised and
`Content-Length` + `nosniff` are sent.

## 12. PROJECTED-INCOME EDITING VERIFIED? **YES**

The three scenarios — Getting Started / Consistent Producer / Top Performer, with
Mike's stated activity and income ranges — are config, not code and not an image.
Edited through Careers Manager during testing and the change reached the public
page. The "Illustrative earnings scenarios only" disclaimer is present. **No
package-price cards and no commission tables on the Careers page.**

Exactly one published role: **Sales Consultant — Independent Contractor (1099)**.
No fake openings were seeded.

## 13. BOOKING CODE BEHAVIOR VERIFIED? **YES**

Accepted as `?r=` or `?booking_code=`, validated against
`^[A-Za-z0-9._~-]{6,128}$`, forwarded as `code`. The website **never resolves a
person locally** — brand scope, active membership and revocation are all decided
by the platform. An invalid code returns the backend's single safe public
message with no internal reason leaked.

## 14. TIMEZONE BEHAVIOR VERIFIED? **YES**

The visitor's IANA zone is detected and sent as `timezone`. Slots carry
`start_utc` plus both local renderings; the page renders the visitor's zone and
sends `start_utc` back **verbatim**. No client-side arithmetic on the booked
time, so a DST boundary cannot shift it.

## 15–18. CONFLICT, IDEMPOTENCY, NULL JOIN URL, REPLAY

| Case | Verified |
|---|---|
| **15.** 409 slot taken | passed through; safe message; form fields preserved; availability refreshes |
| **16.** Idempotency | `submission_id` generated once per rendered form, reused verbatim on resubmission, never regenerated after an uncertain response. No automatic POST retry loop. |
| **17.** `join_url: null` | treated as **booked**. No fake Zoom button — the Join control renders only for a real `https://` URL from the backend |
| **18.** `already_booked` replay | treated as success, same `reference`, "Already booked" wording, no second success state |

## 19. FALLBACK INTAKE VERIFIED? **YES**

On 503 the page exposes "Send My Information Instead", which posts to the
existing `POST /site-intake/evosyspro/demo-request` with its own separate
payload. Confirmed reaching the intake endpoint, not the booking one.

## 20–27. SAFETY

| # | Question | Answer |
|---|---|---|
| 20 | Booking email gates all OFF? | **YES** — `OUTBOUND_EMAIL_PUBLIC_BOOKING_CONFIRMATION`, `..._INTERNAL`, `..._REMINDERS` all default off and none was enabled |
| 21 | Real email sent? | **NO** |
| 22 | Real SMS sent? | **NO** |
| 23 | Real voice initiated? | **NO** |
| 24 | Real Zoom created? | **NO** |
| 25 | Real calendar event created? | **NO** |
| 26 | Cadence enabled? | **NO** |
| 27 | Automatic outreach enabled? | **NO** |

**Notification ownership is single.** There is no `mail()` and no
`evosys_notify()` on the booking path — grepped and asserted. The page renders
from `confirmation_email.status` and never claims an email was sent unless the
backend says so:

| status | Copy shown |
|---|---|
| `sent` | "Check your inbox for the details." |
| `pending_meeting_link` | "You're booked — your meeting link is on its way." |
| `delivery_disabled` | same safe booked message |
| `no_recipient` / `failed` | "You're booked. We'll be in touch to confirm." |

## SECURITY REVIEW — TWO MORE DEFECTS FIXED

**Defect 3 — no brute-force limit on `/careers/admin/`.** A public login form
protecting applicants' names, phone numbers and resumes, with nothing between an
unauthenticated script and the password but the password itself. Now eight
failures per IP per fifteen minutes, **persisted to disk rather than to the
session** so dropping a cookie does not reset it. Verified: refuses even the
correct password while throttled, and recovers on its own once the window
passes. It fails **open** only if storage is unwritable — a broken throttle must
not lock Mike out of his own hiring pipeline.

**Defect 4 — CSV injection in the applicant export.** Every value in that file
was typed by a stranger on a public form, and a cell beginning `=`, `+`, `-` or
`@` executes as a formula when the file opens in Excel — on the machine of
whoever reviews applications. Neutralised with a leading apostrophe, after
stripping leading tabs and newlines that would otherwise hide the trigger.

**Also fixed:** the public careers page echoed raw exception text to visitors.
Curated refusals still show; anything else becomes one generic sentence and the
real error goes to the PHP log.

**Reviewed and already correct:** CSRF on every admin mutation with
`hash_equals`; `session_regenerate_id(true)` on login; `password_hash` /
`password_verify`; honeypot and time-trap on both public forms; output escaped
with `htmlspecialchars` throughout; `Options -Indexes`; security headers;
`private/` and `storage/` denied; duplicate-application protection; no SSN, DOB,
race, disability, marital, medical or banking fields collected, with an explicit
on-form warning not to submit them.

**Defence in depth added:** the root `.htaccess` now independently refuses
`.json`, `.pdf`, `.doc` and `.docx`. One misconfiguration should not be enough to
publish real people's resumes.

## 28–29. TESTS RUN AND RESULTS

| Suite | Result |
|---|---|
| PHP syntax, all 10 PHP files | **PASS** |
| `node --check` on `assets/site.js` | **PASS** |
| `node --check` on the rendered inline booking JS (3 blocks) | **PASS** |
| Synthetic booking acceptance (43 checks) | **43 passed, 0 failed** |
| Careers acceptance (45 checks) | **45 passed, 0 failed** |
| Login throttle | **PASS** — blocks at 8, refuses correct password while blocked, self-heals |
| Backend booking + gate + chain suites (123) | **123 passed** |
| Backend quorum + compat + outbound suites (139) | **139 passed** |

**88 synthetic website checks, 262 backend tests, 0 failures.**

No full backend regression was re-run and none was warranted: **zero backend
files changed**. The last full regression at `1b36298` was 4,960 passed / 14
skipped / 0 failed, and this commit cannot have moved it.

No existing test was weakened. Six checks failed on first run; five were my own
harness being wrong (a stale capture file, PHP's dev server ignoring `.htaccess`,
a `grep -c` counting lines, and wrong field names for the income form) and were
corrected to assert real behaviour. The sixth was a missing dotfile in the test
copy.

## 33. REMAINING LAUNCH CONFIGURATION ONLY

Nothing below is code. Nothing below is done.

1. **`private/config.php`** — copy from `config.example.php`; set
   `PUBLIC_BOOKING_BASE_URL` to `https://<api-host>/public-booking/evosyspro`
   and `DEMO_WEBHOOK_URL`. Not in the repo, by design.
2. **`CAREERS_ADMIN_PASSWORD_HASH`** — one `password_hash()` value for first
   sign-in; changed from Settings afterwards.
3. **`CAREERS_STORAGE_DIR` above `public_html`** — strongly recommended. Two
   `.htaccess` layers protect it where it is, but all of that depends on the
   host honouring `.htaccess`. Above the document root, no web-server rule has
   to be right.
4. **Backend push and deploy** — the booking API does not exist in production
   yet. Until it does, the site correctly shows its unavailable state and offers
   the fallback.
5. **Platform scheduling configuration** — reporting chains, a default inbound
   owner, booking codes, work hours, calendar connections and a Zoom provider.
   Listed in §25 of `DISCOVERY_DEMO_PLATFORM_BOOKING_REPORT.md`.
6. **Outbound gates** — one at a time, when Mike decides. Suggested order:
   INTERNAL (the team sees bookings, no customer is emailed), then CONFIRMATION,
   then REMINDERS.
7. **Upload to the document root** and confirm `AllowOverride` is enabled.

## 34. BLOCKERS

**None.**

Two things to be aware of, neither blocking:

- **End-to-end against the real platform is not yet possible.** Every booking
  test ran against a synthetic backend implementing the §19.3 shapes exactly.
  That proves the contract is honoured; it cannot prove production behaviour
  until the backend is deployed and configured.
- **No calendar-architecture defect was found.** EvoSys remains the scheduling
  source of truth, participants' own Google/Microsoft calendars remain
  authoritative availability inputs, and nothing about that model was altered.
  The website reads availability and never computes it.

---

REAL CUSTOMER EMAILS: NO
REAL SMS: NO
REAL VOICE: NO
REAL CALENDAR EVENTS: NO
REAL ZOOM MEETINGS: NO
CADENCE: NO
AUTOMATIC OUTREACH: NO
PUSHED: NO
DEPLOYED: NO
