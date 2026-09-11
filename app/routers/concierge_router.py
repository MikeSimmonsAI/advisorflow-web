"""
Public concierge endpoint — powers the "Ask BookaBoost" AI chat
on bookaboost.live. No auth required. CORS open to bookaboost.live.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List, Literal
import openai
import os
import time
import logging

from app.deps import get_db
from app.models.billing_models import BrandBillingPlan
from app.models.models import Platform

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/concierge", tags=["concierge"])

_SYSTEM_PROMPT_TEMPLATE = """You are the BookaBoost AI concierge on the BookaBoost website. BookaBoost is a live, operational AI revenue platform for established service businesses. Your job is to answer questions honestly, help visitors understand the product, recommend the right plan, and guide them toward requesting a demo.

ABOUT BOOKABOOST:
- Revives dormant leads and books appointments automatically through AI email, SMS, and voice calls
- Built for established service businesses with 2,500+ leads: roofing, funeral services, insurance, medical, real estate, automotive, legal, home services
- Currently in live pilot with an established funeral services client in the Dallas TX market
- NOT a CRM, NOT a marketing tool — a complete AI revenue system connecting lead revival to booked appointment

WHAT IS LIVE AND WORKING RIGHT NOW:
- AI email cadence: 8 unique GPT-4o emails over 14 days, each with a different angle, referencing prior attempts naturally
- AI SMS follow-up with same cadence logic (carrier approval in progress)
- AI voice calls: outbound calling, inbound 24/7 answering, bulk campaigns to hundreds simultaneously
- Voicemail detection and automatic voicemail leaving
- Booking detection on calls — confirmation email fires automatically
- Escalation intelligence: detects anger, grief, legal language — AI pauses, team is alerted immediately
- Appointment booking with real Outlook calendar availability, double-booking prevention
- Multi-location dashboard, role-based access for reps, managers, executives
- Microsoft 365 / Outlook integration for email and calendar

COMING SOON:
- Social media lead capture (Facebook, TikTok, Instagram lead forms feeding directly into platform)
- Sub-60-second inbound lead response
- White-label agency program

{pricing}

HOW TO RECOMMEND A PLAN:
- Use ONLY the tiers, prices and capacity in the PRICING section above. It is
  read from the live catalogue at request time.
- Recommend the smallest tier whose stated capacity covers what the visitor
  describes. If the PRICING section does not state a figure for something they
  ask about, say it is quoted rather than guessing a number.
- Anything above the largest listed tier → the Custom tier: book a call.
- Anyone unsure → ask about their lead count, industry, and team size before
  recommending.

VS COMPETITORS:
- GoHighLevel: broader marketing suite but built for agencies reselling to clients, not for the service business itself. Bloated, steep learning curve, hidden usage costs. BookaBoost is simpler and purpose-built.
- Retell AI / Bland AI / Vapi: voice-only infrastructure tools for developers. No lead management, no email, no appointment booking built in. You have to stitch everything together yourself.
- 11x.ai: enterprise B2B SDR platform, $70M+ VC-backed. Targets tech companies and sales teams, not service businesses. Very different buyer.
- Structurely / Verse.ai: SMS-focused, heavy on real estate. Less voice, less multi-industry.
- BookaBoost advantage: only platform combining dormant lead revival + AI email + SMS + AI voice + appointment booking + Outlook calendar + multi-location — all in one system, purpose-built for service businesses.

TONE AND STYLE:
- Confident, direct, honest. Do not oversell or exaggerate.
- Keep answers to 2-4 sentences unless the visitor asks for more detail.
- If something is not built yet, say so plainly.
- If someone asks something outside your knowledge, say so and suggest they request a demo.
- End responses with a natural follow-up question when it helps move the conversation forward.
- Never make up features or capabilities.
- If someone is ready to move forward, tell them to click "Request a Demo" on the page."""


# ── Pricing block — sourced from DB at request time ──────────────────────────
# BILL-08: prices must never be hardcoded here. The single source of truth is
# BrandBillingPlan. We cache the blurb for 60 s to avoid a DB hit per message.

# WHAT A FALLBACK IS ALLOWED TO SAY WHEN IT DOES NOT KNOW THE PRICES.
#
# This used to be a full rate card typed in by hand: per-tier prices, lead
# ceilings, "AI voice 300 min/mo", "voice 750 min/mo", "Priority support +
# 24-month price lock", "month 13 free", overage rates. Several of those were
# already wrong — AI Voice is a separate add-on now, Professional's ceiling is
# not 7,500, and nobody ever configured a 24-month term — and the concierge
# would recite them, confidently, to a prospect, whenever the catalogue lookup
# failed for any reason.
#
# A FALLBACK THAT INVENTS COMMERCIAL TERMS IS WORSE THAN A FALLBACK THAT
# ADMITS IT DOES NOT HAVE THEM. This one admits it. The live catalogue is the
# only thing allowed to quote a price.
_STATIC_PRICING_FALLBACK = """PRICING:
- The live pricing catalogue could not be read for this request, so you do not
  currently have prices, tier capacity, allowances or contract terms.
- Do NOT state, estimate, or imply any price, lead limit, user limit, message
  allowance, discount, overage rate or contract length. Say plainly that you
  cannot pull current pricing right now and offer to have someone send it over
  or book a short call.
