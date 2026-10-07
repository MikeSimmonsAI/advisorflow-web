# SCI GO / NO-GO checklist (single launch gate)

Run: `sci-go-no-go-copy-audit-20261006-1944`. Branch `sci-program`, staging only. Written 2026-10-06 CT.
Nothing was sent, bought or changed in production, Twilio or any carrier account to produce this file.

Two different gates live here. Do not read one as the other.

| Gate | Meaning | Verdict now |
|---|---|---|
| **T. Thursday 2026-10-08 CONTROLLED TEST** | Staging only. Approved test recipients only. No real SCI customer is contacted. | **CONDITIONAL GO** for the staging-only scope in section T below. **NO-GO** for anything that reaches a real customer. |
| **P. FULL PRODUCTION ROLLOUT** | SCI code on production, real numbers, real outbound to the ~535 clean contacts. | **NO-GO.** See sections B, C and D. |

"Evidence" names a commit, harness result, staging proof, or an explicit unresolved state. Where no evidence is on record the item says UNRESOLVED.

---

## A. COMPLETE / VERIFIED

| # | Item | Evidence |
|---|---|---|
| A1 | Source/contact cleanup + holds: 551 Lead IDs; 10 source rows / 9 contacts held; 535 clean contacts | Relay directive state; hold/review gates enforced in `identity.send_refusal` and `message_brain.quality` (`on_hold`, `in_review`). Held records stay in place (not deleted). |
| A2 | Entity / campus / pool mapping: 39 named entities, 30 physical campuses (internal), six regional pools 205/334/850/251/706/318, 38 of 39 entities mapped | `650ac55` (campus lock), `46cd08d` (six-pool architecture), `e084807` (campuses report pool + readiness). Harness rows 1-31. |
| A3 | Oaklawn Central Care Center handling (the code side) | Harness row 17: no area code, no pool, no sender. 9 contacts stay unsendable. Resolution of the location itself is C1. |
| A4 | Global opt-out parser: "Can I stop by Friday?" is not an opt-out; STOP/unsubscribe/explicit phrases still are | `7d14387`; harness rows 46-74 (all PASS). |
| A5 | HOT / ACTIVE / LOW logic, cadence pause on any reply, cadence ends on opt-out, no auto-send of drafts | Harness rows 75-114; `f12a70e`. |
| A6 | Wrong-number / wrong-person goes to Data Review, not sold to | Harness rows 88-93; classification defect fixed in `f9659e5`. |
| A7 | 116-scenario stdlib readiness harness | `f9659e5`; `handoff/SCI_READINESS_HARNESS_RESULTS.md`: 116 run, 116 PASS. Stdlib only; it is not a substitute for B1. |
| A8 | Manager role matrix (code level) | `278df02`, `ae9437e`; `handoff/SCI_MANAGER_ROLE_RESULTS.md`: 46 rows, 42 PASS, 0 FAIL, 4 NEEDS-STAGING. |
| A9 | `/sci` front door works for God Mode; Michael's staging manager mirror + one SCI manager membership provisioned | `63a2396`, `78551de`; relay-stated staging verification. |
| A10 | Program Center facelift live on SCI staging | `dac2a15`, deployed by Render (relay-stated). |
| A11 | HOT inbound email proof (appointment_intent, HOT, cadence pause, management email alert, no auto-send) | Staging proof recorded in relay issue #1 and `handoff/SCI_OVERNIGHT_2026-10-06.md` section 6. |
| A12 | Staging outbound email authentication: DKIM, SPF, DMARC pass at Gmail; one-click List-Unsubscribe; plain-text part | `handoff/SCI_OVERNIGHT_2026-10-06.md` section 1 (Gmail only). |
| A13 | Real customer messaging is OFF | Campaign families are created OFF; automated sends refuse while a family is off (`identity.CAMPAIGN_OFF_REFUSAL`). |
| A14 | Kerry first-touch copy passes the deterministic quality gate (all 8 families, email and SMS) | `handoff/SCI_KERRY_COPY_AUDIT.md` (run offline against the real `message_brain.py`). Wording caveats are in that file; they are gate items F-1 to F-4 in section D. |

## B. READY BUT NEEDS LIVE CONTROLLED PROOF

| # | Item | Why it is not yet proven | Evidence today |
|---|---|---|---|
| B1 | Dependency-backed pytest and DB integration suites | **Unrun on this runner.** `sqlalchemy` is not installed here (`ModuleNotFoundError`), so only stdlib harnesses can execute. Last full run on record is Windows targeted: 401 passed (`SCI_OVERNIGHT_2026-10-06.md` section 4), which predates `46cd08d`..`dac2a15`. The pool/matrix tests in `e084807` were authored, not run. | UNRESOLVED. Run `pytest` with dependencies installed before Thursday. |
| B2 | Michael's first interactive activation / login | The membership exists; no evidence in the repo that he has activated and logged in. Role matrix row "Family Service Center nav appears for Michael after login" is NEEDS-STAGING. | UNRESOLVED (human check). |
| B3 | Pool webhook mapping and known / unknown inbound behavior | Logic proven in harness rows 32-45 and tests; the six pool rows (`workspace_id` NULL, label `pool:<id>`) do not exist because no numbers exist. | Code only. Needs B/C numbers first. |
| B4 | Controlled SMS proof, approved test recipient only | Needs a purchased number (C2) and an approved SMS path (C3). Inbound-only simulation can run today. | UNRESOLVED. |
| B5 | Real staging voice call, forwarding and transcription proof | Location greeting, voicemail transcription and unmatched queue are built (`56cf3dd`); never exercised on a real number. | UNRESOLVED. |
| B6 | Email placement checks: Gmail, Outlook, Yahoo, iCloud | Only Gmail seen. The test message landed in All Mail, outside Inbox, with no INBOX label (probable account filter). The Outlook, Yahoo and iCloud checks have not run. | Partial (Gmail, not Inbox). |
| B7 | Staging mailbox poll | Microsoft rejected the staging client secret after the 00:56 CT update (`AADSTS7000215`). HOT proof later succeeded, so confirm it is healthy again before Thursday. | Re-check `GET /god/staging/sci/status`. |
| B8 | Outlook filing folders (39) | Probe built, not completed while the mailbox was blocked. | `SCI_OVERNIGHT_2026-10-06.md` section 1. |

