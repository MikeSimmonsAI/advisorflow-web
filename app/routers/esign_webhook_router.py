"""E-signature provider callbacks.

    POST /esign/docuseal/webhook

No user session (the provider calls it), so it sits outside the wholesale
router and its feature gate. Nothing in the payload is trusted: the
submission id is only used to find OUR document (one that we sent through
DocuSeal), and its status is then read back from DocuSeal's own API with our
key. A forged call can at most make us ask DocuSeal a question.

Optional shared secret: when WHOLESALE_DOCUSEAL_WEBHOOK_SECRET is set, the
call must carry it in the X-Docuseal-Secret header (DocuSeal's webhook
settings let you add that header).
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.wholesale_models import WholesaleDocument
from app.services import wholesale_esign as esign

log = logging.getLogger(__name__)

router = APIRouter(prefix="/esign", tags=["esign-webhooks"])


def _submission_id(payload) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    event = str(payload.get("event_type") or "")
    if event.startswith("submission."):
        sid = data.get("id")
    else:                                   # form.* events describe one submitter
        sid = (data.get("submission") or {}).get("id") or data.get("submission_id")
    return str(sid) if sid is not None and str(sid).isdigit() else None


@router.post("/docuseal/webhook")
async def docuseal_webhook(request: Request, db: Session = Depends(get_db)):
    secret = os.environ.get("WHOLESALE_DOCUSEAL_WEBHOOK_SECRET")
    if secret:
        got = request.headers.get("X-Docuseal-Secret") or ""
        if not hmac.compare_digest(got, secret):
            raise HTTPException(status_code=401, detail="Bad webhook secret")
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True, "ignored": "not json"}
    sid = _submission_id(payload)
    if not sid:
        return {"ok": True, "ignored": "no submission id"}
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.signature_provider == "docuseal",
                   WholesaleDocument.external_ref == sid).first())
    if doc is None:
        return {"ok": True, "ignored": "not one of ours"}
    provider = esign.PROVIDERS["docuseal"]
    if not provider.is_configured():
        return {"ok": True, "ignored": "provider not configured"}
    try:
        outcome = esign.submission_outcome(provider.fetch_submission(sid))
    except Exception as exc:
        log.warning("docuseal webhook: could not read submission %s: %s", sid, exc)
        raise HTTPException(status_code=502, detail="Could not confirm with DocuSeal")  # DocuSeal retries
    from app.routers.wholesale_contracts_router import apply_signature_outcome
    changed = apply_signature_outcome(db, doc, outcome, actor="DocuSeal")
    db.commit()
    return {"ok": True, "changed": changed, "status": esign.normalise(doc.status)}
