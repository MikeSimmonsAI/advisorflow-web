"""Free commercial-cleaning walkthrough request page (owner request 2026-11-22).

    GET  /evosys-sales-public/walkthrough   the public form (no login)
    POST /evosys-sales-public/walkthrough   saves the request, alerts Mike

Linked from Mike's LinkedIn profile, Featured section and page buttons. The
person asking is a facility or property manager who wants a cleaning quote.

RULES
  * Saved as a WalkthroughRequest, never as an EvoSys BookIt opportunity, and
    never enrolled in any automatic outreach. Nothing is sent to the person.
  * Mike and everyone on the EvoSys BookIt alert list get a text and an email
    (the same path as every other EvoSys BookIt alert). A failed alert never
    loses the request: the row is committed first.
  * The page promises nothing the system can't back: no price, no date. It
    says Mike will reach out to set the time.
"""
from __future__ import annotations

import html as _html
import json as _json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.walkthrough_request_models import WalkthroughRequest

log = logging.getLogger(__name__)

PATH = "/evosys-sales-public/walkthrough"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
BUILDING_TYPES = ("Office", "Medical / healthcare", "Retail", "Industrial / warehouse", "Church",
                  "School / daycare", "Restaurant", "Gym / fitness", "Multi-tenant building", "Other")
TIMING = ("As soon as possible", "Within 30 days", "In 1-3 months", "Just getting a comparison quote")


