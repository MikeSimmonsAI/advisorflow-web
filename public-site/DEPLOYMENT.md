# EvoSys Pro public website — deployment notes

This directory is the **deployable EvoSys Pro public website**, versioned in the
platform repo so the site and the API it calls change together. It is *not*
served by the FastAPI backend; it is uploaded to the web host's document root.

## What talks to what

The site calls three public endpoints on the platform and nothing else:

```
GET  /public-booking/evosyspro/meeting
GET  /public-booking/evosyspro/slots
POST /public-booking/evosyspro/book
```

with `POST /site-intake/evosyspro/demo-request` as the fallback when online
scheduling is unavailable. All four are reached **server-side from PHP**, so the
browser never sees the API host and no CORS entry is needed.

## Configuration

Copy `private/config.example.php` to `private/config.php` and set:

| Key | Required | What |
|---|---|---|
| `PUBLIC_BOOKING_BASE_URL` | yes | `https://<api-host>/public-booking/evosyspro` |
| `DEMO_WEBHOOK_URL` | yes | fallback intake endpoint |
| `NOTIFY_EMAIL` | no | website-side contact notifications |
| `OPENAI_API_KEY` | no | the "Ask EvoSys Pro" widget only |
| `CAREERS_ADMIN_PASSWORD_HASH` | first run | `password_hash()` output; after first sign-in the password is changed from Settings |
| `CAREERS_STORAGE_DIR` | **recommended** | see below |

`private/config.php` is deliberately **not** in the repo. It holds secrets.

## Put applicant storage above the document root

`CAREERS_STORAGE_DIR` defaults to `storage/careers` inside this tree. That
directory is protected by two independent `.htaccess` rules and a `robots.txt`
disallow, but all of that depends on the web server honouring `.htaccess`.

Applicant storage holds real people's names, phone numbers and resumes. In
production set `CAREERS_STORAGE_DIR` to a path **above** `public_html`, so the
data is not under the document root at all and no web-server rule has to be
correct for it to stay private.

## The website never sends booking mail

Booking confirmations, internal notifications and reminders are owned by the
platform and gated there. There is no `mail()` call on the booking path and the
page reports status only — it renders from `confirmation_email.status` and never
claims an email was sent unless the backend says it was.

## Careers is self-managed

`/careers/admin/` manages open roles, page copy, the projected-income scenarios,
applicants, statuses, private notes, resumes and CSV export. Routine Careers
changes need no developer and no code edit.

Career applicants are a **separate intake concept**: applying creates no Lead,
no Opportunity and no sales-pipeline record, and the application path makes no
call to the platform at all.
