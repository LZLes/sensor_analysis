import uuid

from sqlalchemy import select

from app.bootstrap import seed_initial_admin
from app.config import get_settings
from app.db.models import User
from app.db.session import SessionLocal


def test_seed_initial_admin_creates_user_when_email_configured(monkeypatch):
    email = f"bootstrap-test-{uuid.uuid4()}@example.com"
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", email)
    monkeypatch.setenv("INITIAL_ADMIN_NAME", "Test Admin")
    get_settings.cache_clear()
    try:
        seed_initial_admin()

        db = SessionLocal()
        try:
            user = db.scalar(select(User).where(User.email == email))
            assert user is not None
            assert user.name == "Test Admin"
            assert user.is_admin is True
        finally:
            db.delete(db.scalar(select(User).where(User.email == email)))
            db.commit()
            db.close()
    finally:
        get_settings.cache_clear()


def test_seed_initial_admin_is_idempotent(monkeypatch):
    email = f"bootstrap-test-{uuid.uuid4()}@example.com"
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", email)
    get_settings.cache_clear()
    try:
        seed_initial_admin()
        seed_initial_admin()  # must not raise (e.g. a unique-constraint violation) or create a duplicate

        db = SessionLocal()
        try:
            matches = list(db.scalars(select(User).where(User.email == email)))
            assert len(matches) == 1
            for u in matches:
                db.delete(u)
            db.commit()
        finally:
            db.close()
    finally:
        get_settings.cache_clear()


def test_seed_initial_admin_noop_when_email_blank(monkeypatch):
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", "")
    get_settings.cache_clear()
    try:
        seed_initial_admin()  # must not raise
    finally:
        get_settings.cache_clear()
