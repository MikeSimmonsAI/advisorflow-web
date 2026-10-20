# Atlantis — external dependencies: domain, email, inbox, calendar, suppliers

Prepared 2026-10-19/20 overnight (Lane C). Every fact below was checked read-only on that
night unless marked **UNVERIFIED**. Nothing here was changed: no DNS, no WordPress, no
mailbox, no calendar, no supplier account.

## 1. Domain — atlantislightandpower.com

### What is there today (read-only DNS + one HTTPS HEAD, 2026-10-19 23:58 CT)
| Record | Value |
|---|---|
| Nameservers | `ns47.domaincontrol.com`, `ns48.domaincontrol.com` (GoDaddy DNS) |
| A (apex and www) | `160.153.0.23` (GoDaddy hosting range); `www` is a CNAME to the apex |
| TXT | `ca3-b0e9c82f…` (a hosting/verification token — owner to identify) |
| MX | **none** — this domain receives no email today |
| SPF / DMARC | **none** |
| HTTPS | `200`, response header `server: cloudflare` — **UNVERIFIED** how Cloudflare sits in front (proxy on the hosting side or an earlier record); confirm in GoDaddy before cutover |

The live WordPress site is a production dependency. The EvoSys page is served at
`https://api.evosyspro.live/site/atlantis-light-and-power` and posts its forms to the same
origin (`/site/<slug>/inquiry`), so a domain in front of it must route both paths to the
EvoSys backend.

### Cutover plan (owner action; do not start without a GO)
1. **Decide the target.** Either (a) the whole domain moves to the EvoSys page, or
   (b) a subdomain first (e.g. `go.atlantislightandpower.com`) while WordPress stays on the
   apex. (b) is the low-risk first step and needs no WordPress change.
2. **Platform side (code-owned, before DNS):** the platform's `sites_base_url` decides
   the public address it prints; customer-domain serving at `/` (rather than `/site/<slug>`)
   must be confirmed or added and tested before any DNS change. The page derives its slug
   from the path, so serving it at `/` needs a small change — **not done tonight**.
3. **TLS:** the host must present a certificate for the chosen name (Render custom domain
   or Cloudflare). Verify `https://<name>/` and the form POST in a browser before step 4.
4. **DNS change (owner, in GoDaddy):** add the CNAME/A record the host asks for. Lower the TTL
   to 300 s a day ahead so a rollback is quick.
5. **Verification checkpoints:** page loads on desktop + phone; one form submission from a
   reserved test address (it is filed as a test record automatically); the lead appears in
   Atlantis on Rate Requests under New with the alert and task; old WordPress URLs either
   still work (option b) or redirect.
6. **Rollback:** restore the previous record (`A 160.153.0.23`) — the WordPress site is
   untouched, so rollback is a DNS edit only.
7. **Email impact:** none — the domain has no MX records today. If Atlantis later wants mail
   on this domain, that is a separate, deliberate change.

## 2. Sending identity (email *from* Atlantis)

| Fact (production, read-only) | Value |
|---|---|
| Atlantis `from_email` | **not set** |
| Atlantis `reply_to_email` | **not set** |
| Effect | anything sent for Atlantis would go out under the platform's support address, and replies would go to the shared EvoSys mailbox |
| Outbound | off — nothing is sent from this workspace |

Atlantis's real mail runs on **atlantis-enterprises.com** (Google Workspace: MX
`aspmx.l.google.com`…, SPF `v=spf1 include:_spf.google.com ?all`, **no DMARC**, no DKIM key at
the `google._domainkey` selector — another selector may exist, **UNVERIFIED**).

To make an Atlantis sender real (owner decisions + DNS on atlantis-enterprises.com):
1. Pick the address (e.g. an advisor/team mailbox). Do not invent one.
2. Add the sending service's DKIM record and include it in SPF; tighten SPF from `?all`
   to `~all` once every legitimate sender is listed.
