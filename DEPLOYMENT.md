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
3. Run migrations against it. Either from your machine:
   ```bash
   cd backend
   DATABASE_URL="postgresql://user:password@ep-xxxx...neon.tech/sensor_analysis?sslmode=require" \
     alembic upgrade head
   ```
   or, once you have a Google Cloud project (step 2 below), as a **Cloud
   Run Job** — same container image, no local network access to Neon
   required, and it's the version you'll want once a teammate other than
   you needs to run a migration:
   ```bash
   gcloud run jobs deploy migrate \
     --source backend \
     --region us-central1 \
     --set-secrets "DATABASE_URL=database-url:latest" \
     --command alembic --args upgrade,head

   gcloud run jobs execute migrate --region us-central1 --wait
   ```
   (assumes the `database-url` secret from step 2's Secret Manager section
   below already exists — create that first if you're doing the Job route
   before the service route.)
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

### 2a. Store secrets in Secret Manager

Anything that's actually sensitive (the database URL contains a password;
`SESSION_SECRET` signs auth cookies; the Anthropic key costs money if
leaked) goes in Secret Manager instead of a plain `--set-env-vars` value —
those show up in `gcloud` shell history and the console's env var list in
cleartext, secrets don't:

```bash
printf '%s' "postgresql://user:password@ep-xxxx...neon.tech/sensor_analysis?sslmode=require" \
  | gcloud secrets create database-url --data-file=-
printf '%s' "$(openssl rand -hex 32)" \
  | gcloud secrets create session-secret --data-file=-
printf '%s' "sk-ant-..." \
  | gcloud secrets create anthropic-api-key --data-file=-
```

`gcloud run deploy`/`jobs deploy` auto-grant the service's default runtime
account access to secrets referenced via `--set-secrets`, so no separate
IAM step is needed for the common case.

### 2b. Deploy the service

```bash
cd backend
gcloud run deploy sensor-analysis-api \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets "DATABASE_URL=database-url:latest,SESSION_SECRET=session-secret:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --set-env-vars "FRONTEND_ORIGIN=http://localhost:5173,GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com"
```
(`--allow-unauthenticated` means "anyone can reach the API" at the network
level — your own Google-OAuth-plus-allowlist auth is still what actually
gates access to data; Cloud Run's IAM auth is a separate, coarser layer you
don't need here. Omitting `ANTHROPIC_API_KEY`/the whole secret entirely is
fine too — it just disables the AI Insights endpoint, 503, nothing else
breaks.)

Note the service URL it prints, e.g.
`https://sensor-analysis-api-xxxxx-uc.a.run.app` — you'll need it for the
frontend and for the Google OAuth client's Authorized JavaScript Origins.

Once the frontend has a URL (step 3 below), come back and update
`FRONTEND_ORIGIN` to it and redeploy — CORS will reject the frontend's
requests otherwise. Redeploying after a code change is the same command;
Cloud Run reuses the previous revision's env vars/secrets unless you
explicitly change them.

### 2c. Optional: continuous deploy via Cloud Build

So you don't run `gcloud run deploy` by hand every time:

1. One-time: create the Artifact Registry repo `backend/cloudbuild.yaml` pushes to:
   ```bash
   gcloud artifacts repositories create sensor-analysis \
     --repository-format=docker --location=us-central1
   ```
2. In the GCP Console → Cloud Build → Triggers → Connect Repository, link
   this GitHub repo, then create a trigger (on push to your deploy branch)
   pointing at `backend/cloudbuild.yaml`.
3. Every push after that builds the image, pushes it, and redeploys the
   Cloud Run service automatically — env vars/secrets from 2a/2b carry
   forward unchanged (see the comment in `cloudbuild.yaml` for why).

## 3. Frontend (Vercel)

1. Import the repo into Vercel. In the project's Settings → General, set
   **Root Directory** to `frontend/` (this is a monorepo — Vercel needs to
   know which subdirectory to build; `frontend/vercel.json` only covers the
   build command/output/rewrites, not the root directory).
2. Add an environment variable `VITE_API_BASE` = your Cloud Run URL from
   step 2b above (e.g. `https://sensor-analysis-api-xxxxx-uc.a.run.app`).
3. Deploy. Vercel auto-detects Vite and uses `frontend/vercel.json`'s
   `buildCommand`/`outputDirectory`; the `rewrites` entry makes client-side
   routing (React Router) work on a hard refresh/deep link.
4. Add this Vercel URL as an Authorized JavaScript origin on your Google
   OAuth client, and as `FRONTEND_ORIGIN` on the Cloud Run service
   (step 2b above).

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
