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

EXPOSE 8000

# Run migrations then start the API. Render sets $PORT; default to 8000 locally.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