3. Publish DMARC, starting at `p=none; rua=…` to observe, then `quarantine`.
4. Set Atlantis `from_email` / `reply_to_email` in the platform (staff action).
5. Test with a seed address you own; check headers for SPF/DKIM/DMARC pass.
6. Only then consider enabling any outbound — still separately approved.

## 3. Inbound replies (inbox)

The platform's reply mailbox connection is **Microsoft 365 only** (Graph sign-in,
`/god/email/inbound-mailboxes`). The one connected mailbox is `support@evosyspro.live`
(not bound to Atlantis). **Atlantis's mail is Google Workspace**, so its mailbox cannot be
connected as-is. Options for the owner:
- (a) a Microsoft 365 mailbox for Atlantis replies (works with today's code), or
- (b) forward Atlantis replies from Google to a connected Microsoft mailbox, or
- (c) a development project to add Gmail polling (not started — estimate needed).
Routing already matches a reply to the right workspace by the From/Reply-To address the
workspace sends under, so step 4 of section 2 (an Atlantis Reply-To) is a prerequisite for (a)/(b).

## 4. Calendar (appointments)

Per-user calendar connections support **Google and Microsoft**
(`/calendar/connections/{provider}`); Atlantis has **0** connected calendars. Each advisor
connects their own calendar after signing in; a test-connection endpoint exists. Booking
links and the public booking page are already built. No appointment has been created.
Suggested order: one advisor connects → test → a booking link for "rate review call" →
book with a reserved test address → cancel.

## 5. Suppliers — readiness matrix

| Supplier | Segment | How a customer is handed off today | Technical status | Customer action | Advisor action | How the outcome is confirmed | Auto-enroll | Missing |
|---|---|---|---|---|---|---|---|---|
| Chariot Energy | Homes only (businesses refused in code) | Advisor enrolls with Chariot directly | Adapter built; `CHARIOT_ALLOW_TRANSACTIONS` **off** | Accept plan, receive Chariot documents | Record hand-off; record outcome | Customer or Chariot's promo-code report (staff records the source) | **Off** | Owner GO + credentials review before any switch-on |
| ComparePower | Homes | Customer is sent Atlantis's tracking link | `COMPAREPOWER_AFFILIATE_URL` **not set**; catalog says so | Chooses and enrolls on ComparePower | Send link by hand; record hand-off | ComparePower affiliate dashboard | n/a | The real Atlantis tracking URL (must start `https://`) |
| Frontier / Gexa | Homes and businesses | Quote-based, advisor-led | No API | Signs supplier contract | Request quote by hand; record hand-off and stages | Gexa rep | n/a | Confirm the agreement terms (supplier wording came from PR #116 notes) |
| Commercial procurement | Businesses | Advisor-led quotes | Commercial stages + locations built overnight (dev branch) | Provides bills/usage, signs | Work stages; one reminder per step | Supplier-confirmed contract → `supplier_confirmed` → won | n/a | — |
| Move Concierge | Moving homes | Checklist, manual arrangements | Built; vendor integrations "not configured" | Confirms services active | Tick items; complete only when all ticked (dev branch) | Customer confirmation item | n/a | Vendor relationships |
| Home security (Revamped / ADT) | Homes | Only with customer OK | Partner onboarding not finished | — | — | Revamped Security | n/a | Partner agreement + onboarding |

### ComparePower setting
- Name: `COMPAREPOWER_AFFILIATE_URL` (server environment, backend service).
- Accepted only if it starts with `https://`; otherwise the catalog keeps saying "not set up".
- Validate: after setting, `GET /energy-ops/suppliers` shows `link_configured: true` and the
  link; open the link in a private window and confirm the URL carries Atlantis's affiliate id
  in ComparePower's own dashboard. Do **not** use a generic ComparePower URL — Atlantis would
  lose the credit.

## 6. Default advisor for the website
Production `customer_sites.default_owner_user_id` is empty; alerts fall back to the Atlantis
admin (verified live 2026-10-19). Setting it is a staff publish with
`default_owner_user_id=<advisor id>` (same publish path used for the republish, keeping title,
form kind and consent wording). Owner chooses the person; not changed tonight.
