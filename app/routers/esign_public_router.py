"""EvoSys e-signature - the signer's side. No account: the private link token
IS the authorization (stored only as a hash; replaced on every resend; dead
once the envelope is voided, declined or expired).

    GET  /esign/sign/{token}             the document and where things stand
    POST /esign/sign/{token}/code        email a 6-digit code
    POST /esign/sign/{token}/verify      {code}
    POST /esign/sign/{token}/sign        {consent, typed_name, drawn_png?}
    POST /esign/sign/{token}/decline     {reason}
    GET  /esign/sign/{token}/signed.pdf  the sealed copy, once everyone signed
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services import evosys_esign as ev

router = APIRouter(prefix="/esign/sign", tags=["esign-public"])


def _who(request: Request):
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    ip = fwd or (request.client.host if request.client else None)
    return ip, request.headers.get("user-agent")


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ev.EsignError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/{token}")
def view(token: str, request: Request, db: Session = Depends(get_db)):
    ip, ua = _who(request)
    out = _run(ev.public_view, db, token, ip=ip, ua=ua)
    return out


@router.post("/{token}/code")
def code(token: str, request: Request, db: Session = Depends(get_db)):
    ip, ua = _who(request)
    return _run(ev.send_code, db, token, ip=ip, ua=ua)


class VerifyIn(BaseModel):
    code: str = Field(..., max_length=12)


@router.post("/{token}/verify")
def verify(token: str, payload: VerifyIn, request: Request, db: Session = Depends(get_db)):
    ip, ua = _who(request)
    return _run(ev.verify_code, db, token, payload.code, ip=ip, ua=ua)


class SignIn(BaseModel):
    consent: bool = False
    typed_name: str = Field("", max_length=200)
    drawn_png: Optional[str] = Field(None, max_length=450_000)


@router.post("/{token}/sign")
def sign(token: str, payload: SignIn, request: Request, db: Session = Depends(get_db)):
    ip, ua = _who(request)
    return _run(ev.sign, db, token, consent=payload.consent, typed_name=payload.typed_name,
                drawn_png=payload.drawn_png, ip=ip, ua=ua)


class DeclineIn(BaseModel):
    reason: Optional[str] = Field(None, max_length=600)


@router.post("/{token}/decline")
def decline(token: str, payload: DeclineIn, request: Request, db: Session = Depends(get_db)):
    ip, ua = _who(request)
    return _run(ev.decline, db, token, payload.reason or "", ip=ip, ua=ua)


@router.get("/{token}/signed.pdf")
def signed_pdf(token: str, db: Session = Depends(get_db)):
    title, pdf = _run(ev.signed_pdf_for_token, db, token)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % ev._filename(title),
                             "Cache-Control": "private, no-store"})
