# Wholesale field team build – handoff, Oct 11 2026

Everything below is live on app.evosyspro.live unless marked otherwise. Build plan sheet:
"Field Acquisition Team – Build Plan" (Google Sheets, ID 1Wla2-eYZqcIoRzcMhl9Ceq5EKTTHsBwBfj0YV8bE2hE).
Training guide: Google Doc "EvoSys Field – Training Guide and Script" (ID 1YCdplXwJSZruXcth8R6X0WYf6tw95wLbICfC6Do_1Js).

## What shipped today (PRs, in order)

| PR | Build list | What |
|----|-----------|------|
| #53 | #24, #4, #25 | Uploads keep files in the database when no store is set (prod had none, uploads were refused). Import limits 25 MB. Phone photos shrunk in the browser. 500 KB profile limit gone. Wholesale Team page: roles (Full access, Manager, Team lead, Field specialist, Canvasser) + reports-to tree. |
| #54 | #5, #6, #7 | Server fence for wholesale-only/field logins (app/deps.get_current_user -> wholesale_team.fence_user). Hunts / strategy changes / paid lookups admin + team Manager only (route dependency require_hunt_rights, after the plan check). |
| #55 | #8, #9, #10 | Field appointments: /wholesale/appointments page, book from property page and Callback Center, Central time. |
| #56 | #11 | Field property card; field logins on an ALLOW-list (their appointments, properties on their appointments only). |
| #57 | #12, #13 | Visit notes: seller interview, room-by-room repairs, walkthrough photos. |
| #58 | #16 | Log outcome: signed / follow up (call-back to booker) / dead. |
| #59 | #14, #20 | Offer tab (rep sees only "most you can offer"), offer limit on Team page (default 100% of MAO), approvals with bell notifications. New enum value notificationtype.WHOLESALE_OFFER (auto_migrate ENUM_VALUES_TO_ADD). |
| #60 | #26 | EvoSys Field home-screen app (manifest-field.webmanifest, PNG icons), swapped in on /wholesale pages. |
| #61 | #15 | Owner permission link from the field card; purchase agreement from Visit notes (price = standing offer, company signer = an admin, never the rep; promotes the property, then the existing contract send). |
| #62 | #19 | Team Board (/wholesale/board). |
| (this PR) | #17, #18 | Canvassing routes by ZIP + door-knock form; hot door -> call-back to the canvasser's manager. |

## Where the code is

- Roles / fence: `app/services/wholesale_team.py` (roles, fence, admin-only hunt writes), `app/routers/wholesale_team_router.py`, `app/deps.py` (fence call before `return user`), `app/routers/branding_router.py` (`wholesale_team` in /branding/org).
- Appointments: `app/services/wholesale_appointments.py`, `app/routers/wholesale_appointments_router.py`.
- Visit notes: `app/services/wholesale_field_visits.py`. Offers: `wholesale_field_offers.py`. Agreement: `wholesale_field_agreement.py`. Board: `wholesale_team_board.py`. Canvass: `wholesale_canvass.py` + `wholesale_canvass_router.py`.
- Models (all new tables, create_all makes them): `app/models/wholesale_ops_models.py` – WholesaleFieldAppointment, WholesaleFieldVisit, WholesaleFieldVisitPhoto, WholesaleFieldRules, WholesaleFieldOffer, WholesaleCanvassRoute, WholesaleCanvassDoor. File blobs: `wholesale_models.WholesaleFileBlob`.
- Frontend: `frontend/src/pages/wholesale/appts/*`, `pages/wholesale/team/WholesaleTeam.jsx`, `components/Layout.jsx` (FIELD_NAV, FIELD_PAGES, wholesale-only redirect, field app head), `auth/workspaceAuthority.js` (`canRunHunts`).
- Tests: `tests/test_wholesale_team.py`, `_appointments.py`, `_field_visits.py`, `_field_offers.py`, `_field_agreement.py`, `_team_board.py`, `_canvass.py`; every new id route is in `test_wholesale_cross_tenant.py`.

## Rules worth remembering

- Every grant that existed before today is "Full access" – nobody's access changed until Mike adds people.
- Adding the FIRST team member turns on named logins (only people on the team can open Wholesale); the page requires a confirmation and auto-adds the admin.
- Admin logins cannot be made wholesale-only.
- Field logins never see investor %, fee, MAO; the server strips them.
- Over-limit offers wait for an admin; the rep cannot approve their own.
- The company's signer on a field-sent purchase agreement is an admin, never the rep.
- The system never texts or emails sellers from these screens; the permission link is texted from the rep's own phone.

## Waiting on Mike

- #1 roles and pay, #2 attorney review (the script in the training guide is DRAFT until then), #3 offer limit (currently 100% of the formula max – change on Team page), #22 test run with a test field login, #23 first rep live.
- Seller-facing name "EVO Integrated Properties" (Wholesale Settings → "Name owners see").
- E-sign service must be connected in Wholesale Settings for agreements to actually send.
- Database growth from photos – may eventually need a bigger Render DB plan (costs money; Mike's call).
- VA test login report.

## Known small things

- Training guide: after the two bullets under step 6 the numbering restarts at 1 ("The agreement", "Before you leave"). Cosmetic; fix by hand in Google Docs.
- Local preview harness: `C:\Dev\overnight\preview_run.py` + a throwaway `frontend/vite.preview.local.mjs` (proxy /__api -> 127.0.0.1:8765) – the browser pane blocks direct calls to 127.0.0.1:8765. Delete the config after use; never commit it.
