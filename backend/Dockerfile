FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg (libpq) and matplotlib (headless, no extra libs needed
# beyond what's already in slim + the Agg backend set in analysis/rendering.py).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Cloud Run injects $PORT (8080 by default) and expects the container to
# listen on it; default to 8080 locally too so `docker run` matches.
# Migrations are NOT run here on purpose: Cloud Run can cold-start several
# container instances concurrently under load, and racing `alembic upgrade
# head` across instances is a real failure mode. Run migrations as a
# separate one-off step (see DEPLOYMENT.md) before deploying a new revision.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
