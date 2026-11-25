# Google Sheet → EvoSys field mapping

Row ids are database ids (booking id, `FU-<conversation id>`, `TOLD-<message id>`, `NF-<lead id>`),
never sheet row numbers.

## Appointments
| Sheet column | Stored in | BookIT field |
|---|---|---|
| Appointment ID | `booking_links.id` | `id` |
| Booked On (CT) | first `appointment_notifications.created_at` (else `booking_links.created_at`) | `booked_on` |
| Date / Time (CT) / Day | `booking_links.booked_time` | `date`, `time`, `day` |
| Family / Phone / Email | `leads` | `family`, `phone`, `email` |
| How / Home Address | `booking_links.meeting_type` / `visit_address` | `how`, `home_address` |
| System Status | `booking_links.status` (+ "(moved)") | `status` |
| What They Said Last | last family message + office notes (`lead_notes` by a person) | `said` |
| Assigned To | `handoff_sheet_rows.assigned_to` | `assigned` |
| Remote Call Link / Details, Outcome, Team Notes, Sold?, What Was Sold | `handoff_sheet_rows.staff_json` | `link`, `outcome`, `notes`, `sold`, `sold_what` |
| Last Update (CT) | `handoff_sheet_rows.staff_changed_at` | `last_update` |
| (new) structured sale | `bookit_sales` + `bookit_sale_lines` | Sales |
| (new) ticket disposition | `bookit_dispositions` | Close Ticket |

## Follow-Ups
| Sheet column | Stored in | BookIT field |
|---|---|---|
| Follow-Up ID / Added (CT) | `program_conversations.id` / `created_at` | `id`, `added` |
| Family / Phone / Email | `leads` | |
| Why | conversation state + reason + office notes | `why` |
| Follow Up On | `program_conversations.next_followup_at` | `follow_up_on` |
| What They Said / System Status | last family words / state | `said`, `status` |
| Assigned To, Done?, Team Notes, Appointment Date/Time, How, Home Address, Followed Up On, What Happened | `handoff_sheet_rows.staff_json` | same keys |
| Booking Result | written by the sync | — |
| Last Update (CT) | newest of team change, reply, family message, office note | `last_update` |

## 6 Block (sheet) / Evo 6 Block (BookIT)
Sheet entries still create families through `new_families_pass` (`source = permission_to_access`,
counted as `evo6_block` attribution). BookIT networks use `bookit_referral_batches` (parent) and
`bookit_referral_contacts` (child): name, relationship, phone, email, address, notes, validation,
documented outreach authorization, suppression, assigned advisor, family record (`lead_id`),
enrollment state. **Not yet mirrored to the sheet's 6 Block tab** (BookIT networks are newer
than the tab).

## Team
Name, Phone, Email, Active? → `program_team_members` (synced every pass). Role/Status on the
sheet are display only; BookIT adds `role` and access suspension (management).
