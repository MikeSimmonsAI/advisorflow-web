# Permissions

Enforced on the server in `app/services/bookit/core.py` on every request. Frontend hiding is never
the control.

1. **Authenticated tenant user** (`require_tenant_user`) in the program's workspace (`_program`).
2. **Manager** = a manager of the Restland program workspace (the same check every program screen
   uses). **Advisor** = a `program_team_members` row whose `user_id` is this login.
3. **Not open yet:** until `bookit.live` is set, only the owner's logins
   (`core.OWNER_LOGINS`, or `bookit.preview_emails`) and `god_admin` get in — another org admin of
   the workspace is refused (standing order, Nov 21). Advisors are refused until it is live.
4. **Record level:** an advisor reads and writes only rows assigned to their own team name
   (appointments, follow-ups, families via any of those, networks they submitted or contacts
   assigned to them, ADD cases and service requests assigned to them, sales where they are the
   closing or assigned advisor). Anything else answers **404** so ids can't be probed.
5. **Management only:** reassigning, approving enrollment, correcting closed sales (with a
   reason), attribution changes, categories, team access, audit, Command Center.
6. Identity always comes from the session; advisor names in a request body are ignored except
   where management sets them.
7. Responses are `Cache-Control: no-store, private`.
8. Michael Schlueter: `michaelpschlueter@gmail.com` holds `org_admin` in the Restland workspace
   (existing permission) — he becomes a BookIT manager only once BookIT is live.
