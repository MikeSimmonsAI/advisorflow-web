# Connecting ATTOM, PropStream and Tracerfy (Project OVERDRIVE Phase B, #439)

For the owner and platform admins. No credential values belong in this file, in GitHub, in chat or in screenshots.

Where to look in the app: **Wholesale → Tools & Settings → Providers & Controls → Data providers** (the first tab).
Each provider shows one status. **CONNECTED appears only after a real answer from the provider.**

| Status | Meaning |
|---|---|
| NOT CONFIGURED | The platform credential is not on the server. Nothing is called. |
| READY TO TEST | The credential is present. Press **Test Connection**. |
| CONNECTED | A real, successful answer came back. |
| DEGRADED | The last attempt failed (network, timeout, provider error). |
| RATE LIMITED | The provider asked us to slow down; calls resume after the time shown. |
| UNAVAILABLE | The provider refused the key, or failed 3 times in a row. |
| ENTITLEMENT MISSING | The key works but the plan does not include the data asked for. |
| IMPORT ONLY | PropStream: no live connection; exports are imported as CSV. |

## ATTOM (property intelligence)

1. A platform admin adds the key to the **advisorflow-backend** service on Render as the environment variable
   **`ATTOM_API_KEY`**, then lets the service redeploy. Workspace users never see the variable name.
2. On Data providers, press **Test Connection** on the ATTOM card. It makes **one** ATTOM request
   (a sample address from ATTOM's own documentation). Expected result: CONNECTED.
3. Still on the ATTOM card (workspace admin): tick **ATTOM on for this workspace** and enter the
   **price per ATTOM request** from your contract. Enter **0** if your plan is flat-rate. Until a price is set,
   no paid ATTOM call is made.
4. Open a property → **Ownership** tab → **Property intelligence**. The panel shows what is on file, what is missing and
   whether ATTOM would help. When it can run, **Get ATTOM property data (up to $X)** appears.

What happens on a lookup (one property, one person, never a scheduled job):

1. What the record already has: nothing missing means no call.
2. A free, working county record covers the gap: use that, no call.
3. The exact missing fields are listed.
4. ATTOM is the provider for those fields.
5. Checks: credential, workspace switch, price, paid-data pause, rate limit, a saved ATTOM answer from the last 30 days (reused, never bought again), a lookup already running.
6. The budget is reserved first (workspace daily and monthly limits), for up to 4 requests: detail/owner, assessment, AVM, sales history.
7. The answer is recorded as **ATTOM's claim**: it fills blanks but **never replaces county records**; a different value is kept as a conflict. Texas sale amounts stay unverified. The AVM is an estimate, never an ARV. The recorded loan is the original amount, never today's payoff.
8. The charge is settled to the requests actually made. Packages your plan does not include are recorded and shown on the card.

Comparable sales from ATTOM are **not** used: in Texas ATTOM's sale amounts are not proof of a closed price
(the existing comps truth gate still applies).

## Tracerfy (contact intelligence)

1. A platform admin adds the token to **advisorflow-backend** as **`TRACERFY_API_TOKEN`**, then redeploys.
2. Press **Test Connection** on the Tracerfy card. It calls Tracerfy's usage endpoint (`GET /v1/api/analytics/`),
   which returns your credit balance. **No lookup is run.** Expected result: CONNECTED and the balance.
3. Check **Wholesale Settings → paid lookup limits** (per day / per month; **0 means off**).
4. Use **Enrich Contacts** on a property, or **Get phones & emails** on a buyer, seller or funding-partner list.

Already built in: a lookup is skipped when a good contact is on file, when the owner was already looked up for another
property (reused), or when a search in the last 7 days found nothing. Do Not Call numbers are never handed on as usable
phones. A miss is free; a find costs $0.10 (5 credits). Tracerfy's instant lookup answers at once, so there are no background jobs to wait on.
One Tracerfy account serves the platform. Each workspace has its own switch, limits, cost ledger and results.

## PropStream (investor property research)

PropStream does not publish a developer API, so nothing connects live. To bring PropStream data in:

1. In PropStream, build a list and **export it as CSV** (include skip-traced phones/emails if you bought them there).
2. **Discovery Inbox → Import list**, choose the file.
3. Read the **preview** first (nothing is saved). Then import.
4. The result shows: rows, **new**, **matched existing**, already imported, **held for review** (unclear address match),
   **rejected** (with the reason per line) and **conflicts** (values that differ from what an equal or stronger source said;
   the county value stands).

Header spellings from any PropStream template are recognised (Address / Property Address / Site Address, Owner 1 First +
Last Name, etc.). Distress columns count only when the cell clearly says yes. They are recorded as **the export's claim**,
not verified fact.

## What stays off until PR #437 (backend memory fix) is approved and live

No provider runs on a schedule from these screens. Bulk skip tracing, recurring provider scans and large provider jobs
stay off. Everything above is person-triggered, one property or one list at a time.
