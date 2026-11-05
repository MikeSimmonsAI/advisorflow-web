"""Harvey workforce - REAL model calls, bounded and accounted (#452 / #506).

The workforce's model reasoning goes through the existing EvoSys AI gateway
(app/services/ai_gateway.py: OpenAI Chat Completions, approved models only,
caps, provider breaker, request logging). Nothing here creates a new provider
account or subscription.

Every call is gated, in this order, and every attempt is recorded in
acquisition_ai_calls (provider, model, tokens, cost provenance, errors):

  1. the owner's global daily AI-call limit (0 = no call is ever made);
  2. the per-client daily limit;
  3. the dollar ceiling - enforced with configured unit prices; a ceiling
     with no prices BLOCKS (we never guess what was spent);
  4. our circuit breaker (opens after repeated provider failures);
  5. a provider key exists;
  6. the gateway's own checks (approved model, budget caps, its breaker),
     under a scoped grant for this one feature and organization.

A call is made at most once per job + purpose (dedupe key); a repeat returns
the stored result. Model output is DATA: deterministic rules (grounding
check, opt-out and suppression) always run after it and win.

Cost: OpenAI API usage is metered per token by the provider. A ChatGPT or
Claude consumer subscription does not pay for these server calls. Unit prices
are read from ACQUISITION_AI_PRICE_IN_PER_1M / ACQUISITION_AI_PRICE_OUT_PER_1M
(USD per million tokens, as on the owner's provider invoice); with no prices
set, spend shows as "Not tracked" - never as $0.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionAICall, AcquisitionAIPolicy
from app.services.acquisition import harvey_adapted as H

log = logging.getLogger("acquisition.ai_worker")
FEATURE = "acquisition_workforce"
PROVIDER = "openai"
BREAKER_MINUTES = 30
UNAVAILABLE_FALLBACK = "AI model unavailable — using deterministic template fallback."


class AIBlocked(Exception):
    """A gate refused the call before any provider request."""


# ── policy & counters ───────────────────────────────────────────────────────
def policy(db: Session) -> AcquisitionAIPolicy:
    p = db.query(AcquisitionAIPolicy).filter(AcquisitionAIPolicy.id == "global").first()
    if p is None:
        p = AcquisitionAIPolicy(id="global")
        db.add(p)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            p = db.query(AcquisitionAIPolicy).filter(AcquisitionAIPolicy.id == "global").one()
    return p


def set_policy(db: Session, actor, data: Dict[str, Any]) -> AcquisitionAIPolicy:
    from fastapi import HTTPException
    p = policy(db)
    for key, lo, hi in (("per_client_daily_calls", 0, 1000), ("max_tokens_per_call", 100, 2000),
                        ("max_calls_per_pass", 0, 50), ("retry_limit", 0, 3), ("breaker_threshold", 1, 20)):
        if data.get(key) is not None:
            try:
                v = int(data[key])
            except (TypeError, ValueError):
                raise HTTPException(422, "%s must be a whole number" % key)
            if not lo <= v <= hi:
                raise HTTPException(422, "%s must be between %d and %d" % (key, lo, hi))
            setattr(p, key, v)
    if "daily_cost_ceiling_usd" in data:
        v = data["daily_cost_ceiling_usd"]
        if v in (None, ""):
            p.daily_cost_ceiling_usd = None
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, "The cost ceiling must be a number")
            if v < 0 or v > 1000:
                raise HTTPException(422, "The cost ceiling must be between $0 and $1000 a day")
            p.daily_cost_ceiling_usd = v
    if data.get("reset_breaker"):
        p.consecutive_failures, p.breaker_open_until = 0, None
    p.updated_by_id, p.updated_at = getattr(actor, "id", None), datetime.utcnow()
    db.commit()
    return p


def unit_prices() -> Tuple[Optional[float], Optional[float]]:
    def f(name):
        raw = (os.getenv(name) or "").strip()
        try:
            v = float(raw)
            return v if v >= 0 else None
        except ValueError:
            return None
    return f("ACQUISITION_AI_PRICE_IN_PER_1M"), f("ACQUISITION_AI_PRICE_OUT_PER_1M")


def _since(now: datetime) -> datetime:
    return now - timedelta(hours=24)


def calls_today(db: Session, org_id: Optional[str] = None, now: Optional[datetime] = None) -> int:
    """Provider requests actually made (ok or provider error) in 24h."""
    now = now or datetime.utcnow()
    q = db.query(AcquisitionAICall).filter(AcquisitionAICall.created_at >= _since(now),
                                           AcquisitionAICall.status.in_(("ok", "error", "rejected", "pending")))
    if org_id is not None:
        q = q.filter(AcquisitionAICall.organization_id == org_id)
    return q.count()


def usage(db: Session, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    rows = db.query(AcquisitionAICall).filter(AcquisitionAICall.created_at >= _since(now)).all()
    made = [r for r in rows if r.status in ("ok", "error", "rejected", "pending")]
    priced = [r.cost_usd for r in made if r.cost_usd is not None]
    unpriced = [r for r in made if r.cost_usd is None and (r.tokens_in or r.tokens_out)]
    return {"calls_made": len(made), "calls_refused": sum(1 for r in rows if r.status == "refused"),
            "errors": sum(1 for r in rows if r.status == "error"),
            "tokens_in": sum(r.tokens_in or 0 for r in made), "tokens_out": sum(r.tokens_out or 0 for r in made),
            "cost_usd": round(sum(priced), 4) if made and not unpriced else (0.0 if not made else None),
            "cost_basis": ("estimate from configured unit prices" if priced and not unpriced
                           else "no calls made" if not made else "not tracked (unit prices not configured)")}


def _spend_today(db: Session, now: datetime) -> Optional[float]:
    rows = (db.query(AcquisitionAICall).filter(AcquisitionAICall.created_at >= _since(now),
                                               AcquisitionAICall.status.in_(("ok", "error", "rejected"))).all())
    if any(r.cost_usd is None and (r.tokens_in or r.tokens_out) for r in rows):
        return None
    return sum(r.cost_usd or 0 for r in rows)


# ── readiness (backed by real configuration checks) ─────────────────────────
def readiness(db: Session, org_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.services import ai_gateway as G
    from app.services.acquisition import workforce as WF
    now = now or datetime.utcnow()
    s, p = WF.state(db), policy(db)
    try:
        model = G.resolve_model("acquisition_draft")
    except Exception as exc:                                     # noqa: BLE001
        model = None
        model_err = str(exc)[:200]
    else:
        model_err = None
    base = {"provider": PROVIDER, "model": model, "calls_allowed_per_day": s.daily_ai_call_limit,
            "calls_today": calls_today(db, now=now), "per_client_daily_calls": p.per_client_daily_calls,
            "max_tokens_per_call": p.max_tokens_per_call, "max_calls_per_pass": p.max_calls_per_pass,
            "daily_cost_ceiling_usd": p.daily_cost_ceiling_usd, "unit_prices_configured": all(
                v is not None for v in unit_prices())}

    def out(state, reason):
        return dict(base, state=state, reason=reason)
    if not s.daily_ai_call_limit:
        return out("disabled", "Disabled: the AI-call budget is 0 per day, so no model is called. "
                               "Drafts use the deterministic service-category template.")
    if model is None:
        return out("unavailable", "Unavailable: the configured model is not approved (%s)" % model_err)
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        return out("unavailable", "Unavailable: no OpenAI API key is configured on the server")
    if p.breaker_open_until and p.breaker_open_until > now:
        return out("unavailable", "Paused after %d provider failures in a row until %s UTC"
                   % (p.consecutive_failures, p.breaker_open_until.strftime("%H:%M")))
    if calls_today(db, now=now) >= s.daily_ai_call_limit:
        return out("blocked", "Budget used up: %d of %d AI calls today" % (calls_today(db, now=now), s.daily_ai_call_limit))
    if p.daily_cost_ceiling_usd is not None:
        if not base["unit_prices_configured"]:
            return out("blocked", "A daily cost ceiling is set but provider unit prices are not configured, "
                                  "so spend can't be checked - calls are held")
        spent = _spend_today(db, now)
        if spent is not None and spent >= p.daily_cost_ceiling_usd:
            return out("blocked", "Daily cost ceiling reached ($%.2f of $%.2f)" % (spent, p.daily_cost_ceiling_usd))
    return out("connected", "Connected: up to %d calls/day through the EvoSys AI gateway (%s)" % (s.daily_ai_call_limit, model))


# ── the invocation ──────────────────────────────────────────────────────────
def _record(db, **kw) -> AcquisitionAICall:
    row = AcquisitionAICall(**kw)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.query(AcquisitionAICall).filter(AcquisitionAICall.dedupe_key == kw.get("dedupe_key")).first()
    return row


def _finish(db: Session, row: AcquisitionAICall, **fields) -> AcquisitionAICall:
    """Complete a reserved call row. If the full record can't be saved, the
    essentials (status, tokens, cost) are saved anyway - a call that was made
    is never left uncounted."""
    for k, v in fields.items():
        setattr(row, k, v)
    try:
        db.commit()
        return row
    except Exception:                                            # noqa: BLE001
        db.rollback()
        log.exception("AI call record %s could not be saved in full", row.id)
        row = db.query(AcquisitionAICall).filter(AcquisitionAICall.id == row.id).one()
        for k in ("status", "tokens_in", "tokens_out", "cost_usd", "attempts"):
            if k in fields:
                setattr(row, k, fields[k])
        row.error = "Full record could not be saved"
        db.commit()
        return row


def invoke(db: Session, *, purpose: str, capability: str, org_id: Optional[str], job_id: Optional[str],
           messages: List[Dict[str, str]], client=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Returns {"ok": True, "data": dict, "call": row} or {"ok": False, "reason": str, "call": row|None}."""
    from app.services import ai_gateway as G
    now = now or datetime.utcnow()
    key = ("%s:%s" % (purpose, job_id))[:300] if job_id else None
    if key:
        prior = db.query(AcquisitionAICall).filter(AcquisitionAICall.dedupe_key == key).first()
        if prior is not None and prior.status == "ok":
            try:
                return {"ok": True, "data": json.loads(prior.output or "{}"), "call": prior, "reused": True}
            except ValueError:
                pass
        if prior is not None:
            return {"ok": False, "reason": "Already attempted for this job (%s)" % prior.status, "call": prior}
    r = readiness(db, org_id, now)
    p = policy(db)
    model = r.get("model")
    if r["state"] != "connected":
        return {"ok": False, "reason": r["reason"], "call": None}
    if org_id and calls_today(db, org_id, now) >= p.per_client_daily_calls:
        row = _record(db, organization_id=org_id, job_id=job_id, purpose=purpose, dedupe_key=key, model=model,
                      capability=capability, status="refused", error="Per-client daily AI limit reached")
        return {"ok": False, "reason": "This company's daily AI limit (%d) is reached" % p.per_client_daily_calls,
                "call": row}
    # RESERVE the call before the provider is asked, so a call is counted
    # against every limit even if the process dies or the record fails later.
    row = AcquisitionAICall(organization_id=org_id, job_id=job_id, purpose=purpose, dedupe_key=key, model=model,
                            capability=capability, status="pending")
    db.add(row)
    try:
        db.commit()
    except IntegrityError:          # another process reserved this job's call first
        db.rollback()
        return {"ok": False, "reason": "Already attempted for this job", "call": None}
    attempts, last_err, t0 = 0, None, time.monotonic()
    pin, pout = unit_prices()
    while attempts <= p.retry_limit:
        attempts += 1
        try:
            with G.scoped_grant(FEATURE, org_id or "platform"):
                resp = G.chat_completion(feature=FEATURE, capability=capability, mode=G.BACKGROUND,
                                         messages=messages, org_id=org_id or "platform", client=client,
                                         max_tokens=p.max_tokens_per_call, temperature=0.2,
                                         response_format={"type": "json_object"})
        except G.AIRefused as exc:
            row = _finish(db, row, status="refused", attempts=attempts,
                          error=("%s: %s" % (type(exc).__name__, exc))[:500])
            return {"ok": False, "reason": "The AI gateway refused the call (%s)" % exc, "call": row}
        except Exception as exc:                                 # noqa: BLE001 - provider error
            last_err = "%s: %s" % (type(exc).__name__, str(exc)[:300])
            continue
        usage_ = getattr(resp, "usage", None)
        tin, tout = getattr(usage_, "prompt_tokens", None), getattr(usage_, "completion_tokens", None)
        cost = (round((tin or 0) / 1e6 * pin + (tout or 0) / 1e6 * pout, 6)
                if pin is not None and pout is not None and (tin is not None or tout is not None) else None)
        basis = "estimate" if cost is not None else "none"
        text = ""
        try:
            text = resp.choices[0].message.content or ""
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except (ValueError, AttributeError, IndexError, TypeError) as exc:
            row = _finish(db, row, status="error", tokens_in=tin, tokens_out=tout, cost_usd=cost, cost_basis=basis,
                          attempts=attempts, latency_ms=int((time.monotonic() - t0) * 1000),
                          error=("Unreadable model output: %s" % exc)[:500], output=text[:4000])
            return {"ok": False, "reason": "The model's answer couldn't be read", "call": row}
        p.consecutive_failures, p.breaker_open_until = 0, None
        row = _finish(db, row, status="ok", tokens_in=tin, tokens_out=tout, cost_usd=cost, cost_basis=basis,
                      attempts=attempts, latency_ms=int((time.monotonic() - t0) * 1000),
                      output=json.dumps(data)[:4000])
        return {"ok": True, "data": data, "call": row}
    p.consecutive_failures = (p.consecutive_failures or 0) + 1
    if p.consecutive_failures >= p.breaker_threshold:
        p.breaker_open_until = now + timedelta(minutes=BREAKER_MINUTES)
    row = _finish(db, row, status="error", attempts=attempts, latency_ms=int((time.monotonic() - t0) * 1000),
                  error=(last_err or "provider error")[:500])
    return {"ok": False, "reason": "The AI provider failed (%s)" % last_err, "call": row}


