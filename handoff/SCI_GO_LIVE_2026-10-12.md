# SCI go-live - overnight Oct 11-12, 2026

Mike: "we go live tomorrow". Rule he set at 01:23: **nothing goes to an SCI
customer until he has seen the list and approved it.** Nothing was sent tonight.

## Verified on production (read-only checks, 01:15-02:00 CT)

| Item | State |
|---|---|
| SCI organization | Exists (0a884078-...), 39 locations, Kerry Allan (Head of Sales) as sender, identity locked |
| 844-917-2171 | Toll-free verification APPROVED (Account Notifications). Voice webhook = production `/voice/inbound`, SMS = production `/sms/webhook/inbound` (both match). Route `voicemail_only`: callers hear "Thank you for calling <cemetery>. You've reached Kerry Allan..." |
| SCI sending switch | `SCI_SMS_SEND_ENABLED` on in production; every one of the 10 campaign families OFF; email runner OFF; daily cap 50 |
| Email | support@evosyspro.live via Resend; SPF, DKIM, DMARC pass; mailbox read OK; replies filed under Inbox/Customers Folder/SCI. Location aliases assigned but not receiving (emails go From support@) |
| Text consent | Readiness C1 "owner holds the original sign-ups; recorded as owner-attested on first text" (Mike reaffirmed 01:20: text and email everyone clean) |
| Booking | evosyspro.live/request-demo booking engine bookable (Discovery + Demo, Michael Schlueter, slots from Oct 12 11:00). SCI messages carry NO booking link (none configured for the 39) |

## Done tonight

- **Alerts** (`/program/settings` management_recipients, staff alerts on): Mike +14695537417 / mike@simmonsstrong.com, Michael Schlueter +15403927776, Kerry Allan +14103009412.
- **Mailing addresses** for 36 of 39 locations, each checked against the location's official Dignity Memorial page (email is refused without one - CAN-SPAM footer). Missing: Oaklawn Central Care Center, Pine Crest Cemetery West, Radney Funeral Home.
- **PR #73** `POST /god/programs/{org}/load-source` (owner only): stage + hold reviews + promote; never sends/enrols/consents. Holds now include source status Not Interested / Disqualified / DNC / Deceased.
- **PR #74** God Mode > SCI & Wholesale access > "3. SCI contacts: load the file" (pick CSV, Check, type LOAD).
- **PR #75** `public-site/wholesale/` microsite (not deployed).
- **Approval sheet** (Google Sheets): "SCI first send - 10 contacts - FOR MIKE'S APPROVAL" - tabs: Read me first / First 10 - approve (exact text + email per person, from the live preview) / Locations & flyers.

## NOT done - why

- **Contacts are not loaded on production (0).** The file can't be moved into the signed-in browser by Claude (blocked as personal-data handling). Mike loads it: God Mode > SCI & Wholesale access > 3, file `C:\Dev\af-wholesale-sell\_xfer\sci_poc_561.csv` (551 + the 10 extra rows from his SCI POC sheet). Check, then LOAD. Sends nothing.
- **Flyers not uploaded.** Pack is AI artwork marked provisional; Mike to approve which to use (Locations & flyers tab). Two flyers carry details that differ from the official listing (Elmwood 39_, Pine Crest 28_).

## Morning order

1. Mike loads the contact file (above).
2. Mike reviews the approval sheet, ticks who to send, notes wording changes.
3. Mike says "send the approved ones" -> Claude sends ONLY those (manual sends, text + email, Kerry Allan | <cemetery>), then watches replies/alerts with him.
4. Then decide: which campaigns to switch on, flyers, the 3 missing addresses, Oaklawn with SCI.

## Wholesale site (wholesale.evosyspro.live) - owner actions

- No DNS record exists for `wholesale.evosyspro.live`. Main site is GoDaddy (132.148.181.228). Create the subdomain in cPanel pointing at e.g. `public_html/wholesale/`, upload `C:\Dev\af-wholesale-sell\_xfer\EvoSys_Wholesale_site_UPLOAD.zip` contents, enable TLS, set `WHOLESALE_INTAKE_DIR` outside the web root.
- Mail is Microsoft 365 (MX evosyspro-live.mail.protection.outlook.com): add `wholesale@evosyspro.live` as an alias there. The contact page already says it becomes active once configured.
- Branding question for Mike: the site says "EvoSys Pro Wholesale"; Mike chose "EVO Integrated Properties" as the name sellers see.
- Careers: the two roles are not yet in Careers Manager (waiting on final pay - now 5% commission).
