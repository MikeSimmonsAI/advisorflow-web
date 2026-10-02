"""READINESS - app, database, jobs and optional providers, reported separately.

`/health` stays the liveness probe Render watches: it answers "is the process
up" and nothing else, so an outage in an OPTIONAL paid provider can never get
the service restarted or taken out of rotation.

This module answers the next questions without collapsing them into one word:

    app        the process and its build
    database   can we round-trip a query, and how long did it take
    jobs       each background loop's LAST recorded run (job_runs ledger):
               error / success / running / never ran - with its age. No made-up "expected
               interval": the ledger says what happened, and an operator
               reads the age.
    providers  optional integrations: NOT CONFIGURED | CONFIGURED | DISABLED.
               Presence of configuration only - never a test call (a test
               call to a paid API is spend), never a secret value.

OVERALL is decided by app + database ONLY. A failed job makes it "degraded";
a missing optional provider never changes it.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session

OK, DEGRADED, FAILED = "ok", "degraded", "failed"
NOT_CONFIGURED, CONFIGURED, DISABLED = "not_configured", "configured", "disabled"


def _env(*names) -> bool:
    return all(bool((os.environ.get(n) or "").strip()) for n in names)


def database(db: Session) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1")).scalar()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {"status": OK if ms < 1500 else DEGRADED, "latency_ms": ms}
    except Exception as exc:                                     # noqa: BLE001
        db.rollback()
        return {"status": FAILED, "error": type(exc).__name__}


def jobs(db: Session) -> Dict[str, Any]:
    try:
        from app.models.job_models import JobName, JobRun
    except Exception:                                            # noqa: BLE001
        return {"status": OK, "items": [], "note": "job ledger unavailable in this build"}
    names = sorted({v for k, v in vars(JobName).items() if k.isupper() and isinstance(v, str)})
    now = datetime.utcnow()
    items: List[Dict[str, Any]] = []
    failed = 0
    try:
        for name in names:
            last = (db.query(JobRun).filter(JobRun.job_name == name)
                    .order_by(JobRun.started_at.desc()).first())
            if last is None:
                items.append({"job": name, "last_status": "never_ran"})
                continue
            age = int((now - (last.finished_at or last.started_at)).total_seconds() // 60) \
                if (last.finished_at or last.started_at) else None
            if last.status in ("error", "failed"):        # the ledger writes 'error'
                failed += 1
            items.append({"job": name, "last_status": last.status, "age_minutes": age})
    except Exception as exc:                                     # noqa: BLE001
        db.rollback()
        return {"status": DEGRADED, "items": items, "error": type(exc).__name__}
    return {"status": DEGRADED if failed else OK, "failed": failed, "items": items}


def providers(db: Session) -> List[Dict[str, Any]]:
    out = []

    def add(key, label, configured, *, disabled=False, note=None, optional=True):
        out.append({"key": key, "label": label, "optional": optional,
                    "status": DISABLED if (configured and disabled) else
                    CONFIGURED if configured else NOT_CONFIGURED,
                    "note": note})

    try:
        from app.services import ai_gateway
        ai_bg = ai_gateway.background_enabled()
    except Exception:                                            # noqa: BLE001
        ai_bg = None
    add("ai", "AI (drafting, classification)", _env("OPENAI_API_KEY"),
        note=("background automation is OFF" if ai_bg is False else None))
    add("email", "Platform email (Resend)", _env("RESEND_API_KEY"),
        note="workspaces may also send from a connected Microsoft 365 mailbox")
    add("sms", "Platform SMS / voice (Twilio)", _env("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
        note="workspaces may hold their own Twilio credentials")
    add("push", "Web push (VAPID)", _env("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"),
        note="in-app notifications work without it")
    add("microsoft", "Microsoft 365 sign-in", _env("MICROSOFT_CLIENT_SECRET"))
    add("google", "Google calendar sign-in", _env("GOOGLE_CLIENT_SECRET"))
    add("zoom", "Zoom meetings", _env("ZOOM_CLIENT_SECRET"))
    add("stripe", "Payments (Stripe)", _env("STRIPE_SECRET_KEY"))
    try:
        from app.services import outbound_brake
        if outbound_brake.engaged():
            out.insert(0, {"key": "emergency_stop", "label": "OUTBOUND EMERGENCY STOP", "optional": False,
                           "status": DISABLED, "note": "All SMS, calls and email are refused (%s)." % outbound_brake.ENV})
    except Exception:                                            # noqa: BLE001
        pass
    add("voice_ai", "AI voice (Retell)", _env("RETELL_API_KEY"))
    try:
        from app.services.evosense import providers as PV
        live = [p for p in PV.PROVIDERS.values()
                if getattr(p, "scope", "") != "sandbox" and p.required_env and p.is_configured()]
        add("property_data", "Wholesale property / skip-trace providers", bool(live),
            note="%d live provider(s); sandbox providers always available" % len(live))
    except Exception:                                            # noqa: BLE001
        add("property_data", "Wholesale property / skip-trace providers", False,
            note="registry unavailable")
    return out


def report(db: Session, *, detail: bool = False) -> Dict[str, Any]:
    from app.main import _build_metadata  # the same build block /health and /version use
    dbr = database(db)
    jr = jobs(db) if dbr["status"] != FAILED else {"status": DEGRADED, "items": [], "note": "database unavailable"}
    overall = FAILED if dbr["status"] == FAILED else DEGRADED if (dbr["status"] == DEGRADED or jr["status"] == DEGRADED) else OK
    out = {"status": overall, "app": {"status": OK, "build": _build_metadata()},
           "database": dbr, "jobs": {"status": jr["status"], "failed": jr.get("failed", 0)}}
    if detail:
        out["jobs"] = jr
        out["providers"] = providers(db)
        out["providers_note"] = ("Optional providers never change the overall status. "
                                 "CONFIGURED means credentials are present; no test call is made.")
    return out
