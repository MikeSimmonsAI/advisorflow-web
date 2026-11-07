"""PUBLIC: the post-visit survey (Issue #507, Restland).

GET  /visit-survey/{token}   - what the page needs (first name, advisor), or
                               its state: open | submitted | expired
POST /visit-survey/{token}   - the family's answers, once

No auth: the 32-character token is the only key. Nothing private is in the
URL or the response. Rate limited like the booking page.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.services.programs import visit_survey as VS

router = APIRouter(prefix="/visit-survey", tags=["visit-survey"])


class SurveyIn(BaseModel):
    rating: Optional[int] = None
    helpful: Optional[str] = None
    comments: Optional[str] = None
    follow_up: Optional[bool] = None
    follow_up_method: Optional[str] = None


_STATUS = {"not_found": 404, "already": 409, "expired": 410, "invalid": 422}


@router.get("/{token}")
@limiter.limit("60/minute")
def survey_context(request: Request, token: str, db: Session = Depends(get_db)):
    try:
        return VS.context(db, token)
    except VS.SurveyError as e:
        raise HTTPException(status_code=_STATUS.get(e.code, 400), detail=e.message)


@router.post("/{token}")
@limiter.limit("10/minute")
def survey_submit(request: Request, token: str, body: SurveyIn, db: Session = Depends(get_db)):
    try:
        return VS.submit(db, token, body.dict())
    except VS.SurveyError as e:
        raise HTTPException(status_code=_STATUS.get(e.code, 400), detail=e.message)
