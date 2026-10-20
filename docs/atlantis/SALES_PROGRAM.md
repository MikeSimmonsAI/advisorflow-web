# Atlantis — sales playbooks, field-to-commercial path, revenue readiness

Draft for Mike and Josh (2026-10-20 overnight). Written to match what the software does
today on the `feature/atlantis-phase2-ops` branch. **No pay rates, commissions, savings
figures or supplier promises appear here — those are owner decisions (last section).**

---

## 1. Residential playbook (advisor)

1. **Open the request.** Rate Requests → New. The qualification panel shows what you have,
   what is missing and the next step.
2. **Opening line:** "Thanks for asking Atlantis to look at your electricity. I'm <name>.
   I'm going to ask a few quick questions so I only show you plans you can actually get."
3. **Find out the situation:** lower rate? bill question? moving? contract ending?
   Don't know the current provider? That's fine — mark it unknown.
4. **Ask for usage the easy way:** "Do you have a recent bill handy? The kWh number on it is
   what matters most." Type the bill lines in (total, period, kWh, charges, actual/estimated).
5. **Never quote a number you haven't seen on a provider's current offer for that address.**
   Say: "Prices depend on your address, your usage and that day's plans. I'll show you the
   real ones."
6. **Pick a supported route** from the eligible suppliers list (homes only for Chariot;
   ComparePower only with Atlantis's link; Gexa by quote; Move Concierge for moves).
7. **Record the hand-off.** It opens a "Confirm outcome" task. A hand-off is not an enrollment.
8. **Confirm the outcome** later and say how you know: *staff report* (the customer told you)
   or *supplier confirmation* (supplier email/portal/rep). Use **Enroll** on Rate Requests only
   when the supplier confirmed it.

## 2. Commercial playbook (advisor)

1. **Who signs?** "Who makes the electricity decision for the business?" Record the
   decision-maker before quoting.
2. **When does the current contract end?** Renewal timing drives everything; record it per
   location.
3. **Get usage documents:** 12 months of bills or a usage report per meter. Stage
   *information requested* opens one reminder; mark *usage received* when you have them.
4. **Several sites:** add each service location (address, utility, meter/ESI ID, supplier,
   contract end). One business, one record — never a customer per meter.
5. **Quotes:** request supplier quotes by hand; mark each location's quote status. Stages:
   quote requested → quote received → proposal presented → decision pending.
6. **No savings promises.** Present what the suppliers quoted, with terms.
7. **Contract:** contract submitted → supplier confirmed (the supplier confirmed the signed
   contract) → won. Won is refused before supplier confirmation.
8. **Lost or deferred:** always record the reason; deferred needs a revisit date (one task
   is created for it).

## 3. Field-team playbook (rep)

1. **Approach:** "Hi, I'm <name> with Atlantis Light & Power. We help neighbors check whether
   they're on a good electricity plan. Can I leave you some information?"
2. **Record every door honestly** (Field → record visit): no answer, door hanger left,
   declined, interested, appointment requested, do not contact, not eligible, business lead.
   The system records what you say happened; it does not claim GPS or proof of location.
3. **Only refer someone who said yes** to an Atlantis advisor contacting them, and take a phone
   or email. That is permission to call about their request — it is **not** text-message
   marketing consent, and nothing is texted automatically.
4. **Do not contact** means do not contact: record it; if they give a phone/email that
   Atlantis already has, the record is marked DNC.
5. **Businesses:** record a *business lead* with the business name — it goes to an advisor as a
   commercial opportunity. Don't quote businesses at the door.
6. **What you'll see:** your own visits, referrals, referrals an advisor has worked, supplier
   hand-offs and confirmed enrollments. Managers see everyone.

---

## 4. Residential → commercial advancement (proposal)

States (proposed program states, **not** job titles or pay classes):
`field trainee → active residential rep → strong residential producer → commercial candidate
→ commercial trainee → approved commercial representative`

Evidence a manager reviews (from recorded data where it exists):
| Signal | Where it comes from today |
|---|---|
| Consistent recorded field activity | Field summary (visits per period) |
| Referral quality | Referrals an advisor has worked / new referrals |
| Supplier hand-offs and confirmed enrollments | Field summary (hand-offs; enrollments only from Enroll) |
| Follow-up discipline | Task completion on their referrals |
| Complaints / DNC on their referrals | DNC outcomes and manager notes |
| Supplier restriction knowledge, bill reading, decision-maker discovery | Training checklist below + manager sign-off |
| Mock commercial pitch | Manager assessment |

Promotion is a manager decision, never automatic, and never on raw door count.

**Commercial training checklist:** business contracts and renewal timing · reading a
commercial bill (demand vs energy, multiple meters) · identifying the decision-maker ·
asking for usage documents · how advisor-led procurement works · what a supplier quote is
and isn't · no savings promises · when to escalate a technical or contract question.

Software support today: managers can see per-rep results. A training-milestone record and a
manager-only "commercial candidate" flag are **not built** (they need a small data change and
a permission decision — see decisions).

## 5. Compensation readiness (structure only — no rates)

A configurable plan would need, per approved agreement:
- **What earns:** verified activity (if paid at all), qualified referral, supplier-confirmed
  enrollment, commercial contract confirmed by the supplier.
- **When it is payable:** after supplier confirmation / after a holding period.
- **Reversals:** if a supplier reverses or the customer cancels within the agreement's window.
- **Manager review** before anything is payable; **audit history** of every change.
Inputs the software already records: rep on each referral (`field_rep_id`), supplier
hand-offs and their confirmed outcomes with source, Enroll date (`enrolled_at`). Nothing is
calculated or paid; no payroll or Stripe change.

## 6. Revenue readiness

| Funnel | Usable today (dev branch) | Missing for real production numbers |
|---|---|---|
| Residential: inquiry → qualified → hand-off → supplier-confirmed → Enroll | All recorded; enrollment only via Enroll | Supplier agreements (what Atlantis earns per enrollment), confirmation reports |
| Commercial: inquiry → discovery → quote → contract → supplier confirmed → won | Stages, locations, reasons | Agreement terms; expected volume is kWh only |
| Field: visit → referral → worked → enrolled | Per-rep summary | Compensation agreement |
Distinctions kept: potential (stage / kWh) vs submitted (hand-off) vs supplier-confirmed
(outcome source) vs enrolled (Enroll) — earned / collected / paid are **not** recorded
anywhere yet and must not be inferred.

## 7. Owner decision sheet
1. Default advisor for website requests (or keep admin fallback).
2. Domain: subdomain first or full cutover (see READINESS_EXTERNAL.md).
3. Atlantis sender address + reply mailbox (Google vs Microsoft — see READINESS_EXTERNAL.md).
4. ComparePower tracking URL.
5. Confirm supplier wording (Gexa quote-only; security partner not onboarded).
6. Field program: which outcomes Atlantis wants reps to record; who are managers.
7. Advancement: approve the states and the evidence list above.
8. Compensation: the actual agreement terms, payable trigger, reversal window.
9. Whether to build the training-milestone / commercial-candidate flags (needs a small schema
   change coordinated through lane rules).
10. Mark the production "Release Check" lead as a test record (one-field change), or leave it.