- Everything else you know about the product is still fine to discuss."""

_pricing_cache: dict = {}  # {platform_id: (fetched_at, blurb)}
_PRICING_TTL = 60          # seconds


def _pricing_blurb(db: Session) -> str:
    """Return a pricing section string sourced from BrandBillingPlan.

    Uses CONCIERGE_PLATFORM_ID env var to scope the query; if unset, uses the
    first active platform. Falls back to _STATIC_PRICING_FALLBACK so the
    concierge never silently returns no pricing at all.
    """
    import time as _time

    platform_id = os.getenv("CONCIERGE_PLATFORM_ID")
    if not platform_id:
        try:
            p = db.query(Platform).filter(Platform.is_active.is_(True)).first()
            platform_id = p.id if p else None
        except Exception:
            platform_id = None

    if not platform_id:
        return _STATIC_PRICING_FALLBACK

    now = _time.monotonic()
    cached_at, cached_blurb = _pricing_cache.get(platform_id, (0, None))
    if cached_blurb and (now - cached_at) < _PRICING_TTL:
        return cached_blurb

    try:
        plans = (db.query(BrandBillingPlan)
                 .filter(BrandBillingPlan.platform_id == platform_id,
                         BrandBillingPlan.is_active.is_(True))
                 .order_by(BrandBillingPlan.sort_order.asc()).all())
    except Exception:
        plans = []

    if not plans:
        return _STATIC_PRICING_FALLBACK

    # EVERY LINE BELOW COMES OUT OF THE CATALOGUE ROW. The three summary
    # lines that used to be appended here — "month-to-month adds 25%",
    # "month 13 free + 24-month price lock guaranteed", "Enterprise: Custom
    # pricing" — were hand-written commercial terms sitting underneath
    # database-sourced prices, which is the worst of both: they READ as
    # authoritative and nothing could correct them but a deploy. The
    # month-to-month rate is a real column, so it is quoted from the column;
    # the annual bonus and the price lock were never configured anywhere and
    # are therefore not claimed.
    from app.services import billing_catalog

    lines = ["PRICING (from the live catalogue):"]
    for plan in plans:
        bits = []
        term = plan.monthly_cents
        mtm = getattr(plan, "month_to_month_cents", None)
        if term:
            bits.append("$%d/mo on a term agreement" % (term // 100))
        if mtm:
            bits.append("$%d/mo month-to-month" % (mtm // 100))
        if plan.annual_cents:
            bits.append("$%d/yr annual" % (plan.annual_cents // 100))
        if not bits:
            bits.append("quoted — book a call")

        capacity = []
        for dim in billing_catalog.capacity_for(plan):
            if dim.get("unlimited"):
                capacity.append("unlimited %s" % dim["label"].lower())
            else:
                capacity.append("%s %s%s" % (
                    format(dim["value"], ","), dim["label"].lower(),
                    " " + dim["unit"] if dim.get("unit") else ""))

        line = "- %s: %s" % (plan.name, "; ".join(bits))
        if capacity:
            line += ". Includes %s" % ", ".join(capacity)
        if plan.description:
            line += ". %s" % plan.description.rstrip(".")
        lines.append(line + ".")
    lines.append("- Setup/implementation fees and add-ons (including AI Voice "
                 "and Lead Scraper) are quoted separately; do not state an "
                 "amount for them.")

    blurb = "\n".join(lines)
    _pricing_cache[platform_id] = (now, blurb)
    return blurb


# Simple per-IP rate limiter: max 30 requests per 60-second window
_rate_store: dict = {}
_RATE_LIMIT = 30
_RATE_WINDOW = 60


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window_start, count = _rate_store.get(ip, (now, 0))
    if now - window_start > _RATE_WINDOW:
        _rate_store[ip] = (now, 1)
    else:
        count += 1
        _rate_store[ip] = (window_start, count)
        if count > _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")


class Message(BaseModel):
    # Only allow user/assistant roles — never let callers inject a system turn
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=2000)


class ConciergeRequest(BaseModel):
    messages: List[Message] = Field(..., max_length=20)


_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


@router.options("/chat")
async def concierge_chat_preflight():
    """Handle CORS preflight for the public concierge chat endpoint."""
    return JSONResponse(content={}, headers=_CORS_HEADERS)


@router.post("/chat")
async def concierge_chat(req: ConciergeRequest, request: Request,
                         db: Session = Depends(get_db)):
    """
    Public endpoint — no auth. Called from bookaboost.live static site.
    Routes visitor messages through OpenAI with BookaBoost system prompt.
    CORS headers are set explicitly here (Access-Control-Allow-Origin: *)
    so this endpoint works from any origin without credentials.

    Pricing is sourced from BrandBillingPlan at request time (60s cache) so
    the concierge never quotes stale prices. See BILL-08.
    """
    _check_rate_limit(request)
    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # Build the live system prompt — pricing comes from DB, never hardcoded.
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(pricing=_pricing_blurb(db))

        # Only forward user/assistant turns — system role is injected exclusively below
        messages = [{"role": m.role, "content": m.content} for m in req.messages]

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": system_prompt}] + messages,
            max_tokens=400,
            temperature=0.7,
        )

        reply = response.choices[0].message.content.strip()
        return JSONResponse(content={"reply": reply}, headers=_CORS_HEADERS)

    except Exception as e:
        logger.error("Concierge error: %s", e)
        return JSONResponse(
            status_code=500,
            content={"error": "Unable to connect. Please request a demo directly."},
            headers=_CORS_HEADERS,
        )
