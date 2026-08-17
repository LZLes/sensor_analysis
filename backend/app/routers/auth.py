from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import GoogleTokenInvalid, get_current_user, verify_google_id_token
from ..db.models import User
from ..db.session import get_db
from ..schemas import GoogleLoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=UserOut)
def login_with_google(body: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)) -> User:
    try:
        claims = verify_google_id_token(body.id_token)
    except GoogleTokenInvalid as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid Google token: {exc}") from exc

    email = claims["email"].lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # No self-signup: the allowlist is the users table itself. An admin
        # must add this teammate's email before they can sign in.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Your account isn't on the team allowlist yet — ask an admin to add you.",
        )
    request.session["user_id"] = str(user.id)
    return user


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
