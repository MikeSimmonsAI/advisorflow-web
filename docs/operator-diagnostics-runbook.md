# Operator Diagnostics Runbook

**GOD-07 — curl-only diagnostic endpoints, documented here so the knowledge survives.**

All endpoints below require a God Mode JWT. Obtain one by logging in as the platform
owner (`/auth/login`) and using the returned `access_token` as the Bearer token.

```
export TOKEN="<paste God Mode JWT here>"
export BASE="https://app.advisorflow.com"   # or your Render URL
```

Nothing in this runbook sends an SMS, email, or calendar invite unless the endpoint
description explicitly says so. Read-only calls are marked *(read-only)*.

---

## Calendar diagnostics

### 1. Connection health for a user *(read-only)*

Shows which calendar providers a user has connected, whether scopes are sufficient,
and what the last sync result was.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/calendar-diagnostics?user_id=<USER_ID>&org_id=<ORG_ID>" | jq .
```

**Query parameters:**
| Parameter | Required | Description |
|-----------|----------|-------------|
| `user_id` | yes | UUID of the advisor |
| `org_id`  | yes | UUID of their organization |

**What to look for:** `connected`, `scope_ok`, `last_sync_at`, `last_sync_error`.

---

### 2. Live read probe — hits the provider's API *(read-only)*

Actually calls the calendar API to confirm tokens are valid and the calendar is readable.
No event is created.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<USER_ID>", "org_id": "<ORG_ID>", "provider": "google"}' \
  "$BASE/god/calendar-probe" | jq .
```

**Body fields:**
| Field      | Required | Values |
|------------|----------|--------|
| `user_id`  | yes | UUID |
| `org_id`   | yes | UUID |
| `provider` | yes | `"google"` or `"microsoft"` |

**What to look for:** `read_ok`, `events_returned`, `error`.

---

### 3. Live write test — creates and immediately deletes a test event

**⚠ Writes to the user's calendar then deletes it. The user may see a notification.**
Use only when the read probe passes but booking still fails.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "<USER_ID>", "org_id": "<ORG_ID>", "provider": "google"}' \
  "$BASE/god/calendar-write-test" | jq .
```

Body fields are the same as the read probe. **What to look for:** `write_ok`, `delete_ok`, `error`.

---

## Email diagnostics *(read-only)*

Shows deliverability configuration for an organization: DKIM, SPF, From address,
SendGrid sub-user status.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/email-diagnostics?org_id=<ORG_ID>" | jq .
```

**What to look for:** `dkim_valid`, `spf_valid`, `from_address`, `sg_status`, `last_send_at`.

---

## SMS / 10DLC diagnostics *(all read-only)*

### 4. Lead SMS trace

Full send history for one lead: every attempt, status, and Twilio SID.
Optionally asks Twilio's API for its view (`ask_provider=true`).

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/sms-trace/<LEAD_ID>?limit=20&ask_provider=true" | jq .
```

---

### 5. Campaign registration for an org

Shows 10DLC campaign status as Twilio sees it.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/sms-trace/campaign/<ORG_ID>" | jq .
```

**What to look for:** `campaign_status`, `brand_status`, `use_case`, `registered_at`.

---

### 6. Inbound SMS trace

Diagnoses why an inbound reply may not have been routed. Checks the last N hours of
Twilio webhook calls to a specific number.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/sms-trace/inbound/<ORG_ID>?to_number=%2B15005550006&hours=12" | jq .
```

**Query parameters:**
| Parameter    | Required | Description |
|--------------|----------|-------------|
| `to_number`  | yes | URL-encoded E.164 Twilio number |
| `from_number`| no  | Handset that replied (narrows results) |
| `hours`      | no  | Look-back window 1–168, default 12 |

---

### 7. Webhook alert trace

Lists Twilio status-callback errors logged for an org in the last N hours.

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/sms-trace/alerts/<ORG_ID>?hours=6" | jq .
```

---

## Session management

### 8. Record exit from a tenant session

Called automatically by the frontend when the platform owner clicks Exit. Safe to call
manually if the session marker is stuck.

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  "$BASE/god/orgs/<ORG_ID>/exit-session" | jq .
```

This logs the exit in the audit trail and returns `{"exited": true}`. It does **not**
clear the `X-Org-Override` cookie — that is a frontend concern.

---

## Screens that already have a UI

These were curl-only at one point and now have frontend screens:

| Endpoint | Screen |
|----------|--------|
| `GET /god/twilio-diagnostics` | **Twilio Diagnostics** in the nav rail |
| `POST /god/maintenance/phone-audit` | **Maintenance Ops** in the nav rail |
| `GET /god/voice/agents` | **Voice Configuration** in the nav rail |
| `PATCH /god/orgs/{id}/attempt-policy` | **Voice Configuration → org tab** |
| `GET /god/diagnostics/user-access` | **Access & Permissions** in the nav rail |
| `GET /god/diagnostics/qualification` | **Lead Qualification** in the nav rail |

---

*Last updated: 2026-09-09 — GOD-07*