# ── capability 1-4: grounded drafting with qualification reasoning ─────────
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
_MONEY_RE = re.compile(r"\$\s?\d|\b\d+\s?%")
_CLAIM_RE = re.compile(r"\b(guarantee\w*|licensed|insured|bonded|certified|award[- ]winning|#1|number one|"
                       r"\d+\+? years|best in)\b", re.I)

DRAFT_SYSTEM = (
    "You draft ONE short first business email for a local service company to a prospective commercial customer. "
    "Use ONLY the facts in the JSON you are given. Never invent names, people, prices, discounts, credentials, "
    "insurance, years in business, results, availability, phone numbers, links or email addresses. If a fact is "
    "not given, leave it out. Plain, specific, under 120 words, no hype. Write for the client's own service "
    "category - never mention a different trade. " + H.UNTRUSTED_RULE + " "
    "Reply with a JSON object: {\"subject\": str, \"body\": str, \"fit\": \"good\"|\"unclear\"|\"poor\", "
    "\"fit_reasons\": [str], \"evidence_used\": [int], \"needs_human\": bool, \"human_reason\": str}. "
    "fit is your reading of whether this business plausibly needs the client's service, from the evidence only; "
    "evidence_used lists the indexes of the evidence items you relied on. Do not include a greeting signature "
    "block beyond the signer's name, and no unsubscribe footer (it is added separately).")


