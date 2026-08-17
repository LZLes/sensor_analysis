"""
Runs once on every container startup (see the Dockerfile CMD), before
uvicorn starts serving. Two jobs, both safe to repeat on every restart:

1. Apply any pending Alembic migrations.
2. If INITIAL_ADMIN_EMAIL is set and no user with that email exists yet,
   create it as an admin. This exists so a first deploy needs nothing
   more than filling in a couple of environment variable text boxes in
   the Render dashboard — no local terminal, no `psql`, no `alembic`
   installed on your own machine.

Deliberately NOT run as a separate Cloud-Run-style one-off job: this app
is deployed as a single Render web service instance (not
auto-scaled to multiple concurrent cold starts the way Cloud Run can be),
so running migrations inline on startup doesn't carry the "multiple
instances race to migrate at once" risk that pattern would have there.
If this ever moves to a platform/plan that runs several instances
concurrently, move this back to a separate deploy step instead.
"""
from __future__ import annotations

import subprocess
import sys
import uuid

from sqlalchemy import select

from .config import get_settings
from .db.models import User
from .db.session import SessionLocal


def run_migrations() -> None:
    result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"alembic upgrade head failed (exit {result.returncode})")


def seed_initial_admin() -> None:
    settings = get_settings()
    email = settings.initial_admin_email.strip().lower()
    if not email:
        return
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            return
        db.add(User(id=uuid.uuid4(), email=email, name=settings.initial_admin_name, is_admin=True))
        db.commit()
        print(f"Created initial admin user: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    run_migrations()
    seed_initial_admin()
