"""
Google OAuth + allowlist auth.

No self-signup: an admin adds a teammate's email directly to the `users`
table first. The frontend gets a Google ID token via Google Identity
Services JS ("Sign in with Google"), POSTs it to /auth/google; this module
verifies the token's signature/audience with Google, looks the email up in
`users`, and — only if found — establishes a session (a signed cookie via
Starlette's SessionMiddleware, storing just the user id).
"""
from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from .config import get_settings
from .db.models import User
from .db.session import get_db


class GoogleTokenInvalid(Exception):
    pass


def verify_google_id_token(token: str) -> dict:
    """Verifies a Google ID token's signature/expiry/audience.
    Returns the decoded claims (contains 'email', 'name', 'email_verified').
    Raises GoogleTokenInvalid on any failure — never trust an unverified token."""
    settings = get_settings()
    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=settings.google_oauth_client_id,
        )
    except Exception as exc:
        raise GoogleTokenInvalid(str(exc)) from exc
    if not claims.get("email_verified", False):
        raise GoogleTokenInvalid("Google account email is not verified.")
    return claims


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency — requires an active session, 401s otherwise.
    Use on every route that needs an authenticated user."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    user = db.get(User, uuid.UUID(user_id))
    if user is None:
        # Their account was removed from the allowlist after the cookie was issued.
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account no longer has access.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required.")
    return user
