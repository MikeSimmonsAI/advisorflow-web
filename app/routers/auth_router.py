from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.deps import get_db, get_current_user
from app.services.auth_service import authenticate_user, create_access_token, hash_password, verify_password
from app.models.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    full_name: str
    organization_id: str
    must_change_password: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        role=user.role,
        full_name=user.full_name,
        organization_id=user.organization_id,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password")
def change_password(
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lets an advisor change their own password - covers the gap flagged
    in the frontend README: advisors were stuck with the temp password
    from app/seed.py with no way to change it themselves. Requires the
    current password to confirm identity, even though the JWT already
    authenticates them, since changing a password is a sensitive action
    worth a second check.
    """
    if not verify_password(req.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if len(req.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must be at least 8 characters")

    current_user.password_hash = hash_password(req.new_password)
    current_user.must_change_password = False
    db.commit()
    return {"success": True}