def _clean(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def _digits(phone: Any) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    return d[-10:] if len(d) >= 10 else ""


def validate(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {
        "contact_name": _clean(raw.get("name"), 120), "job_title": _clean(raw.get("title"), 120),
        "company": _clean(raw.get("company"), 160), "email": _clean(raw.get("email"), 160).lower(),
        "phone": _clean(raw.get("phone"), 25), "property_address": _clean(raw.get("address"), 300),
        "building_type": _clean(raw.get("building_type"), 60), "square_feet": _clean(raw.get("square_feet"), 40),
        "current_service": _clean(raw.get("current_service"), 60), "timing": _clean(raw.get("timing"), 60),
        "preferred_times": _clean(raw.get("preferred_times"), 300),
        "notes": str(raw.get("notes") or "").strip()[:1500],
        "source": re.sub(r"[^a-z0-9_-]", "", str(raw.get("src") or "").lower())[:60] or None,
    }
    errors = {}
    if not out["contact_name"]:
        errors["name"] = "Please enter your name."
    if not out["company"]:
        errors["company"] = "Please enter your company or property name."
    if not _EMAIL.match(out["email"]):
        errors["email"] = "Please enter a valid email."
    if not _digits(out["phone"]):
        errors["phone"] = "Please enter a phone number with area code."
    if len(out["property_address"]) < 5:
        errors["address"] = "Please enter the property address (or at least the city)."
    out["errors"] = errors
    return out


def _ct(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Chicago")).strftime("%a %b %-d, %-I:%M %p CT")


def _alert(db: Session, r: WalkthroughRequest) -> None:
    """Text + email everyone on the EvoSys BookIt alert list. Never raises."""
    try:
        from app.services.evosys_sales import outreach as O
        from app.services.evosys_sales import sms_inbound as SI
        bso = O.brand_id(db)
        if not bso:
            log.warning("[walkthrough request] %s saved but EvoSys BookIt is not set up; no alert", r.id)
            return
        s = O.get_settings(db, bso)
        db.commit()
        where = r.property_address
        line = ("WALKTHROUGH REQUEST: %s (%s) at %s wants a free cleaning walkthrough - %s. Call %s"
                % (r.contact_name, r.company, where, r.building_type or "building", r.phone))
        rows = [("Name", r.contact_name), ("Title", r.job_title), ("Company / property", r.company),
                ("Email", r.email), ("Phone", r.phone), ("Property address", r.property_address),
                ("Building type", r.building_type), ("Size", r.square_feet),
                ("Cleaned today by", r.current_service), ("Wants service", r.timing),
                ("Good times for the walkthrough", r.preferred_times), ("Notes", r.notes),
                ("Came from", r.source), ("Received", _ct(r.created_at or datetime.utcnow()))]
        body = ("<p><b>New free walkthrough request</b> from the walkthrough page. Nothing was sent to them - "
                "please call or email to set the time.</p><table cellpadding='4'>"
                + "".join("<tr><td><b>%s</b></td><td>%s</td></tr>" % (_html.escape(k), _html.escape(v))
                          for k, v in rows if v)
                + "</table>")
        SI.notify_owner(s, line[:320], "Walkthrough request - %s (%s)" % (r.contact_name, r.company), body)
    except Exception:                                       # noqa: BLE001 - the request is already saved
        log.exception("[walkthrough request] alert failed for %s", r.id)


def save(db: Session, data: Dict[str, Any]) -> Dict[str, Any]:
    fields = {k: (v or None) for k, v in data.items() if k != "errors"}
    r = WalkthroughRequest(created_at=datetime.utcnow(), status="new", **fields)
    db.add(r)
    db.commit()
    db.refresh(r)
    _alert(db, r)
    return {"status": "ok"}


def listing(db: Session, limit: int = 200) -> list:
    rows = db.query(WalkthroughRequest).order_by(WalkthroughRequest.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "received": _ct(r.created_at) if r.created_at else None, "name": r.contact_name,
             "title": r.job_title, "company": r.company, "email": r.email, "phone": r.phone,
             "address": r.property_address, "building_type": r.building_type, "square_feet": r.square_feet,
             "current_service": r.current_service, "timing": r.timing, "preferred_times": r.preferred_times,
             "notes": r.notes, "source": r.source, "status": r.status} for r in rows]


def _options(values) -> str:
    return '<option value="">Choose one</option>' + "".join(
        '<option>%s</option>' % _html.escape(v) for v in values)


PAGE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Free Commercial Cleaning Walkthrough &amp; Quote (DFW) - EvoSys Pro</title>
<meta name="description" content="Facility and property managers in Dallas-Fort Worth: request a free, no-obligation walkthrough and written quote from a vetted, insured local commercial cleaning company.">
<meta property="og:title" content="Free Commercial Cleaning Walkthrough & Quote (DFW)">
<meta property="og:description" content="A free, no-obligation walkthrough and written quote from a vetted, insured local commercial cleaning company. About 30 minutes on site.">
<meta name="referrer" content="no-referrer">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--navy:#0b1b38;--navy2:#13284d;--gold:#c9a24a;--gold2:#a8812c;--ivory:#f7f2e8;--card:#fffdf8;--ink:#13213b;--muted:#5d6a80;--line:#e6dcc7;--bad:#b42318}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:var(--ivory);color:var(--ink);font-size:15px;line-height:1.45}
header{background:var(--navy);border-bottom:3px solid var(--gold);padding:14px 22px;display:flex;align-items:center;gap:12px}
header b{font-family:'Cormorant Garamond',Georgia,serif;color:#fff;font-size:24px;font-weight:700}
header span{color:#e4c77d;font-size:12px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;margin-left:auto}
main{max-width:1000px;margin:0 auto;padding:26px 18px 48px;display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:20px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px;box-shadow:0 6px 20px rgba(11,27,56,.06)}
.eyebrow{color:var(--gold2);font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px}
h1{font-family:'Cormorant Garamond',Georgia,serif;font-size:38px;line-height:1.08;color:var(--navy);margin:6px 0 8px}
p.lead{color:var(--muted);margin-bottom:18px}
form{display:grid;grid-template-columns:1fr 1fr;gap:12px 14px}
label{display:flex;flex-direction:column;gap:5px;font-weight:600;font-size:13.5px}
label.wide{grid-column:1/-1}
.opt{font-weight:400;color:var(--muted)}
input,textarea,select{font:inherit;font-size:15px;padding:11px 12px;border:1.5px solid #cdd3dd;border-radius:10px;background:#fff;color:var(--ink);width:100%}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--navy2);box-shadow:0 0 0 3px rgba(201,162,74,.3)}
textarea{min-height:70px;resize:vertical}
.err{color:var(--bad);font-size:12.5px;font-weight:600}
.hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
button{grid-column:1/-1;margin-top:6px;border:0;border-radius:11px;padding:14px;font:inherit;font-weight:700;font-size:17px;color:var(--navy);cursor:pointer;background:linear-gradient(180deg,#ecc762,#d4a53a);box-shadow:0 3px 0 #b88a24}
button:disabled{opacity:.7;cursor:progress}
button:focus-visible{outline:3px solid var(--navy);outline-offset:3px}
.fine{grid-column:1/-1;color:var(--muted);font-size:12.5px}
.msg{grid-column:1/-1;padding:11px 13px;border-radius:10px;background:#fdecea;color:#8a1c12;font-weight:600;display:none}
.done{display:none;text-align:center;padding:30px 10px}
.done h2{font-family:'Cormorant Garamond',Georgia,serif;font-size:32px;color:var(--navy);margin-bottom:8px}
.done p{color:var(--muted)}
aside{background:linear-gradient(180deg,var(--navy),var(--navy2));color:#fff;border-radius:16px;padding:24px}
aside h2{font-family:'Cormorant Garamond',Georgia,serif;font-size:28px;line-height:1.1;margin-bottom:14px}
aside li{list-style:none;display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-top:1px solid rgba(255,255,255,.12)}
aside li:first-child{border-top:0}
aside li i{font-style:normal;width:24px;height:24px;border-radius:50%;background:var(--gold);color:var(--navy);display:inline-flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;flex:none}
aside .who{margin-top:16px;padding-top:14px;border-top:1.5px solid var(--gold);color:#e4c77d;font-weight:600}
aside .who a{color:#e4c77d}
@media(max-width:820px){main{grid-template-columns:1fr}form{grid-template-columns:1fr}h1{font-size:32px}header span{display:none}}
</style></head><body>
<header><b>EvoSys Pro</b><span>Commercial Services</span></header>
<main>
<section class="card">
<div id="form-wrap">
<div class="eyebrow">Dallas&ndash;Fort Worth &middot; Free &middot; No obligation</div>
<h1>Free commercial cleaning walkthrough &amp; quote</h1>
<p class="lead">Tell us about your building. We'll set up a free, no-obligation walkthrough with a vetted, insured local commercial cleaning company, and you'll get a written quote. It takes about 30 minutes on site.</p>
<form id="f" novalidate>
<label>Your name<input name="name" autocomplete="name" maxlength="120" required><span class="err" id="e-name"></span></label>
<label>Your title <span class="opt">(optional)</span><input name="title" autocomplete="organization-title" maxlength="120" placeholder="e.g. Property Manager"></label>
<label>Company or property name<input name="company" autocomplete="organization" maxlength="160" required><span class="err" id="e-company"></span></label>
<label>Email<input name="email" type="email" autocomplete="email" maxlength="160" required><span class="err" id="e-email"></span></label>
<label>Phone<input name="phone" type="tel" autocomplete="tel" maxlength="25" required><span class="err" id="e-phone"></span></label>
<label>Type of building<select name="building_type">__BUILDING__</select></label>
<label class="wide">Property address<input name="address" autocomplete="street-address" maxlength="300" placeholder="Street, city" required><span class="err" id="e-address"></span></label>
<label>About how big? <span class="opt">(optional)</span><input name="square_feet" maxlength="40" placeholder="e.g. 12,000 sq ft or 3 floors"></label>
<label>Who cleans it today? <span class="opt">(optional)</span><select name="current_service"><option value="">Choose one</option><option>A cleaning company</option><option>Our own staff</option><option>Nobody yet</option></select></label>
<label>When would you want service? <span class="opt">(optional)</span><select name="timing">__TIMING__</select></label>
<label>Good days/times for the walkthrough <span class="opt">(optional)</span><input name="preferred_times" maxlength="300" placeholder="e.g. Tue or Thu mornings"></label>
<label class="wide">Anything we should know? <span class="opt">(optional)</span><textarea name="notes" maxlength="1500" placeholder="How often you need cleaning, special areas, after-hours access..."></textarea></label>
<div class="hp" aria-hidden="true"><label>Leave empty<input name="website" tabindex="-1" autocomplete="off"></label></div>
<div class="msg" id="m" role="alert"></div>
<button id="b" type="submit">Request my free walkthrough &rarr;</button>
<p class="fine">Mike Simmons will call or email you to set the time. We won't add you to any mailing list, and there's no cost or obligation.</p>
</form>
</div>
<div class="done" id="done" role="status"><h2>Thank you - we've got it.</h2><p>Mike Simmons will reach out shortly to set up your free walkthrough. Questions in the meantime? Email <a href="mailto:support@evosyspro.live">support@evosyspro.live</a>.</p></div>
</section>
<aside><h2>How it works</h2><ul>
<li><i>1</i><span>Tell us about your building (2 minutes)</span></li>
<li><i>2</i><span>We set up a walkthrough with a vetted, insured local cleaning company</span></li>
<li><i>3</i><span>About 30 minutes on site, at a time that works for you</span></li>
<li><i>4</i><span>You get a written quote. Compare it with what you pay now - no pressure</span></li></ul>
<div class="who">Mike Simmons &middot; EvoSys Pro<br><a href="mailto:support@evosyspro.live">support@evosyspro.live</a></div></aside>
</main>
<script>
const f=document.getElementById('f'),b=document.getElementById('b'),m=document.getElementById('m');
const src=new URLSearchParams(location.search).get('src')||'';
f.onsubmit=async(e)=>{e.preventDefault();if(b.disabled)return;
 ['name','company','email','phone','address'].forEach(k=>document.getElementById('e-'+k).textContent='');m.style.display='none';
 const d=Object.fromEntries(new FormData(f).entries());d.src=src;b.disabled=true;b.textContent='Sending…';
 try{const r=await fetch(__PATH__,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
  const j=await r.json().catch(()=>({}));
  if(r.ok&&j.status==='ok'){document.getElementById('form-wrap').style.display='none';document.getElementById('done').style.display='block';window.scrollTo(0,0);return}
  const det=j.detail||j;
  if(det&&det.errors){Object.entries(det.errors).forEach(([k,v])=>{const el=document.getElementById('e-'+k);if(el)el.textContent=v});
   const first=Object.keys(det.errors)[0];const inp=f.querySelector('[name='+first+']');if(inp)inp.focus()}
  else{m.textContent=(det&&det.message)||(r.status===429?'Too many tries - please wait a minute.':'Something went wrong. Please try again, or email support@evosyspro.live.');m.style.display='block'}
 }catch(err){m.textContent='We could not reach our server. Please try again, or email support@evosyspro.live.';m.style.display='block'}
 b.disabled=false;b.textContent='Request my free walkthrough →'};
</script></body></html>"""


def render() -> str:
    return (PAGE.replace("__PATH__", _json.dumps(PATH))
            .replace("__BUILDING__", _options(BUILDING_TYPES)).replace("__TIMING__", _options(TIMING)))
