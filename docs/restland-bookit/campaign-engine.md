# Evo 6 Block and ADD Lead campaigns

* Each contact has its own `enrollment_state`: `not_enrolled → pending_approval → enrolled →
  paused/stopped/completed`, plus `enrollment_step`.
* **Request** (advisor): only when the contact is valid, not suppressed, not a duplicate and the
  advisor documented that the person agreed to be contacted. The Permission to Access form alone
  is not permission to text or call.
* **Approve** (management): also requires the kind's template to be approved
  (`bookit.campaigns.<evo6|add>.approved`) and no other active campaign for that person.
* **Nothing sends today.** No template is approved; sending will reuse the existing EvoSys
  campaign engine (STOP/DNC, quiet hours, provider rules) once the owner approves the copy and
  cadence. Proposed default (pending approval): Day 1 introduction, Day 3 assistance, Day 7
  appointment invitation, Day 14 final, then complete.
* ADD Lead contacts never enter ordinary pre-planning campaigns; ADD outreach is human-led.
* The existing Restland file-review campaign is untouched.
