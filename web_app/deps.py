"""FastAPI dependency resolving the current request's SessionData via a
cookie, creating a new session on first visit (see session.py's docstring)."""

from __future__ import annotations

from fastapi import Request, Response

from web_app.session import SESSION_COOKIE_NAME, SessionData, store


def get_session(request: Request, response: Response) -> SessionData:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    session_id, data = store.get_or_create(session_id)
    response.set_cookie(SESSION_COOKIE_NAME, session_id, httponly=True, samesite="lax")
    return data