def draft_messages(client_facts: Dict[str, Any], prospect: Dict[str, Any],
                   evidence: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    ev = [{"i": i, "kind": e.get("kind"), "value": H.fence(str(e.get("value") or "")[:400], "website"),
           "source": e.get("source_url")} for i, e in enumerate(evidence[:12])]
    payload = {"client": client_facts, "prospect": prospect, "evidence": ev}
    return [{"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def grounding_problems(data: Dict[str, Any], client_facts: Dict[str, Any], prospect: Dict[str, Any]) -> List[str]:
    """Deterministic checks that ALWAYS win over the model."""
    probs: List[str] = []
    subj, body = str(data.get("subject") or "").strip(), str(data.get("body") or "").strip()
    if not subj or not body:
        return ["empty subject or body"]
    if len(subj) > 150 or len(body) > 2000:
        probs.append("too long")
    allowed = json.dumps([client_facts, prospect]).lower()
    text = subj + "\n" + body
    for rx, what in ((_URL_RE, "a link"), (_EMAIL_RE, "an email address"), (_PHONE_RE, "a phone number")):
        for m in rx.findall(text):
            if m.lower() not in allowed:
                probs.append("contains %s not in the facts (%s)" % (what, m[:60]))
    if _MONEY_RE.search(text) and "$" not in allowed:
        probs.append("mentions a price or percentage")
    for m in _CLAIM_RE.findall(text):
        if m.lower() not in allowed:
            probs.append("makes an unsupported claim (%s)" % m)
    # Another trade's SERVICE, not a shared word: a window washer does "window
    # cleaning" and a pressure washer "cleans" - only a pitch for janitorial
    # work, pest control, lawns etc. counts as the wrong trade.
    trades = {"cleaning": ("janitorial", "office cleaning", "commercial cleaning", "cleaning service", "maid"),
              "residential_cleaning": ("house cleaning", "home cleaning", "maid service"),
              "window_washing": ("window washing",), "pest_control": ("pest", "exterminat"),
              "landscaping": ("lawn", "landscap", "mowing"), "pressure_washing": ("pressure wash", "power wash"),
              "property_maintenance": ("make-ready", "make ready")}
    mine = client_facts.get("category")
    own_words = trades.get(mine, ())
    if mine in ("cleaning", "residential_cleaning"):        # one trade, two buyer types
        own_words = trades["cleaning"] + trades["residential_cleaning"]
    low = text.lower()
    other = sorted({w for key, words in trades.items() if key != mine for w in words
                    if w in low and w not in allowed and not any(w in o for o in own_words)})
    if other:
        probs.append("mentions another trade (%s)" % ", ".join(other))
    if H.opt_out_reason(body):
        probs.append("reads like an opt-out")
    return probs


# ── capability 5-8: reply assistance ────────────────────────────────────────
REPLY_LABELS = ("interested", "not_interested", "question", "objection", "request_for_information",
                "appointment_request", "reschedule_request", "out_of_office", "ambiguous")
REPLY_SYSTEM = (
    "You read ONE reply a business sent to a service company's outreach email and classify it. "
    + H.UNTRUSTED_RULE + " Reply with JSON: {\"label\": one of " + json.dumps(list(REPLY_LABELS)) + ", "
    "\"confidence\": number 0-1, \"appointment_intent\": bool, \"times_mentioned\": [str exactly as written], "
    "\"suggested_next_action\": str, \"needs_human\": bool, \"human_reason\": str}. Never claim a time is "
    "available - only quote times the sender wrote. If unsure, label ambiguous and needs_human true.")


def reply_messages(text: str) -> List[Dict[str, str]]:
    return [{"role": "system", "content": REPLY_SYSTEM},
            {"role": "user", "content": H.fence((text or "")[:3000], "inbound email")}]


def clean_reply_assist(data: Dict[str, Any]) -> Dict[str, Any]:
    label = data.get("label") if data.get("label") in REPLY_LABELS else "ambiguous"
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    return {"label": label, "confidence": conf, "appointment_intent": bool(data.get("appointment_intent")),
            "times_mentioned": [str(t)[:80] for t in (data.get("times_mentioned") or [])][:5],
            "suggested_next_action": str(data.get("suggested_next_action") or "")[:300],
            "needs_human": bool(data.get("needs_human")) or label == "ambiguous" or conf < 0.7,
            "human_reason": str(data.get("human_reason") or "")[:300]}
