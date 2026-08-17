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

# Render (like most container platforms) injects $PORT and expects the
# container to listen on it; default to 8080 locally so `docker run`
# matches.
#
# app/bootstrap.py runs migrations + (optionally) creates the first admin
# user on every startup, so a fresh deploy needs no local terminal at all
# — see that file's docstring for why this is safe specifically on
# Render's single-instance free tier, and what to change if that ever
# stops being true.
CMD python -m app.bootstrap && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
