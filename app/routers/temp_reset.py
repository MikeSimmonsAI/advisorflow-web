"""
TEMPORARY — one-time password reset endpoint.
Delete this file immediately after use.
"""
import os
import bcrypt
from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import Session
from fastapi import Depends
from app.deps import get_db
from sqlalchemy import text

router = APIRouter()

SECRET = "xK9mP2qRv7nL4jW"  # one-time token

@router.post("/internal/reset-pw")
def reset_pw(token: str, db: Session = Depends(get_db)):
    if token != SECRET:
        raise HTTPException(status_code=403, detail="no")
    h = bcrypt.hashpw(b"Newpc!1me", bcrypt.gensalt()).decode()
    db.execute(
        text("UPDATE users SET hashed_password=:h WHERE email='mike@simmonsstrong.com'"),
        {"h": h}
    )
    db.commit()
    return {"ok": True}
