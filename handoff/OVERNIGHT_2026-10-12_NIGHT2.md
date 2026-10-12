# Handoff – night of 12 Oct 2026 (2:00–4:00 AM CT)

Plain-words pass-down. Earlier today: WHOLESALE_FIELD_TEAM_2026-10-12.md and SCI_GO_LIVE_2026-10-12.md.

## Done tonight
| What | Where it stands |
|---|---|
| Contacts missing from the menu | Fixed – PR #77, live. Operations › Contacts, below Import Center. |
| Mike's 19 business cards (EVO-20261012-001) | Leads now (promoted 2:13 AM). Org: EVO Integrated Solutions LLC (also hosts Wholesale – the "EvoSys Wholesale" login only did the upload). |
| Networking event list | Cleaned to 12 (Coby Axt / "Colby Wayne" merged; James Boyd email moved out of the phone column; Matt Humphreys email-only; Jeremiah Hobbs left out – BookaBoost waitlist; "Amir" left out – no data, an Amir Rashid already exists). Imported as EVO-20261012-002 and promoted to leads. No campaign. File also in Mike's Downloads. |
| Lead owner | Mike is the owner. Only login in the account is wholesale@evosyspro.live, so the 31 leads are unowned. Cadences skip unowned leads. Decide: use that login as Mike, or add a Mike Simmons user to the account and assign. |
| EvoSys texting | Not possible yet – no number on the account or the user. |
| SCI 10-Day Aggressive | Draft cadence in SCI org (id 3e650340-…, industry "program", NOT default). Texts days 0,1,3,5,7,9. Becomes live only when set default AND families switched on AND contacts approved. |
| SCI follow-up wording | PR #78 (live c3b92a4). Code: identity.program_touch_text – follow-up texts use the default program cadence's own words instead of repeating re_engagement. Tests: tests/test_program_cadence_touch_text.py. |
| Misplaced EvoSys campaign | Deleted ("Networking 10-Day Aggressive" in EVO org – unused draft). |
| Control Room | PR #81. relay_control.relay_is_on(): off unless a GitHub read key is set (or RELAY_ENABLED=1). /god/relay/state and /worker return relay_off without calling GitHub; page shows one card and stops polling. tests/test_relay_off.py. |
| Field guide for Chad | Artifact "EvoSys Pro Field Guide" – ~220 screens, every control, risky-controls list, 17 problems found (6 fixed overnight). |
| Small fixes | PR #79: Org Manager crash (getPlanLabel), God Mode brand filters, clock label, onboarding wording, signed-PDF download header (also makes test_download_filenames pass). PR #80: Leads compose button always says Send SMS (it always texts). PR #82: EvoSense Discovery Inbox garbled characters (also fixes test_no_mojibake on main). Full suite before these: 7953 passed; both failures were pre-existing and are now fixed. |

## Waiting on Mike
1. Approve the SCI 10-day wording (or change it), then: set it default; set PROGRAM_EMAIL_FOLLOWUP_DAYS=2 on Render for emails every 2 days.
2. SCI: load the contact file (God Mode › SCI & Wholesale Access › section 3), approve the 10-row sheet, then "send the approved ones".
3. SCI text approval scope is "informational" – confirm the aggressive wording fits before heavy texting.
4. SCI email: runner off (PROGRAM_EMAIL_TOUCHES_SENDING), 3 locations missing postal address, PROGRAM_ASSET_BASE_URL not set, RESEND_WEBHOOK_SECRET not set, no inbox seed test.
5. EvoSys: lead owner decision; a texting number if those leads should get texts.
6. Website: parked until GoDaddy login works.
7. Field team: test field login → launch check; app stores paused.

## Problems found while mapping – still open
Lead Cleanup merge has no confirm (and Reassign panel never shows); booking confirmation checkbox doesn't stop the email; Salespeople 'Their pipeline' doesn't filter; Compensation locks out finance-only users; Availability booking settings always save your own; Launch Pad search dead; Cadence template dropdown preview-only; old Leads import panel unreachable; SCI Health buttons manager-only; Executive downloads may fail. Full list in the guide.