## C. BLOCKED / EXTERNAL ACTION REQUIRED

| # | Item | Blocker | Evidence |
|---|---|---|---|
| C1 | Oaklawn Central Care Center (9 contacts, 1 of 39 entities) | Location/address not found; SCI must confirm. Only link to "Oak Lawn Funeral Home (Pensacola)" is a similar name. No area code or pool is assigned and none may be guessed. | `SCI_39_NUMBER_PLAN.md`; harness row 17. Those 9 contacts must stay out of any send. |
| C2 | Six Twilio local numbers (205, 334, 850, 251, 706, 318) | **NOT YET PURCHASED / PROVISIONED.** Every worksheet row says NOT BOUGHT. Purchase is a spend Mike must authorize (D1). | `SCI_TWILIO_SIX_NUMBER_WORKSHEET.md`. |
| C3 | A2P / carrier path for real outbound SMS | Needs a registration whose use case matches multi-location SCI traffic (target AGENTS_FRANCHISES, not approved today), or toll-free verification of the 844 number. Whether the existing EvoSys brand fits is an attestation Mike must make. Existing campaign `CO3YNIF` is registered with no links and no phone numbers in SMS. | `SCI_39_NUMBER_PLAN.md`. Status: UNRESOLVED. |
| C4 | Existing 844 number, backup/overflow only | Its voice webhook still points at Twilio's demo URL (production config, deliberately untouched). It must never be the normal local sender. | Harness rows 18-23; `SCI_OVERNIGHT_2026-10-06.md` section 6. |
| C5 | Kerry Allan exact identity and signature | See D2. The copy signs "Kerry Allan" plus the location only; no title, no direct phone. Whether a real Kerry Allan is the person replying at all 39 entities is not recorded in the repo. | UNRESOLVED. |
| C6 | Production promotion of SCI code | SCI does not exist in production. Promotion (merge to `main`, migrations, `program_setup.py --manager-email` grant) is not authorized. | D4. |
| C7 | Per-entity postal address for the commercial email footer | The quality gate holds email for an entity with no verified postal address. Addresses were not in the source and none were invented. | `SCI_OVERNIGHT_2026-10-06.md` section 6. Verify which of the 38 mapped entities have one. |

## D. MIKE APPROVAL GATES

| # | Gate | Needed before |
|---|---|---|
| D1 | Authorize spend for six local numbers | C2 and any live SMS or voice proof |
| D2 | Confirm Kerry Allan's exact identity and signature (title, whether a real person answers, signature block) | Any first-touch send |
| D3 | A2P / carrier registration decision and attestation (Mike makes it personally) | Any real outbound SMS |
| D4 | Production promotion of SCI code | Anything outside staging |
| D5 | Explicit **GO** for the first live ~10 clean contacts | The first real customer message |
| D6 | Approve first-touch copy variant per family (`SCI_KERRY_COPY_AUDIT.md`), including the open wording items F-1 to F-4 | D5 |
| D7 | Any use of the 844 number as sender or overflow | Production |

---

## T. Thursday 2026-10-08 CONTROLLED TEST, exact scope

**Can be tested with no real customer outreach:**
- `/sci` front door and Program Center for God Mode and Michael (after B2), on staging.
- Inbound email to a location alias from the approved test address: classification, HOT/ACTIVE/LOW, cadence pause, management alert, no auto-send, Outlook filing (after B7/B8).
- One outbound staging email from Kerry to the approved test address (preview and quality-gate verdict for every family and touch).
- Inbound SMS and voice **simulation** against staging webhooks (signed test requests), including known-contact, unknown-sender and wrong-number paths.
- Opt-out phrasing, Data Review, hold and review refusal, Oaklawn refusal.
- Pytest/DB integration run (B1) and the 116-scenario harness re-run.
- Email placement at Outlook, Yahoo and iCloud seed inboxes **owned by Mike** (B6).

**Cannot be tested until the named blocker clears:**
- Any text or call on a real regional number: needs C2 (and D1).
- Real outbound SMS, even to a test phone: needs C2 and C3 (D3); inbound-only works without 10DLC, outbound does not.
- Voice forwarding to a human: needs C2 and a verified callback number (none invented).
- Anything addressed to a real SCI contact, any campaign switched on, any production data: needs D4 and D5.

**Thursday verdict: CONDITIONAL GO** for the staging-only list above, conditional on B1, B2 and B7 being confirmed. **NO-GO** for SMS and voice proofs until six numbers exist, and for all real-customer contact.

**Full production verdict: NO-GO.** Open: B1-B8, C1-C7, D1-D7. The shortest path is D2 (identity), D1 (numbers), D3 (carrier), then the numbers/webhook proofs, then D4, then D5 for ~10 contacts.

## Recommended order
1. Run pytest with dependencies (B1); confirm mailbox health (B7); Michael logs in (B2).
2. Mike answers D2 and D6 (copy facts F-1 to F-4).
3. Mike decides D1 and D3. Then Claude configures staging pool rows and runs B3-B5.
4. Seed-inbox placement (B6). Resolve Oaklawn (C1) with SCI.
5. Only then D4, then D5.
