# Kerry Allan first-touch copy audit (staging only, nothing sent)

Run: `sci-go-no-go-copy-audit-20261006-1944`. Source of truth: `app/services/programs/message_brain.py` (playbooks `PLAYBOOKS`, touch-1 templates `_EMAIL[1]`, `_SMS[1]`) and `identity.py` (display name, SMS sign-off). The real `compose()` and `quality()` ran offline for all 8 campaign families in both channels (sqlalchemy and the DB were stubbed, because dependencies are not installed on this runner; the copy logic was not changed). Test location: Eastern Gate Memorial Gardens.

**Result: 16 of 16 current variants pass the deterministic gate. The 16 neutral variants below also pass. No message was sent.** The gate proves mechanics, not truth, so section 2 lists wording that rests on facts the repo cannot show.

## 1. Audit against the requirements

| Requirement | Result | Note |
|---|---|---|
| Sender always Kerry Allan | PASS | `display_name()` = "Kerry Allan \| <Location>"; identity lock on by default; gate fails any other display name. |
| Exact SCI location in signature/context | PASS | Named in the intro and signature; gate fails a missing or different location. SMS adds "- Kerry Allan, <Location>" via `apply_sms_signoff`. |
| No implied benefits or unsupported facts | PASS with F-1, F-2 | Gate bans prices, free items, eligibility, "thank you for your service", family/ownership claims, pressure language, corporate parent. |
| Reply-first, appointment-second | PASS | Touch 1 asks only for a reply. Appointment is touch 4. |
| Human / local / nonrobotic | PASS with F-3 | Robotic phrases banned ("just checking in", "touching base", "circling back", "kindly"...). |
| No repetitive "just checking in" | PASS | Banned by the gate; also a similarity check (trigrams >= 0.5 holds a repeat). |
| One clear CTA | PASS | One reply CTA; "call us" and "click here" fail the gate. Email touch 1 has two reply asks (resend, or a question); both are "reply". |
| Channel-appropriate length | PASS | Email 73-77 words (limit 170). SMS 213-249 chars with sign-off and STOP line (limit 320). |
| No AI auto-send | PASS | Automated sends need the family switched ON; replies pause the cadence; suggested replies are drafts (harness rows 110-114). Families are OFF today. |

## 2. Findings that need Mike or SCI facts (gate items F-1 to F-4)

- **F-1: delivery is assumed.** Touch-1 email says "I wanted to personally make sure you received what you needed" and "If you'd like me to send the information again". Both assume something was already sent. The source data shows the campaign they responded to, not that a guide or flyer reached them. SMS "did you get what you needed?" has the same assumption. Safe fix: the neutral variants below. Needed fact: was the original item actually delivered for these 535 contacts?
- **F-2: "you requested / asked us for / showed interest".** The "why" clause comes from the campaign family. `veteran_official`, `veteran_spanish`, `cemetery_x_sell`, `re_engagement` ("you were in touch with us about planning") are looser than "requested". Needed fact: source campaign names and statuses for those families. `re_engagement` is the **fallback for any contact with no family**, so a contact with no source campaign still gets a claim the data may not support. Recommend holding no-family contacts for review rather than using that line.
- **F-3: first-person promise.** "I'll get back to you myself" and "I read every reply myself" are truthful only if Kerry personally answers across all 39 entities. Needed fact (D2): who replies, and Kerry Allan's real title and any signature block.
- **F-4: Spanish family copy is English.** `veteran_spanish` is described as Spanish callouts, but the touch-1 copy is in English and does not offer Spanish (only touch 3 does). Do not send it to contacts who may expect Spanish until a Spanish variant is approved.
- **Other notes.** SMS carries no link or phone number (matches campaign `CO3YNIF`); the opt-out line "Reply STOP to opt out." is added last by policy; SMS names Kerry and the location twice (intro and sign-off) which is redundant but within limits. Email also needs the location's postal address in the footer (C7).
- **Oaklawn:** no resolved location, so the gate fails "no resolved location" and nothing can be sent. Correct behavior; no copy exists for it.

## 3. Staging-approved first-touch variants

"Staging-approved" means approved for staging preview and test-recipient use. Real-customer use still needs D5/D6 in `SCI_GO_NO_GO_CHECKLIST.md`.

Fields: `{first}` first name (else "there"), `{loc}` exact entity name, `{why}` the family's clause below, `{Why}` the same with a capital. Email subject per family is in the table.

### 3a. Facts available per family (from `PLAYBOOKS`)

| Family | `{why}` | Email subject (touch 1) | Fact status |
|---|---|---|---|
| veteran_planning_guide | you requested information through our Veteran Planning Guide program | Your Veteran Planning Guide - {loc} | Supported by the family name; confirm F-1. |
| veteran_official | you asked us for veteran planning information | Your veteran planning request - {loc} | Confirm source campaign (F-2). |
| veteran_spanish | you asked us for veteran planning information | Your veteran planning request - {loc} | English only (F-4). |
| general_survey | you returned our planning survey | Thank you for your survey - {loc} | Supported by the family name. |
| life_story | you requested a Life Story from us | Your Life Story request - {loc} | Supported by the family name. |
| cemetery_x_sell | you showed interest in cemetery planning with us | Cemetery planning at {loc} | Confirm source campaign (F-2). |
| cremation | you asked us for cremation information | Your cremation information - {loc} | Supported by the family name. |
| re_engagement | you were in touch with us about planning | Following up from {loc} | Weakest; fallback. Required fact: why this contact is in the database. |

### 3b. Variant N (neutral, recommended for the first controlled use): no delivery assumption

Email (about 50-54 words):

```
Subject: <family subject above>

Hi {first},

This is Kerry Allan with {loc}. You're hearing from me because {why}.

Is there anything you'd like to know, or anything I can send you? Just reply to this email and I'll write back myself.

Kerry Allan
{loc}
```

SMS (209-245 characters including sign-off and STOP line):

```
Hi {first}, this is Kerry Allan with {loc}. {Why}. Anything I can help with? Just reply here. - Kerry Allan, {loc} Reply STOP to opt out.
```

Gate result for N: 16/16 pass, no failures, no warnings. "I'll write back myself" still depends on F-3; replace with "I'll write back" if D2 does not confirm it.

### 3c. Variant A (as implemented today; held until F-1 and F-3 are answered)

Email: `Hi {first},` / `This is Kerry Allan with {loc}. {Why}, and I wanted to personally make sure you received what you needed.` / `If you'd like me to send the information again, just say so and I'll send it to you personally.` / `Is there anything specific you'd like to know? Just reply to this email and I'll get back to you myself.` / `Kerry Allan` / `{loc}`.

SMS: `Hi {first}, this is Kerry Allan with {loc}. {Why} - did you get what you needed? Just reply here. - Kerry Allan, {loc} Reply STOP to opt out.`

Gate result for A: 16/16 pass, no failures, no warnings.

### 3d. Missing-facts rule
If a contact has no resolved entity, no family, an open review or a hold, nothing is composed or sent. If a family's source facts are not confirmed, use Variant N with that family's clause and mark the "Fact status" cell above as required before real use.

## 4. Reproduction
The offline run is not committed. To repeat it with dependencies installed, call `message_brain.compose(packet, 1, channel)` then `message_brain.quality(packet, msg)` for each key in `PLAYBOOKS`. Expect the verdicts above.
