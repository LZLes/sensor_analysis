# Deployment

Free-tier-friendly stack: **Vercel** for the React frontend, **Neon** for a
serverless Postgres that never expires and scales to zero, **Google Cloud
Run** for the FastAPI backend (also scales to zero — you're unlikely to be
billed anything at this app's scale). All three have real, indefinite free
tiers, unlike Render's free web service (spins down + cold-starts on every
idle period) or free Postgres (expires after 30 days).

## 1. Database (Neon)

1. Sign up at neon.tech, create a project (pick a region close to wherever
   Cloud Run ends up, e.g. `us-central1` for both).
2. Neon gives you a connection string immediately, e.g.
   `postgresql://user:password@ep-xxxx.us-east-2.aws.neon.tech/sensor_analysis?sslmode=require`.
   Copy it — `app/config.py` already normalizes the bare `postgresql://`
   scheme to `postgresql+psycopg://` for you, and passes the `?sslmode=require`
   query param straight through (psycopg3 understands it natively).
3. Run migrations against it from your machine (Cloud Run intentionally
   does **not** auto-migrate on boot — see the Dockerfile comment on why):
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

## 2. Backend (Google Cloud Run)

Requires a Google Cloud project with billing enabled (Cloud Run's free tier
is generous, but the project still needs a billing account attached) and
the `gcloud` CLI installed and logged in (`gcloud auth login`).

1. Deploy straight from source — Cloud Build builds `backend/Dockerfile`
   for you, no local Docker required:
   ```bash
   cd backend
   gcloud run deploy sensor-analysis-api \
     --source . \
     --region us-central1 \
     --allow-unauthenticated \
     --set-env-vars "DATABASE_URL=postgresql://user:password@ep-xxxx...neon.tech/sensor_analysis?sslmode=require" \
     --set-env-vars "SESSION_SECRET=$(openssl rand -hex 32)" \
     --set-env-vars "FRONTEND_ORIGIN=http://localhost:5173"
   ```
   (`--allow-unauthenticated` means "anyone can reach the API" at the
   network level — your own Google-OAuth-plus-allowlist auth is still what
   actually gates access to data; Cloud Run's IAM auth is a separate,
   coarser layer you don't need here.)
2. Note the service URL it prints, e.g.
   `https://sensor-analysis-api-xxxxx-uc.a.run.app` — you'll need it for
   the frontend and for the Google OAuth client below.
3. Add the remaining secrets (do this via the Cloud Run console → your
   service → Edit & Deploy New Revision → Variables & Secrets, or append
   more `--set-env-vars` flags to the command above and redeploy):
   - `GOOGLE_OAUTH_CLIENT_ID` — from the Google Cloud Console OAuth client
     you create for "Sign in with Google" (Authorized JavaScript origins:
     your Vercel URL, added in step 3 below).
   - `ANTHROPIC_API_KEY` — for the AI Insights feature. Omitting it just
     disables that one endpoint (503), the rest of the app works fine.
   - For anything you'd rather not leave as a plain env var long-term
     (`ANTHROPIC_API_KEY`, `SESSION_SECRET`), Cloud Run integrates with
     Secret Manager — optional hardening, not required to get started.
4. Once the frontend has a URL (step 3 below), come back and update
   `FRONTEND_ORIGIN` to it and redeploy — CORS will reject the frontend's
   requests otherwise.

Redeploying after a code change is the same command as step 1 — Cloud Run
keeps your env vars across revisions unless you explicitly change them.

## 3. Frontend (Vercel)

1. Import the repo into Vercel. In the project's Settings → General, set
   **Root Directory** to `frontend/` (this is a monorepo — Vercel needs to
   know which subdirectory to build; `frontend/vercel.json` only covers the
   build command/output/rewrites, not the root directory).
2. Add an environment variable `VITE_API_BASE` = your Cloud Run URL from
   step 2.2 above (e.g. `https://sensor-analysis-api-xxxxx-uc.a.run.app`).
3. Deploy. Vercel auto-detects Vite and uses `frontend/vercel.json`'s
   `buildCommand`/`outputDirectory`; the `rewrites` entry makes client-side
   routing (React Router) work on a hard refresh/deep link.
4. Add this Vercel URL as an Authorized JavaScript origin on your Google
   OAuth client, and as `FRONTEND_ORIGIN` on the Cloud Run service
   (step 2.4 above).

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
