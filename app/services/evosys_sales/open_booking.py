"""EvoSys Sales: ONE booking link Mike can send to anybody (owner request 2026-11-18).

The lead booking page (booking_page.py) needs a signed link that names one
lead. This is the front door for people who are not a lead yet:

    GET  /evosys-sales-public/meet   a short "who are you" step, same design
    POST /evosys-sales-public/meet   finds them (same email or phone on the
                                     EvoSys Pro team) or adds them as a lead,
                                     then returns THEIR signed booking page

From there it is the existing booking page, unchanged: Mike's open times, the
Zoom call, the confirmation text, reminders, reschedule and the owner alert.

RULES
  * Never a duplicate: an existing lead with the same email or phone is used,
    and nothing on their record is overwritten (a history entry says they came
    through this link).
  * A new person starts in Discovery with NO follow-up row, so the automatic
    outreach never emails or texts them. Booking stops nothing that exists.
  * Do Not Contact is respected: a suppressed email/phone gets no booking page.
  * Someone typing a stranger's email must not see that person's call: when
    the matched lead already has a call booked, the page says so and shows no
    time, link or Zoom.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.sales_models import STAGE_DISCOVERY, Opportunity, OpportunityEvent

log = logging.getLogger(__name__)

PATH = "/evosys-sales-public/meet"
SOURCE_SYSTEM = "evosys_open_booking"
# Whose calendar the call goes on: the login that owns Mike's EvoSys Pro leads.
DEFAULT_OWNER_EMAIL = "platform@evosyspro.live"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def link() -> str:
    from app.services.evosys_sales import booking_page as BP
    return BP.base_url() + PATH


def owner(db: Session) -> Optional[User]:
    email = (os.environ.get("EVOSYS_OPEN_BOOKING_OWNER_EMAIL") or DEFAULT_OWNER_EMAIL).strip().lower()
    return db.query(User).filter(func.lower(User.email) == email, User.is_active.is_(True)).first()


def _digits(phone: Any) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-10:] if len(d) >= 10 else ""


def _clean(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def validate(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {"name": _clean(raw.get("name"), 120), "company": _clean(raw.get("company"), 160),
           "email": _clean(raw.get("email"), 160).lower(), "phone": _clean(raw.get("phone"), 25),
           "topic": _clean(raw.get("topic"), 500)}
    errors = {}
    if not out["name"]:
        errors["name"] = "Please enter your name."
    if not out["company"]:
        errors["company"] = "Please enter your business name."
    if not _EMAIL.match(out["email"]):
        errors["email"] = "Please enter a valid email."
    if not _digits(out["phone"]):
        errors["phone"] = "Please enter a mobile number with area code."
    out["errors"] = errors
    return out


def find(db: Session, bso_id: str, email: str, phone: str) -> Optional[Opportunity]:
    q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bso_id)
    if email:
        hit = q.filter(func.lower(Opportunity.email) == email.lower()).order_by(Opportunity.created_at).first()
        if hit:
            return hit
    d = _digits(phone)
    if d:
        for o in q.filter(Opportunity.phone.isnot(None)).all():
            if _digits(o.phone) == d:
                return o
    return None


def start(db: Session, data: Dict[str, Any]) -> Dict[str, Any]:
    """Find or add the person, then hand back their own booking page."""
    from app.services import sales_suppression as SDNC
    from app.services.evosys_sales import booking_page as BP
    from app.services.evosys_sales import outreach as O
    bso = O.brand_id(db)
    host = owner(db)
    if not bso or host is None:
        log.warning("[open booking] not set up: brand=%s owner=%s", bso, bool(host))
        return {"status": "error", "message": "Online booking isn't available right now. Please call or text us."}
    if SDNC.find(db, bso, data["phone"], data["email"]):
        return {"status": "error", "message": "Please contact us directly to set up a time."}
    o = find(db, bso, data["email"], data["phone"])
    created = False
    if o is not None:
        if SDNC.for_opportunity(db, o):
            return {"status": "error", "message": "Please contact us directly to set up a time."}
        if BP.current(db, o) is not None:
            # Never show someone else's call to whoever typed their email.
            return {"status": "already", "message": "You already have a call booked with us. Check the text or "
                                                     "email confirmation you received, or text us to change it."}
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="open_booking_used",
                                summary="Came back through Mike's booking link",
                                detail="Entered: %s, %s, %s, %s" % (data["name"], data["company"], data["email"],
                                                                     data["phone"]),
                                occurred_at=datetime.utcnow()))
    else:
        o = Opportunity(brand_sales_org_id=bso, owner_user_id=host.id, company_name=data["company"],
                        contact_name=data["name"], email=data["email"], phone=data["phone"],
                        stage=STAGE_DISCOVERY, status="open", source="Mike's booking link",
                        source_system=SOURCE_SYSTEM, intro_note=data["topic"] or None,
                        stage_changed_at=datetime.utcnow())
        db.add(o)
        db.flush()
        db.add(OpportunityEvent(opportunity_id=o.id, event_type="created",
                                summary="Added from Mike's booking link",
                                detail=("They said: " + data["topic"]) if data["topic"] else None,
                                occurred_at=datetime.utcnow()))
        created = True
    db.commit()
    url = BP.url_for(o.id)
    return {"status": "ok", "created": created, "redirect": url.replace(BP.base_url(), "")}


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Book a Business Growth Call - EvoSys Pro</title><meta name="robots" content="noindex"><meta name="referrer" content="no-referrer">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--navy:#0b1b38;--navy2:#13284d;--gold:#c9a24a;--gold2:#a8812c;--ivory:#f7f2e8;--card:#fffdf8;--ink:#13213b;--muted:#5d6a80;--line:#e6dcc7;--bad:#b42318}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--ivory);color:var(--ink);font-size:15px;line-height:1.45}
header{background:var(--navy);border-bottom:3px solid var(--gold);padding:14px 22px;display:flex;align-items:center;gap:12px}
header b{font-family:'Cormorant Garamond',Georgia,serif;color:#fff;font-size:24px;font-weight:700}
header span{color:#e4c77d;font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;margin-left:auto}
main{max-width:980px;margin:0 auto;padding:26px 18px 48px;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;box-shadow:0 6px 20px rgba(11,27,56,.06)}
.eyebrow{color:var(--gold2);font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px}
h1{font-family:'Cormorant Garamond',Georgia,serif;font-size:38px;line-height:1.08;color:var(--navy);margin:6px 0 8px}
p.lead{color:var(--muted);margin-bottom:18px}
form{display:grid;grid-template-columns:1fr 1fr;gap:12px 14px}
label{display:flex;flex-direction:column;gap:5px;font-weight:600;font-size:13.5px}
label.wide{grid-column:1/-1}
input,textarea{font:inherit;font-size:15px;padding:11px 12px;border:1.5px solid #cdd3dd;border-radius:10px;background:#fff;color:var(--ink);width:100%}
input:focus,textarea:focus{outline:none;border-color:var(--navy2);box-shadow:0 0 0 3px rgba(201,162,74,.3)}
textarea{min-height:70px;resize:vertical}
.err{color:var(--bad);font-size:12.5px;font-weight:600;min-height:0}
.hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
button{grid-column:1/-1;margin-top:6px;border:0;border-radius:11px;padding:14px;font:inherit;font-weight:700;font-size:17px;color:var(--navy);cursor:pointer;background:linear-gradient(180deg,#ecc762,#d4a53a);box-shadow:0 3px 0 #b88a24}
button:disabled{opacity:.7;cursor:progress}
button:focus-visible{outline:3px solid var(--navy);outline-offset:3px}
.fine{grid-column:1/-1;color:var(--muted);font-size:12.5px}
.msg{grid-column:1/-1;padding:11px 13px;border-radius:10px;background:#fdecea;color:#8a1c12;font-weight:600;display:none}
.msg.ok{background:#edf6ef;color:#1e4f2c}
aside{background:linear-gradient(180deg,var(--navy),var(--navy2));color:#fff;border-radius:16px;padding:24px}
aside h2{font-family:'Cormorant Garamond',Georgia,serif;font-size:28px;line-height:1.1;margin-bottom:14px}
aside li{list-style:none;display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-top:1px solid rgba(255,255,255,.12)}
aside li:first-child{border-top:0}
aside li i{font-style:normal;width:24px;height:24px;border-radius:50%;background:var(--gold);color:var(--navy);display:inline-flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;flex:none}
aside .who{margin-top:16px;padding-top:14px;border-top:1.5px solid var(--gold);color:#e4c77d;font-weight:600}
@media(max-width:820px){main{grid-template-columns:1fr}form{grid-template-columns:1fr}h1{font-size:32px}header span{display:none}}
</style></head><body>
<header><b>EvoSys Pro</b><span>Business Growth Call</span></header>
<main>
<section class="card">
<div class="eyebrow">Book a call with Mike Simmons</div>
<h1>Let's talk about growing your business</h1>
<p class="lead">A 30-minute Zoom call. Tell us who you are, then pick a time that works for you.</p>
<form id="f" novalidate>
<label>Your name<input name="name" autocomplete="name" maxlength="120" required><span class="err" id="e-name"></span></label>
<label>Business name<input name="company" autocomplete="organization" maxlength="160" required><span class="err" id="e-company"></span></label>
<label>Email<input name="email" type="email" autocomplete="email" maxlength="160" required><span class="err" id="e-email"></span></label>
<label>Mobile phone<input name="phone" type="tel" autocomplete="tel" maxlength="25" required><span class="err" id="e-phone"></span></label>
<label class="wide">What would you like to talk about? <span style="font-weight:400;color:var(--muted)">(optional)</span><textarea name="topic" maxlength="500"></textarea></label>
<div class="hp" aria-hidden="true"><label>Leave empty<input name="website" tabindex="-1" autocomplete="off"></label></div>
<div class="msg" id="m" role="alert"></div>
<button id="b" type="submit">Choose a time &rarr;</button>
<p class="fine">We'll text and email your confirmation, the Zoom link and reminders for this call. Reply STOP to texts anytime.</p>
</form>
</section>
<aside><h2>What we'll cover</h2><ul>
<li><i>1</i><span>Where you want your business to grow</span></li>
<li><i>2</i><span>How EvoSys Pro brings in and follows up with new customers</span></li>
<li><i>3</i><span>Whether it's a good fit - no pressure</span></li></ul>
<div class="who">Mike Simmons &middot; EvoSys Pro</div></aside>
</main>
<script>
const f=document.getElementById('f'),b=document.getElementById('b'),m=document.getElementById('m');
f.onsubmit=async(e)=>{e.preventDefault();if(b.disabled)return;
 ['name','company','email','phone'].forEach(k=>document.getElementById('e-'+k).textContent='');m.style.display='none';
 const d=Object.fromEntries(new FormData(f).entries());b.disabled=true;b.textContent='One moment…';
 try{const r=await fetch(__PATH__,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
  const j=await r.json().catch(()=>({}));
  if(r.ok&&j.status==='ok'&&j.redirect){location.href=j.redirect;return}
  const det=j.detail||j;
  if(det&&det.errors){Object.entries(det.errors).forEach(([k,v])=>{const el=document.getElementById('e-'+k);if(el)el.textContent=v});
   const first=Object.keys(det.errors)[0];const inp=f.querySelector('[name='+first+']');if(inp)inp.focus()}
  else{m.textContent=(det&&det.message)||(r.status===429?'Too many tries - please wait a minute.':'Something went wrong. Please try again.');
   m.className='msg'+(j.status==='already'?' ok':'');m.style.display='block'}
 }catch(err){m.textContent='We could not reach our server. Please try again.';m.className='msg';m.style.display='block'}
 b.disabled=false;b.textContent='Choose a time →'};
</script></body></html>"""


def render() -> str:
    import json as _json
    return PAGE.replace("__PATH__", _json.dumps(PATH))
