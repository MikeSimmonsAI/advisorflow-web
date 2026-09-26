# EvoSys Wholesale — Intake → Identity → Lead workflow reconciliation

Date: 2026-09-26. Scope approved by Mike ("move directly into the
already-defined Intake → Identity → Lead Workflow reconciliation…").
Tenant-scoped throughout; nothing here sends a seller anything, buys anything
or touches Twilio configuration.

## Where things stood (audit, 2026-09-26)

A `/sell` inquiry reached `POST /site-intake/wholesale/{key}/seller-inquiry` and
wholesale's own `create_property` + `attach_seller`. The general Universal
Intake (`public_capture`, `ContactRegistry`, `org_contacts`) was never used,
and `org_contacts` / `org_contact_source_ids` still do not exist in this tree
(the `feature/universal-intake` branch is unmerged). Gaps found:

1. nobody was notified, and inquiry records were unassigned (so even the
   hot-reply alert, which goes to `lead.assigned_to`, never fired);
2. inbound seller SMS went to the generic funeral-planning auto-responder
   (booking link, auto-send), never to the seller record;
3. AI prompts had no seller context and called an inquiry seller a COLD lead
   ("do not imply a previous enquiry");
4. dedupe: phone-only person match (no email), exact-string address match, a
   different person on the same property OVERWROTE the deal's seller, a
   returning seller attached silently to a dead/closed deal, inquiry notes on
   a repeat were lost;
5. `PATCH /wholesale/sellers/{id}` existed but no UI used it, it could not
   clear a field, did not validate/dedupe a phone, ignored notes;
6. no Lead ↔ Wholesale navigation either way;
7. "booked" had no meaning for a seller (drafts always carried a booking
   link; a `booked` lead goes to the post-booking concierge);
8. `/sell` consent capture was correct but the presentation was a single
   dense paragraph.

## What changed

| Area | Change | Where |
|---|---|---|
| Identity — property | Normalized address match (suffix/direction/unit tolerant, same ZIP) | `wholesale_seller_intake._same_place` (uses `evosense.identity`) |
| Identity — person | Phone first, then email; org-scoped, non-test | `_existing_lead` |
| Returning person | Name filled if blank, inquiry appended to notes (dated), cold → warm | `submit` |
| Different person, same property | Attached as ADDITIONAL contact ("verify ownership"); deal's seller of record unchanged | `attach_seller(set_primary=False)` |
| Dead/closed deal | NEW deal (re-engaged); old deal untouched; `deal.reopened` event | `wholesale_service.reopen_deal` |
| Assignment | `WholesaleSettings.inquiry_assignee_id` (Settings → Contact → Seller inquiries) | router `PATCH /wholesale/settings`, `GET /wholesale/settings/assignees` |
| Notifications | In-app bell item `WHOLESALE_INQUIRY` to the assignee, else the tenant's own admins; `Notification.link` opens the deal | `wholesale_notify`, `NotificationBell.jsx` |
| Inbound seller SMS | Read onto the seller record (`apply_seller_reply`, sends nothing) + bell item; never auto-answered; pipeline launch skips sellers | `sms_router`, `wholesale_seller_context.handle_inbound_reply`, `pipeline_service` |
| Seller-aware AI | Prompt block: inquiry vs records relationship, what they told us, consent state, guardrails (no price/offer/ARV, no commitments, no booking link, ignore funeral framing) | `wholesale_seller_context`, hooked into `draft_reply_service` (SMS + email), `pipeline_service`, `ai_conversation_service` |
| Booking semantics | Sellers never get a booking link (drafts, compose context, pipeline). "Booked" = `appointment_status == scheduled` + `appointment_at` on the seller profile; never the Lead's `booked` status | `WholesaleSellerProfile.appointment_at`, `APPOINTMENT_STATUSES` |
| Editable seller data | PATCH clears on null, validates phone/email/appointment, 409 on a phone owned by another lead, consent does NOT carry to a new number, notes appended & signed; editor UI on the deal's Seller tab | `wholesale_router.update_seller`, `WholesaleDeal.jsx` `SellerEditor` |
| Lead ↔ Wholesale | Lead page shows the seller's properties/deals (seller of record vs additional contact); deal Seller tab links to the contact record | `compose_router` `wholesale` field, `LeadDetail.jsx` |
| `/sell` opt-in | Plain-language summary above the box; verbatim registered disclosure unchanged as the label; unchecked; panel highlights only when ticked | `public-site/sell/index.php` (needs cPanel upload) |
| Intake timeline | `60_days` accepted (the reply reader can produce it) | `TIMELINES` |

Schema (auto-migrate, additive): `notifications.link`, enum value
`notificationtype.WHOLESALE_INQUIRY`, `wholesale_settings.inquiry_assignee_id`,
`wholesale_seller_profiles.appointment_at`.

Tests: `tests/test_wholesale_intake_reconciliation.py` (17).

## Deliberately NOT done (needs a decision or the Core branch)

* **Universal Intake convergence.** Routing `/sell` through `public_capture` /
  `ContactRegistry` and mapping sellers to `org_contacts` waits for the
  `feature/universal-intake` merge; the seams (`converged_contact_ref`,
  source ids) are reserved. Wholesale did not build a second contact DB.
* **Capacity at intake** still refuses (503 to the form) when the plan's lead
  limit is full, where general forms HOLD the person. Changing that is a
  product/billing decision.
* **Confirmation SMS retry** after a quiet-hours refusal is not built (SMS
  program is OFF; nothing is sent).
* **Email / push to staff** for inquiries: in-app only for now.
* **`/sell` upload** to the web host (cPanel) is Mike's step.

## Production verification (2026-09-26, commit 5012ee5)

Via the live `/sell` form (SMS program OFF, no Messaging Service — the gate
refused the confirmation: `PROGRAM_DISABLED,MESSAGING_SERVICE_NOT_CONFIGURED`)
with clearly marked records ("Zztest Smoke", "Zztest Cousin",
100 Smoketest Street, Dallas 75201, 214-555-016x, @example.com):

* 1st inquiry (consent ticked): created, lead `warm_lead`, consent of record
  kept, no booking link offered (`compose` context), Lead page shows the
  Wholesale seller panel.
* 2nd, "100 SMOKETEST ST.", new phone, same email: same property, same lead
  (`matched_by: email`), notes appended, same reference.
* 3rd, different person: "additional contact", deal's seller unchanged.
* Deal marked lost, 4th inquiry: `deal.reopened`, new deal, old deal still
  `dead / price_too_high`.
* Seller PATCH: bad phone / bad appointment status → 422; phone change →
  consent note, new number NOT eligible (`NO_SMS_CONSENT`), old evidence kept;
  cleared field, appointment scheduled (shown on the Seller tab), signed note;
  Lead status stays `new`.
* Draft (`/sms/draft-reply`): seller-aware, references the inquiry and
  property, no link, no price.
* Found and fixed: `/wholesale/sms/consents` headline reported consent given
  for a PREVIOUS number as "consent on file" → now reports the current number
  only and flags earlier-number history.
* Not observable from Mike's session: the bell item itself (it goes to the
  workspace's org admin user; god admins are deliberately not recipients).
  Covered by tests.

The smoke-test deals were closed out as lost; the records remain, named ZZTEST.
