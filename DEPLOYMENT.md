# Deployment

**Render** hosts both the frontend (static site) and the backend (Docker
web service); **Neon** hosts Postgres. No credit card required anywhere.
This replaced two earlier options considered for this app — see the
"Hosting" section of the migration plan for the full history — Google
Cloud Run required a billing-enabled project even for its free tier, and
Vercel's serverless functions have a 250 MB size limit that this backend's
scientific-computing dependencies (numpy+scipy+pandas+matplotlib alone
measure 277 MB installed) blow past on their own.

The one tradeoff worth knowing up front: Render's free web service spins
down after 15 minutes idle and takes 30-60s to wake on the next request.
For a tool the team dips into occasionally rather than continuously,
that's a real but bounded cost — no data loss, just a slow first load.

`render.yaml` at the repo root is a Render **Blueprint** — it describes
both services declaratively, so most of this is "click Apply" rather than
filling in forms by hand.

## 1. Database (Neon)

1. Sign up at neon.tech, create a project.
2. Neon gives you a connection string immediately, e.g.
   `postgresql://user:password@ep-xxxx.us-east-2.aws.neon.tech/sensor_analysis?sslmode=require`.
   Copy it — `app/config.py` already normalizes the bare `postgresql://`
   scheme to `postgresql+psycopg://` for you, and passes the `?sslmode=require`
   query param straight through (psycopg3 understands it natively).
3. Run migrations against it from your machine:
   ```bash
   cd backend
   DATABASE_URL="postgresql://user:password@ep-xxxx...neon.tech/sensor_analysis?sslmode=require" \
     alembic upgrade head
   ```
4. Bootstrap the first admin user (there's no self-signup — `users` rows
   ARE the allowlist):
   ```bash
   psql "postgresql://user:password@ep-xxxx...neon.tech/sensor_analysis?sslmode=require" -c \
     "INSERT INTO users (id, email, name, is_admin) VALUES (gen_random_uuid(), 'you@yourteam.com', 'Your Name', true);"
   ```
   From then on, add teammates the same way (direct SQL, until an admin UI exists).

## 2. Both services (Render Blueprint)

1. In the Render dashboard: **New +** → **Blueprint**, connect this GitHub
   repo. Render reads `render.yaml` and shows you two services to create:
   - `sensor-analysis-api` — the FastAPI backend (Docker, `backend/Dockerfile`).
   - `sensor-analysis-frontend` — the React frontend (static site, built
     from `frontend/`).
2. Click **Apply**. Render creates both, generates `SESSION_SECRET`
   automatically, and leaves the rest of the env vars blank (marked
   `sync: false` in `render.yaml`) for you to fill in next.
3. Once `sensor-analysis-api` has deployed, copy its URL (e.g.
   `https://sensor-analysis-api.onrender.com`) and set these in its
   **Environment** tab:
   - `DATABASE_URL` — the Neon connection string from step 1.
   - `GOOGLE_OAUTH_CLIENT_ID` — from the Google Cloud Console OAuth client
     you create for "Sign in with Google" (Authorized JavaScript origins:
     the frontend's Render URL from step 4 below).
   - `ANTHROPIC_API_KEY` — for the AI Insights feature. Leaving it unset
     just disables that one endpoint (503), everything else works fine.
4. Once `sensor-analysis-frontend` has deployed, copy its URL (e.g.
   `https://sensor-analysis-frontend.onrender.com`) and set:
   - On `sensor-analysis-frontend`: `VITE_API_BASE` = the backend URL from
     step 3. **Redeploy the frontend after setting this** — Vite bakes env
     vars in at build time, not read at runtime, so a plain env var change
     alone won't take effect.
   - On `sensor-analysis-api`: `FRONTEND_ORIGIN` = this frontend URL, then
     it redeploys automatically. CORS will reject the frontend's requests
     until this is set correctly.
5. Add the frontend's URL as an Authorized JavaScript origin on the Google
   OAuth client from step 3.

From here, every push to the connected branch redeploys both services
automatically — that's Render's default behavior, no separate CI config
needed (unlike Cloud Build, which this app used briefly and no longer needs).

## Local development

Backend:
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL for your local Postgres, etc.
alembic upgrade head
uvicorn app.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev   # proxies to http://localhost:8000 by default (VITE_API_BASE)
```

Run tests:
```bash
cd backend && pytest
cd frontend && npx vitest run
```
