# Deployment and rollback

* Ships with the normal flow: PR → checks → squash merge → Render deploys `main`
  (backend `advisorflow-backend`, static frontend builds from `frontend/`).
* Tables are created by `create_all` on start-up; all additive. No existing table is altered.
* Visibility is gated in data, not code: `outreach_programs.booking_policy_json.bookit`
  (`live`, `preview_emails`, `suspended`, `campaigns`).

## Rollback
1. **Hide it:** leave `bookit.live` off (default) — only owner logins can open it. To hide it
   from the owner too, set `bookit.preview_emails` to `["nobody@invalid"]`.
2. **Code:** revert the BookIT PR; the new tables can stay (unused) — no data is lost.
3. Pending board edits (`handoff_pending_edits`) are applied or recorded; nothing is deleted.
