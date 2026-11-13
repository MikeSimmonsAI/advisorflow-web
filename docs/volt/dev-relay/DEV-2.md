# DEV-2: applicant CSV contact fields

Contact data IS stored: name and phone on `volt_reps` (first_name, last_name, phone), email on the linked
`users` row (`volt_reps.user_id`). They were simply absent from the CSV catalog. Added columns: Full name
(existing `applicant` key, relabelled), First name, Last name, Email, Phone.

- Contact columns are opt-in: not in any default export; ACA-only staff get them only via owner grants
  (view/export field lists now accept first_name, last_name, email, phone). Saved presets and column order unchanged.
- Phone-width pickers use a compact 44px touch-row grid (`.v-checklist`).
- Tests: `tests/test_volt_recruiting.py` (38 passed). Not run: frontend build (not available in this scope check).
- Not merged or deployed; needs owner approval for the exact tested commit.
