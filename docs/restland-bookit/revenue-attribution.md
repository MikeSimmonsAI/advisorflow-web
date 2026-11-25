# Sales, revenue and attribution

* Amounts are integer cents, computed on the server (`services/bookit/sales.py::compute`):
  line gross = quantity × unit; line net = gross − line discount; contract discount taken once;
  adjustments stored separately; net can't go below $0.
* One sale per idempotency key — resubmitting never adds revenue twice.
* `closed` counts; `pending` never counts; `void` is excluded. "Sold? Yes" on the sheet without a
  BookIT sale is reported as **missing amount** and excluded from totals.
* Closed contract value is **not** cash collected or recognized revenue; BookIT records contracted
  amounts only.
* Corrections to a closed sale are management-only, need a reason and are audited (old/new,
  who, when, why).

## Attribution (decided from records, never from the request)
1. Contact of an Evo 6 Block / ADD network → `evo6_block` / `add_lead` (keeps the batch, contact
   and referring advisor even if they book through a follow-up or a public link).
2. Family in the Restland campaign → `evosys_follow_up` if booked through a follow-up, else
   `evosys_appointment`.
3. Family entered on the sheet's 6 Block tab → `evo6_block`.
4. Anything else in the workspace → `independent`; nothing found → `unknown`.
Booking method is kept separately (`follow_up` or `booking_link`; AI-offered vs self-booked is not
distinguishable in today's data — documented limitation).

## Metric definitions
* Show rate = Showed ÷ (Showed + No-Show). Cancellations/reschedules aren't no-shows.
* Closing rate = attended appointments with ≥1 closed sale ÷ attended appointments.
* Average contract value = net closed value ÷ closed contracts.
* Closed value per generated appointment = EvoSys-attributed net closed value ÷ appointments in
  the window.
* Cohorts: appointments by appointment date, sales by sale date, referrals by creation date.
  N/A when the denominator is 0. No prior-period comparisons are fabricated.
