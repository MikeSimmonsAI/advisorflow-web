# DEV-2: applicant CSV contact fields

Contact data IS stored: name and phone on `volt_reps` (first_name, last_name, phone), email on the linked
`users` row (`volt_reps.user_id`). They were simply absent from the CSV catalog. Added columns: Full name
(existing `applicant` key, relabelled), First name, Last name, Email, Phone.

- AMENDED (DEV-3): owner default export/initial selection is now Full name, Email, Phone, City, State, then the
  remaining prior defaults (First/Last name stay selectable, not default). Frontend starts with these selected;
  deselections and presets are respected; Download/Save disabled when nothing selected. Backend no-`column` default
  matches (`reports.DEFAULT_CSV_COLUMNS`). ACA-only: default is only owner-granted export fields; never auto-gains contact.
- Original DEV-2 note: ACA-only staff get them only via owner grants
  (view/export field lists now accept first_name, last_name, email, phone). Saved presets and column order unchanged.
- Phone-width pickers use a compact 44px touch-row grid (`.v-checklist`).
- Tests: `tests/test_volt_recruiting.py` (38 passed); frontend `npm run build` succeeded.
- Not merged or deployed; needs owner approval for the exact tested commit.
